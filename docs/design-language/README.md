# Samryetha 设计语言：复刻规格与源码核对

审计日期：2026-09-26。源码基线：`0d0571b`。对象：Samryetha 主论坛，以及同仓库的 Lako、Tasks、i18n 子站。

这是一份**按当前实现反向整理的复刻规格**。主站参数是默认基准；子站差异在第 19 节单列。本文没有修改应用样式。

配套文件：[可复制主题变量](tokens.css) · [可交给其他模型的复刻提示词](replication-prompt.md)。

核对方式：读取当前 CSS、React 组件、主题与动画代码，并检查运行中的首页、帖子详情、设置页及手机宽度设置页。没有把旧文档中的宣言、源码注释或某一张截图直接当作最终事实。

## 1. 整体气质与视觉构成

可以概括为：**冷调中性底、雾蓝强调、细线分区、紧凑控件、宽松版面、轻量连续动效。**

它更接近一个讲究阅读节奏的社区工具。页面上的主体是文本和信息关系，品牌存在于持续一致的颜色、尺寸和交互中。

复刻时最影响相似度的六件事：

1. **页面底本身就是内容容器。** 首页讨论列表没有逐条白色卡片，行与行靠细线分开。
2. **大尺度留白与小尺度控件同时存在。** 桌面栏间可达 88px，meta 只有 12px，按钮一般 30–44px 高。
3. **低饱和雾蓝和实色蓝分工明确。** 链接、细条、辅助图标是雾蓝；选中按钮与消息气泡才用实色蓝。
4. **主 CTA 随主题反色。** 浅色为墨黑按钮，深色为近白按钮，文字分别取页面底色。
5. **标题层级由字号、少量字重与字距共同表达。** 当前字重主要是 400 / 500 / 600 / 700，而不是旧指南的 590 / 650 / 690。
6. **动效解释状态和位置的变化。** 指示条移动、文字微模糊、相邻按钮补位、共享标题过渡，构成动态辨识度。

下面属于观察归纳，不是源码中存在的正式品牌命名：可称作“冷灰雾蓝的编辑式工具界面”。仓库没有证据表明它采用某个外部设计系统的完整规范。

## 2. 哪些文件才是权威来源

| 层级 | 位置 | 作用 |
|---|---|---|
| 主站视觉 | [frontend/src/globals.css](../../frontend/src/globals.css) | 主题、布局、组件外观、响应式、CSS 动效 |
| 主站主题 | [lib/theme.ts](../../frontend/src/lib/theme.ts)、[frontend/index.html](../../frontend/index.html) | 主题选择、首屏主题、系统主题跟随 |
| 组件结构与行为 | [packages/ui-commons/src](../../packages/ui-commons/src) | Button、Dialog、ConfirmDialog、Dropdown |
| 页签行为 | [use-tab-indicator.ts](../../frontend/src/lib/use-tab-indicator.ts)、[use-animated-tabs.ts](../../frontend/src/lib/use-animated-tabs.ts) | 几何测量、初次定位、active/committed 两阶段切换 |
| 帖子动态交互 | [thread-page.tsx](../../frontend/src/thread-page.tsx) | Save/Follow、按钮补位、回复树几何 |
| 图标 | [icons.tsx](../../frontend/src/icons.tsx) | 现有 SVG 路径、视框与线宽 |
| Lako 通用控件 | [lako/packages/ui/src/ui.css](../../lako/packages/ui/src/ui.css) | 带前缀的 Dropdown、Dialog、Tabs、InputBox、Toggle、Notification |
| Lako 登录视觉 | [auth.css](../../lako/packages/ui/src/auth.css) | 登录面板与授权流程的独立样式 |
| 子站 | [Tasks](../../tasks/site/src/styles.css)、[i18n](../../i18n/site/src/styles.css)、[Lako Web](../../lako/web/app/styles.css) | 各宿主实际差异 |
| 历史语义指南 | [frontend/design.md](../../frontend/design.md) | 设计意图有参考价值；多处数值和描述已经落后于实现 |

`samryetha-ui-commons/styles.css` 当前基本上是一份契约注释，**并没有完整组件外观**。复制这个包的样式文件并不能获得主站视觉；外观仍由宿主的 `globals.css` 提供。

`@lako/ui` 的 `ui.css` 则确实提供控件外观。两者不能混为一套可以互换的 CSS。

## 3. 完整主站颜色表

主题块位于 `globals.css:111` 附近。颜色按照功能命名，不按照品牌色号命名。

| Token | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | `#f7f8f8` | `#111416` | 页面大底、顶栏底、主 CTA 文字 |
| `--surface` | `#ffffff` | `#171b1e` | 卡片、浮层、聚焦后的输入面 |
| `--surface-2` | `#f0f2f3` | `#1e2428` | 次按钮、代码块、次级表面 |
| `--ink` | `#171a1c` | `#eceeef` | 主文字、主 CTA 底色 |
| `--muted` | `#70777b` | `#8f989d` | 摘要、次级信息、未激活导航 |
| `--faint` | `#a7adaf` | `#687075` | placeholder、弱辅助、时间戳 |
| `--line` | `#e1e4e5` | `#292e31` | 细分隔、输入和面板边框 |
| `--accent` | `#597585` | `#87a8b7` | 雾蓝链接、选项文字、导航条 |
| `--accent-fill` | `#3d7dbf` | `#4a86cf` | 彩色实选状态、消息气泡、计数角标 |
| `--accent-soft` | `#e7eef1` | `#1d2a30` | hover、选中背景、头像底色 |
| `--on-accent` | `#ffffff` | `#ffffff` | 实色蓝上的文字和图标 |
| `--danger` | `#9a5555` | `#d48686` | 普通危险状态与错误通知 |
| `--danger-strong` | `#b8463a` | `#e06c5b` | 较强的危险按钮文字 |
| `--error` | `#986d70` | `#c28e91` | 登录等字段错误 |
| `--success` | `#2d7d50` | `#4ade80` | 成功、已通过、完成 |
| `--warning` | `#a06e1e` | `#e0a458` | 待处理、紧急 |
| `--guide` | ink 的 14% 透明混合 | `rgba(255,255,255,.20)` | 回复树连接线 |
| `--shadow` | `0 1px 0 rgba(20,24,26,.03)` | `none` | 普通表面极轻的阴影 |

