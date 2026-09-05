"""Voice control channel for Claude Code sessions.

Inbound: when the approver calls the Telnyx number, the call is routed to a
control agent (instead of the public GRC bot). Spoken instructions become
queued tasks.

A worker on the developer's machine polls /api/voicetask/next, runs each task
through a headless Claude Code session, and posts the result back. Completion
triggers an outbound "report" call that reads the outcome aloud and accepts
follow-up instructions.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from typing import Optional

from loguru import logger

_tasks: dict[str, dict] = {}
_order: list[str] = []
_control_calls: set[str] = set()
_report_calls: dict[str, str] = {}  # call_control_id -> task_id

# Named Claude sessions. The server only tracks the active NAME; the worker
# owns the mapping from names to real Claude Code session ids.
_active_session = "main"
_session_names: set[str] = {"main"}

# Outbound feedback calls to registered contacts.
_feedback: dict[str, dict] = {}
_feedback_calls: dict[str, str] = {}  # call_control_id -> feedback_id (the contact leg)
_feedback_reports: dict[str, str] = {}  # call_control_id -> feedback_id (the report leg)


def contacts() -> dict[str, str]:
    """Registered contacts {lowercase name: E.164 number} from FEEDBACK_CONTACTS."""
    try:
        raw = json.loads(os.getenv("FEEDBACK_CONTACTS", "{}"))
        return {str(k).lower(): str(v) for k, v in raw.items()}
    except Exception:
        return {}


def resolve_contact(name: str) -> Optional[tuple[str, str]]:
    """Fuzzy-match a spoken name to a registered contact; never invent numbers."""
    spoken = (name or "").lower().strip()
    if not spoken:
        return None
    book = contacts()
    if spoken in book:
        return spoken, book[spoken]
    for known, number in book.items():
        first = known.split()[0]
        if first and (first in spoken or spoken.split()[0] == first):
            return known, number
    return None


def active_session() -> str:
    return _active_session


def switch_session(name: str) -> str:
    global _active_session
    clean = "".join(ch for ch in name.lower().strip() if ch.isalnum() or ch in " -_")[:40].strip()
    if not clean:
        clean = "main"
    _active_session = clean
    _session_names.add(clean)
    logger.info(f"[VoiceControl] active session -> {clean!r}")
    return clean


def session_names() -> list[str]:
    return sorted(_session_names)


def submit_feedback(contact_name: str, number: str, topic: str) -> dict:
    fb_id = uuid.uuid4().hex[:12]
    fb = {
        "id": fb_id,
        "contact": contact_name,
        "number": number,
        "topic": topic.strip(),
        "status": "calling",
        "summary": "",
        "created_at": time.time(),
    }
    _feedback[fb_id] = fb
    logger.info(f"[Feedback:{fb_id}] calling {contact_name} about {topic[:80]!r}")
    return fb


def bind_feedback_call(call_control_id: str, fb_id: str) -> None:
    _feedback_calls[call_control_id] = fb_id


def feedback_for_call(call_control_id: Optional[str]) -> Optional[dict]:
    fb_id = _feedback_calls.get(call_control_id or "")
    return _feedback.get(fb_id) if fb_id else None


def bind_feedback_report(call_control_id: str, fb_id: str) -> None:
    _feedback_reports[call_control_id] = fb_id


def feedback_report_for_call(call_control_id: Optional[str]) -> Optional[dict]:
    fb_id = _feedback_reports.get(call_control_id or "")
    return _feedback.get(fb_id) if fb_id else None


def record_feedback(fb_id: str, summary: str) -> Optional[dict]:
    fb = _feedback.get(fb_id)
    if not fb:
        return None
    fb["summary"] = summary.strip()[:2000]
    fb["status"] = "collected"
    logger.info(f"[Feedback:{fb_id}] collected: {fb['summary'][:100]!r}")
    return fb


def feedback_call_ended(fb_id: str) -> Optional[dict]:
    fb = _feedback.get(fb_id)
    if fb and fb["status"] == "calling":
        fb["status"] = "no_feedback"
        fb["summary"] = fb["summary"] or "The call ended before any feedback was collected."
    return fb


def get_feedback(fb_id: str) -> Optional[dict]:
    return _feedback.get(fb_id)


def encode_state(kind: str, ref: str) -> str:
    return base64.b64encode(json.dumps({"kind": kind, "id": ref}).encode()).decode()


def decode_state(state: Optional[str], kind: str) -> Optional[str]:
    if not state:
        return None
    try:
        data = json.loads(base64.b64decode(state))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("kind") == kind:
        return data.get("id")
    return None


def _normalize(number: str) -> str:
    return "".join(ch for ch in (number or "") if ch.isdigit() or ch == "+")


def is_controller(number: Optional[str]) -> bool:
    approver = _normalize(os.getenv("APPROVAL_PHONE_NUMBER", ""))
    return bool(approver) and _normalize(number or "") == approver


def bind_control_call(call_control_id: str) -> None:
    _control_calls.add(call_control_id)


def is_control_call(call_control_id: Optional[str]) -> bool:
    return call_control_id in _control_calls


def bind_report_call(call_control_id: str, task_id: str) -> None:
    _report_calls[call_control_id] = task_id


def report_task_for_call(call_control_id: Optional[str]) -> Optional[dict]:
    task_id = _report_calls.get(call_control_id or "")
    return _tasks.get(task_id) if task_id else None


def encode_report_state(task_id: str) -> str:
    return base64.b64encode(json.dumps({"kind": "report", "id": task_id}).encode()).decode()


def decode_report_state(state: Optional[str]) -> Optional[str]:
    if not state:
        return None
    try:
        data = json.loads(base64.b64decode(state))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("kind") == "report":
        return data.get("id")
    return None


def submit(instruction: str, source: str = "call", session: Optional[str] = None) -> dict:
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "instruction": instruction.strip(),
        "session": session or _active_session,
        "status": "queued",
        "result": "",
        "source": source,
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
    }
    _tasks[task_id] = task
    _order.append(task_id)
    logger.info(f"[VoiceTask:{task_id}] queued: {instruction[:120]!r}")
    return task


def next_pending() -> Optional[dict]:
    for task_id in _order:
        task = _tasks[task_id]
        if task["status"] == "queued":
            task["status"] = "running"
            task["started_at"] = time.time()
            logger.info(f"[VoiceTask:{task_id}] handed to worker")
            return task
    return None


def finish(task_id: str, ok: bool, result: str) -> Optional[dict]:
    task = _tasks.get(task_id)
    if not task:
        return None
    task["status"] = "done" if ok else "failed"
    task["result"] = result.strip()[:2000]
    task["finished_at"] = time.time()
    logger.info(f"[VoiceTask:{task_id}] {task['status']}: {task['result'][:120]!r}")
    return task


def get(task_id: str) -> Optional[dict]:
    return _tasks.get(task_id)


def latest() -> Optional[dict]:
    return _tasks[_order[-1]] if _order else None
