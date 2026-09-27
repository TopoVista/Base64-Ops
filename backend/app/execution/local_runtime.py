import os
import signal
import subprocess
import time
from pathlib import Path

from app.execution.models import ExecutionRequest, ExecutionResult
from app.execution.policy import ExecutionPolicy
from app.services.redaction_service import RedactionService


class RestrictedLocalRuntime:
    """Restricted local execution runtime for static validation and local development mode."""

    def __init__(self) -> None:
        self.redactor = RedactionService()

    def execute(self, request: ExecutionRequest, repo_path: Path) -> ExecutionResult:
        started = time.monotonic()
        clean_env = ExecutionPolicy.sanitize_environment()

        work_dir = (repo_path / request.working_directory).resolve()

        cmd = [request.executable] + request.arguments

        try:
            kwargs: dict = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["preexec_fn"] = os.setsid

            process = subprocess.Popen(
                cmd,
                cwd=work_dir,
                env=clean_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                **kwargs,
            )

            try:
                stdout_bytes, stderr_bytes = process.communicate(timeout=request.timeout_seconds)
                exit_code = process.returncode
                status_str = "success" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired:
                self._kill_process_tree(process.pid)
                stdout_bytes, stderr_bytes = process.communicate()
                exit_code = None
                status_str = "timeout"

            duration_ms = round((time.monotonic() - started) * 1000)

            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            stdout_summary, stdout_trunc = self._bound_output(stdout_text, request.max_stdout_bytes)
            stderr_summary, stderr_trunc = self._bound_output(stderr_text, request.max_stderr_bytes)

            return ExecutionResult(
                request_id=request.id,
                status=status_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                stdout_summary=self.redactor.redact(stdout_summary),
                stderr_summary=self.redactor.redact(stderr_summary),
                stdout_truncated=stdout_trunc,
                stderr_truncated=stderr_trunc,
                resource_usage={"duration_ms": duration_ms},
                failure_type="timeout" if status_str == "timeout" else None,
            )

        except Exception as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            return ExecutionResult(
                request_id=request.id,
                status="runtime_error",
                exit_code=None,
                duration_ms=duration_ms,
                stderr_summary=self.redactor.redact(str(exc)),
                failure_type=type(exc).__name__,
            )

    def _kill_process_tree(self, pid: int) -> None:
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass

    @staticmethod
    def _bound_output(text: str, max_bytes: int) -> tuple[str, bool]:
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text, False
        half = max_bytes // 2
        head = encoded[:half].decode("utf-8", errors="ignore")
        tail = encoded[-half:].decode("utf-8", errors="ignore")
        summary = f"{head}\n\n... [OUTPUT TRUNCATED] ...\n\n{tail}"
        return summary, True
