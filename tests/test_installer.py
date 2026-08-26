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
