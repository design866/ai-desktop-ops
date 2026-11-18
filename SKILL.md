---
name: ai-desktop-ops
description: Use when 把本机 AI 应用（有端口的 web 服务 / 独立窗口程序）收成桌面应用侧边栏的常驻工作台。
---

# AI Desktop Ops — AI 应用的侧边栏工作台

把本地一堆 AI 工具（出图服务、Agent 控制台、向量库前端、本地 IDE、标注工具）
收成侧边栏里的一列条目：绿点=在跑、点一下=拉起并内嵌、快捷键直接跳、常驻不可折叠。

## 什么时候用这份技能

- 用户说「把这些本地 AI 工具/开源软件放到侧边栏」「做个应用启动器工作台」「像 IDE 侧栏那样」
- 用户抱怨「每次用都要记端口、开终端、翻书签」
- 要把一类**本机可执行的东西**统一成入口：dev server、`uvicorn`、`jupyter lab`、Electron 独立程序

不适用：纯云服务（没进程可管）、需要登录态的远端系统、要把别人电脑上的应用收进来。

## 三条硬规则（先看，别踩）

1. **启动能力放 hub，不放插件。** 插件跑在渲染进程，给它 `spawn` 等于把宿主变成提权后门。
   插件只做「读状态 / 渲染 / 内嵌」，启动一律 `POST /api/start/<id>`。
2. **内嵌用裸 `<iframe>`，不要用沙箱 iframe 组件。** 沙箱会剥掉 `allow-same-origin`，
   guest 变 opaque origin，Vite/React 类 SPA 一读 `localStorage` 就 `SecurityError` 白屏。
3. **清单里绝不写本机绝对路径。** 用 `{HERE}/{HOME}/{APPDATA}/{LOCALAPPDATA}/{PY}` 占位，
   换机器只改运行时环境，不改清单。

## 规则：只要装了、能开独立窗口的 AI 应用，就该出现在侧栏

这是这套东西的**验收标准**，不是可选项：用户装了一个能自己开窗口的 AI 软件（本地 IDE、
Electron 客户端、桌面 Agent、模型管理器），它就应该在侧栏占一行，绿点=在跑、灰点=没开、
点一下=拉起。不许以「它没有端口」「它不能被 iframe 嵌」为由把它排除在外。

判定只看事实，不看类型：

| 事实 | 结论 |
|---|---|
| 有**可见顶层窗口**（`IsWindowVisible` 且标题非空） | 独立窗口应用 → `kind: native`，登记 |
| 有端口、`curl` 能通、不发 `X-Frame-Options: DENY` | web 服务 → `kind: web`，登记 + 可内嵌 |
| 只是装在硬盘上、从没起过窗口、找不到 exe | **先不登记**，等它真跑起来再收 |

要点：

- **「装着」不算证据，「此刻有窗口」才算**。枚举窗口 → 反查 pid → 拿 exe 完整路径，
  这条链证明了两件事：程序真的能跑、它的可执行文件在哪（`exe` 字段要的正是它）。
- **`kind: native` 只拉起、不内嵌**。点 native 条目 = 已在跑就提示「已在运行」，没跑就
  `start`；舞台（内嵌区）只留给 web。别为了统一外观把 native 塞进 iframe —— 它没有 URL。
- **探活靠 exe 名比对**，不靠端口（原生程序不监听端口）。同名多实例（`chrome.exe` 开了十个窗）
  必然误判成「在跑」，这是可接受的上界；要精确到实例得走窗口句柄，代价大于收益。
- **占位变量替换绝对路径**：`{LOCALAPPDATA}\Programs\X\X.exe`、`{HOME}\Documents\X\X.exe`，
  照抄 `tools/list-windows-apps.py --json` 的输出即可，它已经换好了。
- **不登记这三类**：①系统壳窗口（`ApplicationFrameHost`/`TextInputHost`/开始菜单）；
  ②控制台宿主（`WindowsTerminal`/`conhost`）——窗口是真的但不是「应用」；
  ③宿主自己（`Hermes.exe` 一类），侧栏里放一个「打开宿主」的按钮没有意义。
  前两类脚本默认已滤掉，需要时 `--all` / `--keep-console`。

一键拿候选清单（本仓库自带，只依赖标准库 + Win32）：

```powershell
python tools/list-windows-apps.py            # 表格：exe / pid / 窗口标题
python tools/list-windows-apps.py --json     # 直接可粘进 apps.json 的 native 条目
```

拿到片段 → 贴进 `hub/apps.json` → `curl -X POST http://127.0.0.1:7777/api/start/<id>` →
`/api/state` 里该条 `alive:true`，才算这条真收进来了。

## 步骤

### 1. 起 hub

```powershell
cd hub
copy apps.example.json apps.json
python hub.py            # 默认 127.0.0.1:7777
```

验证（必须真看到 JSON，别只看命令 exit 0）：

```powershell
curl.exe -s http://127.0.0.1:7777/api/health      # {"ok": true, "n": 5}
curl.exe -s http://127.0.0.1:7777/api/state
```

