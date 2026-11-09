"""ECS manager with a fake AWS client: reads, updates and AWS failures."""

import pytest
from botocore.exceptions import NoCredentialsError

from ecs_manager import ECSManager, ECSManagerError
from fakes import FakeECSClient, client_error


def make(client):
    return ECSManager("cloudpilot-ai-cluster", "cloudpilot-ai-service", "ap-southeast-2", client=client)


def test_get_spec():
    spec = make(FakeECSClient(desired=2, running=2)).get_spec()
    assert spec["desired_count"] == 2
    assert spec["cpu"] == 256 and spec["memory"] == 512
    assert spec["task_definition"] == "cloudpilot-ai:1"
    assert spec["containers"][0]["ports"] == [8000]


def test_status_stable_in_progress_failed():
    assert make(FakeECSClient(rollout="COMPLETED")).get_status()["overall"] == "stable"
    assert make(FakeECSClient(rollout="IN_PROGRESS")).get_status()["overall"] == "in_progress"
    assert make(FakeECSClient(rollout="FAILED")).get_status()["overall"] == "failed"


def test_status_in_progress_while_old_deployment_drains():
    old = {"id": "ecs-svc/0", "status": "ACTIVE", "rolloutState": "COMPLETED", "taskDefinition": "x:1"}
    assert make(FakeECSClient(rollout="COMPLETED", deployments=[old])).get_status()["overall"] == "in_progress"


def test_update_desired_count():
    client = FakeECSClient()
    result = make(client).update_desired_count(3)
    assert result["desired_count"] == 3
    assert ("update_service", {"desiredCount": 3, "taskDefinition": None}) in client.calls


def test_update_task_size_preserves_settings():
    client = FakeECSClient()
    result = make(client).update_task_size(512, 1024)

    registered = next(c[1] for c in client.calls if c[0] == "register_task_definition")
    assert registered["cpu"] == "512" and registered["memory"] == "1024"
    # Existing configuration is carried over unchanged ...
    assert registered["family"] == "cloudpilot-ai"
    assert registered["executionRoleArn"].endswith("cloudpilot-ai-task-execution-role")
    assert registered["containerDefinitions"][0]["image"].endswith("cloudpilot-ai:latest")
    assert registered["containerDefinitions"][0]["logConfiguration"]["logDriver"] == "awslogs"
    assert registered["runtimePlatform"]["cpuArchitecture"] == "X86_64"
    assert registered["tags"] == [{"key": "Project", "value": "cloudpilot-ai"}]
    # ... and read-only metadata is not sent back to AWS.
    for key in ("taskDefinitionArn", "revision", "status", "registeredAt", "compatibilities"):
        assert key not in registered

    assert result["previous_task_definition"] == "cloudpilot-ai:1"
    assert result["new_task_definition"] == "cloudpilot-ai:2"
    assert client.current_arn.endswith("cloudpilot-ai:2")


def test_container_memory_larger_than_task_is_rejected():
    client = FakeECSClient()
    client.task_defs[client.current_arn]["containerDefinitions"][0]["memory"] = 2048
    with pytest.raises(ECSManagerError) as err:
        make(client).update_task_size(256, 1024)
    assert err.value.status_code == 400


def test_access_denied_is_translated():
    client = FakeECSClient(fail_with={"update_service": client_error("AccessDeniedException", "not authorized")})
    with pytest.raises(ECSManagerError) as err:
        make(client).update_desired_count(2)
    assert err.value.status_code == 403
    assert "permission" in err.value.message.lower()


def test_service_not_found_is_translated():
    client = FakeECSClient(fail_with={"describe_services": client_error("ServiceNotFoundException")})
    with pytest.raises(ECSManagerError) as err:
        make(client).get_spec()
    assert err.value.status_code == 404


def test_missing_credentials_is_translated():
    client = FakeECSClient(fail_with={"describe_services": NoCredentialsError()})
    with pytest.raises(ECSManagerError) as err:
        make(client).get_status()
    assert err.value.status_code == 401


def test_register_failure_does_not_update_service():
    client = FakeECSClient(fail_with={"register_task_definition": client_error("ClientException", "invalid")})
    with pytest.raises(ECSManagerError):
        make(client).update_task_size(512, 1024)
    assert not any(c[0] == "update_service" for c in client.calls)