### 3.1 配色的使用语法

三组关键搭配：

```css
/* 主操作；会随主题反转 */
background: var(--ink);
color: var(--bg);

/* 彩色实选 */
background: var(--accent-fill);
color: var(--on-accent);

/* 轻选中 / hover */
background: var(--accent-soft);
color: var(--ink);
```

不要把 `--accent` 和 `--accent-fill` 合并。两者都偏蓝，但承担的视觉重量不同。选中态也不只有蓝色：格式切换、主题切换、Inbox tabs、后台筛选 pill 目前都是 `ink/bg` 反色。

### 3.2 透明混色是系统的一部分

| 场景 | 当前写法 |
|---|---|
| 顶栏分隔 | `color-mix(in srgb, var(--line) 92%, transparent)` |
| 搜索框底 | `color-mix(in srgb, var(--surface) 72%, transparent)` |
| 普通表单底 | `color-mix(in srgb, var(--surface) 76%, transparent)` |
| 登录面板底 | `color-mix(in srgb, var(--surface) 88%, transparent)` |
| 列表整行 hover | `color-mix(in srgb, var(--accent-soft) 32%, transparent)` |
| action-btn hover | `color-mix(in srgb, var(--accent-soft) 30%, transparent)` |
| 回复操作 hover | `color-mix(in srgb, var(--accent-soft) 55%, transparent)` |
| 选中会话 | `color-mix(in srgb, var(--accent-soft) 72%, transparent)` |
| 未读通知 | `color-mix(in srgb, var(--accent-soft) 46%, transparent)` |
| 输入 focus 边框 | `color-mix(in srgb, var(--accent) 62%, var(--line))` |
| 输入 focus 外圈 | `0 0 0 3px color-mix(in srgb, var(--accent) 8%, transparent)` |

与 `transparent` 混合会保留透明度，最后颜色取决于下方背景。不能把“surface 76%”简单替换为一个固定浅灰或让整个输入框 opacity=.76；后者还会使文字和边框一起变淡。

### 3.3 例外也应保留

删除确认按钮单独使用 `#e5484d`，hover `#ef5358`，pressed `#cf343a`，白字；它没有直接使用 `--danger`。

反馈纯文字标签仍有局部颜色：suggestion `#8daebe`、bug `#d58b8b`、urgent `#c1844d`。其与 `admin-badge` 的主题化语义色是两条不同实现。准确复刻应按具体 selector 取值。

## 4. 字体、字重和文字密度

### 4.1 当前字体栈

```css
--font: "Segoe UI Variable Text", "Segoe UI",
  -apple-system, BlinkMacSystemFont,
  "SF Pro Text", "SF Pro Display", "Helvetica Neue",
  "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;

html {
  font-synthesis: none;
  font-kerning: normal;
  font-optical-sizing: auto;
}

body {
  font-family: var(--font);
  letter-spacing: normal;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
```

主站不是“默认用 Inter”。这是系统字体栈，具体字符最终由设备可用字体和字符覆盖决定。浏览器计算样式可确认 CSS 字体栈，但不能单凭它证明每个中文字符使用了哪一个真实字体文件。

跨操作系统做像素级复刻，必须统一浏览器、缩放比例、字体可用性和实际 fallback。复制栈的名称不会在另一台设备上自动安装这些字体。

### 4.2 当前排版阶梯

未单独声明 line-height 的元素会受基础样式影响。主站加载 Tailwind preflight；本次桌面页面默认继承行高为 1.5。下表优先列显式规则。

| 用途 / selector | 字号 | 字重 | 行高 | 字距 |
|---|---:|---:|---:|---:|
| 品牌 `.wordmark` | 15px | 700 | 继承 | normal |
| 首页标题 `.feed-title` | 30px | 600 | 1.05 | -.015em |
| 帖子详情标题 `.thread-detail-title` | 30px | 700 | 1.25 | -.01em |
| 设置、反馈页标题 | 28px | 600 | 1.05 | -.015em |
| 个人页姓名 | 27px | 600 | 1.08 | -.015em |
| 设置内容 h2 | 23px | 600 | 1.15 | -.01em |
| 移动菜单主条目 | 22px | 600 | 继承 | normal |
| 列表标题 `.thread-title` | 17px | 600 | 1.46 | normal |
| 版块名 `.board-name` | 16px | 600 | 继承 | normal |
| 正文 `.thread-detail-body` | 15.5px | 400 | 1.75 | normal |
| 回复正文 `.ra-body` | 14.5px | 400 | 1.68 | normal |
| 后台行标题 | 14px | 600 | 继承 | normal |
| 列表摘要 `.thread-preview` | 13.5px | 400 | 1.65 | normal |
| 个人简介 | 13.5px | 400 | 1.6 | normal |
| 版块描述 | 13px | 400 | 1.55 | normal |
| 主按钮、次按钮 | 13px | 600 | 继承 | normal |
| 主导航 | 14px | 400 | 64px | normal |
| 筛选 tab | 13px | 400 | 继承 | normal |
| 列表 meta | 12px | 400 | 继承 | normal |
| 列表作者 `.sender` | 跟随 meta | 500 | 继承 | normal |
| 设置标签 | 11px | 600 | 继承 | normal |
| 后台组标签 | 11px | 600 | 继承 | .06em、uppercase |
| 状态徽章 | 10.5px | 600 | 继承 | .02em、capitalize |
| 消息时间 | 9.5px | 继承 | 继承 | normal |

数值例子：30px × 1.05 = 31.5px 行高；17px × 1.46 = 24.82px；15.5px × 1.75 = 27.125px。浏览器中的盒子尺寸可能因子像素量化出现小数差异。

