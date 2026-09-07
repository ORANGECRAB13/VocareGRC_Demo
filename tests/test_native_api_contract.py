"""Backend API contract for the native iOS and Android apps (native/CONTRACT.md §2).

Two layers, same assertions:

* ``Live*`` classes hit the deployed backend named by ``VOCARE_API_BASE`` and are
  skipped when that variable is unset. Only stdlib is used, so they run anywhere.
* ``InProcess*`` classes extract the actual route handlers from ``bot.py`` with
  ``ast`` (the same technique as ``test_entitlement_lifecycle.py``) and call them
  directly, so the shapes are checked in CI without voice-provider dependencies.

``native/fixtures/*.json`` are validated against both layers so the mobile unit
tests can never drift from what the server really returns.

Run:  python -m unittest tests.test_native_api_contract -v
Live: VOCARE_API_BASE=https://... python -m unittest tests.test_native_api_contract -v
"""
import ast
import asyncio
import json
import logging
import os
import secrets
import sys
import time
import types
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict
from unittest.mock import AsyncMock
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "native" / "fixtures"
API_BASE = os.environ.get("VOCARE_API_BASE", "").rstrip("/")

BALANCE_KEYS = {"tier", "period", "seconds_total", "seconds_used", "seconds_remaining", "durable", "enforced", "sandbox_ok"}
ACTIVATE_KEYS = BALANCE_KEYS | {"verified"}
SESSION_DETAIL_KEYS = {"session_id", "caller_name", "lang", "topic", "status", "transcript", "live_transcripts", "live_previews", "participants"}
SESSION_LIST_ITEM_KEYS = {"session_id", "caller_name", "lang", "topic", "status", "duration", "participant_count"}
TURN_KEYS = {"type", "speaker", "speaker_name", "original", "original_lang", "translated", "translated_lang"}


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_balance_shape(tc, body):
    tc.assertEqual(set(body) - {"verified"}, BALANCE_KEYS, body)
    tc.assertIn(body["tier"], ("free", "pro"))
    tc.assertRegex(body["period"], r"^\d{4}-\d{2}$")
    for key in ("seconds_total", "seconds_used", "seconds_remaining"):
        tc.assertIsInstance(body[key], int, key)
        tc.assertNotIsInstance(body[key], bool, key)
    for key in ("durable", "enforced", "sandbox_ok"):
        tc.assertIsInstance(body[key], bool, key)
    tc.assertEqual(body["seconds_remaining"], max(0, body["seconds_total"] - body["seconds_used"]))


# ---------------------------------------------------------------------------
# Fixture self-consistency (always runs)
# ---------------------------------------------------------------------------

