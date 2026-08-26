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
