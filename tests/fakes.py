"""A fake ECS client so tests never call real AWS."""

import copy

from botocore.exceptions import ClientError

TASK_DEF_ARN_1 = "arn:aws:ecs:ap-southeast-2:123456789012:task-definition/cloudpilot-ai:1"

BASE_TASK_DEF = {
    "taskDefinitionArn": TASK_DEF_ARN_1,
    "family": "cloudpilot-ai",
    "revision": 1,
    "status": "ACTIVE",
    "executionRoleArn": "arn:aws:iam::123456789012:role/cloudpilot-ai-task-execution-role",
    "networkMode": "awsvpc",
    "requiresCompatibilities": ["FARGATE"],
    "compatibilities": ["EC2", "FARGATE"],
    "runtimePlatform": {"operatingSystemFamily": "LINUX", "cpuArchitecture": "X86_64"},
    "cpu": "256",
    "memory": "512",
    "volumes": [],
    "placementConstraints": [],
    "registeredAt": "2026-11-09T00:00:00Z",
    "registeredBy": "arn:aws:iam::123456789012:user/test",
    "containerDefinitions": [
        {
            "name": "cloudpilot-ai",
            "image": "123456789012.dkr.ecr.ap-southeast-2.amazonaws.com/cloudpilot-ai:latest",
            "essential": True,
            "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/cloudpilot-ai"}},
        }
    ],
}


def client_error(code: str, message: str = "boom", operation: str = "Operation") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class FakeECSClient:
    def __init__(self, desired=1, running=1, rollout="COMPLETED", deployments=None, fail_with=None):
        self.desired = desired
        self.running = running
        self.task_defs = {TASK_DEF_ARN_1: copy.deepcopy(BASE_TASK_DEF)}
        self.current_arn = TASK_DEF_ARN_1
        self.rollout = rollout
        self.extra_deployments = deployments or []
        self.fail_with = fail_with  # {"method_name": exception}
        self.calls = []

    def _maybe_fail(self, name):
        if self.fail_with and name in self.fail_with:
            raise self.fail_with[name]

    def describe_services(self, cluster, services):
        self.calls.append(("describe_services", cluster, services))
        self._maybe_fail("describe_services")
        return {
            "services": [
                {
                    "serviceName": services[0],
                    "status": "ACTIVE",
                    "launchType": "FARGATE",
                    "desiredCount": self.desired,
                    "runningCount": self.running,
                    "pendingCount": 0,
                    "taskDefinition": self.current_arn,
                    "deployments": [
                        {
                            "id": "ecs-svc/1",
                            "status": "PRIMARY",
                            "rolloutState": self.rollout,
                            "rolloutStateReason": "test",
                            "taskDefinition": self.current_arn,
                            "desiredCount": self.desired,
                            "runningCount": self.running,
                            "pendingCount": 0,
                            "failedTasks": 0,
                        }
                    ]
                    + self.extra_deployments,
                    "events": [{"message": "(service cloudpilot-ai-service) has reached a steady state."}],
                }
            ],
            "failures": [],
        }

    def describe_task_definition(self, taskDefinition, include=None):
        self.calls.append(("describe_task_definition", taskDefinition))
        self._maybe_fail("describe_task_definition")
        return {"taskDefinition": copy.deepcopy(self.task_defs[taskDefinition]), "tags": [{"key": "Project", "value": "cloudpilot-ai"}]}

    def register_task_definition(self, **kwargs):
        self.calls.append(("register_task_definition", kwargs))
        self._maybe_fail("register_task_definition")
        revision = len(self.task_defs) + 1
        arn = f"arn:aws:ecs:ap-southeast-2:123456789012:task-definition/{kwargs['family']}:{revision}"
        registered = copy.deepcopy(kwargs)
        registered.pop("tags", None)
        registered.update({"taskDefinitionArn": arn, "revision": revision, "status": "ACTIVE"})
        self.task_defs[arn] = registered
        return {"taskDefinition": registered}

    def update_service(self, cluster, service, desiredCount=None, taskDefinition=None):
        self.calls.append(("update_service", {"desiredCount": desiredCount, "taskDefinition": taskDefinition}))
        self._maybe_fail("update_service")
        if desiredCount is not None:
            self.desired = desiredCount
        if taskDefinition is not None:
            self.current_arn = taskDefinition
            self.rollout = "IN_PROGRESS"
        return {"service": {"desiredCount": self.desired, "runningCount": self.running, "taskDefinition": self.current_arn}}