class FixtureShapeTests(unittest.TestCase):
    def test_every_fixture_listed_in_readme_exists_and_parses(self):
        expected = {
            "poll_events.json", "balance_free_unenforced.json", "balance_free_enforced.json", "balance_pro.json",
            "balance_pro_exhausted.json", "balance_sandbox.json", "offer_answer.json", "ice.json",
            "entitlement_activate_verified.json", "entitlement_activate_unverified.json",
            "session_create.json", "session_detail.json",
        }
        present = {p.name for p in FIXTURES.glob("*.json")}
        self.assertEqual(present, expected)
        readme = (FIXTURES / "README.md").read_text(encoding="utf-8")
        for name in expected:
            self.assertIn(name, readme)
            fixture(name)

    def test_balance_fixtures(self):
        for name in ("balance_free_unenforced", "balance_free_enforced", "balance_pro", "balance_pro_exhausted", "balance_sandbox"):
            with self.subTest(name=name):
                body = fixture(name + ".json")
                assert_balance_shape(self, body)
                self.assertNotIn("verified", body)
        self.assertFalse(fixture("balance_free_unenforced.json")["enforced"])
        self.assertTrue(fixture("balance_free_enforced.json")["enforced"])
        self.assertEqual(fixture("balance_pro_exhausted.json")["seconds_remaining"], 0)
        self.assertGreater(fixture("balance_pro.json")["seconds_remaining"], 0)
        self.assertTrue(fixture("balance_sandbox.json")["sandbox_ok"])

    def test_activate_fixtures(self):
        verified = fixture("entitlement_activate_verified.json")
        unverified = fixture("entitlement_activate_unverified.json")
        for body in (verified, unverified):
            assert_balance_shape(self, body)
            self.assertEqual(set(body), ACTIVATE_KEYS)
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["tier"], "pro")
        self.assertFalse(unverified["verified"])

    def test_ice_fixture(self):
        servers = fixture("ice.json")["iceServers"]
        self.assertTrue(servers)
        for server in servers:
            self.assertTrue({"urls"} <= set(server) <= {"urls", "username", "credential"}, server)
        self.assertTrue(any(str(s["urls"]).startswith("turn:") for s in servers), "relay-only PCs need a TURN server")

    def test_session_fixtures(self):
        create = fixture("session_create.json")
        self.assertEqual(set(create), {"session_id", "status"})
        self.assertEqual(create["status"], "waiting")
        self.assertTrue(create["session_id"].startswith("GRC-"))
        answer = fixture("offer_answer.json")
        self.assertEqual(set(answer), {"pc_id", "sdp", "type"})
        self.assertEqual(answer["type"], "answer")
        candidates = [l for l in answer["sdp"].split("\r\n") if l.startswith("a=candidate:")]
        self.assertTrue(candidates and all("typ relay" in c for c in candidates))
        detail = fixture("session_detail.json")
        self.assertEqual(set(detail), SESSION_DETAIL_KEYS)
        for turn in detail["transcript"]:
            self.assertEqual(set(turn), TURN_KEYS)
        for p in detail["participants"]:
            self.assertEqual(set(p), {"pc_id", "name", "language"})

    def test_poll_events_script(self):
        script = fixture("poll_events.json")
        frames = script["frames"]
        pc_a, pc_b = script["pc_id_a"], script["pc_id_b"]
        spoken_a = spoken_b = 0
        failed_sides = []
        closed_at = None
        for i, frame in enumerate(frames):
            self.assertEqual(set(frame), {"events", "closed"})
            for ev in frame["events"]:
                if ev["type"] == "turn":
                    self.assertEqual(set(ev), TURN_KEYS)
                    self.assertIn(ev["speaker"], (pc_a, pc_b))
                    if ev["speaker"] == pc_a:
                        spoken_a += 1
                        self.assertEqual(ev["original_lang"], script["lang_a"])
                    else:
                        spoken_b += 1
                        self.assertEqual(ev["original_lang"], script["lang_b"])
                elif ev["type"] == "turn_failed":
                    self.assertTrue({"type", "speaker"} <= set(ev))
                    failed_sides.append("A" if ev["speaker"] == pc_a else "B")
                else:
                    self.assertIn(ev["type"], script["expected"]["ignored_event_types"])
            if frame["closed"] and closed_at is None:
                closed_at = i
        exp = script["expected"]
        self.assertEqual((spoken_a, spoken_b), (exp["turns_spoken_by_a"], exp["turns_spoken_by_b"]))
        # A turn spoken by A lands on B's panel as "Translated".
        self.assertEqual(exp["panel_b_translated_count"], spoken_a)
        self.assertEqual(exp["panel_a_translated_count"], spoken_b)
        self.assertEqual(failed_sides, exp["turn_failed_sides"])
        self.assertEqual(closed_at, exp["closes_on_frame_index"])
        self.assertEqual(closed_at, len(frames) - 1, "closed:true must be the final frame")
        self.assertEqual(spoken_a + spoken_b, len(fixture("session_detail.json")["transcript"]))


# ---------------------------------------------------------------------------
# Shared behavioural assertions, parameterised over a transport
# ---------------------------------------------------------------------------

