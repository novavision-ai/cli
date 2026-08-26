import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from novavision.docker_manager import DockerManager

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "compose"


def _docker_ready():
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="Live Compose coverage runs on Linux CI"),
    pytest.mark.skipif(not _docker_ready(), reason="Docker daemon is not available"),
]


def _seed_server(nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        (FIXTURES / "docker-compose.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (app_folder / "docker-compose.yml").write_text(
        (FIXTURES / "demo" / "docker-compose.yml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return server_folder


def test_compose_start_app_and_stop(fake_logger, nv_home):
    server_folder = _seed_server(nv_home)
    manager = DockerManager(logger=fake_logger)
    try:
        assert manager.start_server_folder(server_folder) is True
        assert manager._server_is_running(server_folder) is True
        assert manager._start_app("demo") is True
        assert manager._stop_app("demo") is True
    finally:
        manager.stop_server_folder(server_folder)
