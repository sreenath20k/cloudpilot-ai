"""
Validation of requested ECS changes.

Turns a parsed chat intent into a concrete, safe action - or a clear explanation
of why it cannot be applied. Nothing here talks to AWS.
"""

from dataclasses import dataclass, field

from config import Settings

# Valid AWS Fargate CPU (units) -> memory (MiB) combinations.
# Source: AWS ECS docs, "Task size" for Fargate (Linux).
FARGATE_MEMORY_OPTIONS: dict[int, list[int]] = {
    256: [512, 1024, 2048],
    512: list(range(1024, 4096 + 1, 1024)),
    1024: list(range(2048, 8192 + 1, 1024)),
    2048: list(range(4096, 16384 + 1, 1024)),
    4096: list(range(8192, 30720 + 1, 1024)),
    8192: list(range(16384, 61440 + 1, 4096)),
    16384: list(range(32768, 122880 + 1, 8192)),
}


def is_valid_fargate_combo(cpu: int, memory: int) -> bool:
    return memory in FARGATE_MEMORY_OPTIONS.get(cpu, [])


def allowed_cpu_values(settings: Settings) -> list[int]:
    return [c for c in FARGATE_MEMORY_OPTIONS if c <= settings.max_cpu]


def allowed_memory_for_cpu(cpu: int, settings: Settings) -> list[int]:
    return [m for m in FARGATE_MEMORY_OPTIONS.get(cpu, []) if m <= settings.max_memory]


@dataclass
class Plan:
    """Result of validating a requested change."""

    ok: bool
    message: str
    action: dict = field(default_factory=dict)


def plan_desired_count(
    current: int, target: int | None, delta: int | None, settings: Settings
) -> Plan:
    if target is None and delta is not None:
        target = current + delta
    if target is None:
        return Plan(False, "How many tasks should the service run? For example: 'scale to 3 tasks'.")
    if target < 0:
        return Plan(False, f"The task count cannot be negative (requested {target}).")
    if target < settings.min_desired_count or target > settings.max_desired_count:
        return Plan(
            False,
            f"{target} tasks is outside the allowed range "
            f"({settings.min_desired_count}-{settings.max_desired_count}). "
            "The limit keeps costs predictable and is set by MAX_DESIRED_COUNT.",
        )
    if target == current:
        return Plan(False, f"The service is already set to {current} task(s). Nothing to change.")

    direction = "Scale up" if target > current else "Scale down"
    message = f"{direction} the service from {current} to {target} task(s)."
    if target == 0:
        message += " WARNING: with 0 tasks the application URL will stop responding (HTTP 503)."
    return Plan(True, message, {"action": "update_desired_count", "desired_count": target})


def plan_task_size(
    current_cpu: int,
    current_memory: int,
    cpu: int | None,
    memory: int | None,
    settings: Settings,
) -> Plan:
    new_cpu = cpu if cpu is not None else current_cpu
    new_memory = memory if memory is not None else current_memory

    if new_cpu <= 0 or new_memory <= 0:
        return Plan(False, "CPU and memory values must be positive numbers.")

    if new_cpu not in FARGATE_MEMORY_OPTIONS:
        return Plan(
            False,
            f"{new_cpu} is not a valid Fargate CPU value. "
            f"Allowed values here: {', '.join(map(str, allowed_cpu_values(settings)))} "
            "(256 = 0.25 vCPU, 1024 = 1 vCPU).",
        )
    if new_cpu > settings.max_cpu:
        return Plan(
            False,
            f"CPU {new_cpu} is above the configured limit of {settings.max_cpu} (MAX_CPU). "
            f"Allowed values: {', '.join(map(str, allowed_cpu_values(settings)))}.",
        )
    if new_memory > settings.max_memory:
        return Plan(
            False,
            f"Memory {new_memory} MB is above the configured limit of {settings.max_memory} MB (MAX_MEMORY).",
        )
    if not is_valid_fargate_combo(new_cpu, new_memory):
        options = allowed_memory_for_cpu(new_cpu, settings)
        hint = (
            f"With CPU {new_cpu}, memory must be one of: {', '.join(map(str, options))} MB. "
            f"Try: 'set cpu to {new_cpu} and memory to {options[0]}'."
            if options
            else f"No memory size for CPU {new_cpu} fits within the {settings.max_memory} MB limit."
        )
        return Plan(
            False,
            f"CPU {new_cpu} with {new_memory} MB memory is not a valid Fargate combination. {hint}",
        )
    if new_cpu == current_cpu and new_memory == current_memory:
        return Plan(
            False,
            f"The task already uses CPU {current_cpu} and {current_memory} MB memory. Nothing to change.",
        )

    return Plan(
        True,
        f"Change task size from CPU {current_cpu} / {current_memory} MB to "
        f"CPU {new_cpu} / {new_memory} MB. This registers a new task definition revision "
        "and starts a rolling deployment (about 2-5 minutes; the old tasks keep serving until "
        "the new ones are healthy).",
        {"action": "update_task_size", "cpu": new_cpu, "memory": new_memory},
    )
