import json
import subprocess
from unittest.mock import Mock, patch

from novavision.docker_manager import DockerManager


def test_metadata_path_uses_home_novavision(fake_logger, nv_home):
    manager = DockerManager(logger=fake_logger)
    expected = nv_home / ".novavision" / "servers.json"
    assert manager._metadata_path() == expected
    assert expected.as_posix().endswith("/.novavision/servers.json") or str(
        expected
    ).endswith("\\.novavision\\servers.json")


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


def test_choose_server_folder_table_includes_selection_numbers(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server"
    first = server_root / "abcdef"
    second = server_root / "ghijkl"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    fake_logger.answers = ["2"]
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=False):
        assert manager.choose_server_folder(server_root) == second
    assert fake_logger.tables
    assert fake_logger.tables[0]["headers"][0] == "#"
    assert [row[0] for row in fake_logger.tables[0]["rows"]] == ["1", "2"]


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
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manager = DockerManager(logger=fake_logger)
    apps = manager._server_app_compose_files(server_folder)
    assert list(apps) == ["demo"]
    assert apps["demo"] == app_folder / "docker-compose.yml"


def test_start_server_folder_starts_host_metrics(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    server_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services:\n  nv-server:\n    image: alpine:3.20\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_start_server", return_value=True):
        with patch(
            "novavision.docker_manager.start_host_metrics",
            return_value={"pid": 1, "port": 18765},
        ) as start:
            assert manager.start_server_folder(server_folder) is True
    start.assert_called_once()


def test_stop_server_folder_stops_idle_host_metrics(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    server_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "run_docker_compose"):
        with patch.object(manager, "remove_network", return_value=True):
            with patch.object(manager, "_server_is_running", return_value=False):
                with patch("novavision.docker_manager.stop_host_metrics") as stop:
                    assert manager.stop_server_folder(server_folder) is True
    stop.assert_called_once()


def test_stop_host_metrics_stays_up_when_another_server_runs(fake_logger, nv_home):
    first = nv_home / ".novavision" / "Server" / "aaaaaa"
    second = nv_home / ".novavision" / "Server" / "bbbbbb"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    manager = DockerManager(logger=fake_logger)

    def running(folder):
        return folder.name == "bbbbbb"

    with patch.object(manager, "_server_is_running", side_effect=running):
        with patch("novavision.docker_manager.stop_host_metrics") as stop:
            manager._stop_host_metrics_if_idle()
    stop.assert_not_called()


class _FakeComposeStdout:
    def __init__(self, data):
        self._data = data
        self._pos = 0

    def read(self, size=-1):
        if self._pos >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class _FakeComposeProcess:
    def __init__(self, data, returncode=0):
        self.stdout = _FakeComposeStdout(data)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def test_run_docker_compose_streams_progress_without_newlines(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(
        b"Downloading pillow.whl (4.4 MB)\r 10%\r 100%\nDone\n"
    )

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch(
            "novavision.docker_manager.subprocess.Popen", return_value=process
        ) as popen:
            with patch("novavision.docker_manager.subprocess.run") as run:
                manager.run_docker_compose(compose_file, "build", "--no-cache")

    popen.assert_called_once()
    run.assert_not_called()
    assert any(
        "Downloading pillow.whl" in message
        for message in fake_logger.messages_of("process")
    )
    assert any("100%" in message for message in fake_logger.messages_of("process"))


def test_run_docker_compose_prints_final_progress_line_to_console(
    fake_logger, tmp_path
):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    fake_logger.console = Mock()
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(
        b"Downloading pillow.whl (4.4 MB)\r 10%\r 100%\nDone\n"
    )

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch("novavision.docker_manager.subprocess.Popen", return_value=process):
            manager.run_docker_compose(compose_file, "build", "--no-cache")

    printed = [call.args[0] for call in fake_logger.console.print.call_args_list]
    assert printed == ["100%", "Done"]


def test_run_docker_compose_uses_print_stream(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    fake_logger.print_stream = Mock()
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(b"#20 DONE 92.1s\n")

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch("novavision.docker_manager.subprocess.Popen", return_value=process):
            manager.run_docker_compose(compose_file, "build", "--no-cache")

    fake_logger.print_stream.assert_called_once_with("#20 DONE 92.1s")


def test_write_compose_console_does_not_crop_lines(fake_logger):
    fake_logger.console = Mock()
    manager = DockerManager(logger=fake_logger)
    long_line = "Successfully installed " + ("package " * 40)
    manager._write_compose_console(long_line)
    kwargs = fake_logger.console.print.call_args.kwargs
    assert kwargs.get("crop") is False
    assert kwargs.get("soft_wrap") is True


def test_run_docker_compose_skips_blank_ansi_progress_lines(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    fake_logger.console = Mock()
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(
        b"\x1b[2K\r          \r\x1b[2K\n#16 Downloading pillow\n"
    )

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch("novavision.docker_manager.subprocess.Popen", return_value=process):
            manager.run_docker_compose(compose_file, "build", "--no-cache")

    printed = [call.args[0] for call in fake_logger.console.print.call_args_list]
    assert printed == ["#16 Downloading pillow"]


def test_run_docker_compose_build_uses_plain_progress(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(b"done\n")

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch(
            "novavision.docker_manager.subprocess.Popen", return_value=process
        ) as popen:
            manager.run_docker_compose(compose_file, "build", "--no-cache")

    command = popen.call_args[0][0]
    assert command[:4] == ["docker", "compose", "--progress", "plain"]
    assert command[4:6] == ["-f", str(compose_file)]
    assert command[6:] == ["build", "--no-cache"]


def test_run_docker_compose_capture_raises_on_failure(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    fake_logger.log_file_path = str(tmp_path / "install.log")
    manager = DockerManager(logger=fake_logger)
    process = _FakeComposeProcess(b"failed to build\n", returncode=1)

    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch("novavision.docker_manager.subprocess.Popen", return_value=process):
            try:
                manager.run_docker_compose(compose_file, "build")
            except subprocess.CalledProcessError as error:
                assert error.returncode == 1
                assert "failed to build" in error.stderr
            else:
                raise AssertionError("expected CalledProcessError")


def test_start_app_requires_running_server(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=False):
        assert manager._start_app("demo") is False
    assert any("not running" in message for message in fake_logger.messages_of("error"))


def test_running_status_mark_uses_colored_circles_when_encoding_allows(fake_logger):
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_stdout_can_encode_status_marks", return_value=True):
        assert manager._running_status_mark(True) == "[green]●[/green]"
        assert manager._running_status_mark(False) == "[red]●[/red]"


def test_running_status_mark_falls_back_to_ascii(fake_logger):
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_stdout_can_encode_status_marks", return_value=False):
        assert manager._running_status_mark(True) == "[green]*[/green]"
        assert manager._running_status_mark(False) == "[red]*[/red]"


def test_stdout_can_encode_status_marks_utf8(fake_logger):
    manager = DockerManager(logger=fake_logger)
    assert manager._stdout_can_encode_status_marks(encoding="utf-8") is True


def test_stdout_can_encode_status_marks_ascii_non_tty(fake_logger):
    manager = DockerManager(logger=fake_logger)
    assert (
        manager._stdout_can_encode_status_marks(encoding="ascii", is_tty=False) is False
    )


def test_format_server_details_prefixes_running_state(fake_logger, tmp_path):
    folder = tmp_path / "abcdef"
    folder.mkdir()
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=True):
        with patch.object(
            manager, "_stdout_can_encode_status_marks", return_value=True
        ):
            text = manager._format_server_details(folder, {})
    assert text.startswith("[green]●[/green] abcdef")


def test_format_server_details_prefixes_stopped_state(fake_logger, tmp_path):
    folder = tmp_path / "abcdef"
    folder.mkdir()
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=False):
        with patch.object(
            manager, "_stdout_can_encode_status_marks", return_value=True
        ):
            text = manager._format_server_details(folder, {})
    assert text.startswith("[red]●[/red] abcdef")


def test_list_servers_prints_table(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server" / "abcdef"
    server_root.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        json.dumps(
            {"abcdef": {"workspace": "ci", "host": "https://suite.novavision.ai"}}
        ),
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=True):
        assert manager.list_servers() is True
    assert fake_logger.tables
    assert fake_logger.tables[0]["headers"][1] == "ID"


def test_list_servers_json(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server" / "abcdef"
    server_root.mkdir(parents=True)
    fake_logger.json_mode = True
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_server_is_running", return_value=False):
        assert manager.list_servers() is True
    assert fake_logger.json_payloads
    assert fake_logger.json_payloads[0][0]["id"] == "abcdef"
    assert fake_logger.json_payloads[0][0]["running"] is False


def test_show_logs_requires_compose(fake_logger, nv_home):
    server_root = nv_home / ".novavision" / "Server" / "abcdef"
    server_root.mkdir(parents=True)
    manager = DockerManager(logger=fake_logger)
    assert manager.show_logs("server", "abcdef") is False


def test_warn_remaining_apps(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    manager._warn_remaining_apps(server_folder, close_apps=False)
    assert any(
        "--close-apps" in message for message in fake_logger.messages_of("warning")
    )
    fake_logger.messages.clear()
    manager._warn_remaining_apps(server_folder, close_apps=True)
    assert not fake_logger.messages_of("warning")


def test_load_server_metadata(fake_logger, nv_home):
    meta_dir = nv_home / ".novavision"
    meta_dir.mkdir(exist_ok=True)
    (meta_dir / "servers.json").write_text(
        json.dumps({"ci-server": {"workspace": "ci"}}),
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    assert manager._load_server_metadata()["ci-server"]["workspace"] == "ci"