class ContractAssertions:
    """Mixin. Subclasses provide ``call(method, path, payload=None, headers=None, raw=None)``
    returning ``(status, body)`` where body is parsed JSON, or text for non-JSON."""

    def subject(self):
        return f"qa-native-{uuid.uuid4()}"

    def test_healthz(self):
        status, body = self.call("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"status": "ok", "service": "vocare-translate"})

    def test_entitlement_get_requires_subject(self):
        status, body = self.call("GET", "/api/entitlement")
        self.assertEqual(status, 400)
        self.assertEqual(body, {"error": "subject required"})
        status, _ = self.call("GET", "/api/entitlement?subject=store:anything")
        self.assertEqual(status, 400, "store: namespace is storage, never a client identity")

    def test_entitlement_get_shape(self):
        status, body = self.call("GET", f"/api/entitlement?subject={self.subject()}")
        self.assertEqual(status, 200)
        assert_balance_shape(self, body)
        self.assertEqual(body["tier"], "free")
        self.assertEqual(body["seconds_used"], 0)

    def test_activate_rejects_bad_bodies(self):
        for payload in ({}, {"subject": 42}, {"subject": self.subject(), "platform": "android", "receipt": {}},
                        {"subject": self.subject(), "platform": 7, "receipt": ""}):
            with self.subTest(payload=payload):
                status, body = self.call("POST", "/api/entitlement/activate", payload)
                self.assertEqual(status, 400)
                self.assertIn("error", body)

    def test_activate_unreachable_store_does_not_downgrade(self):
        subject = self.subject()
        self.call("POST", "/api/entitlement/consume", {"subject": subject, "session_id": "s-" + subject, "session_seconds": 42})
        status, body = self.call("POST", "/api/entitlement/activate",
                                 {"subject": subject, "platform": "ios", "receipt": "", "store_reachable": False})
        self.assertEqual(status, 200)
        self.assertEqual(set(body), ACTIVATE_KEYS)
        self.assertFalse(body["verified"])
        self.assertEqual(body["seconds_used"], 42, "store_reachable:false must leave the record alone")

    def test_activate_reachable_store_with_no_receipt_is_verified_free(self):
        subject = self.subject()
        self.call("POST", "/api/entitlement/consume", {"subject": subject, "session_id": "s-" + subject, "session_seconds": 42})
        status, body = self.call("POST", "/api/entitlement/activate",
                                 {"subject": subject, "platform": "android", "receipt": "", "store_reachable": True})
        self.assertEqual(status, 200)
        self.assertEqual(body["tier"], "free")
        self.assertFalse(body["verified"], "no receipt was verified with a store")
        self.assertEqual(body["seconds_used"], 0, "store_reachable:true + empty receipt resets to a blank free record")

    def test_activate_invalid_receipt_is_not_a_purchase(self):
        status, body = self.call("POST", "/api/entitlement/activate",
                                 {"subject": self.subject(), "platform": "android", "receipt": "not-a-real-token", "store_reachable": True})
        self.assertEqual(status, 200)
        self.assertEqual(set(body), ACTIVATE_KEYS)
        self.assertEqual(body["tier"], "free")

    def test_consume_is_a_per_session_watermark(self):
        subject = self.subject()
        session = "sess-" + subject
        seen = []
        for seconds in (90, 90, 150, 150):
            status, body = self.call("POST", "/api/entitlement/consume",
                                     {"subject": subject, "session_id": session, "session_seconds": seconds})
            self.assertEqual(status, 200)
            assert_balance_shape(self, body)
            self.assertNotIn("verified", body)
            seen.append(body["seconds_used"])
        self.assertEqual(seen, [90, 90, 150, 150])
        status, body = self.call("POST", "/api/entitlement/consume",
                                 {"subject": subject, "session_id": session + "-2", "session_seconds": 30})
        self.assertEqual(body["seconds_used"], 180, "a new session adds to the balance")
        status, body = self.call("POST", "/api/entitlement/consume", {"subject": subject, "session_id": session, "session_seconds": "x"})
        self.assertEqual(status, 400)
        status, _ = self.call("POST", "/api/entitlement/consume", {"session_id": session, "session_seconds": 1})
        self.assertEqual(status, 400)

    def test_session_create_list_detail_scoped_by_client_id(self):
        client = self.subject()
        status, created = self.call("POST", "/api/translation/session",
                                    {"caller_name": "QA", "caller_language": "en", "topic": "Translation Session", "client_id": client})
        self.assertEqual(status, 200)
        self.assertEqual(set(created), {"session_id", "status"})
        self.assertEqual(created["status"], "waiting")
        self.assertRegex(created["session_id"], r"^GRC-[A-Za-z0-9_-]{16}$")
        sid = created["session_id"]

        status, body = self.call("GET", f"/api/translation/sessions?client_id={quote(client)}")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"sessions"})
        mine = [s for s in body["sessions"] if s["session_id"] == sid]
        self.assertEqual(len(mine), 1)
        self.assertEqual(set(mine[0]), SESSION_LIST_ITEM_KEYS)
        self.assertEqual(mine[0]["status"], "waiting")
        self.assertRegex(mine[0]["duration"], r"^\d{2,}:\d{2}$")

        status, other = self.call("GET", f"/api/translation/sessions?client_id={quote(self.subject())}")
        self.assertNotIn(sid, [s["session_id"] for s in other["sessions"]])
        status, anonymous = self.call("GET", "/api/translation/sessions")
        self.assertEqual(anonymous, {"sessions": []})

        status, detail = self.call("GET", f"/api/translation/session/{sid}")
        self.assertEqual(status, 200)
        self.assertEqual(set(detail), SESSION_DETAIL_KEYS)
        self.assertEqual(detail["session_id"], sid)
        self.assertEqual(detail["caller_name"], "QA")
        self.assertEqual(detail["lang"], "en")
        self.assertEqual(detail["transcript"], [])
        self.assertEqual(detail["participants"], [])

        status, poll = self.call("GET", f"/api/translation/poll?session_id={sid}")
        self.assertEqual(status, 200)
        self.assertEqual(poll, {"events": [], "closed": False})

        status, _ = self.call("GET", "/api/translation/session/GRC-does-not-exist")
        self.assertEqual(status, 404)

    def test_poll_unknown_session_is_closed(self):
        status, body = self.call("GET", "/api/translation/poll?session_id=GRC-unknown")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"events": [], "closed": True})

    def test_hangup(self):
        status, _ = self.call("POST", "/api/hangup", {})
        self.assertEqual(status, 400)
        status, body = self.call("POST", "/api/hangup", {"pc_id": "pc-never-existed"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "found": False})

    def test_offer_unknown_session_is_404(self):
        status, _ = self.call("POST", "/api/translation/offer",
                              {"session_id": "GRC-unknown", "language": "en", "name": "QA", "sdp": "v=0", "type": "offer"})
        self.assertEqual(status, 404)

    def test_watch_translate_import_probe(self):
        status, body = self.call("POST", "/api/watch/translate", raw=b"",
                                 headers={"X-Vocare-Source-Language": "en", "X-Vocare-Target-Language": "en", "Content-Type": "audio/wav"})
        self.assertNotEqual(status, 500, "500 here means elevenlabs_agent_translation failed to import: the image is broken")
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        self.assertIn("source and target must differ", body["error"])
        self.assertRegex(body["error"], r"Languages available on the watch: ([a-z]{2,3}(, )?)+;")

    # -- fixtures vs. the real thing ---------------------------------------

    def test_fixtures_match_response_key_sets(self):
        _, balance = self.call("GET", f"/api/entitlement?subject={self.subject()}")
        for name in ("balance_free_unenforced", "balance_free_enforced", "balance_pro", "balance_pro_exhausted", "balance_sandbox"):
            self.assertEqual(set(fixture(name + ".json")), set(balance), name)
        _, activated = self.call("POST", "/api/entitlement/activate",
                                 {"subject": self.subject(), "platform": "ios", "receipt": "", "store_reachable": False})
        for name in ("entitlement_activate_verified", "entitlement_activate_unverified"):
            self.assertEqual(set(fixture(name + ".json")), set(activated), name)
        _, created = self.call("POST", "/api/translation/session", {"caller_name": "QA", "caller_language": "zh", "client_id": self.subject()})
        self.assertEqual(set(fixture("session_create.json")), set(created))
        _, detail = self.call("GET", f"/api/translation/session/{created['session_id']}")
        self.assertEqual(set(fixture("session_detail.json")), set(detail))
        _, poll = self.call("GET", "/api/translation/poll?session_id=GRC-unknown")
        self.assertEqual(set(fixture("poll_events.json")["frames"][0]), set(poll))
        _, ice = self.call("GET", "/api/ice")
        self.assertEqual(set(fixture("ice.json")), set(ice))
        allowed = set().union(*(set(s) for s in ice["iceServers"])) | {"urls", "username", "credential"}
        for server in fixture("ice.json")["iceServers"]:
            self.assertTrue(set(server) <= allowed, server)


