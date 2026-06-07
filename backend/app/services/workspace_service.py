import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

from fastapi import HTTPException, status

from app.core.config import get_settings
from app.services.github_service import get_github_access_token

SAFE_READ_COMMANDS = {
    "git status",
    "git diff",
    "git log",
    "docker compose config",
    "docker-compose config",
}


def repo_name_from_url(repo_url: str) -> str:
    name = repo_url.rstrip("/").split("/")[-1].removesuffix(".git")
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", name) or "repo"


def workspace_path(user_id: str, slug_id: str) -> Path:
    root = get_settings().workspace_root
    safe_user = re.sub(r"[^a-zA-Z0-9._-]+", "-", user_id)
    safe_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug_id)
    return (root / safe_user / safe_slug).resolve()


def branch_name_from_prompt(prompt: str | None) -> str:
    suffix = "changes"
    if prompt:
        suffix = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:32] or "changes"
    return f"agent/{suffix}"


class WorkspaceService:
    async def ensure_workspace(
        self,
        user_id: str,
        slug_id: str,
        repo_url: str,
        default_branch: str | None,
    ) -> tuple[Path, str]:
        path = workspace_path(user_id, slug_id)
        repo_name = repo_name_from_url(repo_url)
        repo_path = path / repo_name
        if repo_path.exists() and (repo_path / ".git").exists():
            return repo_path, repo_name
        path.mkdir(parents=True, exist_ok=True)
        token = await get_github_access_token(user_id)
        authed_url = self._with_token(repo_url, token)
        args = ["git", "clone", "--depth", "1"]
        if default_branch:
            args.extend(["--branch", default_branch])
        args.extend([authed_url, repo_name])
        result = self._run(args, cwd=path, hide_token=token)
        if result["exitCode"] != 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to clone repository: {result['output']}",
            )
        return repo_path, repo_name

    async def ensure_branch(self, repo_path: Path, branch_name: str) -> dict[str, Any]:
        result = self._run(["git", "checkout", "-B", branch_name], cwd=repo_path)
        return result

    def read_file(self, repo_path: Path, relative_path: str) -> str:
        target = self._safe_path(repo_path, relative_path)
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
        return target.read_text(encoding="utf-8", errors="replace")

    def write_file(self, repo_path: Path, relative_path: str, content: str) -> dict[str, Any]:
        target = self._safe_path(repo_path, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": relative_path, "bytes": len(content.encode("utf-8"))}

    def grep(self, repo_path: Path, query: str) -> dict[str, Any]:
        matches: list[str] = []
        for file_path in self.iter_indexable_files(repo_path):
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if query.lower() in line.lower():
                    matches.append(f"{file_path.relative_to(repo_path)}:{line_number}: {line.strip()}")
                    if len(matches) >= 50:
                        return {"matches": matches, "count": len(matches)}
        return {"matches": matches, "count": len(matches)}

    def git_status(self, repo_path: Path) -> dict[str, Any]:
        return self._run(["git", "status", "--short"], cwd=repo_path)

    def git_diff(self, repo_path: Path) -> dict[str, Any]:
        return self._run(["git", "diff", "--stat"], cwd=repo_path)

    def docker_build_check(self, repo_path: Path) -> dict[str, Any]:
        if not (repo_path / "Dockerfile").exists():
            return {"success": False, "output": "Dockerfile not found", "exitCode": 1}
        return self._run(["docker", "build", "--no-cache", "--pull", "-t", "agent-check:latest", "."], cwd=repo_path)

    def compose_config_check(self, repo_path: Path) -> dict[str, Any]:
        if (repo_path / "docker-compose.yml").exists() or (repo_path / "compose.yml").exists():
            return self._run(["docker", "compose", "config"], cwd=repo_path)
        return {"success": False, "output": "No compose file found", "exitCode": 1}

    def commit_all(self, repo_path: Path, message: str) -> dict[str, Any]:
        self._run(["git", "add", "-A"], cwd=repo_path)
        return self._run(["git", "commit", "-m", message], cwd=repo_path)

    def push_branch(self, repo_path: Path, branch_name: str) -> dict[str, Any]:
        return self._run(["git", "push", "-u", "origin", branch_name], cwd=repo_path)

    def iter_indexable_files(self, repo_path: Path) -> list[Path]:
        ignored_dirs = {".git", "node_modules", "dist", "build", ".next", ".venv", "__pycache__"}
        extensions = {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".py",
            ".md",
            ".mdx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".env.example",
            ".dockerfile",
        }
        files: list[Path] = []
        for root, dirs, names in os.walk(repo_path):
            dirs[:] = [directory for directory in dirs if directory not in ignored_dirs]
            for name in names:
                path = Path(root) / name
                suffix = path.suffix.lower()
                if name == "Dockerfile" or suffix in extensions:
                    if path.stat().st_size <= 250_000:
                        files.append(path)
        return files[:500]

    def _safe_path(self, repo_path: Path, relative_path: str) -> Path:
        target = (repo_path / relative_path).resolve()
        if not str(target).startswith(str(repo_path.resolve())):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe path")
        return target

    def _with_token(self, repo_url: str, token: str) -> str:
        parsed = urlparse(repo_url)
        if parsed.scheme not in {"http", "https"}:
            return repo_url
        netloc = f"x-access-token:{quote(token)}@{parsed.netloc}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))

    def _run(
        self,
        args: list[str],
        cwd: Path,
        timeout: int = 120,
        hide_token: str | None = None,
    ) -> dict[str, Any]:
        if shutil.which(args[0]) is None:
            return {"success": False, "output": f"{args[0]} is not installed", "exitCode": 127}
        completed = subprocess.run(
            args,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        output = (completed.stdout + completed.stderr).strip()
        if hide_token:
            output = output.replace(hide_token, "***")
        return {"success": completed.returncode == 0, "output": output, "exitCode": completed.returncode}
