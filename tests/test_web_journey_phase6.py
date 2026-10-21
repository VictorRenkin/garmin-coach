"""Missing HTTP seams across existing web journeys; no real Garmin or AI."""
from garmin_coach import config, dashboard, history_import, history_storage, onboarding
from tests.test_discovery_phase5_quality import isolated, server, request, declare
from tests.test_user_journey import _write_tokens
import pytest


def test_discovery_user_can_explicitly_connect_later_through_the_real_http_route(server, isolated, monkeypatch):
    snapshots = declare("alice")
    assert request(server, "/api/onboarding/continue-without-garmin", user="alice", method="POST")[0] == 200
    assert request(server, "/connect-garmin", user="alice")[0] == 200
    calls = []
    client = object()
    def authenticate(*args):
        calls.append("explicit-authenticate")
        return client
    def persist(value):
        assert value is client
        _write_tokens(isolated, "alice")
    def setup(user):
        assert user == "alice"
        calls.append("explicit-setup")
        with config.user_context(user):
            for index, sport in enumerate(("running", "cycling", "swimming")):
                history_storage.save_activity_metadata({"activityId": f"synthetic-late-{index}", "activityType": {"typeKey": sport},
                    "startTimeLocal": f"2026-10-20T{8+index:02d}:00:00", "duration": 1800, "distance": 5000})
            history_import.update_history_import({"status": "complete", "pagination_complete": True})
            onboarding.update_garmin_setup("analysis_ready")
        return True
    monkeypatch.setattr(dashboard, "authenticate_with_credentials", authenticate)
    monkeypatch.setattr(dashboard, "persist_client_tokens", persist)
    monkeypatch.setattr(dashboard, "start_initial_setup", setup)
    status, _, body = request(server, "/api/garmin/connect", user="alice", method="POST",
        payload={"email": "synthetic@example.invalid", "password": "synthetic-only", "historyScope": "all"})
    assert status == 202, body
    assert calls == ["explicit-authenticate", "explicit-setup"]
    from pathlib import Path
    for path, before in snapshots.items():
        assert Path(path).read_bytes() == before
    with config.user_context("alice"):
        context = dashboard.coach_context()
        assert context["connexion_sportive"] == {"garmin_connected": True, "discovery_without_garmin": False}
        assert set(context["contexte_multisport"]["context"]["athlete_state"]["sports_observed"]) == {"running", "cycling", "swimming"}
    assert request(server, "/api/activities", user="bob")[2]["activities"] == []


@pytest.mark.parametrize("user", ["unverified", "alice", "bob"])
def test_late_connect_exception_does_not_authorize_other_account_stages(server, isolated, monkeypatch, user):
    if user == "alice":
        declare(user)
        with config.user_context(user):
            state = onboarding.load_onboarding()
            state.update(status="complete", discovery_without_garmin=True)
            onboarding._write_onboarding(state)
        _write_tokens(isolated, user)
    if user == "bob":
        with config.user_context(user):
            state = onboarding.load_onboarding()
            state["status"] = "not_started"
            onboarding._write_onboarding(state)
    calls = []
    monkeypatch.setattr(dashboard, "authenticate_with_credentials", lambda *args: calls.append("unexpected"))
    status, _, _ = request(server, "/api/garmin/connect", user=user, method="POST",
        payload={"email": "synthetic@example.invalid", "password": "synthetic-only"})
    assert status == 409 and calls == []


