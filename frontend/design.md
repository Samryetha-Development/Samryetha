# Samryetha 前端 Design Guidelines

> 权威来源：`frontend/src/globals.css`（本前端全部样式收敛在单一 CSS 文件）。
> 本文件是**语义层指南 + 决策记录**：token、数值、动画为何长这样、新样式往哪长。凡新增视觉,先回 globals.css 改 token,再动组件,最后回填本节。
>
> 更新纪律：样式层不存在"页面独享设计",模块复用同一套 token 与动效语言。给模块写死颜色/半径/duration 前,先问自己"为什么这里要例外"。

---

## 1. 设计原则

**一句话：克制、精确、安静。** 全站视觉是"近白底 + 低饱和雾蓝点缀 + 1px 细线"的纯色块语言,不引入高饱和彩、渐变、重阴影或抽卡式反馈。

由代码里写下的理由,反向归纳为以下五条：

1. **同色系分两层——"浊"与"实"。** `--accent`（雾蓝,浊）只做链接、下划线、指示线；`--accent-fill`（同色系更高饱和的实色）专做选中填充、实心按钮、实心 badge。**浊 accent 禁止直接当填充用**,因为白字压在浊色上无对比。这是全站最重要的颜色纪律。
2. **"纯色块切换"优于"光晕反馈"。** Save/Follow 这类 toggle 状态翻转靠 背景/边框/文字颜色的时间过渡 + 文字 blur + 按钮 spring 一条时间线完成,`background` 用 `.28s` 同一缓动,状态翻转那一个 commit 一起开火。**不做 box-shadow 光晕**——注释原文："那是抽卡/返利感的视觉语言,跟纯色块切换的克制美学冲突"。
3. **层与深度靠"面差"表达,不靠投影。** 浅色仅一枚 `--shadow: 0 1px 0 rgba(20,24,26,.03)` 顶光;深色干脆 `none`,浮层改为用 `--surface` 比 `--bg` 更亮一档来"提"。弹层、下拉、菜单的投影值集中在各自组件,单元素控制（见 §6）。
4. **过渡是"共时"的,不是"争抢"的。** 页面切换用 View Transitions,让浏览器在共享标题的两个几何位置之间插值；列表只安静退场,不参与横向滑动。辅助信息入场做一次极轻上浮,不和标题抢注意力。
5. **内容韧性是设计的一部分。** 长 URL/代码/表格不得撑破版心；移动端输入聚焦不缩放页面；触控热区不小于 44px。规则见 §10。

---

## 2. 色彩系统

### 2.1 语义 token（`:root` / `prefers-color-scheme: dark`）

| Token | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | `#f7f8f8` | `#111416` | 页面底。一切内容默认落在这层 |
| `--surface` | `#ffffff` | `#171b1e` | 卡片/输入浮面,比 bg 高一档 |
| `--ink` | `#171a1c` | `#eceeef` | 主文本 + **实心按钮填充**。注意深色下 ink 近白,语义已反转 |
| `--muted` | `#70777b` | `#8f989d` | 次级文本/未激活导航 |
| `--faint` | `#a7adaf` | `#687075` | 占位符、时间戳、最弱辅助 |
| `--line` | `#e1e4e5` | `#292e31` | 1px 分隔、输入描边 |
| `--accent` | `#597585` | `#87a8b7` | 浊雾蓝：链接、active 态、指示线、图标点缀 |
| `--accent-fill` | `#3d7dbf` | `#4a86cf` | 饱和蓝,唯一允许承载白字的彩色通道（选中/实心按钮/红点/消息气泡） |
| `--accent-soft` | `#e7eef1` | `#1d2a30` | hover 底、选中项底、头像底 |
| `--danger` | `#9a5555` | `#d48686` | 危险语义（删除/封禁/危险按钮填充） |
| `--error` | `#986d70` | `#c28e91` | 输入校验错误文本/描边 |
| `--shadow` | `0 1px 0 rgba(20,24,26,.03)` | `none` | 仅浅色留一枚顶光 |
| `--guide` | ink 14% 透明 | 白 20% | 回复树 SVG 连接线（装饰性,暗色稍实但仍克制） |
| `--content` / `--feed` | `1160px` / `740px` | — | 版心与正文栏宽（§5） |

