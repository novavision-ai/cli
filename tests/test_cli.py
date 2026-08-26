import pytest
from novavision.cli import NovaVisionCLI


def _parser():
    return NovaVisionCLI().create_parser()


def test_version_flag():
    parser = _parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def test_install_requires_token():
    parser = _parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["install", "local"])


def test_install_parses_token_host_and_workspace():
    args = _parser().parse_args(
        [
            "install",
            "local",
            "ci-token",
            "--host",
            "alfa.suite.novavision.ai",
            "--workspace",
            "ci-workspace",
        ]
    )
    assert args.command == "install"
    assert args.device_type == "local"
    assert args.token == "ci-token"
    assert args.host == "alfa.suite.novavision.ai"
    assert args.workspace == "ci-workspace"


def test_start_app_accepts_id():
    args = _parser().parse_args(["start", "app", "--id", "demo"])
    assert args.command == "start"
    assert args.type == "app"
    assert args.id == "demo"


def test_stop_server_close_apps():
    args = _parser().parse_args(["stop", "server", "--close-apps"])
    assert args.command == "stop"
    assert args.type == "server"
    assert args.close_apps is True


def test_service_enable_with_apps():
    args = _parser().parse_args(
        ["service", "enable", "server", "--id", "ci-server", "--apps", "demo"]
    )
    assert args.command == "service"
    assert args.action == "enable"
    assert args.id == "ci-server"
    assert args.apps == ["demo"]