日期、计数等多处使用 `font-variant-numeric: tabular-nums`。反馈序号使用 `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`。

### 4.3 文字的视觉重量

主标题用 ink；说明、摘要用 muted；最弱时间戳和 placeholder 用 faint。不要把全部文字统一成高对比黑白。也不要把 faint 用作主要动作的唯一标签。旧文档的“所有小标题 uppercase”并不成立：`.time-label` 只声明字号、字重、字距，是否大写还取决于实际内容。

## 5. 页面骨架与版心

### 5.1 外层

```css
.shell {
  width: min(calc(100% - 40px), var(--content));
  margin: 0 auto;
}
.app-shell {
  min-height: 100vh;
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}
.app-shell-content { flex: 1 0 auto; }
```

`--content = 1160px`，`--feed = 740px`。这里的百分比基于可用布局宽度；滚动条占位和浏览器缩放会影响实际左右空白。

### 5.2 页面模板

| 页面 | 网格 | gap | 页面内边距 |
|---|---|---:|---|
| 首页 | `minmax(0,740px) 1fr` | 88px | `48px 0 88px` |
| 发帖 | `minmax(0,740px) 1fr` | 88px | `48px 0 88px` |
| 个人资料 | `minmax(0,740px) 1fr` | 88px | `48px 0 88px` |
| 设置 | `180px minmax(0,1fr)` | 72px | `48px 0 88px` |
| 后台 | `180px minmax(0,1fr)` | 72px | `48px 0 88px` |
| 反馈 | `210px minmax(0,1fr)` | 56px | `48px 0 88px` |
| Inbox | 普通单栏，内部聊天网格 | — | `48px 0 88px` |
| 帖子详情 | **整版心单栏** | — | article 为 `34px 0 70px` |

首页版心达到 1160px 时：740 + 88 + 332 = 1160。右栏为 332px。

设置页版心达到 1160px 时：180 + 72 + 908 = 1160。浏览器窄于最大宽时内容列会缩小。

帖子详情没有复用首页的 740px 阅读栏。本次在 1160px 版心中实测正文和回复容器均为 1160px。若自行把详情收成 740px，属于重新设计。

侧栏一般 `position:sticky; top:108px`，两栏 `align-items:start`。正文轨道与内部可伸缩内容使用 `minmax(0,…)` / `min-width:0`，避免长文本撑大列宽。

### 5.3 顶栏

| 项目 | 精确参数 |
|---|---|
| 外层 | sticky / top 0 / z-index 20 / bg 底 / 1px 底边 |
| 内层 | 64px 高；flex；space-between；gap 24px |
| 品牌 | 15px / 700 / 不换行 |
| 主导航 | gap 26px；margin-left 34px；flex:1 |
| 导航项 | 14px；行高 64px；默认 muted，hover/active ink |
| 指示条 | 高 2px；bottom:-1px；accent；999px 圆角 |
| 右侧动作组 | gap 8px |
| 搜索 | 220×36px；圆角 10px；gap 8px；padding 0 9px 0 11px |
| 搜索文本 | 13px，placeholder faint |
| 发帖按钮 | 13px/600；padding 10px 16px；margin-left 4px；全圆 |

顶栏是**不透明背景**。将它改成半透明毛玻璃，会让滚动内容穿过，改变当前视觉。

### 5.4 页脚

margin-top 48px；padding `22px 0 30px`；1px line 顶边。内部居中、gap8px、12px faint。短页面通过 app-shell 的 flex 撑满视口。

## 6. 间距、圆角、线条和层次

这里没有统一导出的 spacing/radius token 集合；下面是从具体 selector 提取的常用尺寸。不要将它解释为严格 4px 或 8px 网格，实际存在 7、9、11、13、14、18、22 等值。

| 尺度 | 常见用途 |
|---|---|
| 2–4px | 微小圆点、控件内部 inset、紧邻辅助文字 |
| 7–10px | 图标/文字间距、meta、按钮组、标签与输入 |
| 12–18px | 字段 padding、行内组、卡片局部间距 |
| 20–28px | 章节内部、表单与标题、移动页面起始 |
| 34–48px | 页头到表单、回复区、页面顶部 |
| 56 / 72 / 88px | 双栏的主要负空间 |

| 圆角 | 用途 |
|---:|---|
| 4px | 气泡与发送者相连的一角 |
| 7px | 下拉选项、回复微操作 |
| 8px | 设置导航、后台按钮、小筛选 |
| 9px | 后台输入、登录输入内部、局部小按钮 |
| 10px | 搜索、设置输入、列表 hover 区、代码块、图片 |
| 11px | 发帖输入、主站下拉框、通知 |
| 12px | 用户菜单、统计卡、灯箱图片 |
| 14px | 会话面板、消息气泡、移动用户块、Lako 通用 Dialog |
| 16px | 主站 Dialog、登录面板 |
| 999px / 50% | 主站主按钮、pill、徽章、头像、开关 |

行分隔线通常为 1px。讨论/版块行使用 `::after`，左右各 inset12px；这样 hover 背景可以比文字和分隔线更宽。顶层回复不用横线分组。已关闭反馈、成员分组等折叠区可使用 dashed 顶线。

普通内容基本没有明显投影，但浮层有明确投影：

| 浮层 | box-shadow |
|---|---|
| 用户菜单 | `0 14px 34px rgba(20,24,26,.14)` |
| 主站下拉 | `0 14px 32px rgba(20,24,26,.16)` |
| 通知 | `0 10px 28px rgba(20,24,26,.14)` |
| 主站 Dialog | `0 24px 70px rgba(0,0,0,.55)` + `0 0 48px accent 9%` |
| 开关滑块 | `0 1px 3px rgba(20,24,26,.18)` |

因此“全站完全无阴影”不准确。深色的 `--shadow:none` 也不会清除这些局部直接声明的浮层投影。

## 7. 首页讨论行：最值得按像素复制的组件