**反向语义**要特别注意:按钮填充用 `background: var(--ink); color: var(--bg);`（正字反底）。所以"实心"只存在于两种组合:`ink × bg`（主导按钮、选中 pill、compose）与 `accent-fill × #fff`（选中态、消息气泡、badge）。不要引入第三种"实心"。

### 2.2 警示 / 语义点缀色（文字级,不入 `:root`,就地定义）

这些色走**低饱和描边胶囊**语言——`color` 用语义色,`background/border-color` 用 `color-mix(in srgb, <色> 8–10% / 30–36%, transparent/var(--line))`,不直接铺饱和色块：

| 语义 | 主色 | 出现处 |
|---|---|---|
| 成功 / 已通过 | `#15803d` | notification-success、badge active/public/everyone |
| 已完成（更沉） | `#3f7a5e` | badge done、task-tag-done |
| 紧急 / 过期 | `#a4673a`（expired 再往 ink 方向压 18%） | badge urgent/expired、task-tag-urgent |
| bug | `#a05a5a` / `#d58b8b` | badge bug、feedback-tag-bug |
| suggestion | 复用 `--accent` | feedback-tag-suggestion |
| pending | `#b45309` | admin-badge.pending |

规则：**全站只允许 accent、green、urgent/bug 三族语义点缀,且都以"彩色文字 + 同色 8–10% 底 + 34% 描边"的胶囊形式出现**。feedback 分类标签同理。新加状态色先比对这三族,不要另起色相。

### 2.3 背景层次速查

- 页面底 `--bg`；卡片浮面 `--surface`。
- 输入框底用 `color-mix(in srgb, var(--surface) 72–76%, transparent)`（半透明浮面）,聚焦回正 `--surface`。
- hover 底一律 `var(--accent-soft)`（或 `color-mix` 其 30–72%,按组件密度定深浅）：行级 hover 取 32%、列表项 100%、折叠后可点区 42–55%。
- 选中行/未读：`color-mix(in srgb, var(--accent-soft) 46–72%, transparent)`——比 hover 再深半档以示"已选中/未读"。
- 透明磨砂：顶栏背景 `--bg`（因 sticky 需不透明遮挡滚动内容）,唯一真毛玻璃是头像/弹层外。

---

## 3. 排版

### 3.1 字体与渲染

```
font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont,
  "SF Pro Text", Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
-webkit-font-smoothing: antialiased;
text-rendering: optimizeLegibility;
```
等宽（序列号、序号等极次要信息）用 `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`。全站不用衬线、不用 display 字体。

### 3.2 字号 / 字重 / 字距层级

规则：**标题靠 `font-weight`（620–690）+ 负 `letter-spacing`（-.01 ~ -.035em）表达层级,不靠加粗标签或大写**。正文稳定 450 档内。以下数值照抄 globals.css,新增页面标题从这里取档,不要自由发挥：

| 用途 | 字号 | 行高 | 字重 | letter-spacing | 出处 |
|---|---|---|---|---|---|
| wordmark（顶栏/登录） | 15px | — | 680 | -.01em | `.wordmark` |
| 页级大标题 | 30px | 1.05–1.25 | 650 / 690 | -.035 ~ -.02em | `.feed-title` `.thread-detail-title` |
| 次页标题（settings/feedback/tasks/inbox/profile） | 27–28px | ~1.08 | 650 | -.035em | `.settings-sidebar h1` 等 |
| 节标题 h2（settings 内容头） | 23px | 1.15 | 640 | -.025em | `.settings-content h2` |
| 会话页大号列表项 | 22px | — | 630 | -.01em | `.menu-link`（移动菜单） |
| 卡片主标题/列表强标题 | 14–17px | 1.45–1.5 | 610–650 | -.01 ~ -.015em | `.board-name` `.tasks-row strong` `.thread-title` |
| 正文 / 回复正文 | 14–15.5px | 1.68–1.75 | 400 | — | `.thread-detail-body` `.reply-body` |
| 预览 / 摘要 / 说明 | 12–13.5px | 1.55–1.65 | 400 | — | `.thread-preview` `.profile-bio` `.board-description` |
| 次级 meta（作者/时间/计数） | 11–13px | — | 400–620 | — | `.meta` `.thread-detail-meta` |
| 极弱辅助 / 时间戳 | 9.5–11px | — | — | — | `.feed-date` `.community-note` `.message-time` |

