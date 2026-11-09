"""
CloudPilot AI - chat interface (Streamlit).

Talks to the local FastAPI backend (CONTROL_API_ENABLED=true), which talks to AWS ECS.
Run:  streamlit run app.py
"""

import os
import time

import requests
import streamlit as st

API_URL = os.getenv("CLOUDPILOT_API_URL", "http://127.0.0.1:8000").rstrip("/")
APP_URL = os.getenv("CLOUDPILOT_APP_URL", "")  # public ALB URL (optional, shown in the sidebar)
POLL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 420

EXAMPLES = [
    "Show the current configuration",
    "Check deployment status",
    "Scale to 2 tasks",
    "Scale down to 1 task",
    "Set CPU to 512 and memory to 1024 MB",
    "Set CPU to 256 and memory to 512 MB",
]

st.set_page_config(page_title="CloudPilot AI", page_icon="☁️", layout="wide")


# ---------- backend helpers ----------

def api(method: str, path: str, payload: dict | None = None, timeout: int = 30) -> tuple[bool, dict]:
    """Call the backend. Returns (ok, body). Never raises."""
    try:
        response = requests.request(method, f"{API_URL}{path}", json=payload, timeout=timeout)
    except requests.RequestException:
        return False, {"message": f"Cannot reach the backend at {API_URL}. Is uvicorn running with CONTROL_API_ENABLED=true?"}
    try:
        body = response.json()
    except ValueError:
        body = {"message": response.text}
    if response.ok:
        return True, body
    detail = body.get("detail", body)
    if isinstance(detail, dict):
        message = detail.get("message", str(detail))
    elif isinstance(detail, list):  # FastAPI validation errors
        message = "; ".join(d.get("msg", str(d)) for d in detail)
    else:
        message = str(detail)
    return False, {"message": f"Error {response.status_code}: {message}"}


def add_message(role: str, content: str, kind: str = "info", data: dict | None = None) -> None:
    st.session_state.messages.append({"role": role, "content": content, "kind": kind, "data": data})


def render_message(msg: dict) -> None:
    with st.chat_message(msg["role"], avatar="🧑" if msg["role"] == "user" else "☁️"):
        kind = msg.get("kind", "info")
        if msg["role"] == "user" or kind == "info":
            st.markdown(msg["content"])
        elif kind == "success":
            st.success(msg["content"], icon="✅")
        elif kind == "error":
            st.error(msg["content"], icon="❌")
        elif kind == "warning":
            st.warning(msg["content"], icon="⚠️")
        elif kind == "confirm":
            st.info(msg["content"], icon="❓")
        if msg.get("data"):
            with st.expander("Details"):
                st.json(msg["data"])


# ---------- conversation logic ----------

KIND_FOR_STATUS = {
    "ok": "info",
    "clarification": "warning",
    "rejected": "warning",
    "unsupported": "warning",
    "needs_confirmation": "confirm",
    "started": "success",
}


def handle_user_text(text: str) -> None:
    add_message("user", text)

    # "yes"/"no" while a change is waiting for confirmation
    if st.session_state.pending:
        answer = text.strip().lower()
        if answer in {"yes", "y", "confirm", "ok", "go", "apply", "do it"}:
            apply_pending()
            return
        if answer in {"no", "n", "cancel", "stop", "abort"}:
            cancel_pending()
            return
        st.session_state.pending = None  # a new request replaces the old pending one
        add_message("assistant", "Previous change cancelled - handling your new request.", "warning")

    ok, body = api("POST", "/chat", {"message": text})
    if not ok:
        add_message("assistant", body["message"], "error")
        return
    status = body.get("status")
    data = body.get("data")
    add_message("assistant", body.get("message", ""), KIND_FOR_STATUS.get(status, "info"), data if status == "ok" else None)
    if status == "needs_confirmation":
        st.session_state.pending = body["action"]


def cancel_pending() -> None:
    st.session_state.pending = None
    add_message("assistant", "Cancelled. No changes were made.", "info")