def test_http_complex_double_day_requires_each_consent_and_keeps_delivery_ids_separate(server, isolated, monkeypatch):
    from garmin_coach import plan_proposal
    from tests.test_calendar_3c import source_for, double_proposal
    from tests.test_session_blocks_3b import session as complex_session
    from tests.test_plan_proposal import settings
    from tests.test_garmin_delivery import VerifiedClient
    sources = {user: source_for(user) for user in ("alice", "bob")}
    _write_tokens(isolated, "alice")
    with config.user_context("alice"):
        state = onboarding.load_onboarding()
        state["status"] = "complete"
        onboarding._write_onboarding(state)
    proposal = double_proposal(sources["alice"])
    complex_structure = complex_session()
    proposal.sessions = [item.model_copy(update={"structure": complex_structure.structure,
        "duration_minutes": complex_structure.duration_minutes}) for item in proposal.sessions]
    monkeypatch.setattr(plan_proposal, "build_plan_generation_input_v2", lambda user, *args, **kwargs: sources[user])
    monkeypatch.setattr(plan_proposal, "load_settings", settings)
    monkeypatch.setattr(plan_proposal.ai_provider, "structured_completion", lambda *args, **kwargs: proposal)
    client = VerifiedClient()
    connects = []
    monkeypatch.setattr(dashboard, "connect", lambda *args: connects.append("explicit-delivery") or client)
    status, _, generated = request(server, "/api/plan-proposal/generate", user="alice", method="POST")
    assert status == 200 and generated["status"] == "available"
    status, _, current = request(server, "/api/plan-proposal", user="alice")
    assert status == 200
    assert [item["session_id"] for item in current["proposal"]["sessions"]] == ["morning", "evening"]
    assert all(len(item["structure"]["blocks"]) == 2 for item in current["proposal"]["sessions"])
    def decide(identity, decision):
        return request(server, "/api/plan-proposal/decision", user="alice", method="POST", payload={
            "session_id": identity, "decision": decision, "input_fingerprint": generated["input_fingerprint"],
            "proposal_fingerprint": current["proposal_fingerprint"]})
    def deliver(index, identity, user="alice"):
        return request(server, "/api/garmin/workout-delivery", user=user, method="POST",
            payload={"session_index": index, "session_id": identity})
    assert deliver(0, "morning")[0] == 409 and connects == []
    assert decide("morning", "accepted")[0] == 200
    assert decide("evening", "refused")[0] == 200
    morning = deliver(0, "morning")
    assert morning[0] == 200 and morning[2]["delivery"]["calendar_status"] == "scheduled"
    assert deliver(1, "evening")[0] == 409
    assert decide("evening", "accepted")[0] == 200
    evening = deliver(1, "evening")
    assert evening[0] == 200
    assert morning[2]["delivery"]["garmin_workout_id"] != evening[2]["delivery"]["garmin_workout_id"]
    assert deliver(0, "morning")[2]["delivery"] == morning[2]["delivery"]
    assert client.uploads == 2 and len(client.schedules) == 2
    assert request(server, "/dashboard", user="alice")[0] == 200
    assert request(server, "/api/plan-proposal", user="bob")[2]["proposal"] is None


def test_registration_verification_login_onboarding_discovery_and_logout_use_real_sessions(server, monkeypatch):
    import json
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    from urllib.request import HTTPRedirectHandler, Request, build_opener
    from garmin_coach import coach_chat
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    def browser(path, payload=None, cookie=None, method="GET"):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        data = json.dumps(payload).encode() if payload is not None else None
        try:
            response = build_opener(NoRedirect()).open(Request(server+path, data=data, headers=headers, method=method), timeout=5)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            return response.status, response.headers, json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body
    monkeypatch.setattr(dashboard, "verify_human", lambda *args, **kwargs: True)
    mail = []
    def fake_mail(email, kind, token):
        url = f"https://synthetic.invalid/verify-email?token={token}"
        mail.append(url)
        return url
    monkeypatch.setattr(dashboard, "send_account_email", fake_mail)
    password = "synthetic-only-long-password"
    identity = {"email": "carol@example.invalid", "password": password}
    registered = browser("/api/account/register", dict(identity, passwordConfirm=password, termsAccepted=True), method="POST")
    assert registered[0] == 201 and registered[2]["verificationPending"] is True
    assert browser("/api/account/login", identity, method="POST")[0] == 403
    verify = urlsplit(mail[-1])  # An unverified login sends a new, rotated link.
    verification = browser(verify.path+"?"+verify.query)
    assert verification[0] == 302 and verification[1]["Location"] == "/?verified=1"
    logged = browser("/api/account/login", identity, method="POST")
    assert logged[0] == 200
    cookie = logged[1]["Set-Cookie"].split(";")[0]
    for step, payload in (("goal", {"intent": "progress"}), ("availability", {"availableDays": ["tue", "sat"]}),
        ("subjective-context", {"painOrInjuryAffectingTraining": False, "importantPreferences": ["Mardi si possible."]})):
        assert browser("/api/onboarding/"+step, payload, cookie, "POST")[0] == 200
    assert browser("/api/onboarding/continue-without-garmin", {}, cookie, "POST")[0] == 200
    assert browser("/dashboard", cookie=cookie)[0] == 200
    def fake_worker(user, message_id):
        with config.user_context(user):
            context = dashboard.coach_context()
            assert context["connexion_sportive"]["garmin_connected"] is False
            assert context["contexte_multisport"]["history"]["coverage"]["activity_count"] == 0
            coach_chat.store_response(message_id, coach_chat.CoachResponse(message="Je dispose de tes déclarations, sans historique enregistré."))
    monkeypatch.setattr(dashboard, "queue_coach_response", fake_worker)
    assert browser("/api/coach/messages", {"message": "Quelles données connais-tu ?"}, cookie, "POST")[0] == 202
    messages = browser("/api/coach/messages", cookie=cookie)[2]["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert request(server, "/api/coach/messages", user="bob")[2]["messages"] == []
    logout = browser("/api/logout", cookie=cookie, method="POST")
    assert logout[0] == 200 and "Max-Age=0" in logout[1]["Set-Cookie"]
    assert browser("/api/profile")[0] == 401
