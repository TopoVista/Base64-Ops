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

    if docker_ok and mode == "docker-sandbox":
        active_runtime = "docker-sandbox"
        net_isolation = True
        active_validation = True
        unavailable_reason = None
    elif mode == "restricted-local" and not settings.is_production:
        active_runtime = "restricted-local"
        net_isolation = False
        active_validation = True
        unavailable_reason = None
    else:
        active_runtime = "unavailable"
        net_isolation = False
        active_validation = False
        unavailable_reason = "No authorized isolated execution provider is configured."

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
            "unavailable_reason": unavailable_reason,
            "environment_sanitizing": True,
            "workspace_integrity_checks": True,
        }
    }
