from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.execution.docker_runtime import DockerSandboxRuntime

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/capabilities")
async def get_system_capabilities() -> dict[str, Any]:
    settings = get_settings()
    docker_ok = DockerSandboxRuntime.is_available()
    mode = settings.execution_mode.lower()

    if mode == "disabled":
        active_runtime = "disabled"
        net_isolation = False
        active_validation = False
    elif docker_ok and mode == "docker-sandbox":
        active_runtime = "docker-sandbox"
        net_isolation = True
        active_validation = True
    else:
        active_runtime = "restricted-local"
        net_isolation = False
        active_validation = True

    return {
        "github": {
            "actions_metadata": {
                "implemented": True,
                "configured": bool(settings.github_client_id and settings.github_client_secret),
                "accessible": None,
            },
            "actions_logs": {
                "implemented": True,
                "configured": bool(settings.github_client_id and settings.github_client_secret),
                "accessible": None,
            },
        },
        "execution": {
            "runtime": active_runtime,
            "docker_available": docker_ok,
            "network_isolation": net_isolation,
            "resource_limits": True,
            "active_validation": active_validation,
            "environment_sanitizing": True,
            "workspace_integrity_checks": True,
        }
    }
