# -*- coding: utf-8 -*-
"""列出「现在真有独立窗口」的应用，并直接吐出可以粘进 apps.json 的 native 条目。

用法：
    python tools/list-windows-apps.py            # 表格：exe / pid / 窗口标题
    python tools/list-windows-apps.py --json     # 只吐 apps.json 片段（含占位变量）
    python tools/list-windows-apps.py --all      # 连系统壳窗口一起列出来（默认已过滤）

只依赖标准库 + Win32（ctypes）。为什么不用「扫开始菜单」或「扫 Program Files」：
装着 ≠ 能开。**有可见顶层窗口**才是「这个应用此刻真的跑起来了」的硬证据，
而能开窗口的程序一定能被 exe 直接拉起——这正是 native 条目需要的事实。
"""
import argparse
import ctypes
import ctypes.wintypes as w
import json
import os
import subprocess

user32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
dwm = ctypes.windll.dwmapi

EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_CLOAKED = 14

# 系统壳 / 输入法 / 无主宿主：有窗口但不是「装的应用」，别往侧栏塞
SKIP = {
    "applicationframehost.exe", "textinputhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "widgets.exe", "widgetservice.exe", "lockapp.exe", "systemsettings.exe",
    "explorer.exe", "taskmgr.exe", "mmc.exe", "dwm.exe", "sihost.exe",
    "ctfmon.exe", "msedgewebview2.exe", "runtimebroker.exe", "securityhealthsystray.exe",
    "phoneexperiencehost.exe", "yourphone.exe", "useroobebroker.exe",
}

# 控制台宿主：窗口是真的，但作者们九成不想把它当「应用」登记
CONSOLE = {"windowsterminal.exe", "conhost.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe"}

# 宿主自己：侧栏里放一个「打开宿主」的按钮没有意义（`--all` 可见）
HOST = {"hermes.exe"}


def exe_path(pid):
    """pid -> 完整 exe 路径；权限不足时返回 None（受保护进程常见）。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = w.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def is_cloaked(hwnd):
    """UWP 关闭后窗口并不销毁，只是被 DWM 隐藏（cloaked）——不过滤会列出幽灵条目。"""
    val = ctypes.c_int(0)
    if dwm.DwmGetWindowAttribute(w.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)) == 0:
        return val.value != 0
    return False


def visible_windows():
    rows = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
        if ex & 0x00000080:                    # WS_EX_TOOLWINDOW
            return True
        if is_cloaked(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rows.append({"pid": pid.value, "title": title})
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return rows


def fallback_names():
    """拿不到完整路径时退化成 exe 名（tasklist 不需要权限）。用 bytes 解码，别用 text=True。"""
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=90).stdout or b""
    txt = out.decode("gbk", "replace")
    names = {}
    for line in txt.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            names[int(parts[1])] = parts[0]
    return names


def placeholder(path):
    """把本机绝对路径换成占位变量，清单才能跨机器用。

    长根先替换：`Program Files (x86)` 是 `Program Files` 的前缀超集，顺序反了会拼出坏路径。
    """
    home = os.path.expanduser("~")
    roots = [
        (os.environ.get("ProgramFiles(x86)", ""), "{PROGRAMFILES_X86}"),
        (os.environ.get("PROGRAMFILES", ""), "{PROGRAMFILES}"),
        (os.environ.get("LOCALAPPDATA", ""), "{LOCALAPPDATA}"),
        (os.environ.get("APPDATA", ""), "{APPDATA}"),
        (os.environ.get("PROGRAMDATA", ""), "{PROGRAMDATA}"),
        (home, "{HOME}"),
    ]
    low = path.lower()
    for root, tok in roots:
        if root and low.startswith(root.lower()):
            return tok + path[len(root):]
    return path


def _keep(base, args):
    """一条窗口记录该不该留下——两条输出路径共用，避免表格和 JSON 说不一致的话。"""
    if not args.all and base in SKIP:
        return False
    if not args.keep_console and base in CONSOLE:
        return False
    if not args.all and base in HOST:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="只输出 apps.json 片段（全部条目，含占位变量）")
    ap.add_argument("--all", action="store_true", help="连系统壳窗口一起列出（默认过滤）")
    ap.add_argument("--keep-console", action="store_true", help="保留控制台宿主（终端）条目")
    args = ap.parse_args()

    rows = visible_windows()
    names = fallback_names()
    seen, apps = {}, []
    for r in rows:
        path = exe_path(r["pid"]) or ""
        base = os.path.basename(path).lower() if path else names.get(r["pid"], "?").lower()
        if not _keep(base, args):
            continue
        key = path.lower() if path else base
        if key in seen:
            seen[key]["titles"].append(r["title"])
            continue
        seen[key] = {"exe": path, "name": base, "titles": [r["title"]]}
        apps.append(seen[key])

    apps.sort(key=lambda a: a["name"])

    if args.json:
        out = []
        for a in apps:
            eid = os.path.splitext(a["name"])[0].lower().replace(" ", "-")
            out.append({
                "id": eid,
                "name": os.path.splitext(os.path.basename(a["exe"] or a["name"]))[0],
                "kind": "native",
                "group": "独立窗口",
                "exe": placeholder(a["exe"]) if a["exe"] else "<未取到路径，右键快捷方式看属性>",
            })
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print("窗口 %d 个 -> 独立应用 %d 个%s" % (
        len(rows), len(apps), "" if args.all else "（已滤掉系统壳/终端）"))
    print("%-26s %-7s %s" % ("EXE", "PID", "窗口标题"))
    for r in rows:
        path = exe_path(r["pid"]) or ""
        base = os.path.basename(path).lower() if path else names.get(r["pid"], "?").lower()
        if not _keep(base, args):
            continue
        print("%-26s %-7s %s" % (base, r["pid"], r["title"][:58]))
    print("\n要登记进侧栏，跑：python tools/list-windows-apps.py --json")


if __name__ == "__main__":
    main()
