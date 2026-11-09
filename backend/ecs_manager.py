"""
All AWS ECS calls live here (separate from the API routes).

Supported operations - nothing else is exposed:
  * read the current service specification
  * read deployment status
  * update the desired task count
  * update task CPU/memory (new task definition revision + rolling deployment)
"""

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError, NoCredentialsError

# Fields of a task definition that must be copied into a new revision.
# Everything else returned by DescribeTaskDefinition (revision, status, ARNs,
# registeredAt, ...) is read-only metadata and must NOT be sent back.
PRESERVED_TASK_DEF_FIELDS = (
    "family",
    "taskRoleArn",
    "executionRoleArn",
    "networkMode",
    "containerDefinitions",
    "volumes",
    "placementConstraints",
    "requiresCompatibilities",
    "runtimePlatform",
    "ephemeralStorage",
    "proxyConfiguration",
    "inferenceAccelerators",
    "pidMode",
    "ipcMode",
    "enableFaultInjection",
)

# AWS error code -> (HTTP status, user-friendly explanation)
AWS_ERROR_MAP = {
    "AccessDeniedException": (403, "AWS denied permission for this operation. Check the IAM permissions of the credentials used by the backend."),
    "UnauthorizedOperation": (403, "AWS denied permission for this operation."),
    "ClusterNotFoundException": (404, "The ECS cluster was not found. Check ECS_CLUSTER and AWS_REGION."),
    "ServiceNotFoundException": (404, "The ECS service was not found. Check ECS_SERVICE and AWS_REGION."),
    "ServiceNotActiveException": (409, "The ECS service is not active."),
    "InvalidParameterException": (400, "AWS rejected the request parameters."),
    "ClientException": (400, "AWS rejected the request."),
    "ThrottlingException": (429, "AWS is throttling requests. Wait a few seconds and try again."),
    "ServerException": (502, "AWS ECS returned an internal error. Try again shortly."),
    "UnrecognizedClientException": (401, "The AWS credentials are invalid."),
    "InvalidClientTokenId": (401, "The AWS credentials are invalid."),
    "ExpiredTokenException": (401, "The AWS credentials have expired."),
}


class ECSManagerError(Exception):
    """An AWS failure translated into a clear message and HTTP status code."""

    def __init__(self, message: str, status_code: int = 502, code: str = "AWSError"):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _translate(exc: Exception) -> ECSManagerError:
    if isinstance(exc, ECSManagerError):
        return exc
    if isinstance(exc, NoCredentialsError):
        return ECSManagerError("AWS credentials were not found. Run 'aws configure' on the machine running the backend.", 401, "NoCredentials")
    if isinstance(exc, EndpointConnectionError):
        return ECSManagerError("Could not reach AWS. Check your internet connection and AWS_REGION.", 503, "EndpointConnectionError")
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = error.get("Code", "ClientError")
        detail = error.get("Message", str(exc))
        status, friendly = AWS_ERROR_MAP.get(code, (502, "AWS returned an error."))
        return ECSManagerError(f"{friendly} ({code}: {detail})", status, code)
    if isinstance(exc, BotoCoreError):
        return ECSManagerError(f"AWS SDK error: {exc}", 502, type(exc).__name__)
    return ECSManagerError(f"Unexpected error: {exc}", 500, type(exc).__name__)


def _revision_from_arn(arn: str | None) -> str | None:
    # arn:aws:ecs:region:acct:task-definition/family:7 -> "family:7"
    return arn.rsplit("/", 1)[-1] if arn else None


