import { AuthProvider } from "./lib/auth";
import { TasksPage } from "./tasks-page";

export function TasksStandalone() {
  return (
    <AuthProvider>
      <TasksPage />
    </AuthProvider>
  );
}