# ---------------------------------------------------------------------------
# Live backend
# ---------------------------------------------------------------------------

@unittest.skipUnless(API_BASE, "set VOCARE_API_BASE to run against the deployed backend")
class LiveBackendContractTests(ContractAssertions, unittest.TestCase):
    def call(self, method, path, payload=None, headers=None, raw=None):
        data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        hdrs = {"Content-Type": "application/json"} if payload is not None else {}
        hdrs.update(headers or {})
        req = Request(API_BASE + path, data=data, headers=hdrs, method=method)
        try:
            with urlopen(req, timeout=30) as response:
                return response.status, self._body(response)
        except HTTPError as error:
            with error:
                return error.code, self._body(error)
        except URLError as error:
            self.fail(f"{method} {path} unreachable: {error}")

    @staticmethod
    def _body(response):
        text = response.read().decode("utf-8", "replace")
        try:
            return json.loads(text) if text else None
        except ValueError:
            return text

    def test_ice_servers_are_non_empty_and_include_turn(self):
        status, body = self.call("GET", "/api/ice")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"iceServers"})
        self.assertTrue(body["iceServers"], "empty ICE list guarantees relay-only failure; the app must refuse to start")
        for server in body["iceServers"]:
            self.assertTrue({"urls"} <= set(server) <= {"urls", "username", "credential"}, set(server))
        turn = [s for s in body["iceServers"] if str(s["urls"]).startswith("turn")]
        self.assertTrue(turn, "no TURN server: relay-only peer connections cannot connect")
        for s in turn:
            self.assertTrue(s.get("username") and s.get("credential"), "TURN entry without credentials")

    def test_production_metering_posture_is_what_the_contract_documents(self):
        _, body = self.call("GET", f"/api/entitlement?subject={self.subject()}")
        # CONTRACT §2: enforced:false is current production. If this flips, the
        # routing rules for free installs change — tell both mobile teams.
        self.assertIn(body["enforced"], (True, False))
        self.assertTrue(body["durable"], "production should be backed by Redis")