```css
.thread {
  position: relative;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 18px 24px;
  width: calc(100% + 24px);
  margin-left: -12px;
  padding: 20px 14px;
  border: 0;
  border-radius: 10px;
  background: transparent;
  text-align: left;
  transition: background .14s ease;
}
.thread::after {
  content: "";
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 0;
  height: 1px;
  background: var(--line);
}
.thread:hover {
  background: color-mix(in srgb, var(--accent-soft) 32%, transparent);
}
```

内部排版：标题17px/600/1.46；摘要 margin-top8px、max-width620px、13.5px/1.65；meta margin-top9px、gap8px、12px；作者 ink/500；分类 accent/600；点分隔符2×2px faint；回复数右对齐、最小28px、13px、tabular-nums。

首页标题行 align-items:flex-end，底部20px，标题与日期至少 gap20px。日期为13px muted，不是旧文档里的9.5px。

tabs 在 `.feed > .tabs` 中 margin-bottom 被覆盖成0；`.feed-body > .thread-list` 的 border-top 也被去掉。两处覆盖共同避免 tab 下方多出空带或双线。

紧凑模式：行 padding `11px 14px`、gap `10px 20px`、标题15px/1.4、摘要隐藏。它不是整体 scale 缩小。

## 8. 按钮规格与状态矩阵

| 类 / 用途 | 默认 | hover | pressed | disabled |
|---|---|---|---|---|
| `.primary-action` | min-h40；0 18px；13/600；全圆；ink/bg | opacity .88 | scale .98 | opacity .28；not-allowed |
| `.compose` | 10px 16px；13/600；全圆；ink/bg | opacity .88 | scale .98 | 按组件语义处理 |
| `.action-btn` | 7px 14px；13/600；全圆；surface-2/ink；1px line | accent-soft 30% | scale .985 | 具体上下文声明 |
| `.icon-btn` | 40×40；全圆；透明；grid居中 | accent-soft | scale .96 | 具体上下文声明 |
| `.admin-btn` | min-h30；0 11px；12/500；8px；线框 | ink文字，ink34%混line边框 | 无统一 transform | opacity .45 |
| `.edit-profile` | h36；0 13px；12px；全圆；线框 | accent40%边框、soft42%背景 | 无独立规则 | 按组件语义处理 |
| `.ra-btn` | 2px 6px；13/500；7px；透明；muted | soft55%底、accent字 | 无统一 transform | 按组件语义处理 |
| `.dialog-danger` | min-h34；7px 16px；13/600；全圆；#e5484d/白 | #ef5358 | #cf343a + scale .98 | opacity .45 |

`.action-btn.active`：accent-fill 底与边、on-accent 字。active:hover 为 accent-fill 88% 混 on-accent。`.action-btn.danger` 是 danger-strong 文字。

Save/Follow 有更具体规则：180ms颜色过渡，`:active` 不做CSS transform，实际按压由JS驱动。因此应按完整class组合复刻。

回复提交按钮的 disabled 是另一处例外：opacity仍为1，背景 muted14%，文字faint，图标opacity .55；不能把通用主按钮的 .28 直接套到它上面。

`Button` 的 variant 只是映射宿主类名：primary→primary-action、secondary→action-btn、danger→dialog-danger、admin→admin-btn、login→login-primary、compose→compose、icon→icon-btn。

## 9. 表单、下拉、开关、分段选择

### 9.1 输入尺寸

| 场景 | 高度 / padding | 半径 | 字号 |
|---|---|---:|---:|
| 全局搜索 | h36；0 9px 0 11px | 10px | 13px |
| 发帖标题 input | h44；0 52px 0 14px | 11px | 15px |
| 发帖 textarea | min-h230；14px；line-height1.7 | 11px | 14px |
| 设置 input | h42；0 12px | 10px | 13px |
| 设置 textarea | min-h100；11px 12px；line-height1.6 | 10px | 13px |
| 登录 input frame | 外层h42；内部input h40，0 11px | 外10/内9 | 13px |
| 后台 input | h38；0 11px | 9px | 13px |
| 回复 textarea | 初始/最小128；最大280 | 继承表单 | 继承表单 |

标签与输入间8–9px；普通标签11–12px/600/muted。focus统一混色边框+surface底+3px淡accent外圈，动画140ms。登录 invalid 外框使用 error62%混line，错误文案10.5px/1.45/error。

标题字数放在右下角：right13px、bottom14px、10px faint，input右padding52px给它留空间。

### 9.2 主站 Dropdown

触发器44px高、11px圆角、padding `0 14px 0 12px`、字号13px。展开时 `border-radius:11px 11px 0 0`；面板 `top:100%`，去掉上边框，`border-radius:0 0 11px 11px`，padding4px。

面板初态 opacity0、visibility:hidden、pointer-events:none、Y=-3px；开态归位。透明度110ms，位置140ms。关闭 visibility 延迟140ms，避免退出动画被立即截断。

选项高34px、左右10px、7px圆角、13px；hover为accent-soft；selected为accent文字和5px圆点。箭头由6×6px方形的右/下1.25px边框组成，45°→225°，配合轻微Y偏移。

后台压缩版触发器34px高、8px圆角、12.5px字；选项32px高。

主站 `SDropdown` 最终使用 `samryetha-ui-commons/Dropdown`，没有搜索参数。Tasks/i18n 使用的 `@lako/ui/Dropdown` 可启用搜索，默认触发器40px、圆角10px，选项面板最大高 `min(320px,45vh)`，搜索框34px、sticky置顶。两者都应保留键盘选择、Esc和焦点返回等行为。

### 9.3 Toggle 与分段控件

开关38×22px，1px line边框，999px圆角；off底为faint20%。滑块14×14px，top/left3px，surface色；on底与边为accent-fill，滑块X移动16px，180ms标准减速曲线。轨道颜色160ms。

