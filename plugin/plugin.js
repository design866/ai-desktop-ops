/* AI Desktop Ops —— 把「本地 AI 应用清单」固定成桌面应用的侧边栏工作台
 *
 * 职责切分（关键）：
 *   hub（../hub/hub.py）负责  启动进程 / 端口探活 / 暴露 JSON 状态
 *   本文件只负责            拉状态、渲染条目、把选中的 web 应用内嵌进舞台
 *
 * 插件前端没有 exec/spawn 能力，所以「启动」永远是 POST 给 hub，绝不在这里 spawn。
 *
 * 内嵌用**裸 iframe**，不要用 SDK 的 SandboxedFrame：后者会剥掉 allow-same-origin，
 * 让 guest 变成 opaque origin，Vite/React 这类 SPA 一启动就白屏（localStorage 抛 SecurityError）。
 */
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const PLUGIN_ID = 'ai-desktop-ops'
const PANE_ID = 'ai-desktop-ops'
const SCOPED_ID = PLUGIN_ID + ':' + PANE_ID      // pane 的门牌号：<pluginId>:<paneId>
const HUB = 'http://127.0.0.1:7777'              // ← 改成你的 hub 地址（hub.py 默认 7777）
const POLL_MS = 5000
const WATCHDOG_MS = 6000
const START_TIMEOUT_MS = 45000

