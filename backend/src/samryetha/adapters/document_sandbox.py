"""Linux document sandbox. Only system runtimes, one input and one output are mounted."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

MAX_PDF_BYTES = 100 * 1024 * 1024


class DocumentSandbox:
    def __init__(self, binary: str, timeout_seconds: int) -> None:
        self.binary = shutil.which(binary)
        self.bwrap = shutil.which("bwrap")
        self.timeout = timeout_seconds
        self._ready: bool | None = None

    def _base_command(self) -> list[str]:
        if self.bwrap is None:
            raise OSError("bubblewrap is unavailable")
        command = [self.bwrap, "--unshare-all", "--die-with-parent", "--new-session",
                   "--cap-drop", "ALL", "--clearenv"]
        # Exclude /usr/local and arbitrary application/data directories.
        for name in ("/usr/bin", "/usr/lib", "/usr/lib64", "/usr/share/libreoffice",
                     "/usr/share/fonts", "/usr/share/fontconfig", "/usr/share/icu", "/usr/share/locale"):
            if Path(name).exists():
                command.extend(["--ro-bind", name, name])
        for name in ("bin", "sbin", "lib", "lib64"):
            path = Path("/") / name
            if path.is_symlink():
                command.extend(["--symlink", os.readlink(path), str(path)])
            elif path.exists():
                command.extend(["--ro-bind", str(path), str(path)])
        for name in ("/etc/fonts", "/etc/ld.so.cache"):
            if Path(name).exists():
                command.extend(["--ro-bind", name, name])
        command.extend(["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                        "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8",
                        "--setenv", "HOME", "/work/home", "--setenv", "TMPDIR", "/tmp"])
        return command

    def available(self) -> bool:
        # Arbitrary wrappers may depend on private host paths. Never expose those paths just
        # to make a converter work; deployments must select a system installation instead.
        if (sys.platform != "linux" or self.binary is None or self.bwrap is None
                or not any(Path(self.binary).resolve().is_relative_to(path)
                           for path in ("/usr/bin", "/usr/lib", "/usr/lib64"))
                or not Path(self.binary).exists() or not Path(self.bwrap).exists()
                or not Path("/usr/bin/prlimit").exists()):
            return False
        if self._ready is None:
            try:
                result = subprocess.run(
                    [*self._base_command(), self.binary, "--version"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5, check=False, env={"PATH": "/usr/bin:/bin"},
                )
                self._ready = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                self._ready = False
        return self._ready

    def convert(self, source: Path, output: Path) -> Path | None:
        if not self.available() or self.binary is None:
            raise OSError("document sandbox is unavailable")
        (output / "home").mkdir()
        extension = source.suffix.lower()
        command = [*self._base_command(), "--dir", "/input",
                   "--ro-bind", str(source.resolve()), f"/input/document{extension}",
                   "--bind", str(output.resolve()), "/work", "--chdir", "/work",
                   "/usr/bin/prlimit", "--as=2147483648", f"--cpu={self.timeout}",
                   f"--fsize={MAX_PDF_BYTES}", "--nofile=256", "--", self.binary,
                   "--headless", "--norestore", "--nolockcheck", "--nodefault",
                   "-env:UserInstallation=file:///work/profile", "--convert-to", "pdf",
                   "--outdir", "/work", f"/input/document{extension}"]
        # A PID namespace plus process-group kill also terminates converter descendants.
        # Discard process output rather than collecting unbounded parser output in RAM.
        with subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"}, start_new_session=True,
        ) as process:
            try:
                code = process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                return None
        produced = output / "document.pdf"
        if code != 0 or not produced.is_file() or not 0 < produced.stat().st_size <= MAX_PDF_BYTES:
            return None
        return produced
