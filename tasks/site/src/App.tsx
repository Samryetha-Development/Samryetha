import { useEffect, useLayoutEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { LakoDialog, LakoDropdown, LakoInputBox, LakoNotifications, type LakoNotificationItem } from "@lako/ui";
import { api, ApiError, forumOrigin, type TaskItem, type TaskPriority, type TaskStatus } from "./api";
import { taskLocaleLabels, taskLocales, useTranslations } from "./i18n";

type SortKey = "urgent" | "latest" | "oldest";
type FormState = { category: string; title: string; notes: string; priority: TaskPriority };
const blankForm: FormState = { category: "General", title: "", notes: "", priority: "normal" };

function age(timestamp: number, t: (key: string, vars?: Record<string, string | number>) => string) {
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return t("task.now");
  if (seconds < 3600) return t("task.minutes", { count: Math.floor(seconds / 60) });
  if (seconds < 86400) return t("task.hours", { count: Math.floor(seconds / 3600) });
  return t("task.days", { count: Math.floor(seconds / 86400) });
}

export function App() {
  const { t, locale, setLocale } = useTranslations();
  const [items, setItems] = useState<TaskItem[]>([]);
  const [canWrite, setCanWrite] = useState(false);
  const [me, setMe] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [category, setCategory] = useState("All");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<"all" | TaskPriority>("all");
  const [sort, setSort] = useState<SortKey>("urgent");
  const [mine, setMine] = useState(false);
  const [editing, setEditing] = useState<TaskItem | null>(null);
  const [form, setForm] = useState<FormState>(blankForm);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<TaskItem | null>(null);
  const [notifications, setNotifications] = useState<LakoNotificationItem[]>([]);
  const groupNavRef = useRef<HTMLElement>(null);
  const [groupIndicator, setGroupIndicator] = useState({ y: 0, height: 0, ready: false, animate: false });

  const notify = (message: string, tone: LakoNotificationItem["tone"] = "info") => {
    const id = `${Date.now()}-${Math.random()}`;
    setNotifications((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setNotifications((current) => current.filter((item) => item.id !== id)), 3200);
  };

  const reload = async () => {
    const data = await api.list();
    setItems(data.items);
    setCanWrite(data.canWrite);
    if (data.canWrite) api.me().then((result) => setMe(result.user.id)).catch(() => setMe(null));
  };

  useEffect(() => {
    reload().catch(() => setError(t("task.loadFail"))).finally(() => setLoading(false));
  }, []);

  const categories = useMemo(() => {
    const counts = new Map<string, number>();
    for (const task of items) if (task.status === "open") counts.set(task.category, (counts.get(task.category) ?? 0) + 1);
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [items]);
  const categoryOptions = useMemo(() => {
    const names = new Set(["General", ...categories.map(([name]) => name)]);
    if (form.category) names.add(form.category);
    return [...names];
  }, [categories, form.category]);

  useLayoutEffect(() => {
    const nav = groupNavRef.current;
    const active = nav?.querySelector<HTMLElement>("button.active");
    if (!nav || !active) return;
    const measure = () => setGroupIndicator((current) => ({ y: active.offsetTop, height: active.offsetHeight, ready: true, animate: current.ready }));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(nav);
    observer.observe(active);
    return () => observer.disconnect();
  }, [category, categories]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((task) => {
      if (category !== "All" && task.category !== category) return false;
      if (priority !== "all" && task.priority !== priority) return false;
      if (mine && task.author.id !== me) return false;
      return !needle || task.title.toLowerCase().includes(needle) || task.notes.toLowerCase().includes(needle);
    }).sort((a, b) => {
      if (sort === "urgent" && a.priority !== b.priority) return a.priority === "urgent" ? -1 : 1;
      return sort === "oldest" ? a.createdAt - b.createdAt : b.createdAt - a.createdAt;
    });
  }, [category, items, me, mine, priority, query, sort]);

  const open = visible.filter((task) => task.status === "open");
  const done = visible.filter((task) => task.status === "done");
  const stats = {
    total: items.length,
    open: items.filter((task) => task.status === "open").length,
    urgent: items.filter((task) => task.status === "open" && task.priority === "urgent").length,
    done: items.filter((task) => task.status === "done").length,
  };

  const beginCreate = () => {
    setEditing(null);
    setForm({ ...blankForm, category: category === "All" ? "General" : category });
    setError("");
    setDialogOpen(true);
  };

  const beginEdit = (task: TaskItem) => {
    setEditing(task);
    setForm({ category: task.category, title: task.title, notes: task.notes, priority: task.priority });
    setError("");
    setDialogOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!form.title.trim()) return setError(t("task.titleRequired"));
    setSaving(true);
    setError("");
    try {
      const body = { ...form, title: form.title.trim(), category: form.category.trim() || "General" };
      if (editing) await api.update(editing.id, body); else await api.create(body);
      await reload();
      setDialogOpen(false);
      notify(t("task.saved"), "success");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : t("task.saveFail"));
    } finally {
      setSaving(false);
    }
  };

  const setStatus = async (task: TaskItem, status: TaskStatus) => {
    try {
      const updated = await api.setStatus(task.id, status);
      setItems((current) => current.map((item) => item.id === updated.id ? updated : item));
      notify(t("task.statusUpdated"), "success");
    } catch { notify(t("task.statusFail"), "error"); }
  };

  const remove = async () => {
    if (!deleteTarget) return;
    try {
      await api.delete(deleteTarget.id);
      setItems((current) => current.filter((item) => item.id !== deleteTarget.id));
      setDeleteTarget(null);
      notify(t("task.deleted"), "success");
    } catch { notify(t("task.deleteFail"), "error"); }
  };

  const signIn = `${forumOrigin}/login?returnTo=${encodeURIComponent(window.location.origin + "/")}`;

  const row = (task: TaskItem) => (
    <article className={`task-row${task.status === "done" ? " done" : ""}`} key={task.id}>
      <button className={`check${task.status === "done" ? " checked" : ""}`} type="button" disabled={!canWrite}
        title={t(task.status === "done" ? "task.markOpen" : "task.markDone")}
        onClick={() => void setStatus(task, task.status === "done" ? "open" : "done")} aria-label={task.title}>
        {task.status === "done" ? "✓" : ""}
      </button>
      <div className="task-copy">
        <strong>{task.title}</strong>
        <div className="meta">
          {category === "All" && task.category !== "General" && <span className="tag category">{task.category}</span>}
          {task.priority === "urgent" && <span className="tag urgent">{t("task.urgent")}</span>}
          {task.status === "done" && <span className="tag complete">{t("task.done")}</span>}
          <span>{t("task.by")} <b>{task.author.handle}</b> · {age(task.doneAt ?? task.createdAt, t)}</span>
        </div>
        {task.notes && <p>{task.notes}</p>}
      </div>
      {canWrite && <div className="row-actions">
        <button type="button" onClick={() => beginEdit(task)}>{t("task.edit")}</button>
        <button className="danger" type="button" onClick={() => setDeleteTarget(task)}>{t("task.delete")}</button>
      </div>}
    </article>
  );

  return <>
    <header className="site-header">
      <a className="brand" href="/">Samryetha <span>{t("task.tasks")}</span></a>
      <div className="header-actions">
        <LakoDropdown items={[...taskLocales]} value={locale} onChange={setLocale} getKey={(item) => item} getLabel={(item) => taskLocaleLabels[item]} ariaLabel={t("task.language")} className="language-dropdown" />
        <a href={forumOrigin}>{t("task.forum")}</a>
      </div>
    </header>
    <main className="layout">
      <aside className="sidebar">
        <div><h1>{t("task.tasks")}</h1><p>{t("task.subtitle")}</p></div>
        {canWrite ? <button className="primary" type="button" onClick={beginCreate}>{t("task.newTask")}</button>
          : <a className="primary" href={signIn}>{t("task.signInToAdd")}</a>}
        <nav aria-label={t("task.groups")} ref={groupNavRef}>
          <span className={`group-indicator${groupIndicator.ready ? " ready" : ""}${groupIndicator.animate ? " animate" : ""}`} style={{ height: groupIndicator.height, transform: `translateY(${groupIndicator.y}px)` }} aria-hidden="true" />
          <button className={category === "All" ? "active" : ""} type="button" onClick={() => setCategory("All")}><span>{t("task.all")}</span><small>{stats.open}</small></button>
          {categories.map(([name, count]) => <button className={category === name ? "active" : ""} type="button" key={name} onClick={() => setCategory(name)}><span>{name}</span><small>{count}</small></button>)}
        </nav>
      </aside>
      <section className="content">
        <div className="stats">
          {[[stats.total, "task.total"], [stats.open, "task.openStatus"], [stats.urgent, "task.urgent"], [stats.done, "task.done"]].map(([value, key]) => <div key={key}><strong>{value}</strong><span>{t(String(key))}</span></div>)}
        </div>
        <div className="filters">
          <input type="search" placeholder={t("task.searchPlaceholder")} value={query} onChange={(event) => setQuery(event.target.value)} />
          <LakoDropdown items={["all", "urgent", "normal"] as const} value={priority} onChange={setPriority} getKey={(item) => item} getLabel={(item) => t(item === "all" ? "task.allPriority" : `task.${item}`)} ariaLabel={t("task.priority")} />
          <LakoDropdown items={["urgent", "latest", "oldest"] as const} value={sort} onChange={setSort} getKey={(item) => item} getLabel={(item) => t(`task.${item}First`)} ariaLabel={t("task.sort")} />
          <button className={`mine${mine ? " active" : ""}`} type="button" disabled={!canWrite} onClick={() => setMine((value) => !value)}>{t("task.mine")}</button>
        </div>
        {error && !dialogOpen && <p className="error" role="alert">{error}</p>}
        {loading ? <p className="empty">{t("task.loading")}</p> : visible.length === 0 ? <p className="empty">{items.length ? t("task.noMatch") : t(canWrite ? "task.noTasksWrite" : "task.noTasks")}</p> : <>
          <div>{open.length ? open.map(row) : <p className="empty">{t("task.allDone")}</p>}</div>
          {done.length > 0 && <details><summary>{t("task.completed", { count: done.length })}</summary>{done.map(row)}</details>}
        </>}
      </section>
    </main>

    <LakoDialog
      open={dialogOpen}
      onOpenChange={setDialogOpen}
      title={t(editing ? "task.editTask" : "task.newTaskTitle")}
      className="task-dialog"
      actions={<>
        <button className="lako-dialog-button" type="button" onClick={() => setDialogOpen(false)}>{t("task.cancel")}</button>
        <button className="lako-dialog-button primary" type="submit" form="task-editor" disabled={saving}>{t("task.save")}</button>
      </>}
    >
      <form id="task-editor" className="task-editor" onSubmit={submit}>
        <LakoInputBox
          label={t("task.title")}
          value={form.title}
          maxLength={120}
          placeholder={t("task.titlePlaceholder")}
          error={error === t("task.titleRequired") ? error : undefined}
          onChange={(event) => setForm({ ...form, title: event.target.value })}
        />
        <label className="lako-ui-input-box">
          <span className="lako-ui-input-label">{t("task.group")}</span>
          <LakoDropdown items={categoryOptions} value={form.category} onChange={(value) => setForm({ ...form, category: value })} getKey={(item) => item} getLabel={(item) => item} ariaLabel={t("task.group")} searchable searchPlaceholder={t("task.searchGroups")} emptyLabel={t("task.noGroups")} />
        </label>
        <label className="lako-ui-input-box">
          <span className="lako-ui-input-label">{t("task.priority")}</span>
          <LakoDropdown items={["urgent", "normal"] as const} value={form.priority} onChange={(value) => setForm({ ...form, priority: value })} getKey={(item) => item} getLabel={(item) => t(`task.${item}`)} ariaLabel={t("task.priority")} />
        </label>
        <label className="lako-ui-input-box task-notes-field">
          <span className="lako-ui-input-label">{t("task.notes")}</span>
          <textarea rows={4} maxLength={5000} value={form.notes} placeholder={t("task.notesPlaceholder")} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
        </label>
        {error && error !== t("task.titleRequired") && <p className="error" role="alert">{error}</p>}
      </form>
    </LakoDialog>
    <LakoDialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)} title={t("task.deleteTitle")} description={deleteTarget ? t("task.deleteDesc", { title: deleteTarget.title }) : ""}
      actions={<><button className="lako-dialog-button" type="button" onClick={() => setDeleteTarget(null)}>{t("task.cancel")}</button><button className="lako-dialog-button danger" type="button" onClick={() => void remove()}>{t("task.delete")}</button></>} />
    <LakoNotifications items={notifications} ariaLabel={t("task.notifications")} />
  </>;
}