格式切换：外层全圆、padding3px、gap4px、line边框、surface-2底；子项padding5px 12px、12px/600；选中ink/bg。真实指示背景绝对定位、top/bottom3px，位置与宽度200ms `cubic-bezier(.2,.8,.2,1)`。

主题选择也是ink/bg分段语言，gap2px；外底为faint12%。主站主题选择有light、dark、system三种，但解析后的实际视觉仍只有light/dark。

## 10. 导航与页签的动态几何

| 类型 | 几何 | 动画 |
|---|---|---|
| 顶部主导航 | 2px高横条、bottom:-1px | X/宽240ms，opacity120ms |
| 首页filter tabs | gap22px、tab底padding12px、2px横条 | X/宽220ms，opacity120ms |
| 设置侧栏 | 按钮38px高、gap3px、8px圆角背景 | 背景宽/高/位置220ms |
| 设置侧边细条 | 2×18px、accent | Y位置220ms |
| @lako/ui Tabs | gap22px、tab底padding11px、2px横条 | X/宽220ms |

设置按钮默认13px/400，选中600；同时过渡 `font-weight` 和 `font-variation-settings:"wght"`。字体必须真正支持相关变化，才可能得到连续字重效果。

指示器不是每个tab自带一条border。当前hook读取激活元素的offsetWidth/Height/Left/Top，首次直接定位，active真正变化时才加动画，resize时重新读取并直接定位。

内容切换与高亮不是同一个时刻：点击即更新active和指示条；旧内容淡出125ms后才更新committed内容；下一帧退出is-entering状态，再淡入。自增token阻止快速点击产生的过期计时回调覆盖新选择。

## 11. 浮层、用户菜单和通知

### 11.1 主站 Dialog

遮罩fixed全屏、z90、纯黑70%、backdrop blur8px。面板fixed居中、z91、`translate(-50%,-50%)`，宽 `calc(100% - 40px)`、max-width380px、max-height `calc(100dvh - 32px)`、可纵向滚动。

面板padding `22px 22px 18px`，16px圆角，surface背景，1px line。标题16px/600；描述上margin8px、13.5px/1.55/muted；动作行margin-top22px、gap9px、右对齐。

遮罩进入160ms ease；内容进入180ms标准曲线，scale .96→1、opacity0→1。退出180ms，scale1→.96。Radix Presence等动画完成才卸载。宽度特例要注意级联：例如 `.feedback-modal` 声明width480px，但没有解除基础 `max-width:380px`，仅这两条规则组合时实际仍受380px上限约束。

普通Dialog默认可Esc/点外部关闭；ConfirmDialog使用AlertDialog，点遮罩不关闭、焦点默认落取消。弹层结构在Portal中；不要仅复制视觉而丢掉焦点约束与滚动锁。

### 11.2 用户菜单

右上锚定，距触发器8px，宽220px、max-width `calc(100vw - 24px)`、padding5px、12px圆角、surface底、1px line、z70。初始Y=-4px；opacity110ms、位置140ms；隐藏延迟与退出一致。

用户身份行：32px头像 + minmax(0,1fr)，gap10px，min-h50，padding6px 8px、8px圆角。菜单动作h36、gap10、左右10px、12px字、8px圆角；分隔线margin5px 6px。

### 11.3 Toast

主站容器右上20px、top20px、z60、宽 `min(280px,calc(100vw - 40px))`、gap9px。通知min-h46、padding `11px 14px 11px 12px`、圆角11、字号12/600。

外框为语义色28%混line，左侧3px语义色竖线，底surface。图标容器20px圆形、语义色13%底；内SVG14px、stroke1.9。

主站CSS动画共3秒：0%在X=110%，9%已进入，84%前保持，最后离开。`@lako/ui`通知使用240ms、X24px的单次入场，生命周期由组件管理，不能照搬主站3秒keyframe。

## 12. 徽章、头像与图标

普通状态徽章高20px、左右8px、全圆、1px边框、10.5px/600、字距.02em、capitalize。bug/urgent/done等常用对应语义色34%混line作边框、9%混transparent作底；suggestion底为10%。有的状态只有彩色字和边框，没有彩底。

Inbox计数角标是另一组件：min-width16px、height16px、左右4px、9.5px/700、line-height1、accent-fill/白；不要误用20px状态badge规格。

头像底accent-soft、字accent、全圆。尺寸：用户菜单32px；移动用户块40px；评论40px；个人资料72px，手机56px。资料头像在线点11px，带2px bg描边；右栏在线点8px，外圈accent12%扩4px，当前CSS并未给它循环脉冲动画。

图标以空心线性SVG为主，currentColor、圆端点。**线宽并不统一为1.9**：Search/Hamburger/Close为1.7，Profile/Mail等1.6，Eye1.5，Settings部分路径1.35，Thread加号2。视觉尺寸主要14–19px，通常viewBox24×24。最准确的办法是直接复用 `icons.tsx`。

## 13. 正文、回复树与内容韧性

正文15.5px/1.75，回复14.5px/1.68。段落的纵向空隙来自恢复的 `margin:0 0 1em`，末子margin0；Tailwind preflight会清除浏览器默认margin，迁移时必须留意。

代码块：surface-2底、14px padding、10px圆角、13px字、横向滚动。引用块：左边3px line、左padding14px、muted字。图片max-width100%、圆角10px。表格display:block、width:max-content、max-width100%、overflow-x:auto。标题和正文多处使用overflow-wrap:anywhere。

纯文本内容保留pre-wrap。不能仅靠给页面overflow-x:hidden掩盖长内容问题，那会使代码、表格或按钮被截断。

回复区最终margin-top36px；标题15px/600、margin-bottom2px。每条回复是头像列与内容列的grid。

| 几何 | 桌面 | ≤640px |
|---|---:|---:|
| 头像直径 | 40px | 34px |
| 头像到内容gap | 12px | 11px |
| 每层子树缩进 | 46px | 38px |
| 第3级对子树的缩进 | 40px | 32px |
| 第4级对子树的缩进 | 34px | 26px |
| 顶层card底padding | 16px | 14px |
| 子回复card底padding | 10px | 8px |

