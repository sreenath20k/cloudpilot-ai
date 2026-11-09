"""Validation of requested changes against limits and Fargate rules."""

from config import Settings
from validation import is_valid_fargate_combo, plan_desired_count, plan_task_size

SETTINGS = Settings(
    aws_region="ap-southeast-2",
    ecs_cluster="c",
    ecs_service="s",
    control_api_enabled=True,
    min_desired_count=0,
    max_desired_count=5,
    max_cpu=1024,
    max_memory=4096,
)


def test_fargate_combinations():
    assert is_valid_fargate_combo(256, 512)
    assert is_valid_fargate_combo(512, 1024)
    assert not is_valid_fargate_combo(256, 4096)
    assert not is_valid_fargate_combo(512, 512)
    assert not is_valid_fargate_combo(300, 1024)


def test_desired_count_ok():
    plan = plan_desired_count(1, 3, None, SETTINGS)
    assert plan.ok
    assert plan.action == {"action": "update_desired_count", "desired_count": 3}


def test_desired_count_relative():
    plan = plan_desired_count(2, None, -1, SETTINGS)
    assert plan.ok and plan.action["desired_count"] == 1


def test_desired_count_rejections():
    assert not plan_desired_count(1, -1, None, SETTINGS).ok          # negative
    assert not plan_desired_count(1, 6, None, SETTINGS).ok           # above limit
    assert not plan_desired_count(1, 1, None, SETTINGS).ok           # no change
    assert not plan_desired_count(0, None, -1, SETTINGS).ok          # relative below zero


def test_scale_to_zero_warns():
    plan = plan_desired_count(1, 0, None, SETTINGS)
    assert plan.ok and "WARNING" in plan.message


def test_task_size_ok():
    plan = plan_task_size(256, 512, 512, 1024, SETTINGS)
    assert plan.ok
    assert plan.action == {"action": "update_task_size", "cpu": 512, "memory": 1024}


def test_task_size_keeps_unspecified_value():
    plan = plan_task_size(256, 512, None, 1024, SETTINGS)
    assert plan.ok and plan.action["cpu"] == 256 and plan.action["memory"] == 1024


def test_invalid_combination_suggests_valid_memory():
    plan = plan_task_size(256, 512, 512, None, SETTINGS)  # 512 CPU needs >= 1024 MB
    assert not plan.ok
    assert "1024" in plan.message


def test_task_size_rejections():
    assert not plan_task_size(256, 512, 300, None, SETTINGS).ok       # not a Fargate CPU value
    assert not plan_task_size(256, 512, 2048, 4096, SETTINGS).ok      # above MAX_CPU
    assert not plan_task_size(256, 512, None, 8192, SETTINGS).ok      # above MAX_MEMORY
    assert not plan_task_size(256, 512, 256, 512, SETTINGS).ok        # no change
