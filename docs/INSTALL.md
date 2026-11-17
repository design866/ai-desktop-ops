# 安装

分两半：**Hub**（必须，本机服务）和 **Panel**（可选，装进宿主的侧边栏）。
只装 Hub 也能用 —— 浏览器打开 `http://127.0.0.1:7777` 就是完整工作台。

---

## 一、Hub

### 前置

- Python 3.8+（Windows 上用 `py -3 --version` 确认）
- 零第三方依赖，不需要 pip install 任何东西

### 手动（三行）

```powershell
cd hub
copy apps.example.json apps.json
python hub.py
```

打开 <http://127.0.0.1:7777> 验证：左栏 5 条、右栏能嵌进示例页。

### 脚本（Windows）

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

脚本做四件事：把 `hub\`（含 `examples\`、`plugin\`）按仓库原样复制到 `%LOCALAPPDATA%\AIDesktopOps\`、
在 `hub\apps.json` 生成清单、检查 Python、打印下一步。

安装后目录结构与仓库一致 —— 这是有意的：清单里的 `{HERE}` 指 hub 目录，示例条目用
`{HERE}\..\examples\site`，把 `hub.py` 拍平到 `%LOCALAPPDATA%\AIDesktopOps\` 根下会让示例条目
起不来（`WinError 267`）。

```powershell
cd "$env:LOCALAPPDATA\AIDesktopOps\hub"
python hub.py
```

**不会**改注册表、**不会**装服务、**不会**开机自启 —— 除非你显式加 `-Autostart`。

```powershell
.\install.ps1 -Autostart      # 追加：写 HKCU\…\Run，登录时自启 hub
.\install.ps1 -Uninstall      # 移除自启项（不动文件）
```

### 换端口 / 换监听地址

```powershell
$env:WB_PORT = 7799
$env:WB_APPS = "D:\my\apps.json"
python hub.py
```

⚠️ `WB_HOST` 保持默认 `127.0.0.1`。改成 `0.0.0.0` 等于把一个「按 JSON 起进程」的服务
暴露给整个局域网 —— 不要这么做。

### 让它一直在（自选一种）

| 方式 | 命令 | 说明 |
|---|---|---|
| 登录自启（推荐） | `.\install.ps1 -Autostart` | 用户级 `HKCU\…\Run`，免管理员，不动系统 |
| 手动后台 | `Start-Process pythonw -ArgumentList "$env:LOCALAPPDATA\AIDesktopOps\hub\hub.py" -WindowStyle Hidden` | 用 `pythonw` 才没有黑窗 |
| 托盘常驻 | 自己套 NSSM / WinSW | 需要装第三方，按需 |

---

## 二、装进宿主侧边栏

### Hermes Agent

```
%LOCALAPPDATA%\hermes\desktop-plugins\ai-desktop-ops\plugin.js
```

```powershell
$dst = "$env:LOCALAPPDATA\hermes\desktop-plugins\ai-desktop-ops"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item .\plugin\plugin.js $dst\plugin.js
```

重启宿主 → 右侧出现「工作台」面板。

- 面板默认 `252px`，可拖宽
- 改 `plugin.js` 里的 `const HUB = 'http://127.0.0.1:7777'` 对齐你的 hub 端口
- 验证加载：宿主日志（`%LOCALAPPDATA%\hermes\logs\desktop.log`）里应出现
  `[ai-desktop-ops] loaded <ISO 时间>`

### 其它 Electron 宿主：适配清单

插件对外只要求宿主提供这几样。缺哪个就在 `plugin.js` 顶部替换掉。

| 插件用到的宿主能力 | 用途 | 没有时怎么降级 |
|---|---|---|
| `ctx.register({ id, area:'panes', title, render, data })` | 声明侧栏面板 | 换宿主自己的面板 API |
| `data: { placement:'right', width, collapsible:false }` | 面板位置/宽度/禁折叠 | 只在宿主支持时生效 |
| `host.paneVisibility(scopedId)` | 看门狗判断面板是否可见 | 传 `undefined`，看门狗自动跳过 |
| `host.undismissPane(scopedId)` | 把被关掉的面板叫回来 | 同上，最多少一层"常驻"保障 |
| `host.os.openExternal(url)` | 外部浏览器打开 | 按钮点了没反应，其余不受影响 |
| `host.notify({kind,message})` | native 应用启动提示 | 静默 |

三个约定：

1. **`scopedId` 必须是 `<pluginId>:<paneId>`**。宿主的 `revealPane` / `undismissPane` /
   `paneVisibility` 都要它。
2. **面板渲染函数接收 `{ host }`**，别去 import 宿主的全局单例。
3. **内嵌一定要裸 `<iframe>`**。宿主 SDK 的沙箱 iframe 会剥 `allow-same-origin`，
   让 Vite/React SPA 白屏。这一条几乎适用所有 Electron 宿主。

### 不想装插件

Hub 自带页面已经是完整工作台：

```
http://127.0.0.1:7777          # 左栏 + 舞台
http://127.0.0.1:7777/?rail=1  # 只有左栏（贴进窄侧栏/侧边栏浏览器）
```

`?rail=1` 就是给"侧栏 252px 宽度"准备的：把这一页塞进任何侧栏容器都能看。

---

## 三、验收（别只看 exit code）

```powershell
# 1. hub 活着，条目数对
curl.exe -s http://127.0.0.1:7777/api/health
# {"ok": true, "n": 5}

# 2. 状态里能看见条目
curl.exe -s http://127.0.0.1:7777/api/state

# 3. 启动链真的通（web 条目，design-studio = 示例里第一个应用）
curl.exe -s -X POST http://127.0.0.1:7777/api/start/design-studio
# {"ok": true, "msg": "已拉起", "id": "design-studio"}
sleep 3; curl.exe -s http://127.0.0.1:7777/api/state   # 该条 alive: true

# 4. 面板真的渲染过（装了插件才有这行）
type hub\logs\plugin-alive.txt
# 15:19:21 src=ai-desktop-ops&pane=1&n=5
```

第 4 步只有 `src=…` 而没有 `pane=1&n=…`，说明插件加载了但面板没渲染成 ——
去宿主日志里找渲染异常。

---

## 卸载

1. 删插件目录：`%LOCALAPPDATA%\hermes\desktop-plugins\ai-desktop-ops\`
2. `.\install.ps1 -Uninstall` 清自启项
3. 结束 hub：`Get-NetTCPConnection -LocalPort 7777 | % { Stop-Process -Id $_.OwningProcess -Force }`
4. 删 `%LOCALAPPDATA%\AIDesktopOps\`

> 结束 hub 时**不要**用 `taskkill /T`：它会连带杀掉 hub 拉起的 web 服务。
> 只结束监听 7777 的那个 PID。
