import os
import stat

import pytest

from novavision.cli import NovaVisionCLI, _collect_masked
from novavision.credentials import (
    CredentialsError,
    active_login_label,
    canonical_host,
    credentials_path,
    delete_credentials,
    hosts_match,
    load_credentials,
    resolve_auth,
    save_credentials,
    verify_api_key,
)
from novavision.installer import Installer


def _profile(username="selcukoz"):
    return {
        "success": True,
        "user": {
            "id": 800,
            "email": "selcuk@example.com",
            "username": username,
            "first_name": "Selcuk",
            "last_name": "Oz",
        },
    }


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_canonical_host_matches_saved_server_metadata():
    assert canonical_host("suite.novavision.ai") == "https://suite.novavision.ai"
    assert (
        canonical_host("http://suite.novavision.ai/") == "https://suite.novavision.ai"
    )
    assert hosts_match("https://suite.novavision.ai/", "suite.novavision.ai")
    assert not hosts_match(
        "https://suite.novavision.ai", "https://alfa.suite.novavision.ai"
    )


def test_verify_api_key_uses_profile_and_returns_username(monkeypatch):
    captured = {}

    def fake_get(url, headers, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response(200, _profile())

    monkeypatch.setattr("novavision.credentials.requests.get", fake_get)
    username, error = verify_api_key("suite.novavision.ai", "secret-key")
    assert error is None
    assert username == "selcukoz"
    assert captured["url"] == "https://suite.novavision.ai/api/auth/default/profile"
    assert captured["headers"]["Authorization"] == "Bearer secret-key"


def test_verify_api_key_rejects_an_invalid_key(monkeypatch):
    monkeypatch.setattr(
        "novavision.credentials.requests.get",
        lambda url, headers, timeout: _Response(401, {"message": "unauthorized"}),
    )
    username, error = verify_api_key("https://suite.novavision.ai", "nope")
    assert username is None
    assert "unauthorized" in error
    assert "401" in error


def test_saved_login_is_a_single_record_with_owner_only_permissions(nv_home):
    path = save_credentials("https://suite.novavision.ai", "first-token", "first-user")
    save_credentials("alfa.suite.novavision.ai", "second-token", "selcukoz")
    saved = load_credentials()
    assert path == nv_home / ".novavision" / "credentials"
    assert saved == {
        "host": "https://alfa.suite.novavision.ai",
        "token": "second-token",
        "username": "selcukoz",
    }
    assert not (nv_home / ".novavision" / "config.json").exists()
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert delete_credentials() is True
    assert load_credentials() is None
    assert delete_credentials() is False


def test_corrupt_credentials_are_rejected(nv_home):
    folder = nv_home / ".novavision"
    folder.mkdir()
    (folder / "credentials").write_text("{", encoding="utf-8")
    with pytest.raises(CredentialsError):
        load_credentials()


def test_token_read_order_is_flag_then_positional_then_env_then_file(
    nv_home, monkeypatch
):
    save_credentials("https://suite.novavision.ai", "file-token", "selcukoz")
    env = {"NOVAVISION_TOKEN": "env-token"}
    assert resolve_auth(
        flag_token="flag-token", positional_token="positional", env=env
    ).token == ("flag-token")
    assert resolve_auth(positional_token="positional", env=env).source == "positional"
    assert resolve_auth(env=env).source == "env"
    saved = resolve_auth(env={})
    assert saved.source == "credentials"
    assert saved.token == "file-token"
    assert saved.host == "https://suite.novavision.ai"
    assert saved.username == "selcukoz"
    monkeypatch.delenv("NOVAVISION_TOKEN", raising=False)
    assert resolve_auth(env={}).token == "file-token"


def test_masked_input_prints_a_dot_per_character():
    keys = iter(["a", "b", "\b", "c", "\x00", "H", "\r"])
    shown = []
    value = _collect_masked(lambda: next(keys), shown.append, "•")
    assert value == "ac"
    assert shown == ["•", "•", "\b \b", "•", "\n"]


def test_masked_backspace_erases_the_previous_line():
    keys = iter(["a", "b", "c", "d", "e", "\b", "\b", "\r"])
    shown = []
    value = _collect_masked(lambda: next(keys), shown.append, "•", width=4)
    assert value == "abc"
    assert shown[5] == "\b \b"
    assert shown[6].startswith("\x1b[A")


def test_login_saves_username_for_the_default_host(nv_home, monkeypatch):
    prompts = []
    messages = []

    def fake_getpass(prompt):
        prompts.append(prompt)
        return "secret-key"

    monkeypatch.setattr("novavision.cli.getpass.getpass", fake_getpass)
    monkeypatch.setattr(
        "novavision.cli.verify_api_key",
        lambda host, token: ("selcukoz", None),
    )
    monkeypatch.setattr(
        "novavision.cli.logger.success",
        lambda message: messages.append(message),
    )
    NovaVisionCLI().handle_login(_parser_namespace(host=None))
    assert prompts == [
        "Enter access token (https://suite.novavision.ai/site/profile/edit -> Access Token): "
    ]
    assert messages == ["Logged in as selcukoz"]


def test_login_cancel_warns_on_the_next_line(nv_home, monkeypatch):
    written = []
    warnings = []

    def fake_getpass(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr("novavision.cli.getpass.getpass", fake_getpass)
    monkeypatch.setattr(
        "novavision.cli.sys.stderr.write", lambda text: written.append(text)
    )
    monkeypatch.setattr(
        "novavision.cli.sys.stderr.flush", lambda: written.append("flush")
    )
    monkeypatch.setattr(
        "novavision.cli.logger.warning",
        lambda message: warnings.append(message),
    )
    with pytest.raises(SystemExit):
        NovaVisionCLI().handle_login(_parser_namespace(host=None))
    assert written[:2] == ["\n", "flush"]
    assert warnings == ["Operation cancelled by user"]
    assert not credentials_path().exists()


def test_login_does_not_save_a_rejected_key(nv_home, monkeypatch):
    monkeypatch.setattr("novavision.cli.getpass.getpass", lambda prompt: "secret-key")
    monkeypatch.setattr(
        "novavision.cli.verify_api_key",
        lambda host, token: (None, "API key was rejected. HTTP 401"),
    )
    with pytest.raises(SystemExit):
        NovaVisionCLI().handle_login(_parser_namespace(host=None))
    assert not credentials_path().exists()


def test_active_login_label(nv_home):
    assert active_login_label() is None
    save_credentials("https://suite.novavision.ai/", "secret-key", "selcukoz")
    assert active_login_label() == "selcukoz @ suite.novavision.ai"


def test_list_shows_active_login(nv_home, monkeypatch):
    save_credentials("https://alfa.suite.novavision.ai", "secret-key", "selcukoz")
    notes = []
    monkeypatch.setattr(
        "novavision.cli.logger.note", lambda message: notes.append(message)
    )
    cli = NovaVisionCLI()
    monkeypatch.setattr(cli.docker, "list_servers", lambda: True)
    cli.handle_list_command(_parser_namespace())
    assert notes == ["selcukoz @ alfa.suite.novavision.ai"]


def test_whoami_and_logout(nv_home, monkeypatch):
    save_credentials("https://suite.novavision.ai", "secret-key", "selcukoz")
    messages = []
    monkeypatch.setattr(
        "novavision.cli.logger.info",
        lambda message: messages.append(message),
    )
    NovaVisionCLI().handle_whoami(_parser_namespace())
    assert messages == ["selcukoz (https://suite.novavision.ai)"]
    NovaVisionCLI().handle_logout(_parser_namespace())
    assert not credentials_path().exists()
    with pytest.raises(SystemExit):
        NovaVisionCLI().handle_whoami(_parser_namespace())


def test_install_with_saved_login_uses_that_host_not_config(nv_home, monkeypatch):
    monkeypatch.delenv("NOVAVISION_TOKEN", raising=False)
    config = nv_home / ".novavision"
    config.mkdir()
    (config / "config.json").write_text(
        '{"host": "https://alfa.suite.novavision.ai", "workspace": "ci"}',
        encoding="utf-8",
    )
    save_credentials("https://suite.novavision.ai", "saved-token", "selcukoz")
    captured = {}

    def fake_install(self, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(Installer, "install", fake_install)
    args = (
        NovaVisionCLI()
        .create_parser()
        .parse_args(["install", "local", "--non-interactive", "--workspace", "ci"])
    )
    NovaVisionCLI().handle_install(args)
    assert captured["token"] == "saved-token"
    assert captured["host"] == "https://suite.novavision.ai"
    assert captured["workspace"] == "ci"


def test_install_rejects_explicit_host_that_differs_from_saved_login(
    nv_home, monkeypatch
):
    monkeypatch.delenv("NOVAVISION_TOKEN", raising=False)
    save_credentials("https://suite.novavision.ai", "saved-token", "selcukoz")
    called = {"install": False}

    def fake_install(self, **kwargs):
        called["install"] = True
        return True

    monkeypatch.setattr(Installer, "install", fake_install)
    args = (
        NovaVisionCLI()
        .create_parser()
        .parse_args(["install", "local", "--host", "alfa.suite.novavision.ai"])
    )
    with pytest.raises(SystemExit):
        NovaVisionCLI().handle_install(args)
    assert called["install"] is False


def test_explicit_token_still_uses_the_requested_host(nv_home, monkeypatch):
    monkeypatch.delenv("NOVAVISION_TOKEN", raising=False)
    save_credentials("https://suite.novavision.ai", "saved-token", "selcukoz")
    captured = {}

    def fake_install(self, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(Installer, "install", fake_install)
    args = (
        NovaVisionCLI()
        .create_parser()
        .parse_args(
            [
                "install",
                "local",
                "--token",
                "explicit-token",
                "--host",
                "alfa.suite.novavision.ai",
            ]
        )
    )
    NovaVisionCLI().handle_install(args)
    assert captured["token"] == "explicit-token"
    assert captured["host"] == "alfa.suite.novavision.ai"


def _parser_namespace(host=None):
    return type("Args", (), {"host": host})()
