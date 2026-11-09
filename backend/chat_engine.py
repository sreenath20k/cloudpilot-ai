"""
Deterministic natural-language parser for ECS requests.

Converts plain English into a structured Intent, e.g.
    "Increase the desired task count to 3"  ->  Intent(kind="update_desired_count", desired_count=3)

No LLM is used: every supported phrasing is matched by explicit rules, so the system can
never invent an AWS operation. Anything not understood is answered with a clarification
or an "unsupported" message instead of a guess.
"""

import re
from dataclasses import dataclass, field

HELP_TEXT = (
    "I can manage the CloudPilot ECS service. Try:\n"
    "- \"Show the current configuration\"\n"
    "- \"Check deployment status\"\n"
    "- \"Scale to 3 tasks\" / \"Add 1 task\" / \"Scale down to 1 task\"\n"
    "- \"Set CPU to 512 and memory to 1024 MB\"\n"
    "- \"Increase memory to 2 GB\"\n\n"
    "Every change is validated and needs your confirmation before it runs."
)

NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}

NUM = r"-?\d+(?:\.\d+)?"

CPU_WORDS = r"\b(?:cpu|cpus|vcpu|vcpus|cores?)\b"
MEMORY_WORDS = r"\b(?:memory|mem|ram)\b"
COUNT_SIGNAL = (
    r"\b(?:desired count|task count|number of tasks|replicas?|instances?)\b"
    rf"|{NUM}\s*(?:tasks?|instances?|replicas?|containers?|copies)\b"
    r"|\b(?:add|remove)\b.*\btasks?\b"
)
SCALE_WORDS = r"\bscale\b"
READ_WORDS = r"\b(?:show|what|what's|whats|display|describe|get|check|list|view|tell|current|see)\b"
STATUS_WORDS = r"\b(?:status|deployment|deployments|rollout|health|healthy|progress|deploying|running)\b"
SPEC_WORDS = r"\b(?:config|configuration|spec|specification|settings|details|setup|cpu|memory|tasks?)\b"
HELP_WORDS = r"^\s*(?:help|\?|what can you do\??|commands|examples)\s*$"
DESTRUCTIVE_WORDS = r"\b(?:delete|destroy|terminate|drop|kill)\b"
UNSUPPORTED_TOPICS = {
    r"\bimage\b|\bdocker\b|\btag\b": "changing the container image",
    r"\bport\b|\bsecurity group\b|\bfirewall\b|\bssh\b": "networking or security group changes",
    r"\benv(?:ironment)? ?var": "environment variables",
    r"\bauto ?scal": "auto scaling policies",
    r"\bregion\b": "moving the service to another region",
    r"\bdomain\b|\bssl\b|\bhttps\b|\bcertificate\b": "domains or HTTPS certificates",
    r"\bstorage\b|\bdisk\b|\bvolume\b": "storage changes",
}
UP_WORDS = r"\b(?:up|increase|add|more|raise|grow)\b"
DOWN_WORDS = r"\b(?:down|decrease|reduce|remove|fewer|less|lower|shrink)\b"


@dataclass
class Intent:
    kind: str  # help | get_spec | get_status | update_desired_count | update_task_size | clarify | unsupported
    desired_count: int | None = None
    count_delta: int | None = None
    cpu: int | None = None
    memory: int | None = None
    message: str = ""
    notes: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("’", "'")
    for word, digit in NUMBER_WORDS.items():
        text = re.sub(rf"\b{word}\b", digit, text)
    # "1024mb" -> "1024 mb", "2gb" -> "2 gb"
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    return re.sub(r"\s+", " ", text)


def _numbers(text: str) -> list[float]:
    return [float(n) for n in re.findall(NUM, text)]


def _is_whole(value: float) -> bool:
    return abs(value - round(value)) < 1e-9


def _parse_cpu(segment: str, notes: list[str]) -> tuple[int | None, str | None]:
    """Return (cpu_units, error). Accepts '512', '0.5 vcpu', '1 vcpu'."""
    nums = _numbers(segment)
    if not nums:
        return None, "What CPU value should I set? For example: 'set cpu to 512' (512 = 0.5 vCPU)."
    if len(set(nums)) > 1:
        return None, "I found more than one CPU value in that request. Please give a single value."
    value = nums[0]
    if value <= 0:
        return None, "CPU must be a positive value."
    says_vcpu = re.search(r"\b(?:vcpus?|cores?)\b", segment) is not None
    if says_vcpu or value <= 16:
        units = value * 1024
        if not _is_whole(units):
            return None, f"{value} vCPU is not a valid CPU size."
        notes.append(f"Interpreted {value:g} vCPU as {int(units)} CPU units.")
        return int(units), None
    if not _is_whole(value):
        return None, f"{value} is not a valid CPU value."
    return int(value), None


def _parse_memory(segment: str, notes: list[str]) -> tuple[int | None, str | None]:
    """Return (memory_mib, error). Accepts '1024', '1024 mb', '2 gb', '0.5 gb'."""
    nums = _numbers(segment)
    if not nums:
        return None, "What memory size should I set? For example: 'set memory to 1024 MB'."
    if len(set(nums)) > 1:
        return None, "I found more than one memory value in that request. Please give a single value."
    value = nums[0]
    if value <= 0:
        return None, "Memory must be a positive value."
    if re.search(r"\b(?:gb|gib|g)\b", segment):
        mib = value * 1024
    elif re.search(r"\b(?:mb|mib|m)\b", segment):
        mib = value
    elif value < 100:
        mib = value * 1024
        notes.append(f"No unit given - interpreted {value:g} as {value:g} GB ({int(mib)} MB).")
    else:
        mib = value
    if not _is_whole(mib):
        return None, f"{value:g} does not convert to a whole number of MB."
    return int(mib), None


