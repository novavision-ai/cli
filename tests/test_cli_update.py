import io
import json
import sys

import pytest

from novavision import __version__
from novavision.cli import NovaVisionCLI
from novavision.cli_update import (
    UpdateCheck,
    begin_update_check,
    cache_is_fresh,
    cache_path,
    finish_update_check,
    format_notice,
    install_kind,
    is_pipx_install,
    read_cache,
    running_outside_site_packages,
    self_update,
    upgrade_command,
    version_is_newer,
    write_cache,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_version_compare_handles_multi_digit_releases():
    assert version_is_newer("0.1.10", "0.1.9")
    assert not version_is_newer("0.1.7", "0.1.7")
    assert not version_is_newer("0.1.6", "0.1.7")
    assert not version_is_newer("not-a-version", "0.1.7")


def test_fresh_cache_skips_the_network(nv_home, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "pip")
    write_cache("0.2.0", checked_at=1_000)
    monkeypatch.setattr("novavision.cli_update.time.time", lambda: 1_000 + 60)
    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: True)
    def fail_get(*args, **kwargs):
        raise AssertionError("network check should not run")

    monkeypatch.setattr("novavision.cli_update.requests.get", fail_get)
    check = begin_update_check()
    assert check.latest == "0.2.0"
    assert check.thread is None
    assert cache_is_fresh(read_cache(), now=1_000 + 60)


def test_stale_cache_refreshes_and_notifies(nv_home, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "pip")
    write_cache("0.1.0", checked_at=1_000)
    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: True)
    monkeypatch.setattr(
        "novavision.cli_update.requests.get",
        lambda *args, **kwargs: FakeResponse({"info": {"version": "9.9.9"}}),
    )
    check = begin_update_check()
    stream = io.StringIO()
    finish_update_check(check, stream=stream)
    assert read_cache()["latest"] == "9.9.9"
    assert stream.getvalue() == format_notice("9.9.9")


def test_failed_refresh_keeps_a_known_newer_release(nv_home, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "pip")
    write_cache("0.2.0", checked_at=1_000)
    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: True)

    def fail_get(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr("novavision.cli_update.requests.get", fail_get)
    check = begin_update_check()
    stream = io.StringIO()
    finish_update_check(check, stream=stream)
    assert read_cache()["latest"] == "0.2.0"
    assert stream.getvalue() == format_notice("0.2.0")


def test_a_development_install_does_not_check_pypi(monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "editable")
    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: True)
    monkeypatch.setattr(
        "novavision.cli_update.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    assert begin_update_check() is None


def test_check_stays_quiet_for_json_and_pipes(nv_home, monkeypatch):
    monkeypatch.setattr(
        "novavision.cli_update.requests.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")),
    )
    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: True)
    assert begin_update_check(json_mode=True) is None

    monkeypatch.setattr("novavision.cli_update.stdout_is_tty", lambda: False)
    assert begin_update_check() is None


def test_current_release_does_not_print_a_notice():
    check = UpdateCheck()
    check.latest = __version__
    stream = io.StringIO()
    finish_update_check(check, stream=stream)
    assert stream.getvalue() == ""


def test_install_kind_detects_editable_pipx_and_pip(monkeypatch):
    monkeypatch.setattr("novavision.cli_update.direct_url_marks_editable", lambda: False)
    monkeypatch.setattr(
        "novavision.cli_update.running_outside_site_packages", lambda: True
    )
    assert install_kind() == "editable"

    monkeypatch.setattr(
        "novavision.cli_update.running_outside_site_packages", lambda: False
    )
    monkeypatch.setattr("novavision.cli_update.is_pipx_install", lambda: True)
    assert install_kind() == "pipx"

    monkeypatch.setattr("novavision.cli_update.is_pipx_install", lambda: False)
    assert install_kind() == "pip"
    assert running_outside_site_packages(
        r"C:\Python\Lib\site-packages\novavision\cli_update.py"
    ) is False
    assert is_pipx_install(r"C:\Users\me\.local\pipx\venvs\novavision-cli")
    assert not is_pipx_install(r"C:\Python\Lib\site-packages")


def test_upgrade_command_uses_pipx_when_it_is_installed(monkeypatch):
    monkeypatch.setattr("novavision.cli_update.shutil.which", lambda name: "/bin/pipx")
    command, tool = upgrade_command("pipx")
    assert command == ["/bin/pipx", "upgrade", "novavision-cli"]
    assert tool == "pipx"


