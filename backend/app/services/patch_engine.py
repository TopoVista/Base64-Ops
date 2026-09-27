import difflib
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.schemas.delivery import DeliveryPlan, ProposedFileChange
from app.schemas.patch import ChangeSurface, FileEditIntent, PatchProposal, RepositoryCapability
from app.services.delivery_service import DeliveryService, content_hash
from app.services.redaction_service import RedactionService
from app.utils.datetime import utc_now
from app.utils.ids import new_id


class PatchSafetyError(ValueError):
    """Raised when a patch proposal violates repository safety rules."""


class RepositoryPathPolicy:
    """Authoritative repository path validator."""

    SENSITIVE_NAMES = re.compile(
        r"(^|/)"
        r"(\.env(?:\..*)?|credentials?[^/]*|.*\.(?:pem|key|p12|pfx)|id_rsa|id_ed25519|id_dsa|private_key.*)"
        r"($)",
        re.IGNORECASE,
    )
    RESTRICTED_PREFIXES = ("etc/", "var/", "proc/", "sys/", "windows/", "system32/", ".ssh/", ".git/")

    def validate(self, root: Path, candidate: str, *, allow_create: bool = False) -> Path:
        if not candidate or "\x00" in candidate:
            raise PatchSafetyError("Invalid path: contains null bytes or is empty")
        if re.match(r"^[A-Za-z]:", candidate) or candidate.startswith("/") or candidate.startswith("\\"):
            raise PatchSafetyError("Absolute or drive-letter paths are not allowed")

        normalized = candidate.replace("\\", "/").strip("/")

        if normalized.startswith("../") or "/../" in normalized or normalized.endswith("/..") or normalized == "..":
            raise PatchSafetyError("Path traversal is not allowed")

        lower = normalized.lower()
        if lower.startswith(".git/") or lower == ".git":
            raise PatchSafetyError("Git internals are not editable")

        for prefix in self.RESTRICTED_PREFIXES:
            if lower.startswith(prefix) or f"/{prefix}" in lower:
                raise PatchSafetyError(f"Access to system/restricted directory '{prefix}' is prohibited")

        if self.SENSITIVE_NAMES.search(normalized):
            if not (normalized.endswith(".env.example") or normalized.endswith(".env.template")):
                raise PatchSafetyError("Sensitive secret-bearing files cannot be modified or created")

        root_resolved = root.resolve()
        target = (root_resolved / normalized).resolve(strict=False)

        if not target.is_relative_to(root_resolved):
            raise PatchSafetyError("Path resolves outside the repository root")

        if target.exists() and target.is_symlink():
            symlink_target = target.resolve()
            if not symlink_target.is_relative_to(root_resolved):
                raise PatchSafetyError("Symlink escapes the repository root")

        if not allow_create and not target.is_file():
            raise PatchSafetyError("Target is not an existing regular file")

        return target