def _parse_task_size(text: str) -> Intent:
    notes: list[str] = []
    cpu = memory = None
    # Split "set cpu to 512 and memory to 1024" into parts, one setting per part.
    segments = [s for s in re.split(r"\band\b|,|;|&|\bwith\b", text) if s.strip()]
    for segment in segments:
        has_cpu = re.search(CPU_WORDS, segment)
        has_mem = re.search(MEMORY_WORDS, segment)
        if has_cpu and has_mem:
            return Intent("clarify", message="Please give CPU and memory as separate values, e.g. 'set cpu to 512 and memory to 1024'.")
        if has_cpu:
            if re.search(rf"\bby\s+{NUM}", segment):
                return Intent("clarify", message="Please give the exact CPU value you want (e.g. 'set cpu to 512') rather than an amount to add.")
            cpu, error = _parse_cpu(segment, notes)
            if error:
                return Intent("clarify", message=error)
        elif has_mem:
            if re.search(rf"\bby\s+{NUM}", segment):
                return Intent("clarify", message="Please give the exact memory size you want (e.g. 'set memory to 2048 MB') rather than an amount to add.")
            memory, error = _parse_memory(segment, notes)
            if error:
                return Intent("clarify", message=error)
    if cpu is None and memory is None:
        return Intent("clarify", message="Which value should I change? For example: 'set cpu to 512' or 'set memory to 2 GB'.")
    return Intent("update_task_size", cpu=cpu, memory=memory, notes=notes)


def _parse_count(text: str) -> Intent:
    is_up = re.search(UP_WORDS, text) is not None
    is_down = re.search(DOWN_WORDS, text) is not None

    # Relative: "scale up by 2", "add 1 task", "remove 2 tasks"
    by_match = re.search(rf"\bby\s+({NUM})", text)
    add_match = re.search(rf"\b(?:add|remove)\s+({NUM})\s*(?:more\s+)?(?:tasks?|instances?|replicas?|containers?)\b", text)
    rel = by_match or add_match
    if rel:
        value = float(rel.group(1))
        if not _is_whole(value) or value <= 0:
            return Intent("clarify", message="Please give a positive whole number of tasks to add or remove.")
        if is_up == is_down:
            return Intent("clarify", message="Should I add or remove tasks? For example: 'scale up by 1' or 'scale down by 1'.")
        delta = int(value) if is_up else -int(value)
        return Intent("update_desired_count", count_delta=delta)

    # Absolute: "to 3", "3 tasks", "desired count = 3"
    candidates = re.findall(rf"\bto\s+({NUM})", text)
    candidates += re.findall(rf"({NUM})\s*(?:tasks?|instances?|replicas?|containers?|copies)\b", text)
    candidates += re.findall(rf"\b(?:count|replicas)\s*(?:=|of|is)\s*({NUM})", text)
    values = {float(v) for v in candidates}
    if not values:
        if is_up or is_down:
            return Intent("clarify", message="How many tasks should the service run? For example: 'scale to 3 tasks' or 'scale up by 1'.")
        return Intent("clarify", message="How many tasks should the service run? For example: 'set desired count to 2'.")
    if len(values) > 1:
        return Intent("clarify", message="I found more than one task count in that request. Please give a single number.")
    value = values.pop()
    if value < 0:
        return Intent("clarify", message=f"The task count cannot be negative ({value:g}). Please give 0 or more.")
    if not _is_whole(value):
        return Intent("clarify", message=f"The task count must be a whole number, not {value:g}.")
    return Intent("update_desired_count", desired_count=int(value))


def parse_request(raw: str) -> Intent:
    """Convert a plain-English request into an Intent."""
    if not raw or not raw.strip():
        return Intent("clarify", message="Please type a request. " + HELP_TEXT)

    text = _normalize(raw)

    if re.search(HELP_WORDS, text):
        return Intent("help", message=HELP_TEXT)

    if re.search(DESTRUCTIVE_WORDS, text):
        return Intent(
            "unsupported",
            message="Deleting or destroying resources is not supported from chat. "
            "Infrastructure is removed with 'terraform destroy'. " + HELP_TEXT,
        )

    for pattern, label in UNSUPPORTED_TOPICS.items():
        if re.search(pattern, text):
            return Intent("unsupported", message=f"Sorry, I can't handle {label} from chat. " + HELP_TEXT)

    has_number = bool(re.search(NUM, text))
    mentions_cpu = re.search(CPU_WORDS, text) is not None
    mentions_mem = re.search(MEMORY_WORDS, text) is not None
    mentions_count = re.search(COUNT_SIGNAL, text) is not None
    mentions_scale = re.search(SCALE_WORDS, text) is not None
    is_read = re.search(READ_WORDS, text) is not None
    change_verb = re.search(r"\b(?:set|change|update|make|increase|decrease|reduce|raise|lower|scale|add|remove|run|use|bump|allocate)\b", text)

    # Read-only requests: "show the current configuration", "check deployment status"
    if (is_read or not change_verb) and not has_number:
        if re.search(STATUS_WORDS, text):
            return Intent("get_status")
        if is_read and re.search(SPEC_WORDS, text):
            return Intent("get_spec")
        if text in {"spec", "config", "configuration"}:
            return Intent("get_spec")

    # Task size (CPU / memory)
    if mentions_cpu or mentions_mem:
        if mentions_count and has_number:
            return Intent("clarify", message="Please make one kind of change at a time: either the task count, or the CPU/memory.")
        return _parse_task_size(text)

    # Task count
    if mentions_count or mentions_scale:
        return _parse_count(text)

    if re.search(STATUS_WORDS, text):
        return Intent("get_status")

    return Intent(
        "clarify",
        message="Sorry, I didn't understand that request. " + HELP_TEXT,
    )
