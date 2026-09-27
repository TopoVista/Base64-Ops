import shutil
import subprocess
import time
from pathlib import Path

from app.execution.models import ExecutionRequest, ExecutionResult
from app.services.redaction_service import RedactionService


class DockerSandboxRuntime:
    """Ephemeral Docker container sandbox runtime for active validation."""

    def __init__(self, image: str = "python:3.11-slim") -> None:
        self.image = image
        self.redactor = RedactionService()

    @staticmethod
    def is_available() -> bool:
        if not shutil.which("docker"):
            return False
        try:
            res = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return res.returncode == 0
        except Exception:
            return False

    def execute(self, request: ExecutionRequest, repo_path: Path) -> ExecutionResult:
        started = time.monotonic()
        work_dir_rel = request.working_directory.replace("\\", "/")

        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none" if request.network_policy == "disabled" else "bridge",
            "--memory",
            f"{request.memory_limit_mb or 1024}m",
            "--cpus",
            str(request.cpu_limit or 1.5),
            "-v",
            f"{repo_path.resolve()}:/workspace:rw",
            "-w",
            f"/workspace/{work_dir_rel.strip('/')}".rstrip("/"),
            self.image,
            request.executable,
        ] + request.arguments

        try:
            process = subprocess.run(
                docker_cmd,
                capture_output=True,
                timeout=request.timeout_seconds,
                check=False,
            )

            duration_ms = round((time.monotonic() - started) * 1000)
            stdout_text = process.stdout.decode("utf-8", errors="replace")
            stderr_text = process.stderr.decode("utf-8", errors="replace")

            stdout_summary, stdout_trunc = self._bound_output(stdout_text, request.max_stdout_bytes)
            stderr_summary, stderr_trunc = self._bound_output(stderr_text, request.max_stderr_bytes)

            status_str = "success" if process.returncode == 0 else "failed"

            return ExecutionResult(
                request_id=request.id,
                status=status_str,
                exit_code=process.returncode,
                duration_ms=duration_ms,
                stdout_summary=self.redactor.redact(stdout_summary),
                stderr_summary=self.redactor.redact(stderr_summary),
                stdout_truncated=stdout_trunc,
                stderr_truncated=stderr_trunc,
                resource_usage={"duration_ms": duration_ms, "runtime": "docker-sandbox"},
            )
        except subprocess.TimeoutExpired:
            duration_ms = round((time.monotonic() - started) * 1000)
            return ExecutionResult(
                request_id=request.id,
                status="timeout",
                exit_code=None,
                duration_ms=duration_ms,
                stderr_summary="Execution timed out inside Docker sandbox.",
                failure_type="timeout",
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
