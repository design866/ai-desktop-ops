---
name: app-dock
description: Use when 把本机 AI 应用（有端口的 web 服务 / 独立窗口程序）收成桌面应用侧边栏的常驻工作台。
---

# App Dock — AI 应用的侧边栏工作台

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
- native：找到 `.exe` 真实路径（`where` / 开始菜单快捷方式 / `Get-Process | select Path`）

登记前先问自己：**这个应用允许被 iframe 嵌吗？** 有些服务发 `X-Frame-Options: DENY` 或 CSP
`frame-ancestors`，嵌进去是白屏 —— 这种也登记，但把 `kind` 留 `web` 同时在 `sub` 注明「需浏览器打开」，
或者干脆只给 URL 不给 iframe（见 `docs/CONFIG.md` 的 framebusting 一节）。

### 3. 装插件到宿主侧栏

把 `plugin/plugin.js` 放到宿主的插件目录（Hermes：`%LOCALAPPDATA%\hermes\desktop-plugins\app-dock\plugin.js`），
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
| 重启 hub 后 web 服务全灭 | hub 的子进程跟着死了，重新 start 一遍 |
| 自己的 Python 包被劫持 | 先 `unset PYTHONPATH` 再跑 hub（别的工具常注入 PYTHONPATH） |

## 交付前自检

- [ ] `apps.json` 里没有绝对路径、没有真实用户名
- [ ] 没有凭据/密钥进仓库（`.env`、`logs/` 在 `.gitignore`）
- [ ] hub 只监听 `127.0.0.1`
- [ ] `/api/health` 与 `/api/start` 都实测过，不是推测
- [ ] 截图是真实渲染（别用示意图冒充）