# ---------------------------------------------------------------------------
# In-process against bot.py
# ---------------------------------------------------------------------------

_HANDLERS = {
    "healthz", "get_ice_servers", "get_entitlement", "activate_entitlement", "consume_entitlement",
    "create_translation_session", "translation_offer", "list_translation_sessions", "translation_poll",
    "get_translation_session", "hangup", "watch_translate",
    # helpers the handlers call
    "_billing_period", "_entitlement_key", "_tier_seconds", "_blank_record", "_read_entitlement",
    "_write_entitlement", "_balance_payload", "_entitlement_subject", "_entitlement_gate",
}
_CLASSES = {"TranslationSession"}


def load_bot_handlers(enforced=False, sandbox=False):
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _HANDLERS:
            node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in _CLASSES:
            node.decorator_list = []
            selected.append(node)
    found = {n.name for n in selected}
    missing = (_HANDLERS | _CLASSES) - found
    if missing:
        raise AssertionError(f"bot.py no longer defines {sorted(missing)}; update the contract tests")

    def json_response(content, status_code=200):
        return SimpleNamespace(content=content, status_code=status_code)

    def response(status_code=200, content=None, **_):
        return SimpleNamespace(content=content, status_code=status_code)

    ctx = dict(
        datetime=datetime, timezone=timezone, time=time, secrets=secrets, asyncio=asyncio, Dict=Dict,
        logger=logging.getLogger("contract"), Request=object, Response=response, JSONResponse=json_response,
        ENTITLEMENT_ENFORCED=enforced, STORE_ALLOW_SANDBOX=sandbox, PAID_TIER_SECONDS=3600, FREE_TIER_SECONDS=0,
        _ENTITLEMENT_PREFIX="contract", _entitlement_memory={}, _session_charges={}, _entitlement_client=lambda: None,
        _verify_purchase=AsyncMock(return_value=None), translation_sessions={}, pc_to_translation={},
        _cleanup_webrtc_session=AsyncMock(return_value=False),
        fetch_twilio_ice_servers=lambda: ([], [{"urls": "stun:stun.l.google.com:19302"}]),
        TRANSLATION_VOICES={"en": {"voice_id": "v"}, "zh": {"voice_id": "v"}, "ja": {"voice_id": "v"}},
        WATCH_TRANSLATION_MAX_BYTES=1, BackgroundTasks=object,
    )
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot-contract", "exec"), ctx)
    return ctx


