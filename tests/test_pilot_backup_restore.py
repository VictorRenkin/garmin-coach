"""Stopped-tree copy/restore proof using temporary, synthetic data only."""
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import pytest

from garmin_coach import config, garmin_delivery as delivery, user_accounts
from tests.test_complex_double_day_3d import pair, approve, send, CapturingClient
from tests.test_discovery_phase5_quality import declare


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_isolated_restore_keeps_accounts_consent_confirmed_id_and_uncertain_intent(pair, tmp_path, monkeypatch):
    monkeypatch.setattr(user_accounts, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", tmp_path / "secrets" / "web_session.key")
    user_accounts.USERS_FILE.write_text(json.dumps([
        {"id": pair.user, "email": "alice@example.invalid", "email_verified": True},
        {"id": "bob", "email": "bob@example.invalid", "email_verified": True}]), encoding="utf-8")
    declare(pair.user)
    declare("bob")
    cookie = "motionest_session=" + user_accounts.create_session(pair.user)
    approve(pair, "evening")
    client = CapturingClient()
    confirmed = send(pair, client, 1)
    approve(pair, "morning")

    class Missing(CapturingClient):
        def upload_running_workout(self, workout):
            super().upload_running_workout(workout)
            return {}

    with pytest.raises(delivery.GarminDeliveryError):
        send(pair, Missing(client), 0)
    with config.user_context(pair.user):
        (config.ACTIVITY_HISTORY_DIR / "synthetic.json").write_text('{"activityId":"synthetic"}', encoding="utf-8")
        config.TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        (config.TOKEN_DIR / "synthetic-token.json").write_text('{"fake":"not-a-credential"}', encoding="utf-8")
    before = manifest(tmp_path)
    # Source has no running server/worker; no real directory is used or replaced.
    with TemporaryDirectory(prefix="motionest-restore-proof-") as isolated:
        backup = Path(isolated) / "backup"
        restored = Path(isolated) / "restored"
        shutil.copytree(tmp_path, backup)
        shutil.copytree(backup, restored)
        assert manifest(backup) == manifest(restored) == before
        monkeypatch.setattr(config, "GLOBAL_DATA_DIR", restored)
        monkeypatch.setattr(config, "USERS_DATA_DIR", restored / "users")
        monkeypatch.setattr(config, "SECRETS_DIR", restored / "secrets")
        monkeypatch.setattr(user_accounts, "USERS_FILE", restored / "users.json")
        monkeypatch.setattr(user_accounts, "SESSION_KEY_FILE", restored / "secrets" / "web_session.key")
        assert user_accounts.user_from_cookie(cookie) == pair.user
        assert len(json.loads(user_accounts.USERS_FILE.read_text(encoding="utf-8"))) == 2
        delivery.assert_upload_retry_safe(pair.user, session_index=1, expected_session_id="evening")
        assert send(pair, CapturingClient(client), 1) == confirmed
        with pytest.raises(delivery.GarminDeliveryError) as error:
            delivery.assert_upload_retry_safe(pair.user, session_index=0, expected_session_id="morning")
        assert error.value.code == "upload_uncertain"
        with config.user_context("bob"):
            assert delivery._read_store()["deliveries"] == {}
            assert not config.GARMIN_DELIVERY_FILE.is_file()
            assert not config.TOKEN_DIR.exists()
        assert manifest(tmp_path) == before
