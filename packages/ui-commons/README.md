# samryetha-ui-commons

论坛 / Lako / 翻译站共用的 `Button` / `Dialog` / `ConfirmDialog` / `Dropdown` 原语。

存在的理由：这几类控件的**行为**（动画、焦点、关闭路径）此前在各宿主里被重复实现了
好几遍，而 bug 几乎全出在这些地方 —— Esc 关闭有 6 份实现、滚动锁 4 份、手写遮罩 6 处且
行为互不一致。收敛成一个包之后，这些逻辑只存在一份。

## 用法

```tsx
import { Button, Dialog, ConfirmDialog, Dropdown, buttonClass } from "samryetha-ui-commons";
import "samryetha-ui-commons/styles.css";
```

`react` / `react-dom` / 两个 Radix 包都是 **peerDependencies**：宿主必须自己装。
不这样做的话 Radix 可能出现两份副本，而那种失败是**静默的** —— `Dialog.Trigger`
找不到 `Dialog.Root` 的 context，对话框根本不弹，控制台一条报错都没有。

## 两条硬约束

**1. `open` 在服务端首屏必须为 `false`。**

Radix 的 Portal 不参与 SSR，`Dialog.Content` 在未打开时不渲染。如果首屏就是 `open`，
服务端与客户端产出不一致 → hydration 不匹配。包内没有用 `forceMount` 就是为了守住这条。

**2. 改了包之后，宿主要重跑一次 `install`。**

宿主用 `file:` 引用本包时，pnpm 做的是**安装那一刻的硬链接拷贝**，不是软链。所以：

- 只改了已有文件的内容 → `tsc` 原地覆写，硬链接跟着变，`build:ui` 的顺序就够了。
- **新增了源文件** → 新文件不在那份拷贝里，`tsc` 也救不回来，必须重跑宿主的
  `pnpm install`。这个失败没有任何报错，import 只是解析到旧的 `dist/index.js`。

## 开发

```bash
pnpm install
pnpm typecheck
pnpm build      # tsc → dist/（per-file ESM + .d.ts）
```

构建产物形态沿用 `lako/packages/ui`（`@lako/ui`）：`tsc -p tsconfig.build.json` 出
per-file ESM 到 `dist/`，CSS 作为独立 subpath 导出，`types` 指向 `src`、`import` 指向 `dist`。
`dist/` 被仓库根的 `.gitignore` 忽略，**每次 checkout 后都要先构建**。
