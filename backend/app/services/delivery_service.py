import hashlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from pymongo import ReturnDocument

from app.db.mongo import get_db
from app.schemas.delivery import (
    ApprovalRecord,
    DeliveryPlan,
    DeliveryResult,
    ProposedFileChange,
    ValidationResult,
    ValidationStep,
)
from app.services.github_service import github_api
from app.services.redaction_service import RedactionService
from app.services.workspace_service import WorkspaceService
from app.utils.datetime import utc_now
from app.utils.ids import new_id

RISK_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True)


class DeliveryService:
    def __init__(self) -> None:
        self.workspace = WorkspaceService()
        self.redactor = RedactionService()

    def classify_risk(self, changes: list[ProposedFileChange]) -> tuple[str, list[str]]:
        risk, reasons = "low", []
        if not changes:
            return "low", ["No file changes proposed"]

        total_lines = sum(c.unified_diff.count("\n+") + c.unified_diff.count("\n-") for c in changes)

        for change in changes:
            path = change.path.lower()
            candidate, reason = "medium", "Application code modified"

            if change.change_type == "delete":
                if any(t in path for t in ("migration", "schema", "auth", "security", "ci", ".github", "db/")):
                    candidate, reason = "critical", f"Critical file deletion: {change.path}"
                else:
                    candidate, reason = "high", f"File deletion: {change.path}"
            elif any(token in path for token in (".github/workflows", "terraform", "infra", "migration", "schema")):
                candidate, reason = "high", "CI/CD, infrastructure, or schema configuration modified"
            elif any(token in path for token in ("auth", "security", "credential", "secret")):
                candidate, reason = "high", "Authentication, authorization, or credential surface modified"
            elif any(token in path for token in ("docker", "compose", "config", "main.py", "server.py")):
                candidate, reason = "medium", "Runtime or container configuration modified"
            elif path.endswith((".md", ".rst", ".txt")):
                candidate, reason = "low", "Documentation-only change"
            elif "test" in path or path.startswith("tests/"):
                candidate, reason = "low", "Test-only change"

            if len(changes) > 3 or total_lines > 200:
                if RISK_ORDER[candidate] < RISK_ORDER["high"]:
                    candidate, reason = "high", f"Multi-file ({len(changes)}) or large patch ({total_lines} lines)"

            if RISK_ORDER[candidate] > RISK_ORDER[risk]:
                risk = candidate
            if reason not in reasons:
                reasons.append(reason)

        return risk, reasons

    def validation_steps_for(self, changes: list[ProposedFileChange]) -> list[ValidationStep]:
        steps: list[ValidationStep] = []
        if any(change.path.endswith(".py") for change in changes):
            steps.append(
                ValidationStep(id="python-syntax", kind="typecheck", description="Compile modified Python files")
            )
        if any("compose" in change.path.lower() for change in changes):
            steps.append(
                ValidationStep(id="compose-config", kind="config", description="Validate Docker Compose configuration")
            )
        if any(change.path.endswith((".ts", ".tsx")) for change in changes):
            steps.append(
                ValidationStep(id="tsc-check", kind="typecheck", description="Run TypeScript typecheck (tsc --noEmit)")
            )
        return steps or [ValidationStep(id="review", kind="custom", description="Review exact proposed diff")]

    def validate_plan(self, plan: DeliveryPlan, repo_path: Path | None = None) -> list[ValidationResult]:
        results: list[ValidationResult] = []
        changes = {change.path: change for change in plan.files}
        expected_hashes = {c.path: c.proposed_hash for c in plan.files if c.proposed_hash}

        for step in plan.validation_steps:
            started = time.monotonic()
            if step.id == "python-syntax":
                invalid = []
                for change in changes.values():
                    if change.path.endswith(".py") and change.proposed_content is not None:
                        try:
                            compile(change.proposed_content, change.path, "exec")
                        except SyntaxError as exc:
                            invalid.append(f"{change.path}:{exc.lineno}: {exc.msg}")
                summary = "Python syntax check passed" if not invalid else "; ".join(invalid)
                result_status = "passed" if not invalid else "failed"
            elif repo_path is not None:
                from app.execution.runtime import ExecutionRuntimeManager

                manager = ExecutionRuntimeManager()
                validator_id = {
                    "compose-config": "docker.compose_config",
                    "tsc-check": "typescript.tsc",
                }.get(step.id, "python.syntax")

                res = manager.execute_validator(
                    validator_id=validator_id,
                    arguments=[],
                    repo_path=repo_path,
                    run_id=plan.run_id,
                    repository_id=plan.repository_id,
                    expected_proposed_hashes=expected_hashes,
                )
                result_status = "passed" if res.status == "success" else "failed"
                summary = res.stdout_summary or res.stderr_summary or f"Validation {res.status}"
            else:
                result_status, summary = "skipped", "Pre-approval validation step completed"

            results.append(
                ValidationResult(
                    step_id=step.id,
                    status=result_status,
                    summary=self.redactor.redact(summary),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            )
        return results

    @staticmethod
    def diff_hash(plan: DeliveryPlan) -> str:
        return hashlib.sha256("\n".join(change.unified_diff for change in plan.files).encode()).hexdigest()

    def approval_payload(
        self, plan: DeliveryPlan, action_type: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "repository_id": plan.repository_id,
            "base_branch": plan.base_branch,
            "base_sha": plan.base_sha,
            "delivery_plan_id": plan.id,
            "action_type": action_type,
            "canonical_arguments": arguments or {},
            "diff_hash": self.diff_hash(plan),
        }

    async def persist_plan(self, plan: DeliveryPlan) -> dict[str, Any]:
        document = plan.model_dump()
        document["files"] = [
            {**change.model_dump(), "proposed_content": change.proposed_content} for change in plan.files
        ]
        document["_id"] = plan.id
        document.update({"userId": plan.user_id, "sessionId": plan.session_id, "runId": plan.run_id})
        await get_db().delivery_plans.insert_one(document)
        return document

    async def create_approval(self, plan: DeliveryPlan) -> dict[str, Any]:
        payload = self.approval_payload(plan, "create_draft_pull_request")
        record = ApprovalRecord(
            id=new_id("apr_"),
            user_id=plan.user_id,
            session_id=plan.session_id,
            run_id=plan.run_id,
            repository_id=plan.repository_id,
            delivery_plan_id=plan.id,
            base_branch=plan.base_branch,
            base_sha=plan.base_sha,
            action_type=payload["action_type"],
            canonical_arguments=payload["canonical_arguments"],
            diff_hash=payload["diff_hash"],
            approval_hash=hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
            risk_level=plan.risk_level,
            status="pending",
            created_at=utc_now(),
        )
        document = record.model_dump()
        document["_id"] = record.id
        document.update({"userId": record.user_id, "sessionId": record.session_id, "runId": record.run_id})
        document["deliveryPlanId"] = record.delivery_plan_id
        await get_db().approvals.insert_one(document)
        return document

    def validate_binding(self, approval: dict[str, Any], plan: dict[str, Any], repo_path: Path) -> str | None:
        if approval["delivery_plan_id"] != plan["id"]:
            return "Approval delivery plan does not match the requested plan."
        typed_plan = DeliveryPlan.model_validate(plan)
        if approval["diff_hash"] != self.diff_hash(typed_plan):
            return "Approval invalidated because the proposed patch changed."
        payload = self.approval_payload(typed_plan, approval["action_type"], approval.get("canonical_arguments"))
        if hashlib.sha256(canonical_json(payload).encode()).hexdigest() != approval["approval_hash"]:
            return "Approval invalidated because its canonical binding changed."
        current_branch = self.workspace.current_branch(repo_path)
        if current_branch != approval["base_branch"]:
            return (
                "Approval invalidated because repository branch changed. "
                f"Approved: {approval['base_branch']}; current: {current_branch}."
            )
        current_sha = self.workspace.head_commit(repo_path)
        if current_sha != approval["base_sha"]:
            return (
                "Approval invalidated because repository HEAD changed. "
                f"Approved: {approval['base_sha']}; current: {current_sha}."
            )
        return None

    async def execute(self, *, user_id: str, approval_id: str, repo_path: Path, repo_url: str) -> dict[str, Any]:
        db = get_db()
        approval = await db.approvals.find_one({"id": approval_id, "user_id": user_id})
        if not approval:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
        plan = await db.delivery_plans.find_one({"id": approval["delivery_plan_id"], "user_id": user_id})
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery plan not found")
        reason = self.validate_binding(approval, plan, repo_path)
        if reason:
            await db.approvals.update_one({"id": approval_id, "user_id": user_id}, {"$set": {"status": "invalidated"}})
            return {"status": "invalidated", "reason": reason}

        locked = await db.approvals.find_one_and_update(
            {"id": approval_id, "user_id": user_id, "status": "approved"},
            {"$set": {"status": "executing"}},
            return_document=ReturnDocument.AFTER,
        )
        if not locked:
            return {"status": "already_processed", "reason": "Approval was already executed or is not approved."}

        typed_plan = DeliveryPlan.model_validate(plan)
        branch = f"base64/{typed_plan.id[4:16]}"

        try:
            branch_result = await self.workspace.ensure_branch(repo_path, branch)
            if not branch_result["success"]:
                return await self._fail(locked, typed_plan, "branch", branch_result["output"])

            # Verify current source file hashes against approved original_hash before mutation
            for change in typed_plan.files:
                current_file = repo_path / change.path
                if change.change_type == "modify":
                    if not current_file.exists():
                        return await self._fail(
                            locked,
                            typed_plan,
                            "patch",
                            "The source file changed after the remediation was generated. "
                            "Proposal must be regenerated against the current repository state.",
                        )
                    current_text = current_file.read_text(encoding="utf-8", errors="strict")
                    if content_hash(current_text) != change.original_hash:
                        return await self._fail(
                            locked,
                            typed_plan,
                            "patch",
                            "The source file changed after the remediation was generated. "
                            "Proposal must be regenerated against the current repository state.",
                        )

            # Apply exact approved bytes
            for change in typed_plan.files:
                if change.change_type == "delete":
                    target = repo_path / change.path
                    if target.exists():
                        target.unlink()
                else:
                    self.workspace.write_file(repo_path, change.path, change.proposed_content or "")
                    applied = self.workspace.read_file(repo_path, change.path)
                    if content_hash(applied) != change.proposed_hash:
                        return await self._fail(
                            locked, typed_plan, "patch", "Resulting file hash did not match approved patch"
                        )

            validation = self.validate_plan(typed_plan)
            if any(item.status == "failed" for item in validation):
                return await self._fail(locked, typed_plan, "validation", "Post-patch validation failed", validation)

            commit = self.workspace.commit_all(repo_path, f"base64: {typed_plan.title}")
            if not commit["success"]:
                return await self._fail(locked, typed_plan, "commit", commit["output"], validation)

            pushed = self.workspace.push_branch(repo_path, branch)
            if not pushed["success"]:
                return await self._fail(locked, typed_plan, "push", pushed["output"], validation)

            owner, repo = self._owner_repo(repo_url)
            pr = await github_api(
                user_id,
                "POST",
                f"/repos/{owner}/{repo}/pulls",
                json={
                    "title": typed_plan.title,
                    "body": self.pr_body(typed_plan, validation),
                    "head": branch,
                    "base": typed_plan.base_branch,
                    "draft": True,
                },
            )

            result = DeliveryResult(
                id=new_id("dlr_"),
                delivery_plan_id=typed_plan.id,
                approval_id=approval_id,
                repository_id=typed_plan.repository_id,
                branch_name=branch,
                commit_sha=self.workspace.head_commit(repo_path),
                pull_request_number=pr.get("number"),
                pull_request_url=pr.get("html_url"),
                validation_results=validation,
                status="success",
                created_at=utc_now(),
            )
            document = result.model_dump()
            document["_id"] = result.id
            document["user_id"] = user_id
            await db.delivery_results.insert_one(document)
            await db.approvals.update_one(
                {"id": approval_id, "user_id": user_id}, {"$set": {"status": "executed", "executed_at": utc_now()}}
            )
            return {"status": "success", "result": document}
        except Exception as exc:
            return await self._fail(locked, typed_plan, "external", str(exc))

    async def _fail(
        self,
        approval: dict[str, Any],
        plan: DeliveryPlan,
        stage: str,
        detail: str,
        validation: list[ValidationResult] | None = None,
    ) -> dict[str, Any]:
        result = DeliveryResult(
            id=new_id("dlr_"),
            delivery_plan_id=plan.id,
            approval_id=approval["id"],
            repository_id=plan.repository_id,
            validation_results=validation or [],
            status="failed",
            failure_stage=stage,
            created_at=utc_now(),
        )
        document = result.model_dump()
        document["_id"] = result.id
        document["user_id"] = approval["user_id"]
        await get_db().delivery_results.insert_one(document)
        await get_db().approvals.update_one(
            {"id": approval["id"], "user_id": approval["user_id"]},
            {"$set": {"status": "failed", "executed_at": utc_now()}},
        )
        return {"status": "failed", "failure_stage": stage, "reason": self.redactor.redact(detail), "result": document}

    @staticmethod
    def _owner_repo(repo_url: str) -> tuple[str, str]:
        parts = repo_url.rstrip("/").removesuffix(".git").split("/")
        if len(parts) < 2:
            raise ValueError("Invalid GitHub repository URL")
        return parts[-2], parts[-1]

    def pr_body(self, plan: DeliveryPlan, validation: list[ValidationResult]) -> str:
        checks = "\n".join(f"- [{'x' if item.status == 'passed' else ' '}] {item.summary}" for item in validation)
        evidence = "\n".join(f"- Evidence `{evidence_id}`" for evidence_id in plan.evidence_ids)
        body = "\n\n".join(
            [
                f"## What changed\n\n{plan.title}",
                f"## Why\n\n{plan.rationale}",
                f"## Evidence\n\n{evidence or '- No evidence IDs'}",
                f"## Validation\n\n{checks or '- No validation steps'}",
                f"## Risk\n\n{plan.risk_level.upper()}\n\nReasons:\n"
                + "\n".join(f"- {reason}" for reason in plan.risk_reasons),
                "## Rollback\n\nRevert the delivery commit.",
                f"## Base64 Investigation\n\nRun: `{plan.run_id}`\nBase commit: `{plan.base_sha}`",
            ]
        )
        return self.redactor.redact(body)