class ECSManager:
    def __init__(self, cluster: str, service: str, region: str, client: Any = None):
        self.cluster = cluster
        self.service = service
        self.region = region
        # A client can be injected (tests use a fake one); otherwise boto3 picks up
        # credentials from ~/.aws/credentials, environment variables or an IAM role.
        self.client = client or boto3.client("ecs", region_name=region)

    # ---------- low-level reads ----------

    def _describe_service(self) -> dict:
        try:
            response = self.client.describe_services(cluster=self.cluster, services=[self.service])
        except Exception as exc:
            raise _translate(exc) from exc
        services = response.get("services", [])
        if not services:
            failures = response.get("failures", [])
            reason = failures[0].get("reason", "MISSING") if failures else "MISSING"
            raise ECSManagerError(
                f"ECS service '{self.service}' was not found in cluster '{self.cluster}' ({reason}).",
                404,
                "ServiceNotFound",
            )
        return services[0]

    def _describe_task_definition(self, arn: str) -> tuple[dict, list]:
        try:
            response = self.client.describe_task_definition(taskDefinition=arn, include=["TAGS"])
        except Exception as exc:
            raise _translate(exc) from exc
        return response["taskDefinition"], response.get("tags", [])

    # ---------- public operations ----------

    def get_spec(self) -> dict:
        """Current service specification (count, CPU, memory, image, revision)."""
        service = self._describe_service()
        task_def, _ = self._describe_task_definition(service["taskDefinition"])
        containers = task_def.get("containerDefinitions", [])
        return {
            "cluster": self.cluster,
            "service": self.service,
            "region": self.region,
            "service_status": service.get("status"),
            "launch_type": service.get("launchType"),
            "desired_count": service.get("desiredCount"),
            "running_count": service.get("runningCount"),
            "pending_count": service.get("pendingCount"),
            "task_definition": _revision_from_arn(service.get("taskDefinition")),
            "task_definition_arn": service.get("taskDefinition"),
            "cpu": int(task_def["cpu"]),
            "memory": int(task_def["memory"]),
            "containers": [
                {
                    "name": c.get("name"),
                    "image": c.get("image"),
                    "ports": [p.get("containerPort") for p in c.get("portMappings", [])],
                }
                for c in containers
            ],
        }

    def get_status(self) -> dict:
        """Deployment status, summarised into one overall state plus details."""
        service = self._describe_service()
        deployments = service.get("deployments", [])
        primary = next((d for d in deployments if d.get("status") == "PRIMARY"), None)
        primary_state = (primary or {}).get("rolloutState")

        if primary_state == "FAILED":
            overall = "failed"
        elif primary_state == "COMPLETED" and len(deployments) == 1:
            overall = "stable"
        else:
            overall = "in_progress"

        return {
            "cluster": self.cluster,
            "service": self.service,
            "overall": overall,
            "desired_count": service.get("desiredCount"),
            "running_count": service.get("runningCount"),
            "pending_count": service.get("pendingCount"),
            "task_definition": _revision_from_arn(service.get("taskDefinition")),
            "deployments": [
                {
                    "id": d.get("id"),
                    "status": d.get("status"),
                    "rollout_state": d.get("rolloutState"),
                    "rollout_reason": d.get("rolloutStateReason"),
                    "task_definition": _revision_from_arn(d.get("taskDefinition")),
                    "desired": d.get("desiredCount"),
                    "running": d.get("runningCount"),
                    "pending": d.get("pendingCount"),
                    "failed_tasks": d.get("failedTasks", 0),
                }
                for d in deployments
            ],
            "recent_events": [e.get("message") for e in service.get("events", [])[:5]],
        }

    def update_desired_count(self, desired_count: int) -> dict:
        if desired_count < 0:
            raise ECSManagerError("Desired count cannot be negative.", 400, "ValidationError")
        try:
            response = self.client.update_service(
                cluster=self.cluster, service=self.service, desiredCount=desired_count
            )
        except Exception as exc:
            raise _translate(exc) from exc
        service = response["service"]
        return {
            "operation": "update_desired_count",
            "desired_count": service.get("desiredCount"),
            "running_count": service.get("runningCount"),
            "task_definition": _revision_from_arn(service.get("taskDefinition")),
        }

    def update_task_size(self, cpu: int, memory: int) -> dict:
        """
        1. read the task definition the service currently uses
        2. copy all of its settings, changing only cpu/memory
        3. register it as a new revision
        4. point the service at the new revision (ECS starts a rolling deployment)
        """
        service = self._describe_service()
        current, tags = self._describe_task_definition(service["taskDefinition"])

        # Container-level limits must still fit inside the new task size.
        for container in current.get("containerDefinitions", []):
            if container.get("memory") and container["memory"] > memory:
                raise ECSManagerError(
                    f"Container '{container.get('name')}' reserves {container['memory']} MB, "
                    f"more than the requested task memory of {memory} MB.",
                    400,
                    "ValidationError",
                )
            if container.get("cpu") and container["cpu"] > cpu:
                raise ECSManagerError(
                    f"Container '{container.get('name')}' reserves {container['cpu']} CPU units, "
                    f"more than the requested task CPU of {cpu}.",
                    400,
                    "ValidationError",
                )

        new_definition = {
            key: current[key]
            for key in PRESERVED_TASK_DEF_FIELDS
            if key in current and current[key] not in (None, [], {})
        }
        new_definition["cpu"] = str(cpu)
        new_definition["memory"] = str(memory)
        if tags:
            new_definition["tags"] = tags

        try:
            registered = self.client.register_task_definition(**new_definition)
            new_arn = registered["taskDefinition"]["taskDefinitionArn"]
            response = self.client.update_service(
                cluster=self.cluster, service=self.service, taskDefinition=new_arn
            )
        except Exception as exc:
            raise _translate(exc) from exc

        return {
            "operation": "update_task_size",
            "previous_task_definition": _revision_from_arn(service.get("taskDefinition")),
            "new_task_definition": _revision_from_arn(new_arn),
            "cpu": cpu,
            "memory": memory,
            "desired_count": response["service"].get("desiredCount"),
        }