`/api/state` 冷启可能 1–2 秒（要探全部端口），稳态 ~45ms。**别用毫秒级超时判死。**

### 2. 把用户的应用登记进 `apps.json`

每条最少四个字段：`id` / `name` / `kind` / （`port`+`cmd` 或 `exe`）。

| 类型 | 判定 | 必填 |
|---|---|---|
| `web` | 有端口，能被 iframe 嵌 | `port`、`cmd`、`cwd` |
| `native` | 独立窗口程序（Electron/原生），无法内嵌 | `exe` |

判定靠**事实**，不靠猜：
- web：`netstat -ano | findstr :<port>` 有 LISTENING，或 `curl -s -o NUL -w "%{http_code}" URL`
- native：**有可见窗口** → 反查 exe 真实路径（`python tools/list-windows-apps.py` 一步到位；
  手工路径是 `where` / 开始菜单快捷方式属性 / `Get-Process | select Path`）

「装了有独立窗口的 AI 应用就进侧栏」这条规则的完整判定与排除项见上节，**别在这里二次发明标准**。

登记前先问自己：**这个应用允许被 iframe 嵌吗？** 有些服务发 `X-Frame-Options: DENY` 或 CSP
`frame-ancestors`，嵌进去是白屏 —— 这种也登记，但把 `kind` 留 `web` 同时在 `sub` 注明「需浏览器打开」，
或者干脆只给 URL 不给 iframe（见 `docs/CONFIG.md` 的 framebusting 一节）。

### 3. 装插件到宿主侧栏

把 `plugin/plugin.js` 放到宿主的插件目录（Hermes：`%LOCALAPPDATA%\hermes\desktop-plugins\ai-desktop-ops\plugin.js`），
重启宿主。改文件前先备份：`cp plugin.js backup/plugin-$(date +%H%M%S).js`。

注意：某些宿主的插件目录**拒绝直接写入**（受保护），先写到临时目录再 `cp` 过去。

### 4. 证明它真的在跑（不要只说"已加载"）

三个层次的证据，缺一层就不算完成：

| 层次 | 怎么验 |
|---|---|
| 插件被加载 | 宿主日志出现插件自己 `console.error` 的一行（只有 error 会进日志） |
| 面板真渲染 | `hub/logs/plugin-alive.txt` 出现 `src=…&pane=1&n=<条数>` —— 带 `pane` 和 `n` 才算拿到数据 |
| 启动链通 | `POST /api/start/<id>` 返回 `已拉起`，再 `GET /api/state` 看该条 `alive:true` |

`/api/ping` 只被 `register()` 调一次的话，只能证明"加载"；**必须再在拿到数据后带 `pane=1&n=N` 打一次**，
否则你分不清"插件崩了"和"面板没渲染"。

### 5. 常驻

- 面板声明 `collapsible: false`
- 每 6 秒跑一次看门狗：不可见就 `host.undismissPane(scopedId)`
- 想开机自带 hub：用户级自启（`HKCU\…\Run`）就够，别动系统服务/计划任务

## 排障速查

| 症状 | 先查 |
|---|---|
| 面板空白 | hub 没起 / 端口不对 / 跨源被拦（看 `OPTIONS` 预检有没有回 CORS 头） |
| 条目全灰 | `cmd` 里的 `{PY}` 展开成了解释器 python 而不是 pythonw；或 `cwd` 不存在 |
| 点了变成白屏 | 应用发了 `X-Frame-Options`，或用了沙箱 iframe |
| 起得来但 `alive` 永远 false | 端口填错，或服务绑在 `0.0.0.0`/别的 host 而探的是 `127.0.0.1` |
| native 条目一直灰、点了报「程序文件不存在」 | `exe` 里写了本机绝对路径但没被占位变量替换；或占位拼错（`{LOCALAPPDATA}` 是 `…\Local`，不是 `…\Local\Programs`） |
| native 明明没开却显示绿点 | 探活比的是 exe 名，同名的别的实例在跑（浏览器类常见），属已知上界 |
| 重启 hub 后 web 服务全灭 | hub 的子进程跟着死了，重新 start 一遍 |
| 自己的 Python 包被劫持 | 先 `unset PYTHONPATH` 再跑 hub（别的工具常注入 PYTHONPATH） |

## 交付前自检

- [ ] `apps.json` 里没有绝对路径、没有真实用户名
- [ ] **装了且能自己开窗口的 AI 应用都进了侧栏**（用 `tools/list-windows-apps.py` 对一遍，
      缺的要么补上，要么能说清为什么它是系统壳/终端/宿主自己）
- [ ] 一条 native 条目至少实测过「灰 → 点一下 → 绿」和「关掉 → 变灰」两个方向
- [ ] 没有凭据/密钥进仓库（`.env`、`logs/` 在 `.gitignore`）
- [ ] hub 只监听 `127.0.0.1`
- [ ] `/api/health` 与 `/api/start` 都实测过，不是推测
- [ ] 截图是真实渲染（别用示意图冒充）