card顶padding2px；正文上margin3px；作者14px/600（手机13px）；头部gap7px；操作行上margin5px。

连接线为整树绝对定位SVG：由代码实测头像位置，父子树拥有垂直干线，子节点拥有接入弯线。1px guide色、round、non-scaling-stroke、pointer-events:none。不要为每个节点额外叠一条L形border，会产生重复线和错误转角。

行内回复框在目标下面展开，margin `4px 0 10px`，没有外围卡片，textarea初始128px。

## 14. Inbox、后台与反馈的补充规格

Inbox聊天面板：`300px minmax(0,1fr)`，min-height480px，1px line，14px圆角，surface底。会话行padding14px 16px、gap4px；选中为accent-soft72%。

消息列表padding18px、gap10px。气泡max-width72%、padding9px 12px、14px圆角、13px/1.5；自己的气泡accent-fill/白，右下角4px；对方faint18%底，左下角4px。时间9.5px、opacity .72、上margin3px。输入区padding12px 16px，textarea最小44px高。

后台统计卡：网格 `repeat(auto-fill,minmax(120px,1fr))`、gap10px；卡padding14px 15px、radius12、surface76%；数字24px/600/1，标签11.5px。后台行padding16px 0、gap24px、1px底线；动作gap7px；侧栏沿用设置语言。

反馈左栏210px、gap56px，项目项padding10px 12px、radius8、内部gap3px；选中soft底；标题13px/600，计数10.5px/faint。评论块padding12px 14px、radius10、surface50%、**实线**顶边；已关闭反馈折叠区才是dashed顶边。

## 15. 动效的真实参数

### 15.1 曲线语义

| 曲线 | 当前使用 |
|---|---|
| `ease` | hover、普通颜色、透明度 |
| `cubic-bezier(.22,.8,.24,1)` | 指示器、Dialog、主要位置与尺寸变化 |
| `cubic-bezier(.2,.8,.2,1)` | 当前Save/Follow与相邻按钮位置补间 |
| `cubic-bezier(.16,1,.3,1)` | 420ms共享帖子标题 |
| `cubic-bezier(.34,1.56,.64,1)` | 移动菜单入场，有过冲 |
| `cubic-bezier(.4,0,1,1)` | 设置内容退出、部分弹层退出 |
| `cubic-bezier(0,0,.2,1)` | 设置内容淡入 |

`ease`不是linear。要线性转圈时源码显式使用`linear`。

### 15.2 动效表

| 元素 / 操作 | 真实动画 |
|---|---|
| 大部分hover | 120–140ms颜色/背景/边框 |
| 页面内容默认切换 | old淡出130ms；new延迟125ms后淡入130ms |
| 帖子进入背景内容 | 160ms，opacity→0、Y4px、scale.995 |
| 列表标题到详情标题 | 420ms共享元素几何插值 |
| 详情辅助信息入场 | 延迟80ms，240ms，Y7px→0+fade |
| 页签指示条 | 220ms；顶栏240ms |
| 设置内容 | 退出125ms、进入150ms |
| 普通action-btn颜色 | 280ms标准曲线 |
| Save/Follow专属颜色 | **180ms ease覆盖通用280ms** |
| Save/Follow按钮本体 | **180ms，scale .985→1，无过冲** |
| Save/Follow文字 | **200ms，opacity .35→1、blur3px→0** |
| 相邻按钮补位 | 220ms，延迟最多24ms；位置变化≤.5px不处理 |
| 回复卡片增删补位 | 新增220ms、已有卡片280ms |
| 主站Dialog | 遮罩160ms；面板进入/退出180ms，scale .96 |
| 主站Dropdown | opacity110ms、位移140ms |
| Toggle滑块 | 180ms、X16px |
| 加载spinner | 700ms linear infinite |
| 普通数据就绪 | `.content-fade` 180ms fade-in |
| 登录骨架 | 1.35s ease-in-out，opacity .44↔.82 |

### 15.3 连续性比装饰更重要

Save/Follow的JS记录当前Animation。再次触发时从getComputedStyle读取当前帧、取消旧动画、从当前值接管；相邻按钮先记录旧几何，再计算新几何并补位。当前代码不做旧文档描述的统一280ms弹簧过冲。

View Transitions必须同时有相应DOM标记和导航逻辑；只复制CSS不能获得共享标题移动效果。基础root快照自身不做动画，避免整页与内容/标题重复过渡。

### 15.4 减弱动效

主站同时处理系统 `prefers-reduced-motion` 和html的 `data-reduce-motion="true"`。全局animation/transition缩到.01ms，滚动改auto；菜单显式恢复opacity1、transform/filter none；spinner停止并保留加载文案；弹层保留居中transform。

这属于应复刻的行为，但当前实现并非所有路径完全统一：`animateAction`直接检查系统media，而部分其他逻辑调用会考虑用户偏好的`reducedMotion(user)`。若要求所有用户偏好分支一致，需要单独验证，不能仅凭全局CSS认为JS动画也全部被关闭。

## 16. 响应式的精确分工

### 16.1 主站断点

| 宽度 | 实际变化 |
|---|---|
| >900px | 完整顶栏导航；首页双栏；右侧栏sticky |
| ≤900px | 首页/发帖/资料折单栏、右侧栏隐藏；反馈折单栏；主导航隐藏、汉堡显示 |
| ≤900px 的设置/后台 | **仍为两栏**：160px侧栏 + minmax(0,1fr)，gap40px |
| ≤640px | 设置/后台折单栏；设置导航变可横向滚动；顶栏56px；shell总预留28px |
| ≤480px | 顶栏账户姓名隐藏，触发器padding0 6px |

旧指南“900px下所有网格都折单列”不准确。

### 16.2 ≤640px常用尺寸

首页padding24px 0 48px；页头底padding16px；首页标题26px；讨论标题16px；摘要13px；行宽+16px、左margin-8px、padding18px 8px、gap12px；讨论hover背景取消。