def apply_pending() -> None:
    action = st.session_state.pending
    st.session_state.pending = None
    ok, body = api("POST", "/chat/confirm", {"action": action})
    if not ok:
        add_message("assistant", f"The change was NOT applied. {body['message']}", "error")
        return
    if body.get("status") != "started":
        add_message("assistant", body.get("message", "The change was not applied."), "warning")
        return
    add_message("assistant", body["message"], "info", body.get("data"))
    st.session_state.watch_deployment = True


def watch_deployment() -> None:
    """Poll the ECS service until the deployment finishes, fails or times out."""
    st.session_state.watch_deployment = False
    with st.status("Waiting for ECS to apply the change...", expanded=True) as status_box:
        deadline = time.time() + POLL_TIMEOUT_SECONDS
        last = {}
        while time.time() < deadline:
            ok, last = api("GET", "/service/status")
            if not ok:
                status_box.update(label="Could not read deployment status", state="error")
                add_message("assistant", last["message"], "error")
                return
            st.write(
                f"Running {last['running_count']}/{last['desired_count']} task(s) · "
                f"{last['task_definition']} · state: **{last['overall']}**"
            )
            if last["overall"] == "stable" and last["running_count"] == last["desired_count"]:
                status_box.update(label="Deployment completed", state="complete")
                add_message(
                    "assistant",
                    f"Success: the service is stable with {last['running_count']} running task(s) on {last['task_definition']}.",
                    "success",
                    last,
                )
                return
            if last["overall"] == "failed":
                status_box.update(label="Deployment failed", state="error")
                reason = next((d.get("rollout_reason") for d in last["deployments"] if d["status"] == "PRIMARY"), "")
                add_message(
                    "assistant",
                    f"The deployment FAILED. ECS rolls back automatically to the last working revision. {reason or ''}",
                    "error",
                    last,
                )
                return
            time.sleep(POLL_SECONDS)
        status_box.update(label="Still in progress", state="running")
        add_message(
            "assistant",
            "The change was submitted but has not finished yet. Ask 'check deployment status' in a minute.",
            "warning",
            last,
        )


# ---------- page ----------

if "messages" not in st.session_state:
    st.session_state.messages = []
    st.session_state.pending = None
    st.session_state.watch_deployment = False
    add_message(
        "assistant",
        "Hi! I manage the **cloudpilot-ai** ECS service. Ask me to show its configuration, "
        "check a deployment, scale the number of tasks, or change CPU/memory. "
        "I'll validate every change and ask before applying it.",
    )

with st.sidebar:
    st.title("☁️ CloudPilot AI")
    st.caption("AI-powered AWS ECS Service Manager")
    st.subheader("Current service")
    ok, spec = api("GET", "/service/spec", timeout=15)
    if ok:
        c1, c2 = st.columns(2)
        c1.metric("Desired tasks", spec["desired_count"])
        c2.metric("Running tasks", spec["running_count"])
        c1.metric("CPU units", spec["cpu"])
        c2.metric("Memory (MB)", spec["memory"])
        st.caption(f"Cluster: `{spec['cluster']}`")
        st.caption(f"Service: `{spec['service']}`")
        st.caption(f"Task definition: `{spec['task_definition']}`")
        st.caption(f"Region: `{spec['region']}`")
    else:
        st.error(spec["message"])
    if APP_URL:
        st.markdown(f"[Open the running service ↗]({APP_URL})")
    st.button("Refresh", use_container_width=True)
    st.divider()
    st.subheader("Try")
    for example in EXAMPLES:
        if st.button(example, key=f"ex-{example}", use_container_width=True):
            st.session_state.queued = example

st.title("Chat with your ECS service")

queued = st.session_state.pop("queued", None)
typed = st.chat_input("e.g. Scale to 2 tasks")
if queued or typed:
    handle_user_text(queued or typed)

for message in st.session_state.messages:
    render_message(message)

if st.session_state.pending:
    left, right, _ = st.columns([1, 1, 4])
    if left.button("✅ Confirm", type="primary", use_container_width=True):
        add_message("user", "Confirm")
        apply_pending()
        st.rerun()
    if right.button("✖ Cancel", use_container_width=True):
        add_message("user", "Cancel")
        cancel_pending()
        st.rerun()

if st.session_state.watch_deployment:
    watch_deployment()
    st.rerun()
