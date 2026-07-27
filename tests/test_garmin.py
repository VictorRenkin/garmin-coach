from pathlib import Path

from garmin_coach import garmin
from garmin_coach.config import Settings


class FakeGarth:
    def __init__(self) -> None:
        self.dumped_to: str | None = None

    def dump(self, target: str) -> None:
        self.dumped_to = target


class FakeGarmin:
    instances: list["FakeGarmin"] = []

    def __init__(self, email=None, password=None, prompt_mfa=None) -> None:
        self.email = email
        self.password = password
        self.prompt_mfa = prompt_mfa
        self.login_tokenstore: str | None = "not-called"
        self.garth = FakeGarth()
        self.instances.append(self)

    def login(self, tokenstore=None) -> None:
        self.login_tokenstore = tokenstore


def test_first_login_authenticates_before_saving_tokens(tmp_path: Path, monkeypatch) -> None:
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()  # Reproduit le dossier vide qui causait l'erreur.
    FakeGarmin.instances.clear()
    monkeypatch.setattr(garmin, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    monkeypatch.setattr(garmin, "ensure_directories", lambda: None)

    client = garmin.connect(Settings("runner@example.com", "secret", 300))

    assert client.login_tokenstore is None
    assert client.garth.dumped_to == str(token_dir)


def test_complete_tokens_are_reused(tmp_path: Path, monkeypatch) -> None:
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    (token_dir / "oauth1_token.json").write_text("{}", encoding="utf-8")
    (token_dir / "oauth2_token.json").write_text("{}", encoding="utf-8")
    FakeGarmin.instances.clear()
    monkeypatch.setattr(garmin, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    monkeypatch.setattr(garmin, "ensure_directories", lambda: None)

    client = garmin.connect(Settings(None, None, 300))

    assert client.login_tokenstore == str(token_dir)
    assert client.email is None
