import json
import subprocess
from unittest.mock import Mock, patch

import yaml

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
    assert command[4:8] == [
        "--project-directory",
        str(tmp_path),
        "-f",
        str(compose_file),
    ]
    assert command[8:] == ["build", "--no-cache"]


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


def test_compose_invocation_sets_project_directory(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        command = manager._compose_invocation(compose_file, "up", "-d")
    assert command[:2] == ["docker", "compose"]
    assert command[2:6] == [
        "--project-directory",
        str(tmp_path),
        "-f",
        str(compose_file),
    ]
    assert command[6:] == ["up", "-d"]


def test_external_networks_override_marks_existing_named_networks(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services: {}\n"
        "networks:\n"
        "  app_net:\n"
        "    name: c9844b-a3dad8-network-novavision\n"
        "  common_net:\n"
        "    name: c9844b-network-common-novavision\n"
        "  missing_net:\n"
        "    name: missing-network\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    with patch.object(
        manager,
        "_docker_network_names",
        return_value={
            "c9844b-a3dad8-network-novavision",
            "c9844b-network-common-novavision",
        },
    ):
        override = manager._external_networks_override(compose_file)
    assert override is not None
    data = yaml.safe_load(override.read_text(encoding="utf-8"))
    assert data["networks"]["app_net"] == {
        "name": "c9844b-a3dad8-network-novavision",
        "external": True,
    }
    assert data["networks"]["common_net"]["external"] is True
    assert "missing_net" not in data["networks"]


def test_run_docker_compose_up_adds_external_network_override(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services: {}\nnetworks:\n  app_net:\n    name: app-net\n",
        encoding="utf-8",
    )
    override = tmp_path / ".novavision-compose.networks.yml"
    manager = DockerManager(logger=fake_logger)
    with patch(
        "novavision.docker_manager.shutil.which",
        side_effect=lambda name: name == "docker",
    ):
        with patch.object(manager, "_external_networks_override", return_value=override):
            with patch("novavision.docker_manager.subprocess.run") as run:
                manager.run_docker_compose(compose_file, "up", "-d")
    command = run.call_args[0][0]
    assert command.count("-f") == 2
    assert str(override) in command
    assert command[-2:] == ["up", "-d"]


def test_compose_uses_nvidia(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  media:\n    image: alpine\n    runtime: nvidia\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    assert manager._compose_uses_nvidia(compose_file) is True
    compose_file.write_text("services:\n  redis:\n    image: alpine\n", encoding="utf-8")
    assert manager._compose_uses_nvidia(compose_file) is False


def test_wait_for_docker_waits_for_nvidia_when_compose_needs_it(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  media:\n    image: alpine\n    runtime: nvidia\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    runtime_calls = {"count": 0}

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "info"] and "--format" in args:
            runtime_calls["count"] += 1
            stdout = '{"nvidia":{}}' if runtime_calls["count"] > 1 else "{}"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch("novavision.docker_manager.shutil.which", return_value="/bin/docker"):
        with patch("novavision.docker_manager.subprocess.run", side_effect=fake_run):
            with patch("novavision.docker_manager.time.sleep"):
                assert manager.wait_for_docker(compose_files=[compose_file]) is True
    assert runtime_calls["count"] == 2


def test_wait_for_stack_ready_waits_for_wsl_and_media_ports(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services:\n  wsl:\n    image: alpine\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DIGINOVA_WSL_SERVICE_PORT=6525\nDIGINOVA_MEDIA_SERVICE_PORT=8817\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    opened = set()

    def fake_port(host, port, timeout=0.5):
        opened.add(int(port))
        return True

    with patch.object(manager, "_named_containers_running", return_value=True):
        with patch.object(manager, "_tcp_port_open", side_effect=fake_port):
            assert (
                manager.wait_for_stack_ready(
                    compose_file, label="app demo", folder=tmp_path
                )
                is True
            )
    assert opened == {6525, 8817}


def test_wait_for_stack_ready_fails_when_containers_never_run(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n  diginova-media:\n    image: alpine\n", encoding="utf-8"
    )
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_named_containers_running", return_value=False):
        with patch("novavision.docker_manager.time.sleep"):
            assert (
                manager.wait_for_stack_ready(
                    compose_file,
                    label="app demo",
                    folder=tmp_path,
                    timeout_seconds=1,
                    interval_seconds=0,
                )
                is False
            )
    assert any(
        "did not become ready" in message for message in fake_logger.messages_of("error")
    )


def test_start_boot_stack_does_not_start_apps_until_server_ready(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "wait_for_docker", return_value=True):
        with patch.object(manager, "start_server_folder", return_value=True):
            with patch.object(manager, "wait_for_stack_ready", return_value=False):
                with patch.object(manager, "start_server_apps") as start_apps:
                    assert manager.start_boot_stack(server_folder, ["demo"]) is False
    start_apps.assert_not_called()


def test_start_boot_stack_starts_apps_after_server_is_ready(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "wait_for_docker", return_value=True) as wait_docker:
        with patch.object(manager, "start_server_folder", return_value=True) as start_server:
            with patch.object(manager, "wait_for_stack_ready", return_value=True) as wait_ready:
                with patch.object(manager, "start_server_apps", return_value=True) as start_apps:
                    assert manager.start_boot_stack(server_folder, ["demo"]) is True
    compose_files = wait_docker.call_args.kwargs["compose_files"]
    assert server_folder / "docker-compose.yml" in compose_files
    assert app_folder / "docker-compose.yml" in compose_files
    start_server.assert_called_once()
    assert start_server.call_args.kwargs["retries"] == manager.BOOT_START_RETRIES
    wait_ready.assert_called_once()
    start_apps.assert_called_once()
    assert start_apps.call_args.args[1] == ["demo"]
    assert start_apps.call_args.kwargs["wait_ready"] is True
    assert start_apps.call_args.kwargs["retries"] == manager.BOOT_START_RETRIES


def test_start_server_retries_failed_compose_up(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    error = subprocess.CalledProcessError(1, ["docker", "compose", "up", "-d"])
    with patch.object(
        manager, "run_docker_compose", side_effect=[error, None]
    ) as compose_up:
        with patch("novavision.docker_manager.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["docker", "ps"], 0, stdout="", stderr=""
            )
            with patch("novavision.docker_manager.time.sleep"):
                assert manager._start_server(
                    compose_file, retries=2, retry_delay=0
                ) is True
    assert compose_up.call_count == 2


def test_start_server_apps_waits_for_each_app(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "ci-server"
    app_folder = server_folder / "demo"
    app_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services: {}\n", encoding="utf-8"
    )
    (app_folder / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    with patch.object(manager, "_start_server", return_value=True) as start:
        with patch.object(manager, "wait_for_stack_ready", return_value=True) as wait:
            assert manager.start_server_apps(
                server_folder, ["demo"], wait_ready=True, retries=3
            ) is True
    start.assert_called_once()
    assert start.call_args.kwargs["retries"] == 3
    wait.assert_called_once()
    assert wait.call_args.kwargs["label"] == "app demo"


def test_compose_service_containers_expands_env_names(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  pytorch:\n"
        "    container_name: ${AGENT}-${APP_ID}-diginova-pytorch-service\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("AGENT=C9844B\nAPP_ID=A3DAD8\n", encoding="utf-8")
    manager = DockerManager(logger=fake_logger)
    assert manager._compose_service_containers(compose_file) == [
        ("pytorch", "C9844B-A3DAD8-diginova-pytorch-service")
    ]


def test_start_existing_named_containers_starts_stopped_and_skips_compose(
    fake_logger, tmp_path
):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  pytorch:\n"
        "    container_name: C9844B-A3DAD8-diginova-pytorch-service\n"
        "  media:\n"
        "    container_name: C9844B-A3DAD8-diginova-media-service\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)
    states = {
        "C9844B-A3DAD8-diginova-pytorch-service": False,
        "C9844B-A3DAD8-diginova-media-service": True,
    }
    with patch.object(
        manager, "_container_running_state", side_effect=lambda name: states[name]
    ):
        with patch("novavision.docker_manager.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["docker", "ps"], 0, stdout="", stderr=""
            )
            with patch.object(manager, "run_docker_compose") as compose_up:
                assert manager._start_server(compose_file) is True
    started = [
        call.args[0]
        for call in run.call_args_list
        if call.args and call.args[0][:2] == ["docker", "start"]
    ]
    assert ["docker", "start", "C9844B-A3DAD8-diginova-pytorch-service"] in started
    compose_up.assert_not_called()


def test_start_server_compose_up_only_missing_services(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  pytorch:\n"
        "    container_name: existing-pytorch\n"
        "  media:\n"
        "    container_name: missing-media\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)

    def fake_state(name):
        if name == "existing-pytorch":
            return False
        return None

    with patch.object(manager, "_container_running_state", side_effect=fake_state):
        with patch("novavision.docker_manager.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                ["docker", "ps"], 0, stdout="", stderr=""
            )
            with patch.object(manager, "run_docker_compose") as compose_up:
                assert manager._start_server(compose_file) is True
    compose_up.assert_called_once()
    assert compose_up.call_args.args[1:] == ("up", "-d", "media")


def test_stale_container_is_removed_and_recreated(fake_logger, tmp_path):
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        "services:\n"
        "  pytorch:\n"
        "    container_name: stale-pytorch\n",
        encoding="utf-8",
    )
    manager = DockerManager(logger=fake_logger)

    def fake_run(args, **kwargs):
        if args[:2] == ["docker", "start"]:
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr="failed to set up container networking: network not found",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    with patch.object(manager, "_container_running_state", return_value=False):
        with patch("novavision.docker_manager.subprocess.run", side_effect=fake_run) as run:
            with patch.object(manager, "run_docker_compose") as compose_up:
                assert manager._start_server(compose_file) is True
    removed = [
        call.args[0]
        for call in run.call_args_list
        if call.args and call.args[0][:3] == ["docker", "rm", "-f"]
    ]
    assert ["docker", "rm", "-f", "stale-pytorch"] in removed
    compose_up.assert_called_once()
    assert compose_up.call_args.args[1:] == ("up", "-d")
    assert any(
        "recreating it" in message for message in fake_logger.messages_of("warning")
    )