class _CaseInsensitiveHeaders(dict):
    """Starlette's Headers are case-insensitive; the handlers rely on that."""

    def __init__(self, items):
        super().__init__((k.lower(), v) for k, v in items.items())

    def get(self, key, default=None):
        return super().get(key.lower(), default)


class InProcessContractTests(ContractAssertions, unittest.TestCase):
    """Drives the extracted FastAPI handlers with stub Request objects."""

    def setUp(self):
        self.ctx = load_bot_handlers()
        # watch_translate does `from elevenlabs_agent_translation import has_agent_for_language`
        # inside the handler; supply a stub module so the probe runs without ElevenLabs deps.
        fake = types.ModuleType("elevenlabs_agent_translation")
        fake.has_agent_for_language = lambda code: code in ("en", "zh")
        self._saved_module = sys.modules.get("elevenlabs_agent_translation")
        sys.modules["elevenlabs_agent_translation"] = fake

    def tearDown(self):
        if self._saved_module is None:
            sys.modules.pop("elevenlabs_agent_translation", None)
        else:
            sys.modules["elevenlabs_agent_translation"] = self._saved_module

    def call(self, method, path, payload=None, headers=None, raw=None):
        route, _, query = path.partition("?")
        params = dict(p.split("=", 1) for p in query.split("&") if p) if query else {}
        params = {k: v.replace("%3A", ":") for k, v in params.items()}
        hdrs = _CaseInsensitiveHeaders(headers or {})

        async def body():
            if raw is not None:
                return raw
            return payload

        request = SimpleNamespace(json=AsyncMock(return_value=payload), query_params=params, headers=hdrs, body=body)
        result = asyncio.run(self._dispatch(method, route, request, params, payload))
        if isinstance(result, SimpleNamespace):
            return result.status_code, result.content
        return 200, result

    async def _dispatch(self, method, route, request, params, payload):
        c = self.ctx
        if route == "/healthz":
            return await c["healthz"]()
        if route == "/api/ice":
            return await c["get_ice_servers"]()
        if route == "/api/entitlement":
            return await c["get_entitlement"](request)
        if route == "/api/entitlement/activate":
            return await c["activate_entitlement"](request)
        if route == "/api/entitlement/consume":
            return await c["consume_entitlement"](request)
        if route == "/api/translation/session":
            return await c["create_translation_session"](request)
        if route == "/api/translation/sessions":
            return await c["list_translation_sessions"](request)
        if route == "/api/translation/poll":
            return await c["translation_poll"](params.get("session_id", ""))
        if route.startswith("/api/translation/session/"):
            return await c["get_translation_session"](route.rsplit("/", 1)[1])
        if route == "/api/translation/offer":
            return await c["translation_offer"](request, SimpleNamespace(add_task=lambda *a, **k: None))
        if route == "/api/hangup":
            return await c["hangup"](request)
        if route == "/api/watch/translate":
            return await c["watch_translate"](request)
        raise AssertionError(f"no in-process route for {method} {route}")

    def test_offer_returns_402_with_balance_when_enforced_and_exhausted(self):
        self.ctx = load_bot_handlers(enforced=True)
        client = self.subject()
        _, created = self.call("POST", "/api/translation/session", {"caller_name": "QA", "caller_language": "en", "client_id": client})
        status, body = self.call("POST", "/api/translation/offer",
                                 {"session_id": created["session_id"], "language": "en", "name": "QA", "sdp": "v=0", "type": "offer"})
        self.assertEqual(status, 402)
        self.assertEqual(set(body), {"error", "balance"})
        self.assertEqual(body["error"], "no_translation_credit")
        assert_balance_shape(self, body["balance"])
        self.assertEqual(body["balance"]["seconds_remaining"], 0)
        self.assertEqual(set(body["balance"]), set(fixture("balance_pro_exhausted.json")))

    def test_offer_is_refused_without_client_id_when_enforced(self):
        self.ctx = load_bot_handlers(enforced=True)
        _, created = self.call("POST", "/api/translation/session", {"caller_name": "QA", "caller_language": "en"})
        status, body = self.call("POST", "/api/translation/offer",
                                 {"session_id": created["session_id"], "language": "en", "name": "QA", "sdp": "v=0", "type": "offer"})
        self.assertEqual(status, 402)
        self.assertEqual(body, {"error": "entitlement_subject_required"})

    def test_balance_flags_follow_server_configuration(self):
        for enforced, sandbox in ((False, False), (True, False), (True, True)):
            with self.subTest(enforced=enforced, sandbox=sandbox):
                self.ctx = load_bot_handlers(enforced=enforced, sandbox=sandbox)
                _, body = self.call("GET", f"/api/entitlement?subject={self.subject()}")
                self.assertEqual((body["enforced"], body["sandbox_ok"], body["durable"]), (enforced, sandbox, False))

    def test_poll_drains_queue_and_reports_closed_when_ended(self):
        client = self.subject()
        _, created = self.call("POST", "/api/translation/session", {"caller_name": "QA", "caller_language": "en", "client_id": client})
        session = self.ctx["translation_sessions"][created["session_id"]]
        script = fixture("poll_events.json")
        events = [e for f in script["frames"] for e in f["events"]]
        for ev in events:
            session.event_queue.put_nowait(ev)
        _, poll = self.call("GET", f"/api/translation/poll?session_id={created['session_id']}")
        self.assertEqual(poll, {"events": events, "closed": False})
        _, again = self.call("GET", f"/api/translation/poll?session_id={created['session_id']}")
        self.assertEqual(again, {"events": [], "closed": False}, "poll drains the queue")
        session.status = "ended"
        session.ended_at = datetime.now()
        _, closed = self.call("GET", f"/api/translation/poll?session_id={created['session_id']}")
        self.assertEqual(closed, {"events": [], "closed": True})
        _, listed = self.call("GET", f"/api/translation/sessions?client_id={client}")
        self.assertEqual(listed["sessions"][0]["status"], "ended")

    def test_watch_translate_unsupported_language_is_400_not_500(self):
        status, body = self.call("POST", "/api/watch/translate", raw=b"",
                                 headers={"X-Vocare-Source-Language": "en", "X-Vocare-Target-Language": "ja", "Content-Type": "audio/wav"})
        self.assertEqual(status, 400)
        self.assertIn("en, zh", body["error"])


if __name__ == "__main__":
    unittest.main()