发帖padding24px 0 54px；标题行折单列；textarea min-height210px；操作区上下排列。个人页padding28px 0 72px；头像56px；姓名24px。设置/后台padding28px 0 72px、gap28px；字段网格单列，gap21px。

Inbox会话列表与聊天区上下排列，会话列表max-height220px。

### 16.3 手机搜索

最终层叠后的收起控件宽40px、高34px。早一段写的width34px被后面的规则覆盖。input宽0、透明、禁用指针；聚焦后展开。支持`:has`时，聚焦会隐藏顶栏wordmark和user-menu，让actions和搜索占剩余宽度；不支持`:has`时有较窄输入框的回退规则。

已有输入值和失焦后的状态受多组selector共同影响；复刻建议复用完整响应式块，不只挑第一条匹配规则。

### 16.4 触控与可读性

≤640px输入及textarea有16px强制规则；`(hover:none) and (pointer:coarse)`另为所有input/textarea/select提供16px兜底。目的是避免移动Safari聚焦缩放。

小按钮通过伪元素扩大命中区：settings-toggle `inset:-11px -3px`，board-option `-6px 0`，admin-btn/pill/inbox-tab `-7px -4px`，format-toggle `-8px -4px`等。代码意图是接近44px触控热区，但应检查相邻扩展区是否重叠；不能仅凭伪元素规则宣布所有交互目标均已通过可访问性验证。

### 16.5 移动菜单

fixed inset0、z90、bg背景、overflow-y:auto、overscroll-behavior:contain；Portal挂到body。菜单头56px；主条目22px/600，padding15px 14px、radius12、gap12；次条目16px/500/muted；底部padding含safe-area-inset-bottom。

菜单整体200ms淡入、260ms淡出。项目入场350ms：Y=-20→0、blur6→0、opacity0→1；每项延迟45ms。退出300ms统一开始，不错列。Escape和滚动锁是行为的一部分。

## 17. 登录页面与Lako嵌入的区别

仓库保留主站登录样式，也存在Lako授权组件。应先看目标页面实际渲染哪一条流程。

| 项目 | 主站 `.login-*` | Lako `.lako-auth-*` |
|---|---|---|
| 面板最大宽 | 404px | 404px |
| 面板内边距 | 30px | 32px |
| 面板圆角 | 16px，手机14px | 16px |
| 标题 | 25px/600/1.1、-.015em | 31px/600/1.16、-.005em |
| 标题上边距 | 由heading区域控制 | 30px |
| 表单上距 | heading到form | 28px |
| 提交按钮 | h42，全圆，12.5px/600 | 表单按钮min-h36，**10px圆角** |
| 字体 | 主站--font | Text与Display两条字体栈 |

Lako登录面板背景同样是surface88%，细边框和极轻shadow；嵌入模式自身不带外边距。宿主Dialog容器可解除自己的边框/背景/投影，让Lako面板负责外观。

本次在127.0.0.1来源打开登录弹层遇到了“Cross-origin request rejected”，因此**没有将登录内部流程标为本次浏览器验证通过**。随后localhost来源已有会话，可检查设置页。此处登录参数来自当前源码，不是从错误面板推测。

## 18. 层叠、主题和无障碍迁移注意点

主站 `data-theme` 写在html上；本地key为 `samryetha.theme`，默认system。theme.ts将system解析为light/dark，并同步meta theme-color。拷贝tokens.css时仍需实现首屏主题选择，否则会先出现浅色再切暗色。

`html`常驻 `scrollbar-gutter:stable`。Dialog锁滚动后针对 `body[data-scroll-locked]` 将margin-right和padding-right压回0，避免重复补偿。把它迁移到不同的弹层库时，应复核锁滚动机制后再保留相同补偿。

可交互元素主要使用真实button/a/input；focus-visible常用 `2px solid accent`、offset3px；后台offset2px，下拉option负offset2px。输入虽然outline:0，但用focus边框和外圈接替。不能无差别复制outline:none。

主要z-index顺序：回复连接线0、回复内容1；帖子sticky标记10；顶栏20；主站下拉50；通知60；用户菜单70；移动菜单/遮罩90；Dialog91；灯箱1000。`@lako/ui`另用Dialog1000、Notification1100。不同系统混用时可能相撞，应先解决Portal归属和层级，而不是持续加大数字。

## 19. 同一家族的不同实现

### 19.1 颜色与空间差异

| 项目 | 主站 | Tasks | i18n | Lako Web / Auth |
|---|---|---|---|---|
| 浅色bg | #f7f8f8 | **#f6f7f7** | #f7f8f8 | #f7f8f8 |
| 深色bg | #111416 | **#101315** | #111416 | #111416 |
| 浅色ink | #171a1c | **#191c1f** | #171a1c | #171a1c |
| 深色ink | #eceeef | **#edf0f2** | #eceeef | #eceeef |
| 浅色accent | #597585 | **#33728e** | #597585 | #597585 |
| 深色accent | #87a8b7 | **#8ebbd0** | #87a8b7 | #87a8b7 |
| accent-fill | 单独实色蓝 | **直接引用accent** | 单独实色蓝 | 单独实色蓝 |
| 主版心 | 1160px | **1120px** | 1160px | 依页面结构 |
| 顶栏 | 64px sticky | **72px，普通文档流** | 64px sticky | 依页面结构 |
| 主CTA圆角 | 999px | **8px** | 999px | 普通全圆，Auth表单10px |
| 主要断点 | 900 / 640 / 480 | **800 / 520** | **860 / 600** | Web为700 |

Tasks侧栏210px、gap56px，上下padding48/88，但sticky top36px；统计区四列，数字24px。其body15px/1.45；i18n body15px/1.55。主站当前基础line-height由preflight影响，不能盲目复用其他子站body设置。

