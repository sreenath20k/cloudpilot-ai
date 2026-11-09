"""
CloudPilot AI - backend API (FastAPI).

Public endpoints (always on - this is what runs on AWS ECS behind the load balancer):
  GET  /                 basic service information
  GET  /health           HTTP 200 when the service is healthy

Control endpoints (only when CONTROL_API_ENABLED=true - used by the local chat interface):
  GET  /service/status   deployment status of the ECS service
  GET  /service/spec     current ECS service specification
  POST /chat             interpret a plain-English request
  POST /chat/confirm     apply a change the user has confirmed
"""

import os
import socket
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from chat_engine import parse_request
from config import Settings, load_settings
from validation import plan_desired_count, plan_task_size

APP_NAME = "CloudPilot AI"
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")


# ---------- request / response models ----------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, examples=["Scale to 3 tasks"])


class ChangeAction(BaseModel):
    action: Literal["update_desired_count", "update_task_size"]
    desired_count: int | None = Field(default=None, ge=0)
    cpu: int | None = Field(default=None, gt=0)
    memory: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check_values(self) -> "ChangeAction":
        if self.action == "update_desired_count" and self.desired_count is None:
            raise ValueError("desired_count is required for update_desired_count")
        if self.action == "update_task_size" and (self.cpu is None or self.memory is None):
            raise ValueError("cpu and memory are required for update_task_size")
        return self


class ConfirmRequest(BaseModel):
    action: ChangeAction


class ChatResponse(BaseModel):
    # ok: read request answered | needs_confirmation: valid change waiting for "yes"
    # clarification: request unclear | rejected: understood but not allowed
    # unsupported: operation not offered | started: change submitted to ECS
    status: Literal["ok", "needs_confirmation", "clarification", "rejected", "unsupported", "started"]
    message: str
    action: ChangeAction | None = None
    data: dict | None = None


# ---------- dependencies ----------

@lru_cache
def get_settings() -> Settings:
    return load_settings()


@lru_cache
def get_manager():
    # Imported lazily so the public service (/, /health) never needs AWS access.
    from ecs_manager import ECSManager

    settings = get_settings()
    return ECSManager(settings.ecs_cluster, settings.ecs_service, settings.aws_region)


def _run(fn, *args):
    """Call the ECS manager and turn AWS failures into clear HTTP errors."""
    from ecs_manager import ECSManagerError

    try:
        return fn(*args)
    except ECSManagerError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc


def _plan(action_kind: str, manager, settings: Settings, **values):
    """Validate a requested change against the live service specification."""
    spec = _run(manager.get_spec)
    if action_kind == "update_desired_count":
        plan = plan_desired_count(spec["desired_count"], values.get("desired_count"), values.get("count_delta"), settings)
    else:
        plan = plan_task_size(spec["cpu"], spec["memory"], values.get("cpu"), values.get("memory"), settings)
    return plan, spec


# ---------- control routes ----------

control = APIRouter(tags=["ECS control"])


@control.get("/service/spec")
def service_spec(manager=Depends(get_manager)) -> dict:
    return _run(manager.get_spec)


@control.get("/service/status")
def service_status(manager=Depends(get_manager)) -> dict:
    return _run(manager.get_status)


@control.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, manager=Depends(get_manager), settings: Settings = Depends(get_settings)) -> ChatResponse:
    intent = parse_request(request.message)

    if intent.kind == "help":
        return ChatResponse(status="ok", message=intent.message)
    if intent.kind == "clarify":
        return ChatResponse(status="clarification", message=intent.message)
    if intent.kind == "unsupported":
        return ChatResponse(status="unsupported", message=intent.message)
    if intent.kind == "get_spec":
        spec = _run(manager.get_spec)
        return ChatResponse(
            status="ok",
            message=(
                f"Service '{spec['service']}' runs {spec['running_count']}/{spec['desired_count']} task(s) "
                f"using {spec['task_definition']} with CPU {spec['cpu']} and {spec['memory']} MB memory."
            ),
            data=spec,
        )
    if intent.kind == "get_status":
        status = _run(manager.get_status)
        labels = {"stable": "stable - the last deployment completed", "in_progress": "a deployment is in progress", "failed": "the last deployment FAILED"}
        return ChatResponse(
            status="ok",
            message=(
                f"Service is {labels[status['overall']]}. Running {status['running_count']}/{status['desired_count']} "
                f"task(s) on {status['task_definition']}."
            ),
            data=status,
        )

    # A change: validate it against the live service, then ask for confirmation.
    plan, spec = _plan(
        intent.kind,
        manager,
        settings,
        desired_count=intent.desired_count,
        count_delta=intent.count_delta,
        cpu=intent.cpu,
        memory=intent.memory,
    )
    notes = (" " + " ".join(intent.notes)) if intent.notes else ""
    if not plan.ok:
        return ChatResponse(status="rejected", message=plan.message + notes, data={"current": spec})
    return ChatResponse(
        status="needs_confirmation",
        message=plan.message + notes + " Confirm to apply this change.",
        action=ChangeAction(**plan.action),
        data={"current": spec},
    )


@control.post("/chat/confirm", response_model=ChatResponse)
def confirm(request: ConfirmRequest, manager=Depends(get_manager), settings: Settings = Depends(get_settings)) -> ChatResponse:
    action = request.action
    # Re-validate against the live service: it may have changed since the user asked.
    plan, _ = _plan(action.action, manager, settings, **action.model_dump(exclude={"action"}))
    if not plan.ok:
        return ChatResponse(status="rejected", message=plan.message)

    if action.action == "update_desired_count":
        result = _run(manager.update_desired_count, action.desired_count)
        message = f"Desired count updated to {result['desired_count']}. ECS is starting/stopping tasks now."
    else:
        result = _run(manager.update_task_size, action.cpu, action.memory)
        message = (
            f"Registered {result['new_task_definition']} (CPU {result['cpu']}, {result['memory']} MB) "
            f"and started a rolling deployment from {result['previous_task_definition']}."
        )
    return ChatResponse(status="started", message=message, action=action, data=result)


# ---------- app ----------

def create_app(control_api_enabled: bool | None = None) -> FastAPI:
    enabled = get_settings().control_api_enabled if control_api_enabled is None else control_api_enabled
    app = FastAPI(title=APP_NAME, version=APP_VERSION, description="AI-powered AWS ECS Service Manager")

    @app.get("/")
    def root() -> dict:
        return {
            "service": APP_NAME,
            "version": APP_VERSION,
            "status": "running",
            # On ECS each task has its own hostname, so refreshing the page behind the
            # load balancer shows which task answered (useful when demoing scaling).
            "host": socket.gethostname(),
            "control_api": enabled,
            "endpoints": ["/", "/health", "/docs"] + (["/service/spec", "/service/status", "/chat", "/chat/confirm"] if enabled else []),
        }

    @app.get("/health")
    def health() -> dict:
        return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}

    if enabled:
        app.include_router(control)
    return app


app = create_app()