字重为整数 400–700 之间的**精密档位**（570/590/610/620/630/640/650/680/690）。这些非 100 步进的档位是这套字体的性格,新组件沿用,别回退成 500/700 大档。

### 3.3 数字排版

凡数字（计数、时间、在线数、序列号）统一 `font-variant-numeric: tabular-nums`,避免列表内跳动。字号比相邻文本小半档。

---

## 4. 布局与页面骨架

### 4.1 布局模板

全站是"**单栏版面 + 右/左 sticky 侧栏**"的两栏网格,`align-items: start`,纵向节奏 `padding: 48px 0 88px`。列宽用 `minmax(0, Xpx) 1fr`——**左栏必须 `minmax(0,…)`**,否则长内容会把内容列挤爆（这是全站网格的第一守则）。

| 模板 | 网格定义 | 侧栏 | 页面 |
|---|---|---|---|
| Feed（feed + now） | `minmax(0, var(--feed)) 1fr` gap 88 | 右,sticky | 首页 |
| 正文 + aside | `minmax(0, 740px) 1fr` gap 88 | 右,sticky | 发帖、profile |
| 侧栏 + 内容 | `210px minmax(0,1fr)` gap 56 | 左,sticky | feedback、tasks |
| 设置 / 后台 | `180px minmax(0,1fr)` gap 72 | 左,sticky | settings、admin |
| 直通 | `padding 48/88` | — | inbox |

sticky 侧栏统一 `position: sticky; top: 108px`（64 顶栏 + 余量）。窄屏（≤900px）**侧栏失守为静态、网格折单列**（§9）。

### 4.2 顶栏

- `.topbar` sticky `top:0`,高 64px（≤640px 为 56px）,背景 `--bg`（不透明,盖住滚动内容）。
- 结构：wordmark · 主导航（浅色文字 14px,active/hover 变 ink）· 全局搜索 · 操作组（compose 按钮 / 语言切换 / 头像菜单）。
- 导航 active 指示是**滑动小条**：2px 高、`--accent`、absolute 定位、`.24s cubic-bezier(.22,.8,.24,1)` 动画,由 JS 测宽测位（Tab/导航/设置共用同一套 `.filter-indicator`/`.nav-indicator`/`.settings-nav-indicator` 语言）。
- ≤640px 搜索框收起为放大镜图标,`:focus-within` / `:has()` 再展开——**这套"收起-展开"已经是成品,不要另写**。

### 4.3 移动端导航

≤900px 出现汉堡全屏菜单（`.mobile-menu`）:整页 `--bg`、头部 wordmark、主条目 22px 630、底部分组 16px 560。条目进场为**错列 blur-in**:`opacity 0 → 1` + `translateY(-20px→0)` + `blur(6px→0)`,350ms `cubic-bezier(.34,1.56,.64,1)`,每项 `animation-delay: var(--d)` 递增;退出统一 300ms 不延迟。reduced-motion 下必须显式恢复 `opacity:1`（基础态是 0）。**"苹果质感模糊错列"是本站唯一的大场面动画,保持唯一性。**

---

## 5. 半径 / 描边 / 间距

### 5.1 圆角谱

| 半径 | 用在哪 |
|---|---|
| 7px | 列表内微操作项、option 行 |
| 8px | 侧栏导航项、后台小控件、菜单项 |
| 9–11px | 输入框 / textarea / 下拉触发 |
| 12px | 弹层、菜单面板、统计卡、头像卡片 |
| 14px | 大卡片（inbox 对话区、消息卡、移动用户卡） |
| 16px | 登录卡、Alert dialog |
| `999px` | 一切"全圆"：主按钮、药丸 tag/pill/badge、头像、toggle、放大镜 icon 按钮 |

