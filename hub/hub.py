#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Desktop Ops Hub —— 本地 AI 应用注册表 / 启动器 / 探活服务（零依赖，仅标准库）

它只干三件事：
  1. 列出你登记过的本地应用（web 服务 + 独立窗口程序）；
  2. 探活：web 看端口有没有人监听，native 看进程在不在；
  3. 一键拉起 / 停止。

条目登记在同目录的 apps.json（照 apps.example.json 复制改名即可），
**改完存盘即生效，不用重启本服务**。

所有路径字段都支持变量占位，别把机器路径写死：
  {HERE}          hub.py 所在目录
  {HOME}          用户主目录
  {APPDATA}       %APPDATA%（Windows）
  {LOCALAPPDATA}  %LOCALAPPDATA%（Windows）
  {PY}            运行本服务的 Python 解释器
  {id} {name} {port} {url} {exe}   该条目自身的字段

端点：
  GET  /                 内置工作台页面（hub/index.html）
  GET  /api/state        条目 + 实时状态
  POST /api/start/<id>   启动
  POST /api/stop/<id>    停止（web 按端口找监听进程，native 不做强杀）
  GET  /api/ping?src=…   面板自检，把 query 追加进 logs/plugin-alive.txt
  GET  /api/health       {"ok": true, "n": <条目数>}

环境变量：
  WB_PORT     监听端口，默认 7777
  WB_HOST     绑定地址，默认 127.0.0.1（只给本机用，不要随手暴露到 0.0.0.0）
  WB_APPS     指定 apps.json 路径
  WB_LOGDIR   指定日志目录，默认 <HERE>/logs
