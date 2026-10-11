// 拖拽上传的纯逻辑层：校验、逐文件状态机与上传编排。
// Pure logic for drag-and-drop uploads: validation, the per-file state machine and the
// upload orchestration.
//
// 这里刻意不引入 React，也不直接调用 api：页面负责事件与渲染，本模块只负责"哪些文件能传、
// 每个文件现在处于什么状态、失败的原因是什么"，因此可以用一个注入的上传函数直接驱动，
// 不需要浏览器就能验证红绿。
// This module deliberately imports neither React nor api: the page owns events and rendering,
// while this module owns "which files may be uploaded, what state each one is in and why it
// failed". An injected upload function is therefore enough to drive it, and its behaviour can be
// verified without a browser.

export type DropEntryStatus = "pending" | "uploading" | "success" | "failed";

// 失败分类：UI 据此挑选 i18n 文案；未失败时为空串。
// Failure category: the UI picks its i18n copy from this; it is an empty string when the entry
// has not failed.
export type DropEntryFailure = "" | "badExtension" | "fileTooLarge" | "upload";

export type DropEntry = {
  id: string;
  name: string;
  sizeBytes: number;
  status: DropEntryStatus;
  failure: DropEntryFailure;
  // 上传失败时服务端给出的具体原因（如"扩展名不允许"），它比通用文案更有助于自助排查。
  // 服务端没有给出原因、或失败发生在本地校验阶段时为空串。
  // The server's specific reason when an upload fails (an unsupported extension, say), which
  // helps the user recover far better than generic copy. Empty when the server gave none or the
  // failure happened during local validation.
  detail: string;
  // fileTooLarge 时用于 {size} 插值的上限，其余情况为 0。
  // The limit interpolated into {size} for fileTooLarge, and 0 otherwise.
  limitBytes: number;
};

export type DropFileLike = { name: string; size: number };

export type DropLimits = { allowedExtensions: string[]; maxUploadBytes: number };

// 拖入的文件名可能带路径（某些浏览器在目录拖放时给出相对路径），只取最后一段。
// A dropped filename may carry a path (some browsers hand out a relative path for directory
// drops), so only the last segment is kept.
export function baseName(name: string): string {
  const parts = name.split(/[\\/]/);
  return parts[parts.length - 1] ?? name;
}

export function extensionOf(name: string): string {
  const match = /(\.[A-Za-z0-9]+)$/.exec(baseName(name));
  return match ? match[1].toLowerCase() : "";
}

// 标题初值取"去掉扩展名的文件名"：多数人上传的资料标题就是这样，且与上传弹窗同一口径。
// The seeded title is the filename without its extension: that is what most uploads are titled,
// and it matches the upload dialog's rule.
export function titleFromFilename(name: string): string {
  return baseName(name).replace(/\.[A-Za-z0-9]+$/, "").slice(0, 160);
}

function entryId(seed: number, index: number): string {
  return `drop-${seed + index}`;
}

// 把一批拖入的文件转成条目：能传的进入 pending，不能传的当场标记失败并写明原因。
// 本地校验放在最前面，是为了让"体积超限/格式不允许"在几十兆字节被送出去之前就暴露出来。
// Turns a batch of dropped files into entries: uploadable ones start pending, rejected ones fail
// immediately with their reason. Local validation runs first so a size or format rejection
// surfaces before tens of megabytes are pushed over the wire.
export function planDropEntries(files: DropFileLike[], limits: DropLimits, seed: number): DropEntry[] {
  return files.map((file, index) => {
    const entry: DropEntry = {
      id: entryId(seed, index),
      name: baseName(file.name),
      sizeBytes: file.size,
      status: "pending",
      failure: "",
      detail: "",
      limitBytes: 0,
    };
    if (!limits.allowedExtensions.includes(extensionOf(file.name))) {
      return { ...entry, status: "failed" as DropEntryStatus, failure: "badExtension" as DropEntryFailure };
    }
    if (file.size > limits.maxUploadBytes) {
      return {
        ...entry,
        status: "failed" as DropEntryStatus,
        failure: "fileTooLarge" as DropEntryFailure,
        limitBytes: limits.maxUploadBytes,
      };
    }
    return entry;
  });
}

function detailOf(caught: unknown): string {
  if (caught instanceof Error && caught.message) return caught.message;
  return "";
}

// 逐个上传条目。逐个而不是并发，是为了让进度与失败原因与文件名一一对应，
// 也避免一次拖入十几份大文件时把后端与带宽同时打满。
// Uploads the entries one at a time. Sequential rather than concurrent so every progress line and
// every failure reason maps onto exactly one filename, and so dropping a dozen large files does
// not saturate the backend and the link at once.
//
// 失败绝不静默：任何异常都会被记到对应条目的 failed + detail 上并回调出去，
// 本函数自身不抛错，调用方拿到的一定是完整、可见的结果清单。
// A failure is never silent: any exception lands on that entry as failed plus its detail and is
// reported through onUpdate. This function itself never throws, so the caller always ends up with
// a complete, visible result list.
export async function runDropUploads(
  entries: DropEntry[],
  upload: (entry: DropEntry) => Promise<void>,
  onUpdate: (entries: DropEntry[]) => void,
): Promise<DropEntry[]> {
  let current = entries.map((entry) => ({ ...entry }));
  onUpdate(current.map((entry) => ({ ...entry })));
  for (let index = 0; index < current.length; index += 1) {
    if (current[index].status !== "pending") continue;
    current = current.map((entry, at) => (at === index ? { ...entry, status: "uploading" as DropEntryStatus } : entry));
    onUpdate(current.map((entry) => ({ ...entry })));
    try {
      await upload(current[index]);
      current = current.map((entry, at) => (at === index ? { ...entry, status: "success" as DropEntryStatus } : entry));
    } catch (caught) {
      current = current.map((entry, at) => (at === index
        ? { ...entry, status: "failed" as DropEntryStatus, failure: "upload" as DropEntryFailure, detail: detailOf(caught) }
        : entry));
    }
    onUpdate(current.map((entry) => ({ ...entry })));
  }
  return current;
}

// 结果汇总：页面据此决定"全部成功"时提示什么、"有失败"时要不要红色横幅。
// A summary of the outcome, so the page can decide between an all-succeeded notice and a failure
// banner.
export function summarizeDrop(entries: DropEntry[]): { succeeded: number; failed: number; busy: boolean } {
  let succeeded = 0;
  let failed = 0;
  let busy = false;
  for (const entry of entries) {
    if (entry.status === "success") succeeded += 1;
    else if (entry.status === "failed") failed += 1;
    else busy = true;
  }
  return { succeeded, failed, busy };
}
