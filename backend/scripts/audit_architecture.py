"""Check application-service boundaries and import each module in a fresh process.

Run from backend/: uv run python scripts/audit_architecture.py
This complements (rather than replaces) the API/concurrency test suite.
"""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1] / "src" / "samryetha"
APPLICATION_MODULES = {
    "auth/oidc.py",
    "auth/qr_login.py",
    "auth/sessions.py",
    "users/follows.py",
    "feedback/backup.py",
    "events/content_events.py",
    "events/outbox_worker.py",
}
INFRASTRUCTURE_SQL = {"core/db.py", "core/schema.py", "events/outbox.py", "system/health_router.py"}
PUBLIC_FUNCTIONS = {
    "files/service.py": {"normalize_tags", "build_object_url", "verify_upload_signature"},
    "attachments/service.py": {"to_attachment"},
    "auth/oidc.py": {"safe_return_to", "resolve_return_to"},
    "auth/qr_login.py": {"generateQRCodeByURL", "requires_email_confirmation", "mask_email"},
    "authz/service.py": {"is_active", "is_global_mod"},
    "discussions/service.py": {"preview", "to_author", "assert_content_visible"},
    "events/outbox_worker.py": {"register_outbox_handlers"},
    "feedback/backup.py": {"apply_pending_restore"},
    "feedback/service.py": {"generate_agent_key", "agent_can_access_project"},
    "moderation/service.py": {"preview_text"},
    "notifications/service.py": {"item_to_response_data"},
    "search/service.py": {"escape_like"},
    "users/service.py": {"normalize_username", "make_handle", "to_dto"},
}
REQUIRED_CLASSES = {
    "files/service.py": {"FileService"},
    "admin/service.py": {"AdminService"},
    "attachments/service.py": {"AttachmentService", "AttachmentUploadService"},
    "auth/service.py": {"AuthService"},
    "auth/sessions.py": {"SessionService"},
    "auth/oidc.py": {"OidcService", "OidcLoginService"},
    "auth/qr_login.py": {"QrAuthService"},
    "authz/service.py": {"AuthorizationService"},
    "boards/service.py": {"BoardService"},
    "discussions/service.py": {"DiscussionService"},
    "drafts/service.py": {"DraftService"},
    "events/content_events.py": {"ContentEventService"},
    "events/outbox_worker.py": {"OutboxEventService", "OutboxDeliveryService"},
    "feedback/service.py": {"FeedbackService"},
    "feedback/backup.py": {"BackupService", "BackupSettingsService"},
    "messages/service.py": {"MessageService"},
    "moderation/service.py": {"ModerationService"},
    "notifications/service.py": {"NotificationService"},
    "search/service.py": {"SearchService"},
    "tasks/service.py": {"TaskService"},
    "users/service.py": {"UserService"},
    "users/follows.py": {"FollowService"},
}
REPOSITORY_CLASSES = {
    "files/repository.py": {"FileRepository"},
    "admin/repository.py": {"AdminRepository"},
    "attachments/repository.py": {"AttachmentRepository"},
    "auth/repository.py": {"AuthRepository"},
    "auth/oidc_repository.py": {"OidcRepository"},
    "auth/qr_repository.py": {"QrAuthRepository"},
    "authz/repository.py": {"AuthorizationRepository"},
    "boards/repository.py": {"BoardRepository"},
    "discussions/repository.py": {"DiscussionRepository"},
    "drafts/repository.py": {"DraftRepository"},
    "events/repository.py": {"EventRepository"},
    "events/outbox.py": {"OutboxWriter"},
    "feedback/repository.py": {"FeedbackRepository", "DatabaseSnapshotRepository"},
    "messages/repository.py": {"MessageRepository"},
    "moderation/repository.py": {"ModerationRepository"},
    "notifications/repository.py": {"NotificationRepository"},
    "search/repository.py": {"SearchRepository"},
    "tasks/repository.py": {"TaskRepository"},
    "users/repository.py": {"UserRepository"},
}


# 导出完备性在独立解释器里校验：`assert` 会被 `python -O` 整体剥离，会让这条检查静默失效，
# 因此改用显式 SystemExit 携带缺失的公开导出名，优化模式下依旧以非零退出码报错。
# Export completeness is verified in a separate interpreter: `assert` is stripped entirely by
# `python -O`, which silently disables this check, so an explicit SystemExit carries the missing
# public export names instead and still exits non-zero under optimization.
EXPORT_CHECK_PROGRAM = "\n".join(
    (
        "import importlib, sys",
        "module = importlib.import_module(sys.argv[1])",
        'missing = [name for name in getattr(module, "__all__", []) if not hasattr(module, name)]',
        'sys.exit("missing public exports: " + ", ".join(missing)) if missing else None',
    )
)