Tasks当前暗色块没有重新声明`--surface-2`，仍会继承浅色`#edf0f1`。这是现状中的漂移，不应描述成跨站统一规范；它是否在某控件中产生明显浅色面，需按实际使用场景验证。

i18n在860px下将导航移到第二行（45px高），不是主站全屏汉堡菜单。600px下版心总预留32px，标题27px；主内容padding32/60。

### 19.2 两套Dialog的差异

| 参数 | 主站 commons + globals.css | @lako/ui 通用Dialog |
|---|---|---|
| 默认面板宽 | max380px | min(440px,100%) |
| 面板圆角 | 16px | 14px |
| padding | 22px 22px 18px | 24px |
| 标题 | 16px/600 | 21px/600 |
| 遮罩 | 黑70%，blur8px | #080a0b 58%，blur3px |
| shadow | 黑55% + accent9%光圈 | 黑24% |
| 进入 | 180ms | 200ms |
| 退出 | 180ms标准曲线 | 180ms加速曲线 |
| 层级 | 90/91 | overlay1000 |
| 框架 | Radix Portal/Presence | 自有Portal、焦点与退出管理 |

Tasks还会将任务编辑Dialog设为min(500px,calc(100vw - 40px))；不能用默认440px猜测每个业务弹层。

## 20. 旧设计指南与当前代码的差异清单

| 旧描述 | 当前事实 | 复刻处理 |
|---|---|---|
| ui-sans / SF / Inter优先 | Segoe UI Variable Text优先 | 用当前字体栈 |
| 620–690等细分字重构成性格 | 当前主要400/500/600/700 | 逐selector复制 |
| 首页标题650、-.035em | 600、-.015em | 按当前值 |
| wordmark680、-.01em | 700、normal | 按当前值 |
| surface-2尚未定义 | 主站浅深均已定义 | 直接使用变量 |
| 成功/警告多为局部硬编码 | 现有success/warning主题变量 | 主体使用主题变量，记录剩余例外 |
| 所有按钮全圆 | 后台8px、Tasks8px、Lako表单10px | 按组件与宿主区分 |
| Inbox active、format active用彩色蓝 | 当前都用ink/bg | 不泛化selected |
| 900px所有两栏折单栏 | 设置/后台到640px才折 | 采用实际断点表 |
| 图标统一stroke1.9 | 实际约1.35–2，多尺寸 | 复用SVG源 |
| Save/Follow统一280ms spring | 180/200/220ms，按压无过冲 | CSS覆盖+JS一起核对 |
| 评论等小嵌套区都dashed | fb-comments为实线顶边 | 按selector区分 |
| 没有其他加载语言 | Lako嵌入已有骨架pulse | 区分spinner与skeleton场景 |

源码中的某些注释也落后于实现：例如action-btn附近仍写“280ms spring”，但后面的特定class与JS已覆盖。判定优先级应为实际渲染/完整层叠及执行代码，而不是仅看注释。

## 21. 本次验证边界

已核对主站运行时的：

- 首页暗色桌面：1160px版心，740px主栏+88px gap+332px侧栏；搜索36px高；主标题30px/600；列表17px/600，摘要13.5px/1.65。
- 帖子详情：整版心正文与回复区、30px/700详情标题、15.5px/1.75正文、40px头像、14.5px/1.68回复。
- 设置页：180px桌面侧栏、72px gap、42px输入、23px/600内容标题。
- 手机设置页：浏览器实际 `innerWidth=390` CSS px，顶栏56px、单栏、输入16px；文档scrollWidth与clientWidth均378px，当前观察未发生横向溢出。浏览器存在缩放，工具请求宽度不等于CSS像素，因此以页面读取值为准。

浅色主题值、完整动效时序、后台、私信、登录内部和子站详细参数以源码核对为主；没有对所有路由、所有交互状态做浏览器回归，也没有进行字体文件级别或逐像素截图差分。没有修改用户主题设置来获得浅色页面。

## 22. 复刻实施顺序与验收表

建议先复制同目录tokens.css，然后按当前主站样式实现基本骨架。以下顺序最容易定位误差来源：

1. 字体栈、reset、明暗主题、基础背景与文字。
2. 版心、顶栏、首页双栏和单栏详情。
3. 讨论行、meta、tabs、按钮、表单。
4. hover/focus/selected/disabled等完整状态。
5. Dialog、Dropdown、菜单、通知的层级和焦点行为。
6. 内容切换、共享标题、可打断微交互。
7. 手机断点、长内容、触控、减弱动效。
8. 仅在目标需要时加入Tasks/Lako/i18n宿主差异。

验收项目：

- [ ] 主题变量颜色完全一致，主按钮明暗反色正确。
- [ ] 字体、字重、字距和中文fallback一致；不是仅比较font-size。
- [ ] 首页有740px主栏和88px负空间；列表本身没有卡片外框。
- [ ] 列表hover向两侧延伸，分隔线与正文保持对齐。
- [ ] tabs与列表之间没有额外间隙或双线。
- [ ] 主站按钮全圆与后台8px按钮正确区分。
- [ ] 输入焦点只有轻微外圈，错误与禁用各有语义。
- [ ] 页签指示器按真实宽度移动，首屏不从0扫过。
- [ ] 快速切换不会被旧计时回调带回，动画可接管。
- [ ] Dropdown展开接缝、圆角和退出隐藏时机正确。
- [ ] Dialog能关闭、回焦、锁滚动；确认弹层不会因点外面消失。
- [ ] 900px与640px承担不同折叠职责。
- [ ] 真实CSS宽度390px可操作；320px、超长URL、长昵称另作边界测试。
- [ ] 图片、代码、表格和深回复链不撑破页面。
- [ ] 系统与用户减弱动效的CSS/JS路径均验证。
- [ ] 语义状态除了颜色还有文字、选中标记或ARIA表达。

不要只告诉实施者“做一个极简蓝灰风格网站”。把主题变量、版心、排版阶梯、组件状态、动态几何和子站边界一并交付，才能稳定保留 Samryetha 的辨识度。
