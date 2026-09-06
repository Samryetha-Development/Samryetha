import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { AppShell } from "./app-shell";
import { Loading } from "./loading";
import { SDropdown } from "./s-dropdown";
import { api, ApiError, type TaskCategoryCount, type TaskItem, type TaskPriority, type TaskStatus } from "./lib/api";
import { useAuth } from "./lib/auth";
import { formatTime } from "./lib/format";

type PriorityFilter = "" | TaskPriority;
type SortKey = "latest" | "oldest" | "urgent";
type Category = "All" | string;

const PRIORITY_LABEL: Record<TaskPriority, string> = { urgent: "Urgent", normal: "Normal" };
const PRESET_CATEGORIES = ["Frontend", "Backend", "Design", "Infra", "General"];

function CheckGlyph({ done }: { done: boolean }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      {done ? <path d="m5 12 4.5 4.5L19 7" /> : null}
    </svg>
  );
}

export function TasksPage() {
  const { user } = useAuth();
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
      .catch(() => alive && setLoadError("Failed to load tasks."))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

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
      setFormError("Title is required.");
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
      if (mountedRef.current) setFormError(err instanceof ApiError ? err.message : "Failed to save task.");
    }
  };

  const setStatus = async (task: TaskItem, status: TaskStatus) => {
    try {
      const updated = await api.tasks.setStatus(task.id, status);
      if (!mountedRef.current) return;
      setItems((current) => current.map((t) => (t.id === updated.id ? updated : t)));
      setOpError("");
    } catch {
      if (mountedRef.current) setOpError("Failed to update task.");
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
      if (mountedRef.current) setOpError("Failed to delete task.");
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
          aria-label={done ? `Reopen ${task.title}` : `Complete ${task.title}`}
          title={done ? "Mark open" : "Mark done"}
          disabled={!canWrite}
          onClick={() => void setStatus(task, done ? "open" : "done")}
        >
          <CheckGlyph done={done} />
        </button>
        <div className="tasks-row-main">
          <strong>{task.title}</strong>
          <div className="tasks-row-meta">
            {showCategoryTag && task.category !== "General" && <span className="task-tag task-tag-category">{task.category}</span>}
            {task.priority === "urgent" && <span className="task-tag task-tag-urgent">Urgent</span>}
            {done && <span className="task-tag task-tag-done">Done</span>}
            <span className="admin-muted">
              by <b>{task.author.handle}</b> · {formatTime(done ? task.doneAt ?? task.createdAt : task.createdAt)}
            </span>
          </div>
          {task.notes ? <span className="admin-muted task-notes">{task.notes}</span> : null}
        </div>
        {canWrite && (
          <div className="admin-row-actions">
            <button className="admin-btn" type="button" onClick={() => openEdit(task)}>Edit</button>
            <AlertDialog.Root open={confirmDelete?.id === task.id} onOpenChange={(open) => !open && setConfirmDelete(null)}>
              <AlertDialog.Trigger asChild>
                <button className="admin-btn danger" type="button" onClick={() => setConfirmDelete(task)}>Delete</button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="dialog-overlay" />
                <AlertDialog.Content className="dialog-content">
                  <AlertDialog.Title className="dialog-title">Delete this task?</AlertDialog.Title>
                  <AlertDialog.Description className="dialog-description">This permanently removes “{task.title}”. You can’t undo this.</AlertDialog.Description>
                  <div className="dialog-actions">
                    <AlertDialog.Cancel asChild>
                      <button type="button" className="action-btn">Cancel</button>
                    </AlertDialog.Cancel>
                    <AlertDialog.Action asChild>
                      <button type="button" className="dialog-danger" onClick={() => void deleteTask()}>Delete</button>
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

  return (
    <AppShell current="tasks">
      <main className="shell tasks-layout">
        <aside className="tasks-sidebar">
          <div className="tasks-sidebar-head">
            <h1>Tasks</h1>
            <p>What’s next for the build.</p>
          </div>
          {canWrite ? (
            <button className="primary-action tasks-new" type="button" onClick={() => openCreate(category)}>New task</button>
          ) : (
            <a className="primary-action tasks-new" href="/login">Sign in to add</a>
          )}
          <nav className="tasks-groups" aria-label="Task groups">
            <button className={`tasks-group ${category === "All" ? "active" : ""}`} type="button" aria-current={category === "All" ? "true" : undefined} onClick={() => switchCategory("All")}>
              <span>All</span>
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
                <div className="admin-stat"><strong>{stats.total}</strong><span>Total</span></div>
                <div className="admin-stat"><strong>{stats.open}</strong><span>Open</span></div>
                <div className="admin-stat"><strong>{stats.urgent}</strong><span>Urgent</span></div>
                <div className="admin-stat"><strong>{stats.done}</strong><span>Done</span></div>
              </div>

              <div className="admin-filters">
                <label className="admin-search">
                  <span className="sr-only">Search tasks</span>
                  <input type="search" placeholder="Search tasks…" value={query} onChange={(e) => setQuery(e.target.value)} />
                </label>
                <SDropdown
                  items={["", "urgent", "normal"] as PriorityFilter[]}
                  value={priority}
                  onChange={setPriority}
                  getKey={(item) => item || "all-priority"}
                  getLabel={(item) => (item ? PRIORITY_LABEL[item] : "All priority")}
                  ariaLabel="Filter by priority"
                  className="admin-dropdown"
                />
                <SDropdown
                  items={["urgent", "latest", "oldest"] as SortKey[]}
                  value={sort}
                  onChange={setSort}
                  getKey={(item) => item}
                  getLabel={(item) => ({ urgent: "Urgent first", latest: "Latest first", oldest: "Oldest first" })[item]}
                  ariaLabel="Sort tasks"
                  className="admin-dropdown"
                />
                <div className="admin-pills">
                  <button className={`admin-pill ${scope === "all" ? "active" : ""}`} type="button" onClick={() => setScope("all")}>All</button>
                  <button className={`admin-pill ${scope === "mine" ? "active" : ""}`} type="button" disabled={!canWrite} title={canWrite ? undefined : "Sign in to filter by you"} onClick={() => setScope("mine")}>Mine</button>
                </div>
              </div>

              <div className="admin-list">
                {opError && <p className="notice" role="alert">{opError}</p>}
                {visible.length === 0 ? (
                  <div className="empty-state">
                    {items.length === 0
                      ? (canWrite ? "No tasks yet — add the next useful thing." : "No tasks yet.")
                      : "No tasks match these filters."}
                  </div>
                ) : (
                  <>
                    {openItems.length === 0 && doneItems.length > 0 ? (
                      <div className="empty-state">Nothing open here — all done.</div>
                    ) : (
                      openItems.map(renderRow)
                    )}
                  </>
                )}
              </div>

              {doneItems.length > 0 && (
                <details className="tasks-done">
                  <summary>Completed ({doneItems.length})</summary>
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
          <Dialog.Content className="dialog-content tasks-modal" aria-label={editing ? "Edit task" : "New task"}>
            <Dialog.Title className="dialog-title">{editing ? "Edit task" : "New task"}</Dialog.Title>
            <form onSubmit={submit}>
              <label className="form-field">
                <span>Title</span>
                <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={120} placeholder="What needs doing?" />
              </label>
              <label className="form-field">
                <span>Group</span>
                <input value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} maxLength={40} list="task-categories" placeholder="General" />
                <datalist id="task-categories">
                  {categoryOptions.map((c) => <option value={c} key={c} />)}
                </datalist>
              </label>
              <SDropdown
                items={["urgent", "normal"] as TaskPriority[]}
                value={form.priority}
                onChange={(value) => setForm({ ...form, priority: value })}
                getKey={(item) => item}
                getLabel={(item) => PRIORITY_LABEL[item]}
                label="Priority"
                ariaLabel="Task priority"
                className="form-dropdown"
              />
              <label className="form-field">
                <span>Notes</span>
                <textarea value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} maxLength={5000} rows={4} placeholder="Context, scope, links…" />
              </label>
              {formError && <div className="dialog-error">{formError}</div>}
              <div className="dialog-actions">
                <button type="button" className="action-btn" onClick={() => setModalOpen(false)}>Cancel</button>
                <button type="submit" className="primary-action">Save</button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </AppShell>
  );
}
