# apps.json 字段全表

清单就是一个 JSON 对象，`apps` 是数组：

```json
{ "apps": [ { …条目… }, { …条目… } ] }
```

Hub 读的是 `hub/apps.json`（可用环境变量 `WB_APPS` 指到别处）。**存盘即生效**，
下一次 `/api/state` 就按新清单走，不用重启 hub。

---

## 字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `id` | string | ✅ | — | 唯一标识。用在 `/api/start/<id>`、`/api/stop/<id>`、iframe 的 key |
| `name` | string | ✅ | — | 侧栏显示名 |
| `kind` | `"web"` \| `"native"` | ✅ | `"web"` | 决定点它是「内嵌」还是「拉起独立窗口」 |
| `sub` | string | | `""` | 副标题，一行灰字，别超过 ~24 个字（侧栏窄，会省略号） |
| `group` | string | | `"应用"` | 分组。侧栏顶部 tab 由清单里出现过的 `group` 自动生成 |
| `port` | int | web 必填 | — | 探活端口。**TCP 能连上 = 在跑**（不看 HTTP 状态码） |
| `url` | string | | `http://127.0.0.1:<port>` | 覆盖内嵌地址。给反代、给 https、给带路径的入口 |
| `cwd` | string | web 推荐 | hub 目录 | 启动工作目录 |
| `cmd` | string | web 必填 | — | 启动命令，支持占位变量，用 `shell` 执行 |
| `exe` | string | native 必填 | — | 可执行文件路径，支持占位变量 |
| `args` | string[] | | — | **当前实现不读**（`os.startfile` 只认 exe）。要用参数请把 `exe` 指向一个快捷方式/启动器 |
| `hotkey` | string | | — | 单字符 `1`–`9` / `a`–`z`，大小写不敏感，只渲染成徽标 |
| `note` | string | | — | 只给人看，不进 UI |

### 探活语义（重要）

- **web**：探 `127.0.0.1:<port>` 的 TCP 连接。所以服务绑在 `0.0.0.0`、`localhost`、`::1` 都能探到；
  绑在别的网卡地址则探不到 —— 把 `port` 换成实际监听端口，或改用 native。
- **native**：比对进程名。hub 每 3 秒 `tasklist` 一次做缓存，比对 `exe` 的文件名（含 `.exe`，小写）。
- `exists:false` 表示 `exe` 路径不存在 → 侧栏显示「路径不存在」，点它不会静默失败。

---

## 占位变量

| 占位 | 展开成 | 典型用法 |
|---|---|---|
| `{HERE}` | hub.py 所在目录 | `{HERE}/../examples/site` |
| `{HOME}` | 用户主目录 | `{HOME}/projects/foo` |
| `{APPDATA}` | `%APPDATA%`（Roaming） | 少数应用把配置放这儿 |
| `{LOCALAPPDATA}` | `%LOCALAPPDATA%`（Local） | `{LOCALAPPDATA}\Programs\X\X.exe` |
| `{PY}` | 当前 Python 解释器绝对路径 | `{PY} -m http.server {port}` |
| `{id}` `{name}` `{port}` `{url}` `{exe}` | 该条目自己的字段 | 日志文件名、窗口标题 |

展开在启动前做，日志里打的是展开后的命令 —— 排障时能直接复制去终端手跑。

---

## 常见写法

### Vite 前端（首次启动要编译，给足时间）

```json
{
  "id": "my-spa", "name": "My SPA", "kind": "web", "group": "内嵌应用",
  "port": 5173, "cwd": "{HOME}/code/my-spa",
  "cmd": "npm run dev -- --port {port} --strictPort",
  "hotkey": "1"
}
```

> `--strictPort` 很重要：不加的话端口被占时 Vite 自动 +1，而 hub 探的是清单里的端口 → 永远灰点。

### Python 后端 / FastAPI

```json
{
  "id": "api", "name": "API", "kind": "web", "port": 8000,
  "cwd": "{HOME}/code/api",
  "cmd": "{PY} -m uvicorn app.main:app --host 127.0.0.1 --port {port}"
}
```

