// 文件夹树的纯逻辑层：扁平列表 -> 树/面包屑/顺序，全部是纯函数。
// Pure logic for the folder tree: turning the flat list into a tree, a breadcrumb and an order.
//
// 后端刻意下发扁平列表（parentId 相连），由前端拼树：这样"面包屑"与"缩进层级"只有一份实现，
// 且不需要为每种视图各开一个接口。本模块不依赖 React，可以直接被冒烟脚本驱动。
// The backend deliberately ships a flat list joined by parentId and lets the client assemble the
// tree, so the breadcrumb and the indentation share one implementation and no extra endpoint is
// needed per view. This module has no React dependency, so a smoke script can drive it directly.

export type FolderNode = {
  id: number;
  // 后端 DTO 里 parentId 是可缺省字段（顶层文件夹可能整个字段都不下发），
  // 所以这里显式接受 undefined，并在下面统一归一成 null 再比较——
  // 否则"缺省的顶层"与"显式的 null"会被当成两个不同的父级，顶层文件夹会整个消失。
  // The backend DTO marks parentId optional (a top-level folder may omit the field entirely), so
  // undefined is accepted here and normalised to null before any comparison; otherwise an omitted
  // top level and an explicit null would count as two different parents and every top-level folder
  // would disappear.
  parentId?: number | null;
  name: string;
  description: string;
  sortOrder: number;
  resourceCount: number;
  childCount: number;
};

// 当前浏览的文件夹视图。三个取值对应文件管理器的三种位置：
// "root"  = 根目录（默认视图）：顶层文件夹行 + 没有归属任何文件夹的资料；
// 数字     = 某个文件夹：它的直接子文件夹行 + 它自己的资料；
// "all"    = 跨文件夹的"全部资料"：什么都不过滤，仅供搜索/管理时显式选择，绝不是默认视图。
// The folder view being browsed, mirroring the three positions a file manager has: "root" is the
// root directory (the default): top-level folder rows plus resources that belong to no folder; a
// number is one folder: its direct child folders plus its own resources; "all" is the cross-folder
// "everything" view, which filters nothing and exists only as an explicit opt-in for search and
// administration - it is never the default.
export type FolderView = "all" | "root" | number;

export function folderById(folders: FolderNode[], id: number): FolderNode | null {
  return folders.find((folder) => folder.id === id) ?? null;
}

export function childrenOf(folders: FolderNode[], parentId: number | null): FolderNode[] {
  return folders
    .filter((folder) => (folder.parentId ?? null) === parentId)
    .sort((left, right) => left.sortOrder - right.sortOrder || left.id - right.id);
}

// 从顶层到自身的一条链。数据成环（理论上不该发生）时在重复处停下，绝不无限循环——
// 一棵成环的树会让面包屑渲染卡死整个页面。
// The chain from the top level down to the folder itself. A data cycle (which should not happen)
// stops at the repeat rather than looping forever, because a cyclic tree would hang the entire
// breadcrumb render.
export function folderBreadcrumb(folders: FolderNode[], id: number): FolderNode[] {
  const chain: FolderNode[] = [];
  const seen = new Set<number>();
  let current: number | null = id;
  while (current !== null && !seen.has(current)) {
    seen.add(current);
    const folder = folderById(folders, current);
    if (!folder) break;
    chain.unshift(folder);
    current = folder.parentId ?? null;
  }
  return chain;
}

export function folderDepth(folders: FolderNode[], id: number): number {
  return Math.max(0, folderBreadcrumb(folders, id).length - 1);
}

// 深度优先展开为「行」，并给出每行的缩进层级：侧栏与下拉框都用它，保证两处的层级完全一致。
// Flattens into rows in depth-first order along with each row's indent level. The sidebar and the
// picker both use it, so the two can never disagree about the hierarchy.
export function flattenFolders(folders: FolderNode[]): Array<{ folder: FolderNode; depth: number }> {
  const rows: Array<{ folder: FolderNode; depth: number }> = [];
  const seen = new Set<number>();
  const walk = (parentId: number | null, depth: number) => {
    for (const folder of childrenOf(folders, parentId)) {
      if (seen.has(folder.id)) continue;
      seen.add(folder.id);
      rows.push({ folder, depth });
      walk(folder.id, depth + 1);
    }
  };
  walk(null, 0);
  // 父级缺失（数据不该出现，但真出现时不能让它们从界面上彻底消失）：按 id 顺序补在末尾。
  // Folders whose parent is missing (which should not happen, but must not silently vanish from
  // the interface) are appended in id order.
  for (const folder of folders) {
    if (seen.has(folder.id)) continue;
    seen.add(folder.id);
    rows.push({ folder, depth: 0 });
  }
  return rows;
}

// 「父 / 子 / 孙」形式的路径标签，供下拉框选项与提示使用。
// A "parent / child / grandchild" path label, used by the picker's options and tooltips.
export function folderPathLabel(folders: FolderNode[], id: number): string {
  return folderBreadcrumb(folders, id).map((folder) => folder.name).join(" / ");
}

// 视图是否命中某个文件夹；"全部"与"根目录"永远不命中。
// Whether a view targets a concrete folder; "all" and "root" never do.
export function viewFolderId(view: FolderView): number | null {
  return typeof view === "number" ? view : null;
}

// 当前层级应当以"文件夹行"列出的文件夹：根视图取顶层文件夹，具体文件夹取其直接子级。
// "全部资料"是跨文件夹的平铺视图，此时再列文件夹行只会让人误以为资料被它们收着，因此不列。
// The folders that belong on screen as folder rows at the current level: the root view takes the top
// level and a concrete folder takes its direct children. "All" is a flat cross-folder view, where
// folder rows would only suggest that everything on screen sits inside them, so it has none.
export function foldersAtLevel(folders: FolderNode[], view: FolderView): FolderNode[] {
  if (view === "all") return [];
  return childrenOf(folders, typeof view === "number" ? view : null);
}

// 回到上一级的目标视图；根目录与"全部"都没有上一级。
// 顶层文件夹的上一级是根目录，子文件夹的上一级是它的父文件夹——这样列表里永远有一条明确的
// 向上路径，且从任何层级都能一路退回根目录。
// The view one level up; neither the root nor "All" has one. A top-level folder goes up to the root
// and a subfolder goes up to its parent, so the list always carries an explicit way up and every
// level can be walked back to the root.
export function parentFolderView(folders: FolderNode[], view: FolderView): FolderView | null {
  if (view === "all" || view === "root") return null;
  // 父级引用缺失（文件夹已被删或数据异常）时退回根目录，绝不把用户留在一个查不到内容的视图里。
  // A missing parent (the folder was deleted or the data is malformed) falls back to the root rather
  // than leaving the viewer on a view that can only come back empty.
  const parentId = folderById(folders, view)?.parentId ?? null;
  return parentId === null ? "root" : parentId;
}
