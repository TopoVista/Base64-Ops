from pathlib import Path

from app.core.config import get_settings
from app.execution.docker_runtime import DockerSandboxRuntime
from app.execution.local_runtime import RestrictedLocalRuntime
from app.execution.models import ExecutionRequest, ExecutionResult
from app.execution.policy import ExecutionPolicy, ExecutionPolicyError
from app.execution.registry import get_validator
from app.utils.ids import new_id


class ExecutionRuntimeManager:
    """Manager coordinating static vs active execution, runtime selection, and candidate workspace integrity."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.local_runtime = RestrictedLocalRuntime()
        self.docker_runtime = DockerSandboxRuntime(image=self.settings.workspace_docker_image)

    def execute_validator(
        self,
        *,
        validator_id: str,
        arguments: list[str],
        repo_path: Path,
        run_id: str,
        repository_id: str,
        expected_proposed_hashes: dict[str, str] | None = None,
        working_directory: str = ".",
    ) -> ExecutionResult:
        validator = get_validator(validator_id)
        if not validator:
            return ExecutionResult(
                request_id=new_id("req_"),
                status="policy_denied",
                exit_code=None,
                duration_ms=0,
                stderr_summary=f"Unregistered validator ID: {validator_id}",
                failure_type="policy_denied",
            )

        # Build ExecutionRequest using registered validator definition
        request = ExecutionRequest(
            id=new_id("req_"),
            run_id=run_id,
            repository_id=repository_id,
            purpose="test" if validator.executes_repository_code else "static_analysis",
            command_id=validator.id,
            executable=validator.executable,
            arguments=arguments,
            working_directory=working_directory,
            timeout_seconds=validator.default_timeout_seconds,
            network_policy="disabled" if not validator.requires_network else "restricted",
            max_stdout_bytes=self.settings.max_patch_content_bytes,
            max_stderr_bytes=self.settings.max_patch_content_bytes,
            memory_limit_mb=validator.default_memory_limit_mb,
        )

        # Snapshot workspace prior to validator execution
        before_snapshot = ExecutionPolicy.snapshot_workspace(repo_path)

        # Runtime selection: Active repository execution uses Docker sandbox if available
        mode = getattr(self.settings, "execution_mode", "docker-sandbox").lower()
        if validator.executes_repository_code:
            # A missing container runtime is not permission to execute a
            # repository's test/build command on the API or worker host.  In
            # particular, ordinary Render services do not provide the
            # isolation boundary required for this work.  Keep the explicit
            # local-development mode available, but never select it as an
            # implicit fallback.
            if mode == "disabled":
                return self._isolated_runtime_denied(request)
            if mode == "docker-sandbox":
                if not DockerSandboxRuntime.is_available():
                    return self._isolated_runtime_denied(request)
                result = self.docker_runtime.execute(request, repo_path)
            elif mode == "restricted-local" and not self.settings.is_production:
                result = self.local_runtime.execute(request, repo_path)
            else:
                return self._isolated_runtime_denied(request)
        else:
            # Static validators still use the registered executable and the
            # sanitized environment. They do not gain permission to run a
            # repository-provided script merely because this branch is local.
            result = self.local_runtime.execute(request, repo_path)

        # Verify candidate workspace integrity & side-effect policy if hashes were provided
        if expected_proposed_hashes:
            try:
                ExecutionPolicy.verify_workspace_integrity(repo_path, expected_proposed_hashes, before_snapshot)
            except ExecutionPolicyError as exc:
                return ExecutionResult(
                    request_id=request.id,
                    status="failed",
                    exit_code=None,
                    duration_ms=result.duration_ms,
                    stderr_summary=str(exc),
                    failure_type="integrity_failure",
                )

        return result

    @staticmethod
    def _isolated_runtime_denied(request: ExecutionRequest) -> ExecutionResult:
        return ExecutionResult(
            request_id=request.id,
            status="policy_denied",
            exit_code=None,
            duration_ms=0,
            stderr_summary=(
                "Active validation unavailable: no authorized isolated execution provider configured."
            ),
            failure_type="policy_denied",
        )
