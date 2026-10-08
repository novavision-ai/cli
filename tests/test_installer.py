import hashlib
import io
import json
import zipfile
from unittest.mock import Mock, patch

from novavision.installer import Installer


def test_format_host_adds_https_and_trailing_slash(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    assert installer.format_host("alfa.suite.novavision.ai") == "https://alfa.suite.novavision.ai/"


def test_format_host_upgrades_http(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    assert installer.format_host("http://suite.novavision.ai") == "https://suite.novavision.ai/"


def test_format_host_keeps_https(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    assert installer.format_host("https://suite.novavision.ai/") == "https://suite.novavision.ai/"


def test_request_to_endpoint_sends_bearer_token(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    response = Mock()
    with patch("novavision.installer.requests.get", return_value=response) as get:
        result = installer.request_to_endpoint(
            "get",
            "https://example.test/api",
            auth_token="secret-token",
        )
    get.assert_called_once_with(
        "https://example.test/api",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert result is response


def test_prepare_local_device_data(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    data = installer._prepare_device_data(
        "local",
        {
            "device_name": "ci-runner",
            "serial": "ABC123",
            "processor": "cpu",
            "cpu": "CI CPU",
            "gpu": "GPU not found",
            "os": "Linux",
            "disk": "1G/1G",
            "memory": "2.00 GB",
            "architecture": "x86_64",
            "platform": "PC",
        },
        "7001",
    )
    assert data["device_type"] == Installer.DEVICE_TYPE_LOCAL
    assert data["os_api_port"] == "7001"
    assert data["name"] == "ci-runner"


def test_select_port_uses_explicit_value(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    assert installer._select_port("7001") == "7001"
    assert installer._select_port("99999") is None


def test_select_port_defaults_when_non_interactive(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    installer.non_interactive = True
    assert installer._select_port() == "7001"


def test_response_error_text_prefers_json_message(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    response = Mock()
    response.status_code = 400
    response.json.return_value = {"message": "bad token"}
    response.text = '{"message": "bad token"}'
    assert "bad token" in installer._response_error_text(response)
    assert "400" in installer._response_error_text(response)


def test_response_error_text_reports_status_without_html(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    response = Mock()
    response.status_code = 404
    response.json.side_effect = ValueError("not json")
    response.text = "<html><title>Not Found</title><body>nginx</body></html>"
    assert installer._response_error_text(response) == "Request failed (HTTP 404)"

    response.json.side_effect = None
    response.json.return_value = {"message": "Workspace not found"}
    assert (
        installer._response_error_text(response)
        == "Workspace not found (HTTP 404)"
    )


def test_unique_workspaces_keeps_one_row_per_id(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    rows = [
        {"id_workspace_user": 1, "id_workspace": 10, "workspace": {"name": "ci"}},
        {"id_workspace_user": 2, "id_workspace": "10", "workspace": {"name": "ci"}},
        {"id_workspace_user": 3, "id_workspace": 11, "workspace": {"name": "other"}},
    ]
    unique = installer._unique_workspaces(rows)
    assert [row["id_workspace_user"] for row in unique] == [1, 3]


def test_set_workspace_posts_membership_id(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    response = Mock(status_code=200)
    with patch.object(installer, "request_to_endpoint", return_value=response) as request:
        assert installer._set_workspace("https://suite.novavision.ai/", "tok", 15)
    request.assert_called_once_with(
        method="post",
        endpoint="https://suite.novavision.ai/api/workspace/default/set-workspace",
        data={"id": 15},
        auth_token="tok",
    )


def test_send_deploy_status_keeps_token_out_of_the_url(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    response = Mock(status_code=200)
    endpoint = "https://suite.novavision.ai/api/deployment/default/9"
    with patch.object(installer, "request_to_endpoint", return_value=response) as request:
        installer.send_deploy_status({"is_deploy": 1}, "device-token", endpoint)
    request.assert_called_once_with(
        "put",
        endpoint=endpoint,
        data={"is_deploy": 1},
        auth_token="device-token",
    )
    assert "access-token" not in endpoint


def test_uninstall_confirm_cancels(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://suite.novavision.ai"}}',
        encoding="utf-8",
    )
    fake_logger.answers = ["n"]
    installer = Installer(logger=fake_logger)
    installer.non_interactive = False
    with patch.object(installer, "_delete_device") as delete_device:
        assert installer.uninstall(token="ci-token", server_name="abcdef") is False
    delete_device.assert_not_called()
    assert server_folder.exists()


def test_select_gpu_picks_first_when_non_interactive(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    installer.non_interactive = True
    device_info = {"gpu": ["GPU-A", "GPU-B"]}
    installer._select_gpu(device_info)
    assert device_info["gpu"] == "GPU-A"


def test_uninstall_deletes_device_and_local_folder(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://alfa.suite.novavision.ai"}}',
        encoding="utf-8",
    )
    installer = Installer(logger=fake_logger)
    installer.non_interactive = True
    with patch.object(installer, "_delete_device", return_value=True) as delete_device:
        with patch.object(installer.docker, "close_server_apps", return_value=True):
            with patch.object(installer.docker, "stop_server_folder", return_value=True):
                assert installer.uninstall(token="ci-token", server_name="abcdef") is True
    delete_device.assert_called_once_with(
        42, "https://alfa.suite.novavision.ai", "ci-token"
    )
    assert not server_folder.exists()
    assert "abcdef" not in installer._load_server_metadata()


def _write_enabled_server(nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://alfa.suite.novavision.ai", '
        '"service": {"enabled": true, "name": "novavision-server-abcdef"}}}',
        encoding="utf-8",
    )
    return server_folder


def _interactive_installer(fake_logger, answers=None, answer=None):
    if answers is None:
        answers = [answer, "y"] if answer is not None else ["y", "y"]
    fake_logger.answers = list(answers)
    installer = Installer(logger=fake_logger)
    installer.non_interactive = False
    return installer


def test_uninstall_disables_service_when_user_confirms(fake_logger, nv_home):
    server_folder = _write_enabled_server(nv_home)
    installer = _interactive_installer(fake_logger)
    with patch.object(installer.service, "_is_noninteractive", return_value=False):
        with patch.object(installer.service, "_validate_service_privileges", return_value=True):
            with patch.object(installer.service, "disable_server", return_value=True) as disable_server:
                with patch.object(installer, "_delete_device", return_value=True):
                    with patch.object(installer.docker, "close_server_apps", return_value=True):
                        with patch.object(installer.docker, "stop_server_folder", return_value=True):
                            assert installer.uninstall(token="ci-token", server_name="abcdef") is True
    disable_server.assert_called_once_with(server_name="abcdef")
    assert fake_logger.messages_of("question")
    assert not server_folder.exists()


def test_uninstall_disables_service_when_id_is_device_id(fake_logger, nv_home):
    _write_enabled_server(nv_home)
    installer = _interactive_installer(fake_logger)
    with patch.object(installer.service, "_is_noninteractive", return_value=False):
        with patch.object(installer.service, "_validate_service_privileges", return_value=True):
            with patch.object(installer.service, "disable_server", return_value=True) as disable_server:
                with patch.object(installer, "_delete_device", return_value=True):
                    with patch.object(installer.docker, "close_server_apps", return_value=True):
                        with patch.object(installer.docker, "stop_server_folder", return_value=True):
                            assert installer.uninstall(token="ci-token", server_name="42") is True
    disable_server.assert_called_once_with(server_name="abcdef")


def test_uninstall_skips_disable_when_service_not_enabled(fake_logger, nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://alfa.suite.novavision.ai"}}',
        encoding="utf-8",
    )
    installer = Installer(logger=fake_logger)
    installer.non_interactive = True
    with patch.object(installer.service, "disable_server", return_value=True) as disable_server:
        with patch.object(installer, "_delete_device", return_value=True):
            with patch.object(installer.docker, "close_server_apps", return_value=True):
                with patch.object(installer.docker, "stop_server_folder", return_value=True):
                    assert installer.uninstall(token="ci-token", server_name="abcdef") is True
    disable_server.assert_not_called()
    assert not fake_logger.messages_of("question")


def test_uninstall_stops_when_user_declines_service_disable(fake_logger, nv_home):
    server_folder = _write_enabled_server(nv_home)
    installer = _interactive_installer(fake_logger, answers=["y", "n"])
    with patch.object(installer.service, "_is_noninteractive", return_value=False):
        with patch.object(installer.service, "disable_server") as disable_server:
            with patch.object(installer, "_delete_device") as delete_device:
                assert installer.uninstall(token="ci-token", server_name="abcdef") is False
    disable_server.assert_not_called()
    delete_device.assert_not_called()
    assert server_folder.exists()
    assert "abcdef" in installer._load_server_metadata()


def test_uninstall_requires_disable_first_when_non_interactive(fake_logger, nv_home):
    server_folder = _write_enabled_server(nv_home)
    installer = Installer(logger=fake_logger)
    installer.non_interactive = True
    with patch.object(installer.service, "disable_server") as disable_server:
        with patch.object(installer, "_delete_device") as delete_device:
            assert installer.uninstall(token="ci-token", server_name="abcdef") is False
    disable_server.assert_not_called()
    delete_device.assert_not_called()
    assert server_folder.exists()
    assert any("Disable it first" in message for message in fake_logger.messages_of("error"))


def test_uninstall_stops_when_service_disable_fails(fake_logger, nv_home):
    server_folder = _write_enabled_server(nv_home)
    installer = _interactive_installer(fake_logger)
    with patch.object(installer.service, "_is_noninteractive", return_value=False):
        with patch.object(installer.service, "_validate_service_privileges", return_value=True):
            with patch.object(installer.service, "disable_server", return_value=False):
                with patch.object(installer, "_delete_device") as delete_device:
                    assert installer.uninstall(token="ci-token", server_name="abcdef") is False
    delete_device.assert_not_called()
    assert server_folder.exists()
    assert "abcdef" in installer._load_server_metadata()


def test_uninstall_stops_without_privileges_when_user_confirms_disable(fake_logger, nv_home):
    server_folder = _write_enabled_server(nv_home)
    installer = _interactive_installer(fake_logger)
    with patch.object(installer.service, "_is_noninteractive", return_value=False):
        with patch.object(installer.service, "_validate_service_privileges", return_value=False):
            with patch.object(installer.service, "disable_server") as disable_server:
                with patch.object(installer, "_delete_device") as delete_device:
                    assert installer.uninstall(token="ci-token", server_name="abcdef") is False
    disable_server.assert_not_called()
    delete_device.assert_not_called()
    assert server_folder.exists()


def _server_package_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Server/abcdef/docker-compose.yml",
            "services:\n  wsl:\n    image: new\n",
        )
        archive.writestr("Server/abcdef/modules/runner.py", "updated\n")
        archive.writestr("Server/abcdef/wsl/runtime.txt", "fresh\n")
        archive.writestr("Server/abcdef/.env", "KEEP=0\nAPI_WAIT_PER_NODE=20\n")
        archive.writestr(
            "Server/.env",
            "METRIC_CHANNEL=/ws/new\nROOT_PATH=/opt/package\n",
        )
    return buffer.getvalue()


def _installed_server(nv_home):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (server_folder / "docker-compose.yml").write_text(
        "services:\n  wsl:\n    image: old\n", encoding="utf-8"
    )
    (server_folder / ".env").write_text("KEEP=1\n", encoding="utf-8")
    app_file = server_folder / "apps" / "demo" / "local.txt"
    app_file.parent.mkdir(parents=True)
    app_file.write_text("keep\n", encoding="utf-8")
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://suite.novavision.ai", '
        '"workspace": "ci"}}',
        encoding="utf-8",
    )
    return server_folder


def _device_payload():
    return {
        "device_name": "ci-runner",
        "serial": "ABC",
        "processor": "cpu",
        "cpu": "CI CPU",
        "gpu": "GPU",
        "os": "Windows",
        "disk": "1G/1G",
        "memory": "2.00 GB",
        "architecture": "x86_64",
        "platform": "PC",
    }


def test_download_rebuilds_package_before_fetching_it(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    device = {
        "id_device": 42,
        "name": "desk",
        "device_type": 3,
        "os_api_port": "7001",
        "server_package": "old-package",
        "user": {"access_token": "device-token"},
    }
    rebuilt = dict(device)
    rebuilt["server_package"] = "new-package"
    get_response = Mock(status_code=200)
    get_response.json.return_value = device
    put_response = Mock(status_code=200)
    put_response.json.return_value = device
    rebuild_response = Mock(status_code=200)
    rebuild_response.json.return_value = rebuilt
    file_response = Mock(status_code=200, content=b"fresh-zip")
    calls = []

    def request(method, endpoint, data=None, auth_token=None, timeout=None):
        calls.append((method, endpoint, data, auth_token, timeout))
        if "rebuild-server" in endpoint:
            return rebuild_response
        if method == "put":
            return put_response
        if "get-file" in endpoint:
            return file_response
        return get_response

    with patch("novavision.installer.get_system_info", return_value=_device_payload()):
        with patch.object(installer, "request_to_endpoint", side_effect=request):
            content = installer._download_server_package(
                "https://suite.novavision.ai", "user-token", 42
            )

    assert content == b"fresh-zip"
    assert [call[0] for call in calls] == ["get", "put", "post", "get"]
    put_call = calls[1]
    assert put_call[2]["name"] == "desk"
    assert put_call[2]["device_type"] == Installer.DEVICE_TYPE_LOCAL
    assert put_call[2]["os_api_port"] == "7001"
    assert put_call[2]["serial"] == "ABC"
    rebuild_call = calls[2]
    assert "rebuild-server?id=42" in rebuild_call[1]
    assert "expand=user" in rebuild_call[1]
    assert rebuild_call[3] == "device-token"
    assert rebuild_call[4] == Installer.REBUILD_TIMEOUT_SECONDS
    assert "id=new-package" in calls[-1][1]
    assert calls[-1][3] == "device-token"
    assert "id=old-package" not in calls[-1][1]


def test_download_stops_when_rebuild_fails(fake_logger, nv_home):
    installer = Installer(logger=fake_logger)
    device = {
        "id_device": 42,
        "name": "desk",
        "device_type": 3,
        "server_package": "old-package",
        "user": {"access_token": "device-token"},
    }
    failed = Mock(status_code=502)
    failed.json.return_value = {"error": "agent missing", "code": "no_agent"}
    calls = []

    def request(method, endpoint, data=None, auth_token=None, timeout=None):
        calls.append(endpoint)
        if "rebuild-server" in endpoint:
            return failed
        response = Mock(status_code=200)
        response.json.return_value = device
        return response

    with patch("novavision.installer.get_system_info", return_value=_device_payload()):
        with patch.object(installer, "request_to_endpoint", side_effect=request):
            assert (
                installer._download_server_package(
                    "https://suite.novavision.ai", "user-token", 42
                )
                is None
            )

    assert not any("get-file" in endpoint for endpoint in calls)
    errors = " ".join(fake_logger.messages_of("error")).lower()
    assert "no_agent" in errors or "build failed" in errors


def test_update_confirm_cancels(fake_logger, nv_home):
    server_folder = _installed_server(nv_home)
    fake_logger.answers = ["n"]
    installer = Installer(logger=fake_logger)
    with patch.object(installer.docker, "_check_docker_available", return_value=True):
        with patch.object(installer, "_download_server_package") as download:
            assert installer.update(token="ci-token", server_name="abcdef") is False
    download.assert_not_called()
    assert "image: old" in (server_folder / "docker-compose.yml").read_text(
        encoding="utf-8"
    )


def test_update_applies_package_keeps_apps_and_rebuilds(fake_logger, nv_home):
    server_folder = _installed_server(nv_home)
    installer = Installer(logger=fake_logger)
    with patch.object(installer.docker, "_check_docker_available", return_value=True):
        with patch.object(
            installer, "_download_server_package", return_value=_server_package_zip()
        ):
            with patch.object(installer.docker, "_server_is_running", return_value=False):
                with patch.object(installer.docker, "run_docker_compose") as compose:
                    assert (
                        installer.update(
                            token="ci-token", server_name="abcdef", assume_yes=True
                        )
                        is True
                    )
    compose.assert_called_once_with(
        server_folder / "docker-compose.yml", "build", "--no-cache"
    )
    assert "image: new" in (server_folder / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert (server_folder / "modules" / "runner.py").read_text(encoding="utf-8") == (
        "updated\n"
    )
    assert (server_folder / "wsl" / "runtime.txt").read_text(encoding="utf-8") == (
        "fresh\n"
    )
    env_text = (server_folder / ".env").read_text(encoding="utf-8")
    assert "KEEP=1" in env_text
    assert "KEEP=0" not in env_text
    assert "API_WAIT_PER_NODE=20" in env_text
    assert (server_folder / "apps" / "demo" / "local.txt").read_text(
        encoding="utf-8"
    ) == "keep\n"
    parent_env = (server_folder.parent / ".env").read_text(encoding="utf-8")
    assert "METRIC_CHANNEL=/ws/new" in parent_env
    assert f"ROOT_PATH={server_folder.parent}" in parent_env
    assert "/opt/package" not in parent_env
    saved = installer._load_server_metadata()["abcdef"]
    assert saved["package_sha256"] == installer._package_sha256(_server_package_zip())
    assert saved["id_device"] == 42


def test_update_restarts_a_running_server(fake_logger, nv_home):
    server_folder = _installed_server(nv_home)
    installer = Installer(logger=fake_logger)
    with patch.object(installer.docker, "_check_docker_available", return_value=True):
        with patch.object(
            installer, "_download_server_package", return_value=_server_package_zip()
        ):
            with patch.object(installer.docker, "_server_is_running", return_value=True):
                with patch.object(installer.docker, "run_docker_compose") as compose:
                    with patch("novavision.installer.start_host_metrics"):
                        with patch.object(
                            installer, "_wait_for_server_status", return_value=True
                        ):
                            assert (
                                installer.update(
                                    token="ci-token",
                                    server_name="42",
                                    assume_yes=True,
                                )
                                is True
                            )
    assert [call.args[1:] for call in compose.call_args_list] == [
        ("stop",),
        ("build", "--no-cache"),
        ("up", "-d", "--force-recreate"),
    ]
    assert "image: new" in (server_folder / "docker-compose.yml").read_text(
        encoding="utf-8"
    )


def test_update_skips_when_package_hash_matches(fake_logger, nv_home):
    server_folder = _installed_server(nv_home)
    package = _server_package_zip()
    metadata = {
        "abcdef": {
            "id_device": 42,
            "host": "https://suite.novavision.ai",
            "workspace": "ci",
            "service": {"enabled": True, "apps": ["demo"]},
            "package_sha256": hashlib.sha256(package).hexdigest(),
        }
    }
    (nv_home / ".novavision" / "servers.json").write_text(
        json.dumps(metadata), encoding="utf-8"
    )
    installer = Installer(logger=fake_logger)
    with patch.object(installer.docker, "_check_docker_available", return_value=True):
        with patch.object(installer, "_download_server_package", return_value=package):
            with patch.object(installer.docker, "_server_is_running", return_value=False):
                with patch.object(installer.docker, "run_docker_compose") as compose:
                    assert (
                        installer.update(
                            token="ci-token", server_name="abcdef", assume_yes=True
                        )
                        is True
                    )
    compose.assert_not_called()
    assert "image: old" in (server_folder / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert any(
        "same server package" in message for message in fake_logger.messages_of("warning")
    )
    kept = installer._load_server_metadata()["abcdef"]
    assert kept["service"]["enabled"] is True
    assert kept["package_sha256"] == metadata["abcdef"]["package_sha256"]


def test_uninstall_rejects_server_on_a_different_host_than_the_saved_login(
    fake_logger, nv_home
):
    server_folder = nv_home / ".novavision" / "Server" / "abcdef"
    server_folder.mkdir(parents=True)
    (nv_home / ".novavision" / "servers.json").write_text(
        '{"abcdef": {"id_device": 42, "host": "https://alfa.suite.novavision.ai"}}',
        encoding="utf-8",
    )
    installer = Installer(logger=fake_logger)
    with patch.object(installer, "_delete_device") as delete_device:
        assert (
            installer.uninstall(
                token="saved-token",
                server_name="abcdef",
                login_host="https://suite.novavision.ai",
            )
            is False
        )
    delete_device.assert_not_called()
    assert server_folder.exists()
    assert "abcdef" in installer._load_server_metadata()
    assert any(
        "alfa.suite.novavision.ai" in message
        for message in fake_logger.messages_of("error")
    )


def test_update_rejects_server_on_a_different_host_than_the_saved_login(
    fake_logger, nv_home
):
    server_folder = _installed_server(nv_home)
    installer = Installer(logger=fake_logger)
    with patch.object(installer.docker, "_check_docker_available", return_value=True):
        with patch.object(installer, "_download_server_package") as download:
            assert (
                installer.update(
                    token="saved-token",
                    server_name="abcdef",
                    assume_yes=True,
                    login_host="https://alfa.suite.novavision.ai",
                )
                is False
            )
    download.assert_not_called()
    assert "image: old" in (server_folder / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    assert any(
        "registered on https://suite.novavision.ai" in message
        for message in fake_logger.messages_of("error")
    )
