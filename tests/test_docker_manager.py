import json
from unittest.mock import patch

from novavision.docker_manager import DockerManager


def test_metadata_path_uses_home_novavision(fake_logger, nv_home):
    manager = DockerManager(logger=fake_logger)
    expected = nv_home / ".novavision" / "servers.json"
    assert manager._metadata_path() == expected
    assert expected.as_posix().endswith("/.novavision/servers.json") or str(expected).endswith(
        "\\.novavision\\servers.json"
    )


def test_host_label_for_known_suites(fake_logger):
    manager = DockerManager(logger=fake_logger)
    assert manager._host_label("https://alfa.suite.novavision.ai/") == "alfa"
    assert manager._host_label("https://dev.example.com") == "dev"
    assert manager._host_label("https://suite.novavision.ai/") == "suite"
    assert manager._host_label("") == "Unknown"


def test_format_created_at(fake_logger):
    manager = DockerManager(logger=fake_logger)
    assert manager._format_created_at("2026-08-26T13:00:00") == "26 Aug 2026, 13:00"
    assert manager._format_created_at("Unknown") == "Unknown"


def test_choose_server_folder_selects_single_visible_folder(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server"
    only = server_root / "abcdef"
    only.mkdir(parents=True)
    manager = DockerManager(logger=fake_logger)
    assert manager.choose_server_folder(server_root) == only


def test_get_server_folder_by_id(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server" / "ci-server"
    server_root.mkdir(parents=True)
    manager = DockerManager(logger=fake_logger)
    assert manager.get_server_folder("ci-server") == server_root
    assert manager.get_server_folder("missing") is None


def test_server_app_compose_files_skips_server_compose(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manager = DockerManager(logger=fake_logger)
    apps = manager._server_app_compose_files(server_folder)
    assert list(apps) == ["demo"]
    assert apps["demo"] == app_folder / "docker-compose.yml"


def test_run_docker_compose_is_mocked_and_uses_compose_v2(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)

    with patch("novavision.docker_manager.shutil.which", side_effect=lambda name: name == "docker"):
        with patch("novavision.docker_manager.subprocess.run") as run:
            manager.run_docker_compose(compose_file, "up", "-d")

    run.assert_called_once()
    command = run.call_args[0][0]
    assert command[:2] == ["docker", "compose"]
    assert command[2:4] == ["-f", str(compose_file)]
    assert command[4:] == ["up", "-d"]


def test_start_app_requires_running_server(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=False):
        assert manager._start_app("demo") is False
    assert any("not running" in message for message in fake_logger.messages_of("error"))


def test_load_server_metadata(fake_logger, nv_home):
    meta_dir = nv_home / ".novavision"
    meta_dir.mkdir()
    (meta_dir / "servers.json").write_text(
        json.dumps({"ci-server": {"workspace": "ci"}}),
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    assert manager._load_server_metadata()["ci-server"]["workspace"] == "ci"