### JupyterLab（带 token 的 URL 更好用）

```json
{
  "id": "jupyter", "name": "JupyterLab", "kind": "web", "port": 8888,
  "cwd": "{HOME}/notebooks",
  "cmd": "{PY} -m jupyter lab --no-browser --port {port} --ServerApp.token='' --ServerApp.password=''",
  "url": "http://127.0.0.1:8888/lab"
}
```

> 关掉 token 只在你确认这台机器是单人使用、且端口只绑 `127.0.0.1` 时才可接受。

### 静态页（最小可跑示例）

```json
{
  "id": "site", "name": "Demo Site", "kind": "web", "port": 7480,
  "cwd": "{HERE}/../examples/site",
  "cmd": "{PY} -m http.server {port} --bind 127.0.0.1"
}
```

### Electron / 原生独立程序

```json
{
  "id": "editor", "name": "Some Editor", "kind": "native", "group": "独立窗口",
  "exe": "{LOCALAPPDATA}\\Programs\\SomeEditor\\SomeEditor.exe",
  "hotkey": "e"
}
```

### 原生程序：目前只支持「直接拉起 exe」

`os.startfile(exe)` 拉起，**不传命令行参数、不传环境变量**。需要参数的程序（带 `--workspace`
之类）请先做一个快捷方式 / 批处理，或者自己包一层启动器，`exe` 指那个启动器。

### 怎么找到 `exe` 的真实路径

别扫开始菜单、别扫 `Program Files` —— **装着 ≠ 能开**。让程序开着窗口，然后：

```powershell
python tools/list-windows-apps.py --json    # 输出可直接粘的条目，路径已换成 {LOCALAPPDATA}/{HOME} 占位
```

原理：`EnumWindows` 拿到可见顶层窗口 → `GetWindowThreadProcessId` → `QueryFullProcessImageNameW`
拿完整 exe 路径。顺带把系统壳窗口（`ApplicationFrameHost` 等）和控制台宿主滤掉。

---

## 会被 iframe 拒绝的服务（framebusting）

这些在浏览器里能打开，但一嵌进 iframe 就白屏，因为对方发了：

```
X-Frame-Options: DENY
Content-Security-Policy: frame-ancestors 'none'
```

遇到时的三条路（按可维护性排序）：

1. **找到它的允许开关**。很多本地服务支持：
   - Next.js dev：一般可嵌
   - Vite：`server.headers` 里去掉 `X-Frame-Options`
   - FastAPI：`app.add_middleware(...)` 加 CORS 的同时别加 `X-Frame-Options`
   - Jupyter：`--ServerApp.tornado_settings='{"headers":{"Content-Security-Policy":""}}'`（宽，谨慎）
2. **本地反代剥头**：起一个极小的反代去掉 `X-Frame-Options`，清单里 `url` 指反代端口。
3. **不嵌**：`kind` 保持 `web`，但把 `sub` 写成「浏览器打开」，用户点条目时用外部浏览器。

AI Desktop Ops 本身不替你改对方响应头 —— 那属于越权。

---

## 键盘

- `hotkey` 是**渲染用的徽标**：`1`–`9`、`a`–`z`（大小写不敏感），显示在条目右侧，方便你按顺序扫。
- 插件**不注册全局快捷键**——宿主是聊天窗口，抢键会吃掉你正在打的字。要做全局跳转，请在
  宿主侧或系统侧把你的组合键映射到 `POST /api/start/<id>`（或直接调 `/api/state` 自己渲染）。
- 同一个键别用在两条上：后出现的会覆盖前面的（Hub 不做校验）。

---

## 分组

`group` 相同的条目归到一个 tab，顺序按在清单里首次出现的顺序。建议 2–4 组，例如：

- `内嵌应用` —— 有端口的 web 服务
- `独立窗口` —— Electron / 原生程序

条目超过 12 条时，分组比滚动更有效。