规则：**可交互项 ≥7px 起步、按钮与胶囊全圆**；同一组件内触发框与下拉面板保持同圆角,展开时"触发框只改下两角为 0"（`.board-select.open`）。

### 5.2 分隔线模型

- 行间分隔用 `.thread::after`/`.board-row::after` 画 1px `--line`（用伪元素而非 border,便于整行左右内缩 hover 区）。
- 顶层评论组**不用分隔线**,靠纵向间距表达分组（`.rnode.top` 下 16px padding）。
- 可折叠区（已完成任务、已关闭反馈、成员分组）用 **dashed** `--line` 区分于普通硬分隔。
- 不透明度：分隔实线一般不透明;sticky 顶栏线 `color-mix(line 92%, transparent)` 稍让。

### 5.3 间距节奏

- 页面纵向:上 48 / 下 88;窄屏 24–30 / 48–72。
- 列 gap 谱：88（双栏正文）· 72（设置）· 56（feedback/tasks）· 40（900px 折叠）· 24–34（顶栏）· 14–18（表单/网格）· 8–12（图标组/按钮组）。
- 表单网格：双列用 `1fr 1fr gap 14`；设置字段网格 `1fr 1fr gap 14`;≤640px 折单列。
- 区块标题到内容下边界 20–34px 不等,保持"标题底 border + 内容"模式。

---

## 6. 动效系统

### 6.1 缓动

| 曲线 | 语义 | 实例 |
|---|---|---|
| `cubic-bezier(.22,.8,.24,1)` | **标准离/入场**——先快后慢,物理上像"到位即停" | 一切浮层滑入、下划线滑位、toggle、dialog 弹入 `.18s` |
| `cubic-bezier(.16,1,.3,1)` | 强调的减速曲线（更长的尾段） | 共享标题的 `.42s` View Transition |
| `cubic-bezier(.34,1.56,.64,1)` | **弹簧过冲**,配 blur 才有苹果质感 | 移动菜单错列入场(350ms) |
| `ease`（线性内过渡） | 轻、仅透明度类 | hover 背景、alpha |

### 6.2 时长档位

- hover / 按下 / 小元素：`.12–.14s`
- 出现/淡入淡出内容块：`.16–.18s`（`.content-fade .18s`）
- 位置/几何动画（滑条、indicator、菜单）：`.22–.28s`
- 状态翻转的颜色过渡（toggle 按钮）`.28s`——与 spring 同一条线
- 大画面（菜单错列、dialog、共享标题）：300–420ms

### 6.3 过渡的"共时开火"纪律

toggle 按钮（Save/Follow/Task 完成）状态翻转时,颜色过渡 + 文字 blur + 按钮 spring 由 JS `element.animate()` 统一驱动（可打断接管）,CSS 只留常驻样式与颜色 `.28s`。**新增"状态翻转反馈"时复用此路径,不要再叠一套自己的特效。**

### 6.4 View Transitions（页面导航）

- 每个 `.page` 级容器挂 `view-transition-name: page-content`;标题可另挂 `thread-title` 参与共享插值。
- `page-content` 默认淡入淡出 `.13s`,新内容 `.125s` 后淡入;thread 进/退方向另有 16ms 上下文位移脚本。
- **前提是共享标题两个状态的几何浏览器可插值**——列表到详情页靠同根标题的 translate/scale 接力,才能做到"标题滑上去、列表安静退出"。加新页面先确认它是否享受 thread-enter/thread-return 语义。

### 6.5 弹层 / 下拉 / 菜单

- **遮罩用纯黑** `rgba(0,0,0,.7)` + `blur(8px)`,注释原话:"深下去而不是洗灰——ink 在暗色下是近白,不能拿来叠遮罩"。new dialog 淡入淡出 `.16s`,内容 pop `.18s`（scale .96→1）。
- 下拉/浮层基础态 `opacity:0; visibility:hidden; pointer-events:none; translateY(-4px)`,开态一条 `.14s` 滑入。关闭要同时推迟 `visibility` 到 transition 结束（`0s linear .14s`）——否则会闪断。
- 投影按"浮层离地高度"分级：popover 用 `0 14px 34px rgba(20,24,26,.14)`,dialog 在深色上要更重 `0 24px 70px rgba(0,0,0,.55)` + 一圈低饱和 `--accent 9%` 光晕把卡片"提"起来。

