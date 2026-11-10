"""OpenAI interpreter with a fake OpenAI client (no network, no API key)."""

import json
from types import SimpleNamespace

import pytest

from llm_parser import INTENT_SCHEMA, LLMParser, LLMParserError, intent_from_llm_output


class FakeCompletions:
    def __init__(self, content=None, error=None):
        self.content, self.error, self.calls = content, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        message = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_client(payload=None, error=None, raw=None):
    content = raw if raw is not None else json.dumps(payload)
    completions = FakeCompletions(content, error)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def out(kind, **values):
    base = {"kind": kind, "desired_count": None, "count_delta": None, "cpu": None, "memory": None, "message": ""}
    base.update(values)
    return base


def test_sends_strict_schema_and_parses_count():
    client, completions = fake_client(out("update_desired_count", desired_count=3))
    intent = LLMParser("key", "test-model", client=client).parse("bump it to three containers")
    assert intent.kind == "update_desired_count" and intent.desired_count == 3
    call = completions.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["schema"] == INTENT_SCHEMA
    assert call["messages"][-1] == {"role": "user", "content": "bump it to three containers"}


def test_task_size_and_relative_count():
    client, _ = fake_client(out("update_task_size", cpu=512, memory=1024))
    intent = LLMParser("k", "m", client=client).parse("half a vCPU and 1 gig")
    assert (intent.cpu, intent.memory) == (512, 1024)

    client, _ = fake_client(out("update_desired_count", count_delta=-1))
    intent = LLMParser("k", "m", client=client).parse("one fewer task")
    assert intent.desired_count is None and intent.count_delta == -1


def test_api_failure_raises_parser_error():
    client, _ = fake_client(error=RuntimeError("401 invalid api key"))
    with pytest.raises(LLMParserError):
        LLMParser("k", "m", client=client).parse("scale to 2")


def test_non_json_output_raises_parser_error():
    client, _ = fake_client(raw="sure! scaling now")
    with pytest.raises(LLMParserError):
        LLMParser("k", "m", client=client).parse("scale to 2")


@pytest.mark.parametrize(
    "data, kind",
    [
        ({"kind": "drop_database"}, "clarify"),                     # unknown action
        ("not a dict", "clarify"),
        (out("update_desired_count"), "clarify"),                    # no value
        (out("update_desired_count", desired_count=-2), "clarify"),  # negative
        (out("update_task_size"), "clarify"),                        # no cpu/memory
        (out("update_task_size", cpu=-256), "clarify"),
        (out("update_desired_count", desired_count=True), "clarify"),  # bool is not a count
        (out("unsupported", message="Deleting is not supported."), "unsupported"),
        (out("help"), "help"),
        (out("get_status"), "get_status"),
    ],
)
def test_malformed_or_special_outputs(data, kind):
    assert intent_from_llm_output(data).kind == kind
