import ctypes
import platform
from unittest.mock import Mock, patch

import pytest

from novavision.service_manager import ServiceManager


def test_linux_privilege_check_rejects_non_root(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Linux")
    service = ServiceManager(logger=fake_logger)
    monkeypatch.setattr(service, "_is_root", lambda: False)
    assert service._validate_service_privileges() is False
    assert any("sudo" in message.lower() or "root" in message.lower() for message in fake_logger.messages_of("error"))


def test_linux_privilege_check_accepts_root(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Linux")
    service = ServiceManager(logger=fake_logger)
    monkeypatch.setattr(service, "_is_root", lambda: True)
    assert service._validate_service_privileges() is True


def test_windows_admin_detection_true(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Windows")
    fake_shell32 = Mock()
    fake_shell32.IsUserAnAdmin.return_value = 1
    fake_windll = Mock()
    fake_windll.shell32 = fake_shell32
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    service = ServiceManager(logger=fake_logger)
    assert service._is_windows_admin() is True


def test_windows_admin_detection_false(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Windows")
    fake_shell32 = Mock()
    fake_shell32.IsUserAnAdmin.return_value = 0
    fake_windll = Mock()
    fake_windll.shell32 = fake_shell32
    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)
    service = ServiceManager(logger=fake_logger)
    assert service._is_windows_admin() is False


def test_windows_privilege_check_rejects_non_admin(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Windows")
    service = ServiceManager(logger=fake_logger)
    monkeypatch.setattr(service, "_is_windows_admin", lambda: False)
    assert service._validate_service_privileges() is False
    assert any("Administrator" in message for message in fake_logger.messages_of("error"))


def test_windows_privilege_check_accepts_admin(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Windows")
    service = ServiceManager(logger=fake_logger)
    monkeypatch.setattr(service, "_is_windows_admin", lambda: True)
    assert service._validate_service_privileges() is True


def test_disable_server_requires_privileges(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    service = ServiceManager(logger=fake_logger)
    with patch.object(service, "_validate_service_privileges", return_value=False):
        with patch.object(service, "_disable_native_service") as disable_native:
            assert service.disable_server(server_name="abcdef") is False
    disable_native.assert_not_called()


@pytest.mark.skipif(platform.system() == "Windows", reason="geteuid is a Unix API")
def test_is_root_matches_geteuid(fake_logger, nv_home, monkeypatch):
    service = ServiceManager(logger=fake_logger)
    monkeypatch.setattr("novavision.service_manager.os.geteuid", lambda: 0)
    assert service._is_root() is True
    monkeypatch.setattr("novavision.service_manager.os.geteuid", lambda: 1000)
    assert service._is_root() is False


def test_non_windows_is_not_admin(fake_logger, nv_home, monkeypatch):
    monkeypatch.setattr("novavision.service_manager.platform.system", lambda: "Linux")
    service = ServiceManager(logger=fake_logger)
    assert service._is_windows_admin() is False


def test_normalize_service_apps(fake_logger, nv_home):
    service = ServiceManager(logger=fake_logger)
    assert service._normalize_service_apps(None) == []
    assert service._normalize_service_apps(["demo", "demo", " other "]) == ["demo", "other"]
    assert service._normalize_service_apps(["demo", "*"]) == ["*"]


def test_docker_desktop_startup_skips_prompt_when_not_tty(fake_logger, nv_home, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("novavision.service_manager.sys.stdin", Mock(isatty=lambda: False))
    service = ServiceManager(logger=fake_logger)
    assert service._confirm_docker_desktop_startup("Windows") is True
    assert not fake_logger.messages_of("question")


def test_docker_desktop_startup_skips_prompt_in_ci(fake_logger, nv_home, monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr("novavision.service_manager.sys.stdin", Mock(isatty=lambda: True))
    service = ServiceManager(logger=fake_logger)
    assert service._confirm_docker_desktop_startup("Windows") is True
    assert not fake_logger.messages_of("question")


def test_docker_desktop_startup_asks_when_tty(fake_logger, nv_home, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("novavision.service_manager.sys.stdin", Mock(isatty=lambda: True))
    fake_logger.answers = ["y"]
    service = ServiceManager(logger=fake_logger)
    assert service._confirm_docker_desktop_startup("Windows") is True
    assert fake_logger.messages_of("question")


def test_docker_desktop_startup_rejects_when_not_enabled(fake_logger, nv_home, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("novavision.service_manager.sys.stdin", Mock(isatty=lambda: True))
    fake_logger.answers = ["n"]
    service = ServiceManager(logger=fake_logger)
    assert service._confirm_docker_desktop_startup("Windows") is False
    assert any("Docker Desktop" in message for message in fake_logger.messages_of("error"))


def test_run_service_action_starts_boot_stack_with_apps(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision").mkdir(exist_ok=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"service": {"enabled": true, "apps": ["demo"]}}}',
        encoding="utf-8",
    )
    docker = Mock()
    docker.get_server_folder.return_value = server_folder
    docker.start_boot_stack.return_value = True
    service = ServiceManager(logger=fake_logger, docker_manager=docker)
    assert service.run_service_action("start-server", "abcdef") is True
    docker.start_boot_stack.assert_called_once_with(server_folder, ["demo"])
    docker.wait_for_docker.assert_not_called()
    docker.start_server_apps.assert_not_called()