### 6.6 加载

- 转圈 22px：`2px --line` 底 + `--accent` 顶,`.7s` 线性转。**纯转圈不是本站的语言主力**,数据就绪后 `content-fade` 淡入代替生硬 blink。
- reduced-motion 下 spinner 停转,靠 "Loading…" 文案。

### 6.7 `prefers-reduced-motion: reduce`

全站统一处理：所有 `animation-duration .01ms`、transition 压缩、View Transition 禁用、spinner 停转、menu link 显式恢复可见、`.action-btn:active` 去 transform。**新增动画时必须补上 reduced-motion 分支,并且注意"基础态是隐藏的"组件（menu-link、dialog）要显式把 opacity/transform 还原。**

---

## 7. 组件语言

### 7.1 按钮层级（从主到次）

| 层级 | 外观 | 触发态 | 实例 |
|---|---|---|---|
| 主按钮 `.primary-action` | `--ink` 填充,字反 `--bg`,全圆,min-h 40,650 | hover 降 `opacity .88`;active `scale .98`;disabled `opacity .28 + cursor not-allowed` | 提交/登录/New |
| 强调填充（无独立类名,就地） | `--accent-fill` 填充白字 | 同上 | task-toggle.checked、badge |
| compose | `--ink` 填充 全圆 650 | hover 降不透明度 | 顶栏发帖 |
| toggle 药丸 `.action-btn` | `--surface-2` 底 + 1px `--line`,590 | active → `--accent-fill` 白字;danger 变红;active scale .96 | Save/Follow |
| 次级线按钮 `.admin-btn` / `.edit-profile` | 透明底 1px `--line`,550 | hover 边框转 ink + 字变 ink | 后台操作、编辑 |
| icon 按钮 `.icon-btn` | 40×40 全圆透明 | hover 铺 `--accent-soft`;active scale .96 | 放大镜、菜单、齿轮 |
| 文字级 `.ra-btn` / `.reply-action` | 透明,次色 540 | hover 铺 accent-soft + 字转 accent | 回复操作、删除 |

规则：**站内"实心高饱和"只给"主 CTA / 已选中 / 已触发"三态**,其余一律线框/文字;危险语义在任意层级都通过 danger 色文字转红表达,不做红填充（唯一红填充是 dialog 确认删除 `.dialog-danger`）。

### 7.2 选中 / active 状态语言

- 导航/页签/侧栏高亮 = accent 滑动小条或 `--accent-soft` 底色 + 字转 ink。
- "实选态"（真的切换了值的控件）用 **`--accent-fill` 白字**:inbox 活跃 tab、format toggle active、task 完成圈、通知红点 badge。
- **不要把 `--accent`（浊）当实选填充**——回到 §1.1。

### 7.3 输入控件

- 统一家族：1px `--line` 描边 + 半透明浮面底,聚焦时 `border-color` 转 `accent 62% 混 line` + 3px `accent 8%` 外发光 + 底回正 `--surface`。**这套焦点样式（描边变蓝 + 一圈微光）全站一致,新输入框照抄,不要自创。**
- 尺寸：单行 input 高 42–44px,圆角 10–11;textarea 圆角同,最小高按用途（回复框 128px、正文 230px）。
- 辅助字数/快捷键用小字 `10px` 浮在右下（`position:absolute` + 上右 padding 给输入留空间）。
- toggle（设置）：38×22 轨道 + 14px 圆形 thumb,on 态轨道转 `--accent-fill`、thumb 右移 16px,`.18s` spring。
- checkbox `accent-color: var(--accent-fill)`。

### 7.4 头像 / 图标

