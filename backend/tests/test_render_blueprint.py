from pathlib import Path

import yaml


def _environment(service: dict) -> dict[str, dict]:
    return {item["key"]: item for item in service["envVars"] if "key" in item}


def test_render_workers_scale_safely_and_keep_oauth_secrets_out_of_worker_roles() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))
    services = {service["name"]: service for service in manifest["services"]}

    assert services["base64-ops-api"]["autoDeployTrigger"] == "checksPass"
    assert services["base64-ops-api"]["scaling"]["maxInstances"] == 10
    indexer = services["base64-indexer"]
    observer = services["base64-ci-observer"]
    assert indexer["scaling"]["minInstances"] == 1
    assert indexer["scaling"]["maxInstances"] == 4
    assert observer["scaling"]["maxInstances"] == 3

    for worker in (indexer, observer):
        env = _environment(worker)
        assert env["MONGO_URI"]["fromService"]["name"] == "base64-ops-api"
        assert env["GITHUB_TOKEN_ENCRYPTION_KEY"]["fromService"]["name"] == "base64-ops-api"
        assert env["BACKGROUND_JOB_LEASE_SECONDS"]["value"] == "900"
        assert "GITHUB_CLIENT_SECRET" not in env
        assert "RAZORPAY_KEY_SECRET" not in env


def test_mvp_blueprint_keeps_the_100_to_500_user_default_to_one_api_service() -> None:
    root = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((root / "render.mvp.yaml").read_text(encoding="utf-8"))

    assert len(manifest["services"]) == 1
    api = manifest["services"][0]
    env = _environment(api)
    assert api["type"] == "web"
    assert api["plan"] == "0.5c-512mb"
    assert api["autoDeployTrigger"] == "checksPass"
    assert env["INDEXING_MODE"]["value"] == "inline"
    assert env["CI_VERIFICATION_MODE"]["value"] == "on_read"
    assert env["REDIS_URL"]["sync"] is False
