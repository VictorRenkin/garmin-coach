import sys
from pathlib import Path

from garminconnect import GarminConnectAuthenticationError

from garmin_coach import cli


def test_login_displays_yes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["garmin-coach", "login"])
    monkeypatch.setattr(cli, "connect", lambda settings: object())

    assert cli.main() == 0
    output = capsys.readouterr().out
    assert "GARMIN COACH" in output
    assert "CONNEXION RÉUSSIE" in output
    assert "Prêt à analyser tes courses" in output


def test_login_displays_no_on_authentication_error(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["garmin-coach", "login"])

    def fail(settings):
        raise GarminConnectAuthenticationError("secret technical detail")

    monkeypatch.setattr(cli, "connect", fail)

    assert cli.main() == 1
    output = capsys.readouterr().out
    assert "CONNEXION ÉCHOUÉE" in output
    assert "secret technical detail" not in output


def test_logout_removes_only_local_garmin_tokens(tmp_path: Path, monkeypatch, capsys) -> None:
    token_dir = tmp_path / "garmin_tokens"
    token_dir.mkdir()
    (token_dir / "oauth1_token.json").write_text("secret", encoding="utf-8")
    (token_dir / "oauth2_token.json").write_text("secret", encoding="utf-8")
    unrelated = token_dir / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(cli, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(sys, "argv", ["garmin-coach", "logout"])

    assert cli.main() == 0
    assert not (token_dir / "oauth1_token.json").exists()
    assert not (token_dir / "oauth2_token.json").exists()
    assert unrelated.is_file()
    assert "DÉCONNEXION RÉUSSIE" in capsys.readouterr().out