"""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
LOGDIR = os.environ.get("WB_LOGDIR") or os.path.join(HERE, "logs")
APPS_FILE = os.environ.get("WB_APPS") or os.path.join(HERE, "apps.json")
PAGE_FILE = os.path.join(HERE, "index.html")

WEB_START_GRACE = 40.0          # 起服务后等端口的时间窗（Next/Vite 首编译可能 30-60s）
_EXE_CACHE = {"ts": 0.0, "set": set()}
_EXE_TTL = 3.0
_STARTING = {}                  # id -> 拉起时刻
_LOCK = threading.Lock()


# ── 注册表 ───────────────────────────────────────────────────────────────

def _vars(app):
    return {
        "HERE": HERE,
        "HOME": os.path.expanduser("~"),
        "APPDATA": os.environ.get("APPDATA", ""),
        "LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
        "PROGRAMFILES": os.environ.get("PROGRAMFILES", ""),
        "PROGRAMFILES_X86": os.environ.get("PROGRAMFILES(X86)", os.environ.get("ProgramFiles(x86)", "")),
        "PROGRAMDATA": os.environ.get("PROGRAMDATA", ""),
        "PY": sys.executable,
        "id": app.get("id", ""),
        "name": app.get("name", ""),
        "port": str(app.get("port") or ""),
        "url": app.get("url") or "",
        "exe": app.get("exe") or "",
    }


def _expand(value, app):
    """把 {VAR} 换成实际值；未知变量原样保留，方便一眼看出拼错。"""
    if not isinstance(value, str):
        return value
    table = _vars(app)
    return re.sub(r"\{(\w+)\}", lambda m: table.get(m.group(1), m.group(0)), value)


def load_apps():
    """读 apps.json（按 mtime 缓存）。字段缺失只补默认值，不猜内容。"""
    try:
        st = os.stat(APPS_FILE)
    except OSError:
        return []
    cache = load_apps.__dict__
    if cache.get("_mtime") == st.st_mtime and "_apps" in cache:
        return cache["_apps"]
    try:
        with open(APPS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as exc:
        print("[hub] apps.json 读取失败: %s" % exc, file=sys.stderr, flush=True)
        return cache.get("_apps", [])

    items = raw.get("apps") if isinstance(raw, dict) else raw
    apps = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        app = dict(item)
        app.setdefault("kind", "web")
        app.setdefault("group", "应用")
        app.setdefault("name", app["id"])
        for key in ("url", "cmd", "cwd", "exe", "sub"):
            if key in app:
                app[key] = _expand(app[key], app)
        if app["kind"] == "web" and not app.get("url") and app.get("port"):
            app["url"] = "http://127.0.0.1:%s" % app["port"]
        apps.append(app)
    cache["_mtime"] = st.st_mtime
    cache["_apps"] = apps
    return apps


def by_id(app_id):
    for app in load_apps():
        if app.get("id") == app_id:
            return app
    return None


# ── 探活 ─────────────────────────────────────────────────────────────────

def run_text(cmd, timeout=15):
    """跑一条外部命令，返回解码后的文本；失败返回空串。

    不能直接用 subprocess 的 text=True：中文 Windows 上 netstat / tasklist
    输出是 GBK，按 UTF-8 解会抛 UnicodeDecodeError，读取线程挂掉后 stdout
    变成 None，调用方一 splitlines 就崩（这是真踩过的坑，别改回去）。
    """
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        raw = subprocess.run(cmd, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, timeout=timeout,
                             **kwargs).stdout or b""
    except (OSError, subprocess.SubprocessError):
        return ""
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", "replace")


def probe(port, timeout=0.45):
    """端口有没有人在听。IPv4 和 IPv6 都要试：Vite 一类常只绑 [::1]。"""
    if not port:
        return False
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((host, int(port))) == 0:
                    return True
        except (OSError, ValueError):
            continue
    return False


def running_exes():
    """当前进程名集合（小写、带 .exe）。3 秒缓存，别每个条目都跑一遍进程表。"""
    now = time.time()
    with _LOCK:
        if now - _EXE_CACHE["ts"] < _EXE_TTL:
            return _EXE_CACHE["set"]
    names = set()
    if os.name == "nt":
        out = run_text(["tasklist", "/FO", "CSV", "/NH"])
        for line in out.splitlines():
            head = line.split('","')[0].strip('" \r')
            if head.lower().endswith(".exe"):
                names.add(head.lower())
    else:
        out = run_text(["ps", "-eo", "comm="])
        for line in out.splitlines():
            base = os.path.basename(line.strip()).lower()
            if base:
                names.add(base)
    with _LOCK:
        _EXE_CACHE.update({"ts": now, "set": names})
    return names


def exe_name(path):
    return os.path.basename(path or "").lower()


def state_of(app):
    kind = app.get("kind", "web")
    started = _STARTING.get(app.get("id"))
    starting = bool(started) and (time.time() - started) < WEB_START_GRACE
    if kind == "web":
        alive = probe(app.get("port"))
        if alive:
            _STARTING.pop(app.get("id"), None)
            starting = False
        return {
            "id": app.get("id"),
            "name": app.get("name"),
            "sub": app.get("sub") or ((":%s" % app["port"]) if app.get("port") else ""),
            "group": app.get("group"),
            "kind": "web",
            "port": app.get("port"),
            "url": app.get("url"),
            "hotkey": app.get("hotkey"),
            "alive": alive,
            "exists": True,
            "starting": starting,
        }
    exe = app.get("exe") or ""
    exists = bool(exe) and os.path.exists(exe)
    alive = exists and exe_name(exe) in running_exes()
    return {
        "id": app.get("id"),
        "name": app.get("name"),
        "sub": app.get("sub") or os.path.basename(exe or ""),
        "group": app.get("group"),
        "kind": "native",
        "port": None,
        "url": None,
        "exe": exe,
        "hotkey": app.get("hotkey"),
        "alive": alive,
        "exists": exists,
        "starting": False,
    }


# ── 启动 / 停止 ───────────────────────────────────────────────────────────

def launch(app):
    kind = app.get("kind", "web")
    st = state_of(app)
    if st["alive"]:
        return {"ok": True, "msg": "已在运行", "id": app.get("id")}

    if kind == "native":
        exe = app.get("exe") or ""
        if not exe or not os.path.exists(exe):
            return {"ok": False, "msg": "程序文件不存在: %s" % (exe or "(未配置 exe)")}
        try:
            if os.name == "nt":
                os.startfile(exe)  # noqa: S606 - 本机工具，按用户登记路径启动
            else:
                subprocess.Popen([exe], cwd=app.get("cwd") or os.path.dirname(exe),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            return {"ok": False, "msg": "启动失败: %s" % exc}
        deadline = time.time() + 6
        while time.time() < deadline and not state_of(app)["alive"]:
            time.sleep(0.4)
        return {"ok": True, "msg": "已拉起", "id": app.get("id")}

    cmd = app.get("cmd")
    if not cmd:
        return {"ok": False, "msg": "这条是 web 条目但没配 cmd，无法启动"}
    cwd = app.get("cwd") or HERE
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)           # 防止外层注入的同名包劫持子进程导入
    env.update({k: str(v) for k, v in (app.get("env") or {}).items()})
    flags = 0
    si = None
    if os.name == "nt":
        # CREATE_NO_WINDOW 而不是 DETACHED_PROCESS：detach 之后 node/next
        # 自己再申请控制台时，Win11 会把默认终端拉到任务栏，用户看到黑窗。
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | \
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
    try:
        # stdin 必须留条路：某些交互式 dev server 读到 EOF 就退出
        subprocess.Popen(cmd, cwd=cwd, env=env, shell=True,
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags,
                         startupinfo=si)
    except OSError as exc:
        return {"ok": False, "msg": "启动失败: %s" % exc}
    with _LOCK:
        _STARTING[app.get("id")] = time.time()
    return {"ok": True, "msg": "已拉起", "id": app.get("id")}


def port_pid(port):
    """谁在监听这个端口。先找 LISTEN 行，找不到再退一步认端口匹配。"""
    if not port:
        return None
    needle = ":%s" % port
    if os.name == "nt":
        out = run_text(["netstat", "-ano"])
        fallback = None
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4 or not parts[1].endswith(needle):
                continue
            if not parts[-1].isdigit():
                continue
            if "LISTEN" in line.upper():
                return int(parts[-1])
            fallback = fallback or int(parts[-1])
        return fallback
    if shutil.which("lsof"):
        for line in run_text(["lsof", "-ti", "tcp:%s" % port, "-sTCP:LISTEN"],
                             timeout=10).splitlines():
            if line.strip().isdigit():
                return int(line.strip())
    return None


def stop(app):
    if app.get("kind", "web") != "web":
        return {"ok": False, "msg": "独立窗口程序不支持从面板强杀，请用它自己的退出"}
    pid = port_pid(app.get("port"))
    if not pid:
        return {"ok": True, "msg": "没有记录到进程（端口未监听）"}
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "msg": "停止失败: %s" % exc}
    return {"ok": True, "msg": "已结束", "pid": pid}


# ── HTTP ─────────────────────────────────────────────────────────────────

def read_page():
    try:
        with open(PAGE_FILE, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ("<!doctype html><meta charset=utf-8><title>AI Desktop Ops</title>"
                "<p style=\"font:14px system-ui;padding:20px\">找不到 index.html，"
                "但 API 可用：<a href=\"/api/state\">/api/state</a>")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # 别把每条轮询都刷到终端
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")   # 插件面板跨源 fetch 需要
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send(200, read_page(), "text/html; charset=utf-8")
        elif path == "/api/state":
            apps = [state_of(a) for a in load_apps()]
            self._send(200, json.dumps({"apps": apps, "ts": time.time()}, ensure_ascii=False))
        elif path == "/api/ping":
            hit = parsed.query[:300]
            try:
                os.makedirs(LOGDIR, exist_ok=True)
                with open(os.path.join(LOGDIR, "plugin-alive.txt"), "a", encoding="utf-8") as f:
                    f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), hit))
            except OSError:
                pass
            self._send(200, json.dumps({"ok": True, "q": hit}))
        elif path == "/api/health":
            self._send(200, json.dumps({"ok": True, "n": len(load_apps())}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        path = urlparse(self.path).path
        m = re.match(r"^/api/(start|stop)/([\w\-]+)$", path)
        if not m:
            return self._send(404, json.dumps({"error": "not found"}))
        action, app_id = m.group(1), m.group(2)
        app = by_id(app_id)
        if not app:
            return self._send(404, json.dumps({"ok": False, "msg": "未知条目"}, ensure_ascii=False))
        try:
            res = launch(app) if action == "start" else stop(app)
        except Exception as exc:                       # 别让一个条目把连接掐死
            res = {"ok": False, "msg": "服务端异常: %s" % exc}
            return self._send(500, json.dumps(res, ensure_ascii=False))
        self._send(200, json.dumps(res, ensure_ascii=False))


def check():
    apps = load_apps()
    print("apps.json: %s" % APPS_FILE)
    if not apps:
        print("  (空 —— 复制 apps.example.json 为 apps.json 再登记条目)")
        return 1
    print("%-16s %-6s %-8s %s" % ("ID", "KIND", "ALIVE", "NOTE"))
    for app in apps:
        st = state_of(app)
        note = st.get("url") or st.get("exe") or ""
        if not st.get("exists", True):
            note = "路径不存在: " + note
        print("%-16s %-6s %-8s %s" % (st["id"], st["kind"],
                                      "yes" if st["alive"] else "no", note))
    return 0


def main():
    if "--check" in sys.argv:
        sys.exit(check())
    port = int(os.environ.get("WB_PORT", "7777"))
    host = os.environ.get("WB_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    print("AI Desktop Ops Hub on http://%s:%d  (%d 条)" % (host, port, len(load_apps())), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
