import { useCallback, useState } from "react";
import { en } from "../../../frontend/src/lib/locales/en";
import { zhCN } from "../../../frontend/src/lib/locales/zh-CN";
import { zhTW } from "../../../frontend/src/lib/locales/zh-TW";
import { ja } from "../../../frontend/src/lib/locales/ja";
import { ko } from "../../../frontend/src/lib/locales/ko";
import { es } from "../../../frontend/src/lib/locales/es";
import { fr } from "../../../frontend/src/lib/locales/fr";
import { de } from "../../../frontend/src/lib/locales/de";

type Catalog = Record<string, string>;

const fallback: Catalog = {
  "task.tasks": "Tasks", "task.subtitle": "What’s next for the build.", "task.newTask": "New task",
  "task.signInToAdd": "Sign in to add", "task.groups": "Task groups", "task.all": "All", "task.total": "Total",
  "task.openStatus": "Open", "task.urgent": "Urgent", "task.normal": "Normal", "task.done": "Done", "task.mine": "Mine",
  "task.searchPlaceholder": "Search tasks…", "task.allPriority": "All priority", "task.urgentFirst": "Urgent first",
  "task.latestFirst": "Latest first", "task.oldestFirst": "Oldest first", "task.noTasks": "No tasks yet.",
  "task.noTasksWrite": "No tasks yet — add the next useful thing.", "task.noMatch": "No tasks match these filters.",
  "task.allDone": "Nothing open here — all done.", "task.completed": "Completed ({count})", "task.by": "by",
  "task.edit": "Edit", "task.delete": "Delete", "task.deleteTitle": "Delete this task?",
  "task.deleteDesc": "This permanently removes “{title}”. You can’t undo this.", "task.cancel": "Cancel", "task.save": "Save",
  "task.editTask": "Edit task", "task.newTaskTitle": "New task", "task.title": "Title",
  "task.titlePlaceholder": "What needs doing?", "task.group": "Group", "task.groupPlaceholder": "General",
  "task.priority": "Priority", "task.notes": "Notes", "task.notesPlaceholder": "Context, scope, links…",
  "task.loadFail": "Failed to load tasks.", "task.titleRequired": "Title is required.", "task.saveFail": "Failed to save task.",
  "task.statusFail": "Failed to update task.", "task.deleteFail": "Failed to delete task.", "task.markOpen": "Mark open",
  "task.markDone": "Mark done", "task.reopen": "Reopen {title}", "task.complete": "Complete {title}",
  "task.loading": "Loading…", "task.saved": "Task saved.", "task.deleted": "Task deleted.",
  "task.statusUpdated": "Task status updated.", "task.language": "Language", "task.forum": "Forum", "task.sort": "Sort",
  "task.notifications": "Notifications", "task.searchGroups": "Search groups…",
  "task.noGroups": "No groups found.", "task.now": "now", "task.minutes": "{count}m", "task.hours": "{count}h", "task.days": "{count}d",
};

export const taskLocales = ["en", "zh-CN", "zh-TW", "ja", "ko", "es", "fr", "de"] as const;
export type TaskLocale = typeof taskLocales[number];
const catalogs: Record<TaskLocale, Catalog> = { en, "zh-CN": zhCN, "zh-TW": zhTW, ja, ko, es, fr, de };
const supported = new Set<string>(taskLocales);
export const taskLocaleLabels: Record<TaskLocale, string> = {
  en: "English", "zh-CN": "简体中文", "zh-TW": "繁體中文", ja: "日本語", ko: "한국어", es: "Español", fr: "Français", de: "Deutsch",
};

function normalizeLocale(language: string): TaskLocale | null {
  if (language === "zh-Hans") return "zh-CN";
  if (language === "zh-Hant") return "zh-TW";
  if (supported.has(language)) return language as TaskLocale;
  const base = language.split("-")[0];
  if (base === "zh") return "zh-CN";
  return supported.has(base) ? base as TaskLocale : null;
}

function detectLocale(): TaskLocale {
  const stored = window.localStorage.getItem("samryetha-tasks-locale");
  if (stored) {
    const normalized = normalizeLocale(stored);
    if (normalized) return normalized;
  }
  for (const language of navigator.languages) {
    const normalized = normalizeLocale(language);
    if (normalized) return normalized;
  }
  return "en";
}

export function useTranslations() {
  const [locale, setLocaleState] = useState<TaskLocale>(detectLocale);
  const setLocale = useCallback((next: TaskLocale) => {
    window.localStorage.setItem("samryetha-tasks-locale", next);
    setLocaleState(next);
  }, []);
  const t = useCallback((key: string, vars: Record<string, string | number> = {}) => {
    let value = catalogs[locale][key] ?? fallback[key] ?? key;
    for (const [name, replacement] of Object.entries(vars)) value = value.replaceAll(`{${name}}`, String(replacement));
    return value;
  }, [locale]);
  return { t, locale, setLocale };
}
