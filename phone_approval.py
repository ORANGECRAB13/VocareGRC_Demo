"""Phone-call approvals for Claude Code sessions.

A Claude Code PreToolUse hook posts a pending action here; the server dials the
approver through the existing Telnyx Call Control application, an ElevenLabs
voice agent reads the request aloud and captures an approve/deny decision, and
the hook polls until the decision lands (or the request times out).

This module owns the approval store and the Telnyx outbound-dial plumbing.
The voice agent itself lives in bot.py next to the other Telnyx pipelines so it
can reuse the same STT/LLM/TTS factories.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import time
import uuid
from typing import Optional

import aiohttp
from loguru import logger

# How long a request may sit unanswered before the hook should give up on it.
APPROVAL_TTL_SECONDS = 180

_approvals: dict[str, dict] = {}
_by_call_control: dict[str, str] = {}


def _secret() -> str:
    return os.getenv("APPROVAL_SHARED_SECRET", "").strip()


def check_secret(provided: Optional[str]) -> bool:
    configured = _secret()
    if not configured:
        return False
    return hmac.compare_digest(configured, (provided or "").strip())


def approver_number() -> str:
    return (
        os.getenv("APPROVAL_PHONE_NUMBER", "").strip()
        or os.getenv("TRANSFER_PHONE_NUMBER", "").strip()
    )


def encode_client_state(approval_id: str) -> str:
    raw = json.dumps({"kind": "approval", "id": approval_id}).encode()
    return base64.b64encode(raw).decode()


def decode_client_state(state: Optional[str]) -> Optional[str]:
    """Return the approval id if this client_state marks an approval call."""
    if not state:
        return None
    try:
        data = json.loads(base64.b64decode(state))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("kind") == "approval":
        return data.get("id")
    return None


def get(approval_id: str) -> Optional[dict]:
    record = _approvals.get(approval_id)
    if record and record["status"] in ("pending", "calling"):
        if time.monotonic() - record["created_monotonic"] > APPROVAL_TTL_SECONDS:
            record["status"] = "no_answer"
            record["detail_status"] = "timed out waiting for a decision"
    return record


def get_by_call_control(call_control_id: Optional[str]) -> Optional[dict]:
    if not call_control_id:
        return None
    approval_id = _by_call_control.get(call_control_id)
    return _approvals.get(approval_id) if approval_id else None


def bind_call(approval_id: str, call_control_id: str) -> None:
    _by_call_control[call_control_id] = approval_id
    record = _approvals.get(approval_id)
    if record:
        record["call_control_id"] = call_control_id


def record_decision(approval_id: str, decision: str, spoken: str = "") -> bool:
    record = _approvals.get(approval_id)
    if not record or record["status"] not in ("pending", "calling"):
        return False
    record["status"] = "approved" if decision == "approve" else "denied"
    record["decided_at"] = time.time()
    record["spoken"] = spoken
    logger.info(f"[Approval:{approval_id}] decision recorded: {record['status']}")
    return True


def mark_call_ended(approval_id: str) -> None:
    record = _approvals.get(approval_id)
    if record and record["status"] in ("pending", "calling"):
        record["status"] = "no_answer"
        record["detail_status"] = "call ended without a decision"
        logger.info(f"[Approval:{approval_id}] call ended with no decision")


async def create_and_dial(title: str, detail: str) -> dict:
    """Create an approval record and place the outbound Telnyx call."""
    api_key = os.getenv("TELNYX_API_KEY", "").strip()
    app_id = os.getenv("TELNYX_CALL_CONTROL_APP_ID", "").strip()
    from_number = os.getenv("TELNYX_PHONE_NUMBER", "").strip()
    to_number = approver_number()
    missing = [
        name
        for name, value in [
            ("TELNYX_API_KEY", api_key),
            ("TELNYX_CALL_CONTROL_APP_ID", app_id),
            ("TELNYX_PHONE_NUMBER", from_number),
            ("APPROVAL_PHONE_NUMBER", to_number),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Phone approval is not configured — missing {', '.join(missing)}")

    approval_id = uuid.uuid4().hex[:12]
    record = {
        "id": approval_id,
        "title": title,
        "detail": detail,
        "status": "calling",
        "detail_status": "",
        "created_at": time.time(),
        "created_monotonic": time.monotonic(),
        "call_control_id": None,
        "spoken": "",
    }
    _approvals[approval_id] = record

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.telnyx.com/v2/calls",
            json={
                "connection_id": app_id,
                "to": to_number,
                "from": from_number,
                "client_state": encode_client_state(approval_id),
                # Give up ringing before the record itself times out.
                "timeout_secs": 40,
            },
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            body = await resp.text()
            if resp.status not in (200, 201, 202):
                record["status"] = "error"
                record["detail_status"] = f"dial failed: {resp.status}"
                logger.error(f"[Approval:{approval_id}] dial failed: {resp.status} {body[:200]}")
                raise RuntimeError(f"Telnyx dial failed with status {resp.status}")
            try:
                call_control_id = json.loads(body)["data"]["call_control_id"]
                bind_call(approval_id, call_control_id)
            except Exception:
                logger.warning(f"[Approval:{approval_id}] could not parse call_control_id")

    logger.info(f"[Approval:{approval_id}] dialing {to_number[-4:].rjust(len(to_number), '*')}")
    return record
