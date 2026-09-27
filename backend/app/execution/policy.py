import os
import re
from pathlib import Path

from app.services.delivery_service import content_hash


class ExecutionPolicyError(ValueError):
    """Raised when execution policy is violated or candidate integrity fails."""


class ExecutionPolicy:
    SAFE_ENV_KEYS = {
        "PATH",
        "LANG",
        "LC_ALL",
        "PYTHONUTF8",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "CI",
        "HOME",
        "USERPROFILE",
    }

    SECRET_PATTERN = re.compile(
        r".*(_TOKEN|_SECRET|_PASSWORD|_KEY|AWS_|AZURE_|GOOGLE_|GITHUB_|OPENAI_|CLERK_|MONGO_|DATABASE_URL|SSH_).*",
        re.IGNORECASE,
    )

    @classmethod
    def sanitize_environment(cls, base_env: dict[str, str] | None = None) -> dict[str, str]:
        source = base_env if base_env is not None else dict(os.environ)
        clean_env: dict[str, str] = {}

        for key, value in source.items():
            if key in cls.SAFE_ENV_KEYS and not cls.SECRET_PATTERN.match(key):
                clean_env[key] = value

        clean_env["CI"] = "true"
        clean_env["PYTHONUTF8"] = "1"
        return clean_env

    @staticmethod
    def snapshot_workspace(repo_path: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for item in repo_path.rglob("*"):
            if item.is_file() and ".git" not in item.parts:
                rel = str(item.relative_to(repo_path)).replace("\\", "/")
                try:
                    hashes[rel] = content_hash(item.read_text(encoding="utf-8", errors="ignore"))
                except Exception:
                    continue
        return hashes

    @classmethod
    def verify_workspace_integrity(
        cls,
        repo_path: Path,
        expected_proposed_hashes: dict[str, str],
        before_snapshot: dict[str, str],
    ) -> None:
        # 1. Verify candidate target file content was not modified by the validator
        for rel_path, expected_hash in expected_proposed_hashes.items():
            target = repo_path / rel_path
            if not target.exists():
                raise ExecutionPolicyError(f"Validation deleted candidate file: {rel_path}")
            current_hash = content_hash(target.read_text(encoding="utf-8", errors="strict"))
            if current_hash != expected_hash:
                raise ExecutionPolicyError(
                    f"Validation altered proposed source file '{rel_path}'. Candidate integrity check failed."
                )

        # 2. Check for unexpected side-effect creations of sensitive or tracked files
        after_snapshot = cls.snapshot_workspace(repo_path)
        for rel_path, _hash in after_snapshot.items():
            if rel_path not in before_snapshot and rel_path not in expected_proposed_hashes:
                lower = rel_path.lower()
                if any(token in lower for token in (".env", "credential", "token", "secret", "private_key")):
                    raise ExecutionPolicyError(
                        f"Validator generated suspicious sensitive file side-effect: '{rel_path}'"
                    )
