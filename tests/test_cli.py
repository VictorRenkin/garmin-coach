import sys

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
