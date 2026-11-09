"""API tests with FastAPI's TestClient and a fake ECS client (no real AWS calls)."""

import pytest
from fastapi.testclient import TestClient

import main
from config import Settings
from ecs_manager import ECSManager
from fakes import FakeECSClient, client_error

SETTINGS = Settings(
    aws_region="ap-southeast-2",
    ecs_cluster="cloudpilot-ai-cluster",
    ecs_service="cloudpilot-ai-service",
    control_api_enabled=True,
    min_desired_count=0,
    max_desired_count=5,
    max_cpu=1024,
    max_memory=4096,
)


@pytest.fixture
def fake_client():
    return FakeECSClient()


@pytest.fixture
def client(fake_client):
    app = main.create_app(control_api_enabled=True)
    manager = ECSManager(SETTINGS.ecs_cluster, SETTINGS.ecs_service, SETTINGS.aws_region, client=fake_client)
    app.dependency_overrides[main.get_manager] = lambda: manager
    app.dependency_overrides[main.get_settings] = lambda: SETTINGS
    return TestClient(app)


def test_health():
    response = TestClient(main.create_app(control_api_enabled=False)).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root():
    response = TestClient(main.create_app(control_api_enabled=False)).get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "CloudPilot AI"


def test_control_api_disabled_by_default_on_public_service():
    public = TestClient(main.create_app(control_api_enabled=False))
    assert public.post("/chat", json={"message": "scale to 3"}).status_code == 404
    assert public.get("/service/spec").status_code == 404


def test_service_spec_and_status(client):
    assert client.get("/service/spec").json()["cpu"] == 256
    assert client.get("/service/status").json()["overall"] == "stable"


def test_chat_read_request(client):
    body = client.post("/chat", json={"message": "show the current configuration"}).json()
    assert body["status"] == "ok"
    assert "CPU 256" in body["message"]


def test_chat_change_needs_confirmation_and_does_not_execute(client, fake_client):
    body = client.post("/chat", json={"message": "scale to 3 tasks"}).json()
    assert body["status"] == "needs_confirmation"
    assert body["action"]["desired_count"] == 3
    assert fake_client.desired == 1  # nothing changed yet


def test_confirm_applies_desired_count(client, fake_client):
    body = client.post("/chat/confirm", json={"action": {"action": "update_desired_count", "desired_count": 3}}).json()
    assert body["status"] == "started"
    assert fake_client.desired == 3


def test_confirm_applies_task_size(client, fake_client):
    body = client.post("/chat/confirm", json={"action": {"action": "update_task_size", "cpu": 512, "memory": 1024}}).json()
    assert body["status"] == "started"
    assert body["data"]["new_task_definition"] == "cloudpilot-ai:2"


def test_invalid_combination_is_rejected(client, fake_client):
    body = client.post("/chat", json={"message": "set cpu to 512"}).json()  # 512 MB is too small for 512 CPU
    assert body["status"] == "rejected"
    assert not any(c[0] == "register_task_definition" for c in fake_client.calls)


def test_out_of_limit_count_is_rejected(client):
    assert client.post("/chat", json={"message": "scale to 50 tasks"}).json()["status"] == "rejected"


def test_unclear_and_unsupported(client):
    assert client.post("/chat", json={"message": "make it better"}).json()["status"] == "clarification"
    assert client.post("/chat", json={"message": "delete the service"}).json()["status"] == "unsupported"


def test_request_validation(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat/confirm", json={"action": {"action": "update_desired_count", "desired_count": -1}}).status_code == 422
    assert client.post("/chat/confirm", json={"action": {"action": "drop_database"}}).status_code == 422


def test_aws_permission_error_returns_403(fake_client, client):
    fake_client.fail_with = {"update_service": client_error("AccessDeniedException", "not authorized")}
    response = client.post("/chat/confirm", json={"action": {"action": "update_desired_count", "desired_count": 2}})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "AccessDeniedException"


def test_aws_not_found_returns_404(fake_client, client):
    fake_client.fail_with = {"describe_services": client_error("ClusterNotFoundException")}
    assert client.get("/service/status").status_code == 404
