import pytest
from novavision.cli import NovaVisionCLI


def _parser():
    return NovaVisionCLI().create_parser()


def test_version_flag():
    parser = _parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0


def test_install_token_is_optional_after_login():
    args = _parser().parse_args(["install", "local"])
    assert args.token is None
    assert args.token_flag is None


def test_token_flag_overrides_positional_token():
    args = _parser().parse_args(
        ["install", "local", "positional-token", "--token", "flag-token"]
    )
    assert args.token == "positional-token"
    assert args.token_flag == "flag-token"


def test_login_logout_and_whoami_parse():
    parser = _parser()
    login = parser.parse_args(["login", "--host", "alfa.suite.novavision.ai"])
    assert login.command == "login"
    assert login.host == "alfa.suite.novavision.ai"
    assert parser.parse_args(["login"]).host is None
    assert parser.parse_args(["logout"]).command == "logout"
    assert parser.parse_args(["whoami"]).command == "whoami"


def test_update_and_uninstall_token_is_optional():
    parser = _parser()
    update = parser.parse_args(["update", "server", "--id", "abcdef", "--yes"])
    assert update.token is None
    assert update.token_flag is None
    uninstall = parser.parse_args(["uninstall", "server", "--id", "abcdef"])
    assert uninstall.token is None
    assert uninstall.id == "abcdef"


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
            "--port",
            "7001",
            "--non-interactive",
        ]
    )
    assert args.command == "install"
    assert args.device_type == "local"
    assert args.token == "ci-token"
    assert args.host == "alfa.suite.novavision.ai"
    assert args.workspace == "ci-workspace"
    assert args.port == "7001"
    assert args.non_interactive is True


def test_update_parses_token_id_and_yes():
    args = _parser().parse_args(
        ["update", "server", "ci-token", "--id", "abcdef", "--yes"]
    )
    assert args.command == "update"
    assert args.type == "server"
    assert args.token == "ci-token"
    assert args.id == "abcdef"
    assert args.yes is True


def test_uninstall_parses_token_and_id():
    args = _parser().parse_args(["uninstall", "server", "ci-token", "--id", "abcdef"])
    assert args.command == "uninstall"
    assert args.type == "server"
    assert args.token == "ci-token"
    assert args.id == "abcdef"


def test_start_server_accepts_id():
    args = _parser().parse_args(["start", "server", "--id", "abcdef"])
    assert args.command == "start"
    assert args.type == "server"
    assert args.id == "abcdef"


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


def test_list_and_status_and_logs_parse():
    parser = _parser()
    assert parser.parse_args(["list"]).command == "list"
    status = parser.parse_args(["status", "--id", "abcdef"])
    assert status.command == "status"
    assert status.id == "abcdef"
    logs = parser.parse_args(
        ["logs", "server", "--id", "abcdef", "--follow", "--tail", "20"]
    )
    assert logs.command == "logs"
    assert logs.type == "server"
    assert logs.follow is True
    assert logs.tail == 20


def test_global_flags_and_uninstall_yes():
    parser = _parser()
    listed = parser.parse_args(["list", "--json", "--quiet", "--no-color"])
    assert listed.json_mode is True
    assert listed.quiet is True
    assert listed.no_color is True
    uninstall = parser.parse_args(
        ["uninstall", "server", "ci-token", "--id", "abcdef", "--yes"]
    )
    assert uninstall.yes is True


def test_install_host_defaults_to_none_before_config_merge():
    args = _parser().parse_args(["install", "local", "ci-token"])
    assert args.host is None
    assert args.workspace is None


def test_internal_metrics_parser():
    parser = NovaVisionCLI()._create_internal_metrics_parser()
    args = parser.parse_args(["serve", "--port", "18765", "--interval", "2"])
    assert args.action == "serve"
    assert args.port == 18765
    assert args.interval == 2.0