- 头像 = `--accent-soft` 底 + 浊 `--accent` 首字（或 emoji 场景灰底）,全圆。尺寸谱:顶栏 32 / 菜单 40 / 回复 40（窄屏 34）/ 资料卡 72 / 在线点用 8px pulse 圆（`--accent` + 12% 光圈）叠在头像右下。
- SVG 图标统一 `fill:none; stroke:currentColor; stroke-linecap/linejoin:round; stroke-width:1.9`,继承文字色——图标不单独配色。
- 在头像/emoji 里**不用渐变、不用彩色背景**,保持那层灰蓝的克制。

### 7.5 badge / tag / pill

统一 20px 高、全圆、`10.5px / 620 / capitalize + 02em`：
- 中性：`1px --line`,字 `--muted`。
- 语义：字=语义色,border/background=`color-mix` 其 30–36% / 8–10%（§2.2）。
- 强调选中（实选）:填充 `--ink` 字 `--bg`。
大小写规则：胶囊文字 `capitalize`、短标签 `uppercase`(区块小标,`.time-label` 等 `620 .02em`)。

### 7.6 反馈/任务的小型嵌套区

feedback 评论、任务完成折叠、设置组都用"`border-top dashed` + 摘要头 + `summary::before` 转 45° 小箭头"实现——**chevron 一律用 6px 边框画的 45° 方块,不引字体图标**（`.board-chevron`、`summary::before`）。

### 7.7 分隔辅助

评论区状态条、会话内消息气泡、文档内代码块都用 `<hr>` 或明确边框,消息气泡同侧小圆角收边（mine 右下 4px、theirs 左下 4px）形成"对话粘连"。

---

## 8. 可访问性 & 键盘

- 焦点可见：所有交互元素的 `:focus-visible` = `outline: 2px solid var(--accent); outline-offset: 3px`（个别内部 1–2px）。**不要用 `outline: none` 吞掉键盘焦点**,下拉内 option 用负 offset 收进角。
- 文档结构语义：全部用原生 `<button>/<a>/<input>` 或 Radix；无 div 化按钮。
- `.sr-only` 预留。下拉/菜单用 Radix Dialog（自带 Esc/焦点圈）或手写 `:focus-visible` 等效。
- **滚动锁补偿**：html 常驻 `scrollbar-gutter: stable`,防锁滚动时布局抖动;同时必须压回 `body[data-scroll-locked]` 的 `margin/padding-right`（react-remove-scroll-bar 与 gutter 双份叠加会把内容多挤一栏宽）。
- iOS/触屏规则汇总到 §9.4。

---

## 9. 响应式

断点只有两个 + 一个特例：**900px（栏折叠）→ 640px（紧凑）→ 480px（省空间）**。

### 9.1 ≤900px
- 两栏 → 单栏,侧栏转静态/隐藏：`.now`、`primary-nav` 隐藏,汉堡菜单出现。
- 页面纵向 padding 收紧（上 30、bottom 同款）。

### 9.2 ≤640px
- `.shell` 页边距 40→28;顶栏高 56;回复缩进/头像几何整套收紧（§回复树）。
- 列表行 hover 底取消（触屏无 hover）,点击态替代。
- tabs / 侧栏 nav 允许横向滚动（`scrollbar-width:none`,`::-webkit-scrollbar{display:none}`）,不换行。
- 双列表单网格折单列;表单主按钮全宽化。
- 输入框字号强制 16px 以上,防 iOS 聚焦自动缩放（见 §9.4）。
- 窄屏切版禁止重排 hover 依赖。写新响应式规则**先找 640 与 900 两组,别开第三档**。

### 9.3 ≤480px
- 头像菜单的 `.user-menu-name` 隐藏,只留头像（省宽度）。

### 9.4 触屏 / iOS 规则（>640px 的大屏触屏设备同样适用）

- `@media (hover:none) and (pointer:coarse)` 下 `input/textarea/select { font-size: 16px !important }`——iPad 桌面档布局聚焦输入也会被 Safari 缩放。
- ≤640px 内再用 `!important` 压过 class 级字号（iOS 规则优先于组件）。
- **44px 触控热区**：视觉尺寸不变,`::before` 向四周扩出命中区（toggle ±11px、行内操作 ±10–15px、小型线按钮 ±7px）——集中列在文件末尾的"触控热区"清单里,新增小型按钮记得把名字加进去。

