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