def main() -> int:
    failures: list[str] = []
    modules: list[str] = []
    classes = 0
    methods = 0
    pure_functions = 0
    repository_classes = 0
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        application = path.name == "service.py" or relative in APPLICATION_MODULES
        functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        legacy = set()
        for name, node in functions.items():
            deps = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            persistence = any(
                arg.arg in {"conn", "db"}
                or (arg.annotation and ast.unparse(arg.annotation) in {"Connection", "Database"})
                for arg in deps
            )
            if application and persistence:
                legacy.add(name)
                failures.append(f"{relative}:{node.lineno}: top-level persistence use case {name}")
            if not application and any(
                arg.annotation and ast.unparse(arg.annotation) in {"Connection", "Database"} for arg in deps
            ):
                failures.append(f"{relative}:{node.lineno}: top-level persistence function {name}")
            if relative != "system/health_router.py" and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in {"execute", "exec_driver_sql"}
                for call in ast.walk(node)
            ):
                failures.append(f"{relative}:{node.lineno}: top-level SQL function {name}")
            if application and not name.startswith("_") and name not in PUBLIC_FUNCTIONS.get(relative, set()):
                failures.append(f"{relative}:{node.lineno}: unreviewed public application function {name}")
            if application:
                pure_functions += 1
        declared = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        for name in REQUIRED_CLASSES.get(relative, set()) - declared:
            failures.append(f"{relative}: missing application service {name}")
        classes += len(REQUIRED_CLASSES.get(relative, set()) & declared)
        for name in REPOSITORY_CLASSES.get(relative, set()) - declared:
            failures.append(f"{relative}: missing persistence class {name}")
        repository_classes += len(REPOSITORY_CLASSES.get(relative, set()) & declared)
        for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            for method in (node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
                if cls.name in REQUIRED_CLASSES.get(relative, set()) | REPOSITORY_CLASSES.get(relative, set()):
                    methods += method.name != "__init__"
                    if method.name != "__init__" and any(
                        arg.arg == "conn" for arg in method.args.args + method.args.kwonlyargs
                    ):
                        failures.append(f"{relative}:{method.lineno}: instance method repeats Connection")
                    for call in (node for node in ast.walk(method) if isinstance(node, ast.Call)):
                        if isinstance(call.func, ast.Name) and call.func.id in legacy:
                            failures.append(f"{relative}:{call.lineno}: service delegates to legacy {call.func.id}")
                        if (
                            isinstance(call.func, ast.Attribute)
                            and isinstance(call.func.value, ast.Name)
                            and call.func.value.id == "self"
                        ):
                            if any(
                                isinstance(arg, ast.Attribute)
                                and isinstance(arg.value, ast.Name)
                                and arg.value.id == "self"
                                and arg.attr == "_conn"
                                for arg in call.args
                            ):
                                failures.append(f"{relative}:{call.lineno}: self method repeats owned Connection")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"execute", "exec_driver_sql"}
            ):
                if not path.stem.endswith("repository") and relative not in INFRASTRUCTURE_SQL:
                    failures.append(f"{relative}:{node.lineno}: SQL outside persistence/infrastructure")
            if application and isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("schema"):
                failures.append(f"{relative}:{node.lineno}: application imports private schema")
            if path.name.endswith("router.py") and isinstance(node, ast.ImportFrom):
                if (node.module and node.module.endswith("repository")) or any(
                    alias.name == "repository" for alias in node.names
                ):
                    failures.append(f"{relative}:{node.lineno}: Router bypasses Application Service")
        # Public exports must neither repeat nor reference removed names.
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
            ):
                exports = ast.literal_eval(node.value)
                if len(exports) != len(set(exports)):
                    failures.append(f"{relative}: duplicate public exports")
        parts = list(path.relative_to(ROOT.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules.append(".".join(parts))
    print(
        f"Source audit: {classes} required service classes, {methods} service/persistence methods, {pure_functions} reviewed helper/infrastructure functions",
        flush=True,
    )
    print(
        f"Persistence audit: {repository_classes} required repositories/writers, no top-level Connection/Database persistence functions",
        flush=True,
    )
    if failures:
        print("\n".join(failures), flush=True)
        return 1
    print("Source boundaries passed; testing independent import orders...", flush=True)

    def check_import(module: str) -> str | None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                EXPORT_CHECK_PROGRAM,
                module,
            ],
            cwd=ROOT.parents[1],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return f"{module}: {result.stderr}" if result.returncode else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        for error in pool.map(check_import, modules):
            if error:
                failures.append(error)
    print(f"Independent imports: {len(modules)} modules, {len(failures)} failures", flush=True)
    if failures:
        print("\n".join(failures), flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