---

## 10. 内容韧性（长内容兜底）

- 标题/预览/正文/资料/版块描述一律 `overflow-wrap: anywhere`,既防单词撑破又压低 grid `min-content`,防止轨道被超长 URL 顶爆。
- 表格 `display:block; width:max-content; max-width:100%; overflow-x:auto` 横向滚动;代码块 `pre` 同滚动（浅色块底 `--surface-2`、14px padding、10 圆角）。
- Markdown 渲染容器要**主动恢复块级间距**：Tailwind preflight 把 `p/ul/ol/blockquote/pre/hr/h1–6` margin 清零会吞段落空行,必须在 `.thread-detail-body > *` 上给 `margin:0 0 1em`,末子 `margin-bottom:0`。**新增 md 渲染区照抄这段,不然排版会塌。**
- 正文图片 `max-width:100% + radius 10`,灯箱点开。
- 深回复链缩进随深度 clamp：`--reply-indent: min(calc(--reply-depth * 14px), 56px)`(移动 14→12 再砍),超过 ~3 层后每层再收窄,防止极深链右漂。缩进改动**只作用于子树容器 `.rnode.d3/.d4 > .reply-children`,绝不动节点自身**（会污染弯头几何）。

---

## 11. 回复树 / 连接线专项

YouTube 式头像树,是全站最特殊的一块。几何参数集中在 `.replies`：

- 头像即节点,`--ava: 40px`;头像列→内容 `--gap: 12px`;每层嵌套水平位移 `--indent: 46px`（垂直生长不缩进横向爆）;≤640px 整套收为 34/11/38。
- **连接线是一条覆盖整树的 SVG overlay**（`.reply-connectors`）,坐标由 JS 实测头像几何绘制,非 CSS 拼接。所有权模型：父节点/子树拥有垂直主干,子节点只拥有接入主干的一段圆角分支;一条逻辑线 = 一条连续 path。线条 = `--guide` 色、1px、round 端头、`vector-effect: non-scaling-stroke`。
- 深回复（回复回复）**不做横向平铺**,永远竖向嵌套,靠缩进+连接线表达归属。
- 顶层评论不用分隔线,分组靠 16px 底部留白;子回复更紧凑 10px。
- 连接线加任何东西（节点 hover 高亮整支线、折叠子树）前先过一遍几何归属,别重复画线。

---

## 12. 落地清单（新增视觉时对着过一遍）

- [ ] 新颜色/新半径/新时长先定义 token（`globals.css :root` + dark 分支）,不在组件里写死十六进制
- [ ] hover 底用 `accent-soft` 家族,别自创;实选态用 `accent-fill × 白`;链接/active 用浊 `accent`
- [ ] 按钮一定落在 §7.1 某个层级里,别造新层
- [ ] 焦点用统一 `:focus-visible` 2px accent
- [ ] 新动画:选 §6.1–6.2 的曲线/时长档;补 reduced-motion 分支（注意基础态隐藏组件要还原）
- [ ] 数字加 `tabular-nums`;胶囊字重 620+、`capitalize`
- [ ] 标题字号/字距从 §3.2 表里取,不自由发挥
- [ ] 双栏左列 `minmax(0,…)`;页面纵向 48/88
- [ ] 响应式:进 640/900 断点,不开新档;小输入触屏字号 ≥16px;新增小型按钮进"触控热区"清单
- [ ] md 渲染容器补块级 margin;长内容 `overflow-wrap:anywhere`

## 13. 已知漂移（改样式时顺手修）

- `globals.css` 引用了 `--surface-2`（`.format-toggle`、`pre`、`.action-btn` 底色等多处）,但 `:root` / dark 分支里**没有定义**——依赖它时会静默失效（invalid → transparent）。若它不是有意依赖外层,补 `--surface-2` 到两个 `:root`,浅色比 `--surface` 略沉、深色比 surface 略亮一档。