def test_upgrade_command_falls_back_to_pip(monkeypatch):
    monkeypatch.setattr("novavision.cli_update.shutil.which", lambda name: None)
    command, tool = upgrade_command("pipx")
    assert command == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "novavision-cli",
    ]
    assert tool == "pip"
    pip_command, pip_tool = upgrade_command("pip")
    assert pip_command == command
    assert pip_tool == "pip"


def test_self_update_refuses_a_development_install(fake_logger, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "editable")

    def fail_run(*args, **kwargs):
        raise AssertionError("upgrade should not run")

    monkeypatch.setattr("novavision.cli_update.subprocess.run", fail_run)
    assert self_update(fake_logger) is False
    assert any("development install" in message for message in fake_logger.messages_of("error"))


def test_self_update_reports_the_current_release(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "pip")
    monkeypatch.setattr(
        "novavision.cli_update.requests.get",
        lambda *args, **kwargs: FakeResponse({"info": {"version": __version__}}),
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("upgrade should not run")

    monkeypatch.setattr("novavision.cli_update.subprocess.run", fail_run)
    assert self_update(fake_logger) is True
    assert any("already up to date" in message for message in fake_logger.messages_of("success"))


def test_self_update_runs_pip_and_returns_its_status(fake_logger, monkeypatch):
    monkeypatch.setattr("novavision.cli_update.install_kind", lambda: "pip")
    monkeypatch.setattr(
        "novavision.cli_update.fetch_latest_version", lambda timeout=5: "9.9.9"
    )
    calls = []

    def fake_run(command):
        calls.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("novavision.cli_update.subprocess.run", fake_run)
    assert self_update(fake_logger) is True
    assert calls == [
        [sys.executable, "-m", "pip", "install", "--upgrade", "novavision-cli"]
    ]

    calls.clear()

    def fail_run(command):
        calls.append(command)
        return type("Result", (), {"returncode": 1})()

    monkeypatch.setattr("novavision.cli_update.subprocess.run", fail_run)
    assert self_update(fake_logger) is False
    assert calls


def test_run_notifies_after_a_failed_command(monkeypatch, nv_home):
    monkeypatch.setattr(sys, "argv", ["novavision", "whoami"])
    seen = {}
    monkeypatch.setattr(
        "novavision.cli.begin_update_check",
        lambda json_mode=False: seen.setdefault("begin", json_mode) or "token",
    )
    monkeypatch.setattr(
        "novavision.cli.finish_update_check",
        lambda check: seen.setdefault("finish", check),
    )
    with pytest.raises(SystemExit):
        NovaVisionCLI().run()
    assert seen["begin"] is False
    assert seen["finish"] == "token"


def test_json_mode_is_passed_to_the_update_check(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["novavision", "list", "--json"])
    monkeypatch.setattr(NovaVisionCLI, "handle_list_command", lambda self, args: None)
    seen = {}
    monkeypatch.setattr(
        "novavision.cli.begin_update_check",
        lambda json_mode=False: seen.setdefault("begin", json_mode),
    )
    monkeypatch.setattr("novavision.cli.finish_update_check", lambda check: None)
    NovaVisionCLI().run()
    assert seen["begin"] is True


def test_self_update_command_does_not_check_for_a_notice(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["novavision", "self-update"])
    monkeypatch.setattr(NovaVisionCLI, "handle_self_update", lambda self, args: None)
    called = []
    monkeypatch.setattr(
        "novavision.cli.begin_update_check",
        lambda json_mode=False: called.append(json_mode),
    )
    monkeypatch.setattr("novavision.cli.finish_update_check", lambda check: called.append(check))
    NovaVisionCLI().run()
    assert called == [None]


def test_listen_does_not_check_for_cli_updates(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["novavision", "_listen"])
    monkeypatch.setattr("novavision.cli.serve_update_listener", lambda logger: 0)
    called = []
    monkeypatch.setattr(
        "novavision.cli.begin_update_check",
        lambda json_mode=False: called.append("begin"),
    )
    with pytest.raises(SystemExit):
        NovaVisionCLI().run()
    assert called == []


def test_invalid_cache_is_ignored(nv_home):
    path = cache_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"latest": "nope", "checked_at": 1}), encoding="utf-8")
    assert read_cache() is None
