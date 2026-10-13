from typing import Any

from fastapi import APIRouter

from app.core.config import get_settings
from app.execution.docker_runtime import DockerSandboxRuntime
from app.services.github_service import github_app_is_configured, github_incident_reporting_is_configured

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

    app_verification_configured = github_app_is_configured()
    github_check_reporting_configured = github_incident_reporting_is_configured()
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
            "candidate_verification": {
                "implemented": True,
                "mode": "github_app_dispatch" if app_verification_configured else "static_only",
                "configured": app_verification_configured,
                # Accessibility is repository/installation-specific and is
                # only proven when a tenant saves its reviewed workflow binding.
                "accessible": None if app_verification_configured else False,
                "unavailable_reason": None
                if app_verification_configured
                else (
                    "Targeted candidate verification requires a configured GitHub App "
                    "and a reviewed repository-owned workflow."
                ),
            },
            "incident_check_reporting": {
                "implemented": True,
                "mode": "github_check" if github_check_reporting_configured else "disabled",
                "configured": github_check_reporting_configured,
                # A particular repository is only accessible after its
                # existing App installation binding is verified server-side.
                "accessible": None if github_check_reporting_configured else False,
                "unavailable_reason": None
                if github_check_reporting_configured
                else "GitHub Check reporting requires an enabled GitHub App and Checks: write permission.",
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