// ── 样式（只用主题变量，不写死颜色） ────────────────────────────────
const S = {
  wrap: { display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, fontSize: 12 },
  head: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px 6px',
    borderBottom: '1px solid var(--ui-stroke-secondary)', flex: '0 0 auto',
  },
  headTitle: { flex: 1, fontWeight: 600, color: 'var(--ui-text-secondary)', letterSpacing: .2 },
  iconBtn: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    width: 20, height: 20, border: 0, borderRadius: 5, background: 'transparent',
    color: 'var(--ui-text-quaternary)', cursor: 'pointer', fontSize: 13, lineHeight: 1,
  },
  tabs: { display: 'flex', flexWrap: 'wrap', gap: 4, padding: '7px 8px 2px' },
  tab: {
    border: 0, borderRadius: 5, background: 'transparent', color: 'var(--ui-text-quaternary)',
    font: 'inherit', fontSize: 11, padding: '2px 7px', cursor: 'pointer',
  },
  tabOn: { background: 'var(--ui-accent-soft, rgba(127,127,127,.14))', color: 'var(--ui-accent)', fontWeight: 600 },
  list: { flex: 1, overflowY: 'auto', padding: '6px 6px 12px' },
  row: {
    display: 'flex', alignItems: 'center', gap: 8, width: '100%', border: 0,
    borderRadius: 7, background: 'transparent', color: 'inherit', font: 'inherit',
    textAlign: 'left', padding: '6px 7px', margin: '1px 0', cursor: 'pointer',
  },
  rowSel: { background: 'var(--ui-accent-soft, rgba(127,127,127,.14))' },
  dot: (alive, waiting) => ({
    width: 7, height: 7, flex: '0 0 7px', borderRadius: '50%',
    background: alive ? '#22c55e' : (waiting ? '#e9a23b' : 'var(--ui-text-quaternary)'),
    opacity: alive || waiting ? 1 : .45,
    transition: 'opacity .15s',
  }),
  meta: { flex: 1, minWidth: 0 },
  name: {
    display: 'block', fontSize: 12, color: 'var(--ui-text-secondary)',
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  sub: {
    display: 'block', fontSize: 10, color: 'var(--ui-text-quaternary)',
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  },
  kbd: {
    font: '9.5px/1 ui-monospace,Menlo,monospace', color: 'var(--ui-text-quaternary)',
    border: '1px solid var(--ui-stroke-secondary)', borderRadius: 4, padding: '2px 4px',
    minWidth: 14, textAlign: 'center',
  },
  stage: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' },
  stageBar: {
    display: 'flex', alignItems: 'center', gap: 6, padding: '6px 8px',
    borderBottom: '1px solid var(--ui-stroke-secondary)', flex: '0 0 auto',
  },
  stageTitle: { flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  frame: { flex: 1, width: '100%', border: 0, background: '#fff' },
  hint: {
    flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
    color: 'var(--ui-text-quaternary)', textAlign: 'center', padding: 16, lineHeight: 1.6,
  },
  banner: {
    padding: '6px 10px', fontSize: 11, color: 'var(--ui-text-secondary)',
    borderBottom: '1px solid var(--ui-stroke-secondary)',
  },
}

async function readState() {
  const res = await fetch(HUB + '/api/state', {
    cache: 'no-store',
    signal: AbortSignal.timeout(4000),   // hub 冷启可能要 1–2s，别按毫秒级判死
  })
  if (!res.ok) throw new Error('hub ' + res.status)
  const data = await res.json()
  return Array.isArray(data.apps) ? data.apps : []
}

async function post(hubPath) {
  const res = await fetch(HUB + hubPath, { method: 'POST', signal: AbortSignal.timeout(8000) })
  return res.json()
}

function ping(extra) {
  // 自检：hub 把这次访问写进 logs/plugin-alive.txt，用来证明「插件已加载 + 渲染过 + 跨源 fetch 通」
  try {
    fetch(HUB + '/api/ping?src=' + PLUGIN_ID + (extra || ''), { cache: 'no-store' }).catch(() => {})
  } catch (e) { /* 静默 */ }
}

function Dock(props) {
  const host = props.host
  const [apps, setApps] = useState([])
  const [group, setGroup] = useState('全部')
  const [sel, setSel] = useState(null)
  const [view, setView] = useState('list')      // 'list' | 'stage'
  const [err, setErr] = useState('')
  const [pending, setPending] = useState({})    // id -> true（启动中）
  const pinged = useRef(false)

  const load = useCallback(async () => {
    try {
      const list = await readState()
      setApps(list)
      setErr('')
      if (!pinged.current && list.length) {
        pinged.current = true
        ping('&pane=1&n=' + list.length)          // 拿到数据 = 面板真的渲染过了
      }
      return list
    } catch (e) {
      setErr('hub 无响应（' + HUB + '）')
      return null
    }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    const t = setInterval(() => { load() }, POLL_MS)
    return () => clearInterval(t)
  }, [load])

  // 常驻看门狗：面板被关掉/折叠 → 6 秒内自己回来（"固定在侧栏"的兜底机制）
  useEffect(() => {
    const tick = () => {
      try {
        const vis = host && host.paneVisibility && host.paneVisibility(SCOPED_ID)
        const visibleNow = vis && typeof vis.get === 'function' ? vis.get() : false
        if (!visibleNow && host && typeof host.undismissPane === 'function') {
          host.undismissPane(SCOPED_ID)
        }
      } catch (e) { /* 老版本桌面没有这个门，忽略 */ }
    }
    const t = setInterval(tick, WATCHDOG_MS)
    return () => clearInterval(t)
  }, [host])

  const groups = useMemo(() => {
    const seen = []
    apps.forEach((a) => { const g = a.group || '应用'; if (!seen.includes(g)) seen.push(g) })
    return ['全部'].concat(seen)
  }, [apps])

  const shown = useMemo(
    () => apps.filter((a) => group === '全部' || (a.group || '应用') === group),
    [apps, group],
  )

  const startApp = useCallback(async (app) => {
    setPending((p) => ({ ...p, [app.id]: true }))
    try {
      await post('/api/start/' + app.id)
      const deadline = Date.now() + START_TIMEOUT_MS
      while (Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 1500))
        const list = await load()
        const cur = (list || []).find((a) => a.id === app.id)
        if (cur && cur.alive) break
      }
    } catch (e) {
      setErr('启动请求失败')
    } finally {
      setPending((p) => {
        const next = { ...p }
        delete next[app.id]
        return next
      })
    }
  }, [load])

  const pick = useCallback((app) => {
    setSel(app.id)
    if (app.kind === 'native') {
      // 独立窗口程序：只要能拉起就够了，舞台留在列表
      if (!app.alive) startApp(app)
      if (host && host.notify) {
        host.notify({ kind: 'info', message: app.alive ? app.name + ' 已在运行' : '正在拉起 ' + app.name })
      }
      return
    }
    if (app.alive) {
      setView('stage')
    } else {
      setView('stage')
      startApp(app)
    }
  }, [host, startApp])

  const stopApp = useCallback(async (app) => {
    try { await post('/api/stop/' + app.id) } catch (e) { setErr('停止请求失败') }
    await load()
  }, [load])

  const current = apps.find((a) => a.id === sel) || null

  // ── 舞台：内嵌 web 应用 ───────────────────────────────────────────
  if (view === 'stage' && current && current.kind === 'web') {
    const waiting = pending[current.id] || (!current.alive && !current.starting)
    return jsxs('div', {
      style: S.wrap,
      children: [
        jsxs('div', {
          key: 'bar', style: S.stageBar, children: [
            jsx('button', {
              key: 'back', style: S.iconBtn, title: '返回列表',
              onClick: () => setView('list'),
              children: '‹',
            }, 'back'),
            jsx('span', {
              key: 'title', style: S.stageTitle,
              children: current.name + (current.port ? '  :' + current.port : ''),
            }, 'title'),
            jsx('button', {
              key: 'stop', style: S.iconBtn, title: '停止',
              onClick: () => stopApp(current),
              children: '■',
            }, 'stop'),
            jsx('button', {
              key: 'out', style: S.iconBtn, title: '浏览器打开',
              onClick: () => host && host.os && host.os.openExternal && host.os.openExternal(current.url),
              children: '↗',
            }, 'out'),
          ],
        }, 'bar'),
        current.alive
          ? jsx('iframe', {
              key: 'frame',
              src: current.url,
              style: S.frame,
              title: current.name,
              // 裸 iframe：不加 sandbox —— SPA 需要同源存储，否则白屏
            }, 'frame')
          : jsx('div', {
              key: 'hint', style: S.hint,
              children: waiting
                ? '正在启动 ' + current.name + ' …\n端口一旦监听就会自动嵌进来（首编译可能要几十秒）'
                : '没有监听端口。看应用自己的日志，或检查 apps.json 里的 cmd / port。',
            }, 'hint'),
      ],
    })
  }

  // ── 列表 ─────────────────────────────────────────────────────────
  return jsxs('div', {
    style: S.wrap,
    children: [
      jsxs('div', {
        key: 'head', style: S.head, children: [
          jsx('span', { key: 't', style: S.headTitle, children: '工作台' }, 't'),
          jsx('button', {
            key: 'refresh', style: S.iconBtn, title: '刷新',
            onClick: () => load(),
            children: '⟳',
          }, 'refresh'),
        ],
      }, 'head'),
      err ? jsx('div', { key: 'err', style: S.banner, children: err }, 'err') : null,
      jsx('div', {
        key: 'tabs', style: S.tabs,
        children: groups.map((g) => jsx('button', {
          style: group === g ? { ...S.tab, ...S.tabOn } : S.tab,
          onClick: () => setGroup(g),
          children: g,
        }, g)),
      }, 'tabs'),
      jsxs('div', {
        key: 'list', style: S.list,
        children: shown.length
          ? shown.map((a) => jsxs('button', {
              style: sel === a.id ? { ...S.row, ...S.rowSel } : S.row,
              onClick: () => pick(a),
              title: (a.url || a.exe || '') + (a.exists === false ? '（路径不存在）' : ''),
              children: [
                jsx('span', { style: S.dot(a.alive, pending[a.id] || a.starting) }, 'dot'),
                jsxs('span', {
                  style: S.meta,
                  children: [
                    jsx('span', { style: S.name, children: a.name }, 'n'),
                    jsx('span', {
                      style: S.sub,
                      children: a.exists === false ? '路径不存在' : (a.sub || (a.port ? ':' + a.port : '')),
                    }, 's'),
                  ],
                }, 'meta'),
                a.hotkey ? jsx('span', { style: S.kbd, children: String(a.hotkey).toUpperCase() }, 'k') : null,
              ],
            }, a.id))
          : jsx('div', { style: S.hint, children: '没有条目。复制 apps.example.json 为 apps.json 再登记。' }, 'empty'),
      }, 'list'),
    ],
  })
}

export default {
  id: PLUGIN_ID,
  name: 'AI Desktop Ops',
  register(ctx) {
    // 这一行走 renderer 日志（只有 console.error 会进 desktop.log），用来证明插件被加载
    try { console.error('[ai-desktop-ops] loaded ' + new Date().toISOString()) } catch (e) {}
    ping()   // 只证明 register 被调用过；带 &pane=1&n=… 的那次才证明面板渲染

    ctx.register({
      id: PANE_ID,
      area: 'panes',
      title: '工作台',
      render: (props) => jsx(Dock, { host: props.host, ctx }),
      data: {
        placement: 'right',
        width: '252px',
        minWidth: '220px',
        collapsible: false,          // 不给折叠按钮：这一栏是常驻的
        tabTitleText: '工作台',
      },
    })
  },
}
