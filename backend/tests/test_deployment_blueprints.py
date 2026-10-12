"""Regression checks for the checked-in Render production blueprints.

The deployment host resolves ``sync: false`` and ``fromService`` values at
deploy time.  These tests deliberately substitute representative secret-manager
values and run the resulting environment through the same Pydantic validator
the process uses at startup.  That catches a missing production invariant
before a Blueprint reaches Render.
"""

from pathlib import Path
from typing import Any

import yaml

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]

_PRODUCTION_SECRETS = {
    "MONGO_URI": "mongodb+srv://user:password@cluster.example.invalid/base64",
    "FRONTEND_ORIGIN": "https://base64.example.com",
    "BASE_URL": "https://api.base64.example.com",
    "TRUSTED_HOSTS": "api.base64.example.com",
    "REDIS_URL": "redis://redis.example.invalid:6379/0",
    "RATE_LIMIT_FAIL_CLOSED": "true",
    "TRUSTED_CLIENT_IP_HEADER": "CF-Connecting-IP",
    "CLERK_JWKS_URL": "https://clerk.example.invalid/.well-known/jwks.json",
    "CLERK_ISSUER": "https://clerk.example.invalid",
    "CLERK_AUTHORIZED_PARTIES": "https://base64.example.com",
    "GITHUB_CLIENT_ID": "production-client-id",
    "GITHUB_CLIENT_SECRET": "production-client-secret",
    "GITHUB_OAUTH_STATE_SECRET": "0123456789abcdef0123456789abcdef",
    "GITHUB_TOKEN_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
}


def _blueprint(path: str) -> dict[str, Any]:
    with (ROOT / path).open(encoding="utf-8") as blueprint:
        value = yaml.safe_load(blueprint)
    assert isinstance(value, dict)
    return value


def _resolved_service_environment(service: dict[str, Any]) -> dict[str, str]:
    """Model Render's secret/service references without reading real secrets."""

    environment = dict(_PRODUCTION_SECRETS)
    for entry in service.get("envVars", []):
        key = entry["key"]
        if "value" in entry:
            environment[key] = str(entry["value"])
        elif entry.get("generateValue"):
            # Render's generated value is a base64-encoded 256-bit secret.
            # Preserve the representative value supplied above for settings
            # with a stricter cryptographic format contract.
            environment.setdefault(key, f"generated-{key.lower()}")
        elif key not in environment:
            environment[key] = f"resolved-{key.lower()}"
    return environment


def test_render_blueprints_use_a_strict_ready_check_and_validate_each_role() -> None:
    for blueprint_name in ("render.yaml", "render.mvp.yaml"):
        blueprint = _blueprint(blueprint_name)
        services = blueprint["services"]
        assert isinstance(services, list) and services

        for service in services:
            environment = _resolved_service_environment(service)
            assert environment["ENVIRONMENT"] == "production"
            Settings(**environment)

        api = next(service for service in services if service["type"] == "web")
        assert api["healthCheckPath"] == "/ready"
        assert _resolved_service_environment(api)["EXECUTION_MODE"] == "disabled"

    assert _blueprint("render.mvp.yaml")["services"][0]["plan"] == "0.5c-512mb"


def test_scaled_render_blueprint_keeps_long_running_work_off_the_public_api() -> None:
    services = _blueprint("render.yaml")["services"]
    by_name = {service["name"]: service for service in services}

    assert set(by_name) == {
        "base64-ops-api",
        "base64-maintenance",
        "base64-indexer",
        "base64-ci-observer",
    }
    assert by_name["base64-ops-api"]["type"] == "web"
    assert by_name["base64-ops-api"]["plan"] == "0.5c-512mb"
    assert by_name["base64-maintenance"]["plan"] == "0.5c-512mb"
    assert by_name["base64-indexer"]["plan"] == "0.5c-512mb"
    assert by_name["base64-ci-observer"]["plan"] == "0.5c-512mb"
    assert all("scaling" not in service for service in services)
    assert by_name["base64-indexer"]["envVars"]
    assert by_name["base64-ci-observer"]["envVars"]

    indexer_environment = _resolved_service_environment(by_name["base64-indexer"])
    observer_environment = _resolved_service_environment(by_name["base64-ci-observer"])
    assert indexer_environment["SERVICE_ROLE"] == "indexer"
    assert observer_environment["SERVICE_ROLE"] == "ci_observer"
