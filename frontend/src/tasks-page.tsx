import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type TaskCategoryCount, type TaskItem, type TaskPriority, type TaskStatus } from "./lib/api";
import { useAuth } from "./lib/auth";
import { timeAgo, useI18n, type I18nKey } from "./lib/i18n";

type PriorityFilter = "" | TaskPriority;
type SortKey = "latest" | "oldest" | "urgent";
type Category = "All" | string;

const PRIORITY_KEYS: Record<TaskPriority, I18nKey> = { urgent: "task.urgent", normal: "task.normal" };
const PRESET_CATEGORIES = ["Frontend", "Backend", "Design", "Infra", "General"];

function CheckGlyph({ done }: { done: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {done ? <path d="m5 12 4.5 4.5L19 7" /> : null}
    </svg>
  );
}

export function TasksPage() {
  const { user, loading: authLoading } = useAuth();
  const { locale, t } = useI18n();
  const [items, setItems] = useState<TaskItem[]>([]);
  const [categories, setCategories] = useState<TaskCategoryCount[]>([]);
  const [canWrite, setCanWrite] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  const [category, setCategory] = useState<Category>("All");
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState<PriorityFilter>("");
  const [sort, setSort] = useState<SortKey>("urgent");
  const [scope, setScope] = useState<"all" | "mine">("all");

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<TaskItem | null>(null);
  const [form, setForm] = useState({ category: "General", title: "", notes: "", priority: "normal" as TaskPriority });
  const [formError, setFormError] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<TaskItem | null>(null);
  const [opError, setOpError] = useState("");

  const mountedRef = useRef(true);
  useEffect(() => () => {
    mountedRef.current = false;
  }, []);

  useEffect(() => {
    // 仅管理员拉取；未登录/非 admin 不发请求（页面另行渲染提示）
    if (authLoading) return;
    if (!user || user.role !== "admin") {
      setLoading(false);
      return;
    }
    let alive = true;
    api.tasks
      .list()
      .then((data) => {
        if (!alive) return;
        setItems(data.items);
        setCategories(data.categories);
        setCanWrite(data.canWrite);
        setLoadError("");
      })
      .catch(() => alive && setLoadError(t("task.loadFail")))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [user, authLoading]);

  const me = user?.id;

  const visible = useMemo(() => {
    const kw = query.trim().toLowerCase();
    const list = items.filter((t) => {
      if (category !== "All" && t.category !== category) return false;
      if (scope === "mine" && t.author.id !== me) return false;
      if (priority && t.priority !== priority) return false;
      if (kw && !t.title.toLowerCase().includes(kw) && !t.notes.toLowerCase().includes(kw)) return false;
      return true;
    });
    return [...list].sort((a, b) => {
      if (sort === "urgent") {
        if (a.priority !== b.priority) return a.priority === "urgent" ? -1 : 1;
        return b.createdAt - a.createdAt;
      }
      return sort === "oldest" ? a.createdAt - b.createdAt : b.createdAt - a.createdAt;
    });
  }, [items, category, scope, priority, query, sort, me]);

  const openItems = visible.filter((t) => t.status === "open");
  const doneItems = visible.filter((t) => t.status === "done");

  const stats = useMemo(() => {
    const open = items.filter((t) => t.status === "open").length;
    return {
      total: items.length,
      open,
      urgent: items.filter((t) => t.status === "open" && t.priority === "urgent").length,
      done: items.length - open,
    };
  }, [items]);

  const categoryOptions = useMemo(() => {
    const known = new Set(PRESET_CATEGORIES.concat(categories.map((c) => c.category)));
    return Array.from(known);
  }, [categories]);

  const switchCategory = (next: Category) => {
    setCategory(next);
    setQuery("");
    setPriority("");
    setScope("all");
    setSort(next === "All" ? "urgent" : sort);
  };

  const openCreate = (defaultCategory?: Category) => {
    setEditing(null);
    setForm({
      category: defaultCategory && defaultCategory !== "All" ? defaultCategory : "General",
      title: "",
      notes: "",
      priority: "normal",
    });
    setFormError("");
    setModalOpen(true);
  };

  const openEdit = (task: TaskItem) => {
    setEditing(task);
    setForm({ category: task.category, title: task.title, notes: task.notes, priority: task.priority });
    setFormError("");
    setModalOpen(true);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const title = form.title.trim();
    if (!title) {
      setFormError(t("task.titleRequired"));
      return;
    }
    const categoryValue = form.category.trim() || "General";
    try {
      if (editing) {
        await api.tasks.update(editing.id, { title, notes: form.notes, category: categoryValue, priority: form.priority });
      } else {
        await api.tasks.create({ title, notes: form.notes, category: categoryValue, priority: form.priority });
      }
      setModalOpen(false);
      const data = await api.tasks.list();
      if (!mountedRef.current) return;
      setItems(data.items);
      setCategories(data.categories);
      setCanWrite(data.canWrite);
    } catch (err) {
      if (mountedRef.current) setFormError(err instanceof ApiError ? err.message : t("task.saveFail"));
    }
  };

  const setStatus = async (task: TaskItem, status: TaskStatus) => {
    try {
      const updated = await api.tasks.setStatus(task.id, status);
      if (!mountedRef.current) return;
      setItems((current) => current.map((t) => (t.id === updated.id ? updated : t)));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("task.statusFail"));
    }
  };

  const deleteTask = async () => {
    if (!confirmDelete) return;
    try {
      await api.tasks.del(confirmDelete.id);
      if (!mountedRef.current) return;
      setItems((current) => current.filter((t) => t.id !== confirmDelete.id));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError(t("task.deleteFail"));
    }
    setConfirmDelete(null);
  };

  const renderRow = (task: TaskItem) => {
    const done = task.status === "done";
    const showCategoryTag = category === "All";
    return (
      <div className={`tasks-row ${done ? "is-done" : ""}`} key={task.id}>
        <button
          className={`task-toggle ${done ? "checked" : ""}`}
          type="button"
          aria-label={t(done ? "task.reopen" : "task.complete", { title: task.title })}
          title={t(done ? "task.markOpen" : "task.markDone")}
          disabled={!canWrite}
          onClick={() => void setStatus(task, done ? "open" : "done")}
        >
          <CheckGlyph done={done} />
        </button>
        <div className="tasks-row-main">
          <strong>{task.title}</strong>
          <div className="tasks-row-meta">
            {showCategoryTag && task.category !== "General" && <span className="task-tag task-tag-category">{task.category}</span>}
            {task.priority === "urgent" && <span className="task-tag task-tag-urgent">{t("task.urgent")}</span>}
            {done && <span className="task-tag task-tag-done">{t("task.done")}</span>}
            <span className="admin-muted">
              {t("task.by")} <b>{task.author.handle}</b> · {timeAgo(done ? task.doneAt ?? task.createdAt : task.createdAt, locale)}
            </span>
          </div>
          {task.notes ? <span className="admin-muted task-notes">{task.notes}</span> : null}
        </div>
        {canWrite && (
          <div className="admin-row-actions">
            <button className="admin-btn" type="button" onClick={() => openEdit(task)}>{t("task.edit")}</button>
            <AlertDialog.Root open={confirmDelete?.id === task.id} onOpenChange={(open) => !open && setConfirmDelete(null)}>
              <AlertDialog.Trigger asChild>
                <button className="admin-btn danger" type="button" onClick={() => setConfirmDelete(task)}>{t("task.delete")}</button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="dialog-overlay" />
                <AlertDialog.Content className="dialog-content">
                  <AlertDialog.Title className="dialog-title">{t("task.deleteTitle")}</AlertDialog.Title>
                  <AlertDialog.Description className="dialog-description">{t("task.deleteDesc", { title: task.title })}</AlertDialog.Description>
                  <div className="dialog-actions">
                    <AlertDialog.Cancel asChild>
                      <button type="button" className="action-btn">{t("task.cancel")}</button>
                    </AlertDialog.Cancel>
                    <AlertDialog.Action asChild>
                      <button type="button" className="dialog-danger" onClick={() => void deleteTask()}>{t("task.delete")}</button>
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          </div>
        )}
      </div>
    );
  };

  const sidebarCategories = useMemo(() => {
    const counts = new Map(categories.map((c) => [c.category, c.open]));
    const all = new Set(categories.map((c) => c.category));
    for (const t of items) if (t.status === "open") all.add(t.category);
    return Array.from(all)
      .map((name) => ({ category: name, open: counts.get(name) ?? 0 }))
      .sort((a, b) => b.open - a.open || a.category.localeCompare(b.category));
  }, [categories, items]);

  if (authLoading) {
    return <AppShell current="tasks"><main className="shell tasks-layout"><Loading /></main></AppShell>;
  }
  if (!user || user.role !== "admin") {
    return (
      <AppShell current="tasks">
        <main className="shell tasks-layout">
          <section className="tasks-main">
            <div className="empty-state content-fade">
              {!user ? (
                <> {t("task.signInRequired")} <a className="sender" href="/login">{t("task.signIn")}</a></>
              ) : (
                t("task.adminOnly")
              )}
            </div>
          </section>
        </main>
      </AppShell>
    );
  }

  return (
    <AppShell current="tasks">
      <main className="shell tasks-layout">
        <aside className="tasks-sidebar">
          <div className="tasks-sidebar-head">
            <h1>{t("task.tasks")}</h1>
            <p>{t("task.subtitle")}</p>
          </div>
          {canWrite ? (
            <button className="primary-action tasks-new" type="button" onClick={() => openCreate(category)}>{t("task.newTask")}</button>
          ) : (
            <a className="primary-action tasks-new" href="/login">{t("task.signInToAdd")}</a>
          )}
          <nav className="tasks-groups" aria-label={t("task.groups")}>
            <button className={`tasks-group ${category === "All" ? "active" : ""}`} type="button" aria-current={category === "All" ? "true" : undefined} onClick={() => switchCategory("All")}>
              <span>{t("task.all")}</span>
              <small>{stats.open}</small>
            </button>
            {sidebarCategories.map((g) => (
              <button
                className={`tasks-group ${category === g.category ? "active" : ""}`}
                type="button"
                key={g.category}
                aria-current={category === g.category ? "true" : undefined}
                onClick={() => switchCategory(g.category)}
              >
                <span>{g.category}</span>
                <small>{g.open}</small>
              </button>
            ))}
          </nav>
        </aside>

        <section className="tasks-main">
          {loading ? (
            <Loading />
          ) : loadError ? (
            <div className="empty-state">{loadError}</div>
          ) : (
            <>
              <div className="admin-stat-grid">
                <div className="admin-stat"><strong>{stats.total}</strong><span>{t("task.total")}</span></div>
                <div className="admin-stat"><strong>{stats.open}</strong><span>{t("task.openStatus")}</span></div>
                <div className="admin-stat"><strong>{stats.urgent}</strong><span>{t("task.urgent")}</span></div>
                <div className="admin-stat"><strong>{stats.done}</strong><span>{t("task.done")}</span></div>
              </div>

              <div className="admin-filters">
                <label className="admin-search">
                  <span className="sr-only">{t("task.searchTasks")}</span>
                  <input type="search" placeholder={t("task.searchPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} />
                </label>
                <SDropdown
                  items={["", "urgent", "normal"] as PriorityFilter[]}
                  value={priority}
                  onChange={setPriority}
                  getKey={(item) => item || "all-priority"}
                  getLabel={(item) => (item ? t(PRIORITY_KEYS[item]) : t("task.allPriority"))}
                  ariaLabel={t("task.filterPriority")}
                  className="admin-dropdown"
                />
                <SDropdown
                  items={["urgent", "latest", "oldest"] as SortKey[]}
                  value={sort}
                  onChange={setSort}
                  getKey={(item) => item}
                  getLabel={(item) => t(item === "urgent" ? "task.urgentFirst" : item === "latest" ? "task.latestFirst" : "task.oldestFirst")}
                  ariaLabel={t("task.sort")}
                  className="admin-dropdown"
                />
                <div className="admin-pills">
                  <button className={`admin-pill ${scope === "all" ? "active" : ""}`} type="button" onClick={() => setScope("all")}>{t("task.all")}</button>
                  <button className={`admin-pill ${scope === "mine" ? "active" : ""}`} type="button" disabled={!canWrite} title={canWrite ? undefined : t("task.signInToFilter")} onClick={() => setScope("mine")}>{t("task.mine")}</button>
                </div>
              </div>

              <div className="admin-list">
                {opError && <p className="notice" role="alert">{opError}</p>}
                {visible.length === 0 ? (
                  <div className="empty-state">
                    {items.length === 0
                      ? (canWrite ? t("task.noTasksWrite") : t("task.noTasks"))
                      : t("task.noMatch")}
                  </div>
                ) : (
                  <>
                    {openItems.length === 0 && doneItems.length > 0 ? (
                      <div className="empty-state">{t("task.allDone")}</div>
                    ) : (
                      openItems.map(renderRow)
                    )}
                  </>
                )}
              </div>

              {doneItems.length > 0 && (
                <details className="tasks-done">
                  <summary>{t("task.completed", { count: doneItems.length })}</summary>
                  <div className="admin-list">{doneItems.map(renderRow)}</div>
                </details>
              )}
            </>
          )}
        </section>
      </main>

      <Dialog.Root open={modalOpen} onOpenChange={setModalOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content tasks-modal" aria-label={editing ? t("task.editTask") : t("task.newTaskTitle")}>
            <Dialog.Title className="dialog-title">{editing ? t("task.editTask") : t("task.newTaskTitle")}</Dialog.Title>
            <form onSubmit={submit}>
              <label className="form-field">
                <span>{t("task.title")}</span>
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={120} placeholder={t("task.titlePlaceholder")} />
              </label>
              <label className="form-field">
                <span>{t("task.group")}</span>
                <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={40} list="task-categories" placeholder={t("task.groupPlaceholder")} />
                <datalist id="task-categories">
                  {categoryOptions.map((c) => <option value={c} key={c} />)}
                </datalist>
              </label>
              <SDropdown
                items={["urgent", "normal"] as TaskPriority[]}
                value={form.priority}
                onChange={(value) => setForm({ ...form, priority: value })}
                getKey={(item) => item}
                getLabel={(item) => t(PRIORITY_KEYS[item])}
                label={t("task.priority")}
                ariaLabel={t("task.taskPriority")}
                className="form-dropdown"
              />
              <label className="form-field">
                <span>{t("task.notes")}</span>
                <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength={5000} rows={4} placeholder={t("task.notesPlaceholder")} />
              </label>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>{t("task.cancel")}</button>
                <button type="submit" className="primary-action">{t("task.save")}</button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </AppShell>
  );
}
