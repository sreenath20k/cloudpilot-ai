"""Natural-language parsing: valid, invalid, ambiguous and unsupported requests."""

import pytest

from chat_engine import parse_request


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Increase the desired task count to 3", 3),
        ("Scale down the service to 1 task", 1),
        ("run three tasks", 3),
        ("set desired count to 0", 0),
        ("Scale to 2 tasks", 2),
    ],
)
def test_absolute_task_count(text, expected):
    intent = parse_request(text)
    assert intent.kind == "update_desired_count"
    assert intent.desired_count == expected


@pytest.mark.parametrize(
    "text, delta",
    [("scale up by 2", 2), ("add one task", 1), ("remove 1 task", -1), ("scale down by 1", -1)],
)
def test_relative_task_count(text, delta):
    intent = parse_request(text)
    assert intent.kind == "update_desired_count"
    assert intent.count_delta == delta


@pytest.mark.parametrize(
    "text, cpu, memory",
    [
        ("Change the container CPU allocation to 256", 256, None),
        ("Increase memory allocation to 1024 MB", None, 1024),
        ("set cpu to 512 and memory to 1024", 512, 1024),
        ("increase memory to 2 GB", None, 2048),
        ("set cpu to 0.5 vcpu", 512, None),
        ("set cpu to 1 vcpu and memory to 2gb", 1024, 2048),
    ],
)
def test_task_size(text, cpu, memory):
    intent = parse_request(text)
    assert intent.kind == "update_task_size"
    assert intent.cpu == cpu
    assert intent.memory == memory


@pytest.mark.parametrize(
    "text, kind",
    [
        ("Show the current ECS service configuration", "get_spec"),
        ("what's the current cpu?", "get_spec"),
        ("Check deployment status", "get_status"),
        ("is it healthy?", "get_status"),
        ("status", "get_status"),
        ("help", "help"),
    ],
)
def test_read_requests(text, kind):
    assert parse_request(text).kind == kind


@pytest.mark.parametrize(
    "text",
    [
        "",
        "make it faster",
        "scale up",
        "increase cpu",
        "scale to 3 or 4 tasks",
        "scale to -1",
        "scale to 2.5 tasks",
        "set cpu to 512 and scale to 3 tasks",
        "increase memory by 512",
    ],
)
def test_unclear_or_invalid_requests_ask_for_clarification(text):
    intent = parse_request(text)
    assert intent.kind == "clarify"
    assert intent.message


@pytest.mark.parametrize(
    "text",
    ["delete the service", "destroy everything", "change the image to nginx", "open port 22", "add an environment variable"],
)
def test_unsupported_requests(text):
    intent = parse_request(text)
    assert intent.kind == "unsupported"
    assert "not" in intent.message.lower() or "can't" in intent.message.lower()
