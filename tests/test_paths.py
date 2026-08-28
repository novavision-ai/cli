import os
import platform
from pathlib import Path
from unittest.mock import Mock

import pytest

from novavision.docker_manager import DockerManager
from novavision.service_manager import ServiceManager


def test_config_dir_is_home_novavision(fake_logger, nv_home):
    manager = DockerManager(logger=fake_logger)
    path = manager._metadata_path()
    assert path.parent == nv_home / ".novavision"
    assert path.name == "servers.json"


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows path handling")
def test_windows_home_uses_userprofile_not_appdata(fake_logger, nv_home, monkeypatch):
    monkeypatch.setenv("APPDATA", str(nv_home / "AppData" / "Roaming"))
    manager = DockerManager(logger=fake_logger)
    path = manager._metadata_path()
    appdata = Path(os.environ["APPDATA"])
    assert appdata not in path.parents
    assert path.parent == nv_home / ".novavision"
    assert path.drive


@pytest.mark.skipif(platform.system() != "Windows", reason="Windows command quoting")
def test_windows_service_command_uses_list2cmdline(fake_logger, nv_home):
    service = ServiceManager(logger=fake_logger)
    command = service._command_string([r"C:\Program Files\novavision.exe", "_service"])
    assert "Program Files" in command


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS launchd path")
def test_macos_launchd_uses_library_launch_agents(fake_logger, nv_home):
    service = ServiceManager(logger=fake_logger)
    scope = service._launchd_scope()
    assert scope["plist_dir"] == nv_home / "Library" / "LaunchAgents"
    assert "Application Support" not in str(scope["plist_dir"])


@pytest.mark.skipif(platform.system() != "Linux", reason="Linux sudo home remap")
def test_linux_service_home_follows_sudo_user(fake_logger, tmp_path, monkeypatch):
    import pwd

    user_home = tmp_path / "sudo-user"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "orig-home"))
    monkeypatch.setenv("SUDO_USER", "runner")
    monkeypatch.setattr(pwd, "getpwnam", lambda name: Mock(pw_dir=str(user_home)))
    service = ServiceManager(logger=fake_logger)
    assert service.service_home == Path(user_home)
    assert os.environ["HOME"] == str(user_home)