class PatchEngine:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.paths = RepositoryPathPolicy()
        self.redactor = RedactionService()
        self.delivery = DeliveryService()

    def candidate_files(self, evidence: list[dict[str, Any]], repo_map: dict[str, Any]) -> dict[str, list[str]]:
        candidates: dict[str, list[str]] = {}
        for item in evidence:
            path = item.get("path") or item.get("source")
            if path:
                norm = str(path).replace("\\", "/")
                candidates.setdefault(norm, []).append(f"evidence:{item.get('id', 'unknown')}")
        for key in ("containers", "ci", "manifests", "runbooks"):
            for path in repo_map.get(key, []):
                norm = str(path).replace("\\", "/")
                if norm in candidates:
                    candidates[norm].append(f"repository_map:{key}")
        return candidates

    def build_delivery_plan(
        self,
        *,
        proposal: PatchProposal,
        user_id: str,
        session_id: str,
        run_id: str,
        repository_id: str,
        base_branch: str,
        base_sha: str,
        repo_path: Path,
        evidence: list[dict[str, Any]],
        repo_map: dict[str, Any],
    ) -> tuple[DeliveryPlan, list[ChangeSurface]]:
        if proposal.unresolved_questions:
            raise PatchSafetyError(
                f"Patch proposal has unresolved questions: {'; '.join(proposal.unresolved_questions)}"
            )
        if not proposal.edits:
            raise PatchSafetyError("Patch proposal contains no edits")
        if len(proposal.edits) > self.settings.max_patch_files:
            raise PatchSafetyError("Patch proposal exceeds safe automatic-edit scope (too many files changed)")

        evidence_by_id = {str(item.get("id")): item for item in evidence}
        candidate_paths = self.candidate_files(evidence, repo_map)
        changes: list[ProposedFileChange] = []
        candidate_root = Path(tempfile.mkdtemp(prefix="base64-candidate-"))

        try:
            shutil.copytree(
                repo_path,
                candidate_root / "repo",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(".git"),
            )
            candidate_repo = candidate_root / "repo"

            for intent in proposal.edits:
                self._validate_intent(intent, evidence_by_id, candidate_paths, repo_path)
                original_path = self.paths.validate(repo_path, intent.path, allow_create=intent.operation == "create")
                candidate_path = self.paths.validate(candidate_repo, intent.path, allow_create=True)

                if intent.operation == "delete":
                    original = self._read_text(original_path)
                    original_digest = content_hash(original)
                    del_lines = original.replace(chr(10), chr(10) + "-")
                    diff_str = (
                        f"--- a/{intent.path}\n+++ /dev/null\n"
                        f"@@ -1,{original.count(chr(10)) + 1} +0,0 @@\n-{del_lines}\n"
                    )
                    changes.append(
                        ProposedFileChange(
                            path=intent.path,
                            change_type="delete",
                            original_hash=original_digest,
                            proposed_hash=content_hash(""),
                            unified_diff=diff_str,
                            proposed_content=None,
                        )
                    )
                    continue

                original = "" if intent.operation == "create" else self._read_text(original_path)
                content = intent.proposed_content
                if content is None:
                    raise PatchSafetyError("A text replacement is required for every executable edit")

                if len(content.encode("utf-8")) > self.settings.max_patch_content_bytes:
                    raise PatchSafetyError("Generated replacement content exceeds safe size limit")

                original_digest = content_hash(original) if intent.operation == "modify" else None
                if intent.operation == "modify" and intent.expected_original_hash:
                    if intent.expected_original_hash != original_digest:
                        raise PatchSafetyError(
                            "The source file changed after the remediation was generated. "
                            "Proposal must be regenerated against the current repository state."
                        )

                self._reject_suspicious(content)

                # Maintain original line endings if present
                content = self._preserve_line_endings(original, content)

                candidate_path.parent.mkdir(parents=True, exist_ok=True)
                candidate_path.write_text(content, encoding="utf-8", newline="")

                orig_lines = original.splitlines(keepends=True)
                new_lines = content.splitlines(keepends=True)
                diff = "".join(
                    difflib.unified_diff(
                        orig_lines,
                        new_lines,
                        fromfile=f"a/{intent.path}",
                        tofile=f"b/{intent.path}",
                    )
                )

                changes.append(
                    ProposedFileChange(
                        path=intent.path,
                        change_type=intent.operation,
                        original_hash=original_digest,
                        proposed_hash=content_hash(content),
                        unified_diff=diff,
                        proposed_content=content,
                    )
                )

            total_changed_lines = sum(
                change.unified_diff.count("\n+") + change.unified_diff.count("\n-") for change in changes
            )
            if total_changed_lines > self.settings.max_patch_lines:
                raise PatchSafetyError("Patch proposal exceeds safe automatic-edit scope (too many lines changed)")

            risk, reasons = self.delivery.classify_risk(changes)
            surfaces = self.classify_surfaces(changes)

            return (
                DeliveryPlan(
                    id=new_id("dpl_"),
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    repository_id=repository_id,
                    base_branch=base_branch,
                    base_sha=base_sha,
                    title=proposal.summary,
                    rationale=proposal.rationale,
                    evidence_ids=proposal.evidence_ids,
                    files=changes,
                    validation_steps=self.delivery.validation_steps_for(changes),
                    risk_level=risk,
                    risk_reasons=reasons,
                    created_at=utc_now(),
                ),
                surfaces,
            )
        finally:
            shutil.rmtree(candidate_root, ignore_errors=True)

    def detect_capabilities(self, repo_path: Path) -> list[RepositoryCapability]:
        capabilities: list[RepositoryCapability] = []
        if (repo_path / "pyproject.toml").exists() or (repo_path / "requirements.txt").exists():
            capabilities.append(
                RepositoryCapability(kind="python", source="pyproject.toml", commands=["python-syntax", "ruff"])
            )
        if (repo_path / "package.json").exists() and (repo_path / "tsconfig.json").exists():
            capabilities.append(
                RepositoryCapability(kind="typescript", source="tsconfig.json", commands=["tsc --noEmit"])
            )
        if any((repo_path / f).exists() for f in ("docker-compose.yml", "compose.yml", "docker-compose.yaml")):
            capabilities.append(
                RepositoryCapability(kind="compose", source="compose", commands=["docker compose config"])
            )
        if (repo_path / "pom.xml").exists() or (repo_path / "build.gradle").exists():
            capabilities.append(RepositoryCapability(kind="java", source="build", commands=["mvn test", "gradle test"]))
        if (repo_path / "CMakeLists.txt").exists():
            capabilities.append(RepositoryCapability(kind="cpp", source="CMakeLists.txt", commands=["cmake build"]))
        return capabilities

    def classify_surfaces(self, changes: list[ProposedFileChange]) -> list[ChangeSurface]:
        grouped: dict[str, list[str]] = {}
        for change in changes:
            path = change.path.lower()
            if path.endswith((".md", ".rst", ".txt")):
                surface = "documentation"
            elif "test" in path or path.startswith("tests/"):
                surface = "tests"
            elif ".github/workflows" in path or "ci" in path:
                surface = "ci_cd"
            elif any(k in path for k in ("docker", "compose")):
                surface = "docker"
            elif any(k in path for k in ("auth", "security", "credential", "secret")):
                surface = "security"
            elif any(k in path for k in ("migration", "schema", "db/")):
                surface = "database"
            elif path.endswith((".json", ".yaml", ".yml", ".toml", ".ini")):
                surface = "configuration"
            else:
                surface = "application"

            grouped.setdefault(surface, []).append(change.path)

        return [
            ChangeSurface(surface=name, files=files, reasons=[f"Path classification: {name}"])
            for name, files in grouped.items()
        ]

    def _validate_intent(
        self,
        intent: FileEditIntent,
        evidence_by_id: dict[str, dict[str, Any]],
        candidates: dict[str, list[str]],
        root: Path,
    ) -> None:
        if intent.operation != "create" and not intent.evidence_ids:
            raise PatchSafetyError("Every edit requires supporting evidence")

        if any(item not in evidence_by_id for item in intent.evidence_ids):
            raise PatchSafetyError("Edit references evidence outside this investigation")

        if intent.operation == "modify" and intent.path not in candidates:
            raise PatchSafetyError(
                f"Edit path '{intent.path}' is outside the investigation candidate set. "
                "Only evidenced or mapped files can be modified."
            )

        target = self.paths.validate(root, intent.path, allow_create=intent.operation == "create")

        if target.exists():
            if target.stat().st_size > self.settings.max_patch_file_bytes:
                raise PatchSafetyError("Target file exceeds safe size limit (500 KB)")
            raw_head = target.read_bytes()[:8192]
            if b"\x00" in raw_head:
                raise PatchSafetyError("Binary files are unsupported for automatic editing")

    @staticmethod
    def _read_text(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="strict")

    @staticmethod
    def _preserve_line_endings(original: str, new_content: str) -> str:
        if "\r\n" in original and "\r\n" not in new_content:
            return new_content.replace("\n", "\r\n")
        return new_content

    @staticmethod
    def _reject_suspicious(content: str) -> None:
        patterns = (
            r"curl\s+.*\|\s*(sh|bash)",
            r"wget\s+.*\|\s*(sh|bash)",
            r"os\.system\(",
            r"shell\s*=\s*True",
            r"eval\(",
            r"exec\(",
            r"base64.*exec",
            r"print\s*\([^)]*(secret|token|password|private_key)",
        )
        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE | re.DOTALL):
                raise PatchSafetyError("Security review required: suspicious generated content detected")
