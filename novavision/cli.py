import argparse
import getpass
import shutil
import sys
from pathlib import Path
from datetime import datetime
from novavision import __version__
from novavision.config import DEFAULT_HOST, resolve_install_defaults
from novavision.credentials import (
    CredentialsError,
    active_login_label,
    canonical_host,
    delete_credentials,
    hosts_match,
    load_credentials,
    resolve_auth,
    save_credentials,
    verify_api_key,
)
from novavision.logger import ConsoleLogger
from novavision.installer import Installer
from novavision.docker_manager import DockerManager
from novavision.host_metrics import (
    DEFAULT_BIND,
    DEFAULT_INTERVAL,
    DEFAULT_PORT,
    serve_host_metrics,
)
from novavision.service_manager import ServiceManager
from novavision.update_listener import serve_update_listener

logger = ConsoleLogger()


def _mask_char():
    bullet = "•"
    encoding = getattr(sys.stderr, "encoding", None) or "utf-8"
    try:
        bullet.encode(encoding)
    except Exception:
        return "*"
    return bullet


def _input_is_interactive():
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except Exception:
        return False


def _terminal_width():
    try:
        return max(shutil.get_terminal_size(fallback=(80, 24)).columns, 1)
    except Exception:
        return 80


def _enable_console_vt(stream):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        enable_vt = 0x0004
        if not mode.value & enable_vt:
            kernel32.SetConsoleMode(handle, mode.value | enable_vt)
    except Exception:
        return


def _cursor_state(length, width):
    if length > 0 and length % width == 0:
        # The cursor stays on the last cell until the next character wraps.
        return width - 1, True
    return length % width, False


def _erase_pending_cell():
    # Clear wrap-pending, blank the last cell, then clear wrap-pending again.
    return "\x1b[D\x1b[C \x1b[D\x1b[C"


def _erase_wrapped_cell(width):
    return f"\x1b[A\x1b[{width}G{_erase_pending_cell()}"


def _collect_masked(read_key, write, mask, width=80, start=0):
    width = max(int(width or 0), 1)
    chars = []
    col, pending = _cursor_state(start, width)
    while True:
        key = read_key()
        if not key:
            raise EOFError
        if key in ("\r", "\n"):
            write("\n")
            return "".join(chars)
        if key == "\x03":
            raise KeyboardInterrupt
        if key == "\x04":
            raise EOFError
        if key in ("\x00", "\xe0"):
            read_key()
            continue
        if key == "\x1b":
            continue
        if key in ("\b", "\x7f"):
            if not chars:
                continue
            chars.pop()
            if pending:
                write(_erase_pending_cell())
                pending = False
                col = width - 1
            elif col == 0:
                write(_erase_wrapped_cell(width))
                pending = False
                col = width - 1
            else:
                write("\b \b")
                col -= 1
            continue
        if ord(key) < 32:
            continue
        chars.append(key)
        if pending:
            col = 0
            pending = False
        write(mask)
        col += 1
        if col >= width:
            col = width - 1
            pending = True


def _prompt_secret(prompt):
    if not _input_is_interactive():
        return getpass.getpass(prompt)

    mask = _mask_char()
    _enable_console_vt(sys.stderr)
    width = _terminal_width()
    if sys.platform == "win32":
        import msvcrt

        sys.stderr.write(prompt)
        sys.stderr.flush()
        return _collect_masked(
            msvcrt.getwch,
            _write_secret_feedback,
            mask,
            width=width,
            start=len(prompt),
        )

    return _prompt_secret_posix(prompt, mask)


def _write_secret_feedback(text):
    sys.stderr.write(text)
    sys.stderr.flush()


def _prompt_secret_posix(prompt, mask):
    import termios
    import tty

    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    sys.stderr.write(prompt)
    sys.stderr.flush()
    try:
        tty.setraw(fd)

        def read_key():
            char = sys.stdin.read(1)
            if char == "":
                raise EOFError
            return char

        def write(text):
            if text == "\n":
                text = "\r\n"
            sys.stderr.write(text)
            sys.stderr.flush()

        return _collect_masked(
            read_key, write, mask, width=_terminal_width(), start=len(prompt)
        )
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


EPILOG = """examples:
  novavision login
  novavision whoami
  novavision install local --workspace my-ws
  novavision install local TOKEN --workspace my-ws
  novavision list
  novavision start server --id abcdef
  novavision logs server --id abcdef --follow
  novavision stop server --id abcdef --close-apps
  novavision update server --id abcdef --yes
  novavision uninstall server --id abcdef --yes
  novavision logout
"""


class NovaVisionCLI:
    def __init__(self):
        self.docker = DockerManager(logger=logger)
        self.service = ServiceManager(logger=logger, docker_manager=self.docker)
        self.installer = None

    def _global_parent(self):
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument(
            "-q",
            "--quiet",
            action="store_true",
            help="Show warnings and errors only",
        )
        parent.add_argument(
            "--json",
            action="store_true",
            dest="json_mode",
            help="Print machine-readable JSON for list and status commands",
        )
        parent.add_argument(
            "--no-color",
            action="store_true",
            help="Disable colored output (also honors NO_COLOR)",
        )
        return parent

    def create_parser(self):
        parent = self._global_parent()
        parser = argparse.ArgumentParser(
            prog="novavision",
            description="Manage NovaVision servers and apps on this machine.",
            epilog=EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            parents=[parent],
        )
        parser.add_argument(
            "-v", "--version", action="version", version=f"%(prog)s {__version__}"
        )
        subparsers = parser.add_subparsers(dest="command", help="Available commands")
        subparsers.required = True

        self._add_login_parser(subparsers, parent)
        self._add_logout_parser(subparsers, parent)
        self._add_whoami_parser(subparsers, parent)
        self._add_install_parser(subparsers, parent)
        self._add_update_parser(subparsers, parent)
        self._add_uninstall_parser(subparsers, parent)
        self._add_start_parser(subparsers, parent)
        self._add_stop_parser(subparsers, parent)
        self._add_service_parser(subparsers, parent)
        self._add_list_parser(subparsers, parent)
        self._add_status_parser(subparsers, parent)
        self._add_logs_parser(subparsers, parent)

        return parser

    def _add_login_parser(self, subparsers, parent):
        login_parser = subparsers.add_parser(
            "login",
            help="Save an API key for later commands",
            parents=[parent],
        )
        login_parser.add_argument(
            "--host",
            default=None,
            help=f"Suite host URL. Default: {DEFAULT_HOST}",
        )

    def _add_logout_parser(self, subparsers, parent):
        subparsers.add_parser(
            "logout",
            help="Delete the saved API key",
            parents=[parent],
        )

    def _add_whoami_parser(self, subparsers, parent):
        subparsers.add_parser(
            "whoami",
            help="Show the saved login",
            parents=[parent],
        )

    def _add_token_argument(self, parser, help_text):
        parser.add_argument("token", nargs="?", default=None, help=help_text)
        parser.add_argument(
            "--token",
            dest="token_flag",
            default=None,
            help="API key. Overrides NOVAVISION_TOKEN and the saved login.",
        )

    def _add_install_parser(self, subparsers, parent):
        install_parser = subparsers.add_parser(
            "install",
            help="Register a device and install the local server",
            parents=[parent],
        )
        install_parser.add_argument(
            "device_type",
            choices=["edge", "local", "cloud"],
            help="Device type to register",
        )
        self._add_token_argument(
            install_parser,
            "User authentication token. Optional after login.",
        )
        install_parser.add_argument(
            "--host",
            default=None,
            help=(
                "Suite host URL. Default: "
                f"{DEFAULT_HOST} (or host from ~/.novavision/config.json). "
                "Common values: suite.novavision.ai, alfa.suite.novavision.ai"
            ),
        )
        install_parser.add_argument(
            "--workspace",
            default=None,
            help="Workspace name. Default: value from ~/.novavision/config.json if set",
        )
        install_parser.add_argument(
            "--port",
            default=None,
            help="Server API port. Skips the port prompt when set.",
        )
        install_parser.add_argument(
            "--non-interactive",
            action="store_true",
            help="Skip prompts. Requires --workspace. Defaults to port 7001 if --port is omitted.",
        )

    def _add_update_parser(self, subparsers, parent):
        update_parser = subparsers.add_parser(
            "update",
            help="Download the latest server package and rebuild an existing server",
            parents=[parent],
        )
        update_parser.add_argument(
            "type",
            choices=["server"],
            help="Resource to update",
        )
        self._add_token_argument(
            update_parser,
            "User authentication token used to download the server package. Optional after login.",
        )
        update_parser.add_argument(
            "--id", help="Server folder ID or device ID", required=False
        )
        update_parser.add_argument(
            "--yes",
            action="store_true",
            help="Do not ask for update confirmation",
        )

    def _add_uninstall_parser(self, subparsers, parent):
        uninstall_parser = subparsers.add_parser(
            "uninstall",
            help="Remove a local server and its registered device",
            parents=[parent],
        )
        uninstall_parser.add_argument(
            "type",
            choices=["server"],
            help="Resource to uninstall",
        )
        self._add_token_argument(
            uninstall_parser,
            "User authentication token used to delete the device. Optional after login.",
        )
        uninstall_parser.add_argument(
            "--id", help="Server folder ID or device ID", required=True
        )
        uninstall_parser.add_argument(
            "--yes",
            action="store_true",
            help="Do not ask for uninstall confirmation",
        )

    def _add_start_parser(self, subparsers, parent):
        start_parser = subparsers.add_parser(
            "start", help="Start a server or app", parents=[parent]
        )
        start_parser.add_argument(
            "type", choices=["server", "app"], help="Resource to start"
        )
        start_parser.add_argument(
            "--id",
            help="Server folder ID, or app ID when starting an app",
            required=False,
        )

    def _add_stop_parser(self, subparsers, parent):
        stop_parser = subparsers.add_parser(
            "stop", help="Stop a server or app", parents=[parent]
        )
        stop_parser.add_argument(
            "type", choices=["server", "app"], help="Resource to stop"
        )
        stop_parser.add_argument(
            "--id",
            help="Server folder ID, or app ID when stopping an app",
            required=False,
        )
        stop_parser.add_argument(
            "--close-apps",
            action="store_true",
            help="When stopping a server, also stop apps belonging to that server",
        )

    def _add_service_parser(self, subparsers, parent):
        service_parser = subparsers.add_parser(
            "service", help="Manage automatic server startup", parents=[parent]
        )
        service_parser.add_argument(
            "action", choices=["enable", "disable", "status"], help="Service action"
        )
        service_parser.add_argument(
            "type", choices=["server"], help="Resource to manage"
        )
        service_parser.add_argument("--id", help="Server folder ID", required=False)
        service_parser.add_argument(
            "--apps",
            nargs="+",
            metavar="APP_ID",
            help='App IDs to start with the server service. Use "*" for all apps.',
            required=False,
        )

    def _add_list_parser(self, subparsers, parent):
        subparsers.add_parser("list", help="List installed servers", parents=[parent])

    def _add_status_parser(self, subparsers, parent):
        status_parser = subparsers.add_parser(
            "status", help="Show running state for servers and apps", parents=[parent]
        )
        status_parser.add_argument("--id", help="Server folder ID", required=False)

    def _add_logs_parser(self, subparsers, parent):
        logs_parser = subparsers.add_parser(
            "logs", help="Show Docker Compose logs", parents=[parent]
        )
        logs_parser.add_argument(
            "type", choices=["server", "app"], help="Resource to read logs from"
        )
        logs_parser.add_argument(
            "--id",
            help="Server folder ID, or app ID",
            required=False,
        )
        logs_parser.add_argument(
            "--follow",
            action="store_true",
            help="Follow log output",
        )
        logs_parser.add_argument(
            "--tail",
            type=int,
            metavar="N",
            help="Number of lines to show from the end of the logs",
        )

    def _create_internal_service_parser(self):
        service_parser = argparse.ArgumentParser(
            prog="novavision _service", description=argparse.SUPPRESS
        )
        service_parser.add_argument(
            "action", choices=["start-server", "stop-server"], help=argparse.SUPPRESS
        )
        service_parser.add_argument("--server", required=True, help=argparse.SUPPRESS)
        return service_parser

    def _create_internal_metrics_parser(self):
        metrics_parser = argparse.ArgumentParser(
            prog="novavision _metrics", description=argparse.SUPPRESS
        )
        metrics_parser.add_argument("action", choices=["serve"], help=argparse.SUPPRESS)
        metrics_parser.add_argument(
            "--port", type=int, default=DEFAULT_PORT, help=argparse.SUPPRESS
        )
        metrics_parser.add_argument(
            "--bind", default=DEFAULT_BIND, help=argparse.SUPPRESS
        )
        metrics_parser.add_argument(
            "--interval", type=float, default=DEFAULT_INTERVAL, help=argparse.SUPPRESS
        )
        return metrics_parser

    def _create_internal_listen_parser(self):
        return argparse.ArgumentParser(
            prog="novavision _listen", description=argparse.SUPPRESS
        )

    def _apply_logger_settings(self, args):
        logger.configure(
            quiet=getattr(args, "quiet", False),
            json_mode=getattr(args, "json_mode", False),
            no_color=getattr(args, "no_color", False),
        )

    def _show_active_login(self):
        label = active_login_label()
        if label:
            logger.note(label)

    def _require_auth(self, args):
        try:
            auth = resolve_auth(
                flag_token=getattr(args, "token_flag", None),
                positional_token=getattr(args, "token", None),
            )
        except CredentialsError as exc:
            logger.error(str(exc))
            raise SystemExit(1)
        if not auth.token:
            logger.error("No API key found. Run `novavision login` or pass a token.")
            raise SystemExit(1)
        return auth

    def handle_login(self, args):
        host = canonical_host(args.host or DEFAULT_HOST)
        shown = host.split("://", 1)[-1]
        try:
            token = _prompt_secret(
                f"Enter access token (https://{shown}/site/profile/edit -> Access Token): "
            )
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\n")
            sys.stderr.flush()
            logger.warning("Operation cancelled by user")
            raise SystemExit(1)
        token = token.strip()
        if not token:
            logger.error("API key is required.")
            raise SystemExit(1)

        username, error = verify_api_key(host, token)
        if error:
            logger.error(error)
            raise SystemExit(1)
        save_credentials(host, token, username)
        logger.success(f"Logged in as {username}")

    def handle_logout(self, args):
        if not delete_credentials():
            logger.error("Not logged in.")
            raise SystemExit(1)
        logger.success("Logged out.")

    def handle_whoami(self, args):
        try:
            saved = load_credentials()
        except CredentialsError as exc:
            logger.error(str(exc))
            raise SystemExit(1)
        if not saved:
            logger.error("Not logged in.")
            raise SystemExit(1)
        username = saved.get("username") or "unknown"
        logger.info(f"{username} ({saved['host']})")

    def handle_install(self, args):
        self._show_active_login()
        auth = self._require_auth(args)
        host, workspace = resolve_install_defaults(args.host, args.workspace)
        if auth.source == "credentials":
            if args.host is None:
                host = auth.host
            elif not hosts_match(host, auth.host):
                logger.error(
                    f"Install host is {canonical_host(host)}, "
                    f"but the saved login is for {canonical_host(auth.host)}."
                )
                raise SystemExit(1)
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (
            log_dir / f"install-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        )
        install_logger = logger.copy_settings(log_file_path=str(log_file))
        install_logger.info(f"Logging installation to {log_file}")
        self.installer = Installer(logger=install_logger)
        if args.non_interactive and not workspace:
            logger.error("--non-interactive requires --workspace.")
            raise SystemExit(1)

        success = self.installer.install(
            device_type=args.device_type,
            token=auth.token,
            host=host,
            workspace=workspace,
            port=args.port,
            non_interactive=args.non_interactive,
        )
        if not success:
            raise SystemExit(1)

    def handle_update(self, args):
        self._show_active_login()
        auth = self._require_auth(args)
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (
            log_dir / f"update-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        )
        update_logger = logger.copy_settings(log_file_path=str(log_file))
        update_logger.info(f"Logging update to {log_file}")
        self.installer = Installer(logger=update_logger)
        success = self.installer.update(
            token=auth.token,
            server_name=args.id,
            assume_yes=getattr(args, "yes", False),
            login_host=auth.host if auth.source == "credentials" else None,
        )
        if not success:
            raise SystemExit(1)

    def handle_uninstall(self, args):
        self._show_active_login()
        auth = self._require_auth(args)
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (
            log_dir / f"uninstall-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        )
        uninstall_logger = logger.copy_settings(log_file_path=str(log_file))
        uninstall_logger.info(f"Logging uninstall to {log_file}")
        self.installer = Installer(logger=uninstall_logger)
        success = self.installer.uninstall(
            token=auth.token,
            server_name=args.id,
            assume_yes=getattr(args, "yes", False),
            login_host=auth.host if auth.source == "credentials" else None,
        )
        if not success:
            raise SystemExit(1)

    def handle_docker_command(self, args):
        self._show_active_login()
        if (
            args.command == "stop"
            and getattr(args, "close_apps", False)
            and args.type != "server"
        ):
            logger.error("--close-apps can only be used with stop server.")
            raise SystemExit(1)

        if args.type == "app" and not args.id:
            logger.error("--id is required when starting or stopping an app.")
            raise SystemExit(1)

        if args.type in ["server", "app"]:
            success = self.docker.manage_docker(
                command=args.command,
                type=args.type,
                app_name=args.id if args.type == "app" else None,
                close_apps=getattr(args, "close_apps", False),
                server_name=args.id if args.type == "server" else None,
            )
            if not success:
                raise SystemExit(1)
        else:
            logger.error("Invalid arguments!")
            raise SystemExit(1)

    def handle_service_command(self, args):
        self._show_active_login()
        if args.type != "server":
            logger.error("Only server services are supported.")
            raise SystemExit(1)

        if args.action == "enable":
            success = self.service.enable_server(
                server_name=args.id,
                apps=getattr(args, "apps", None),
            )
        elif args.action == "disable":
            success = self.service.disable_server(server_name=args.id)
        elif args.action == "status":
            success = self.service.status_server(server_name=args.id)
        else:
            logger.error(f"Unknown service action: {args.action}")
            raise SystemExit(1)

        if not success:
            raise SystemExit(1)

    def handle_list_command(self, args):
        self._show_active_login()
        if not self.docker.list_servers():
            raise SystemExit(1)

    def handle_status_command(self, args):
        self._show_active_login()
        if not self.docker.show_status(server_name=args.id):
            raise SystemExit(1)

    def handle_logs_command(self, args):
        self._show_active_login()
        if args.type == "app" and not args.id:
            logger.error("--id is required when reading app logs.")
            raise SystemExit(1)
        if not self.docker.show_logs(
            resource_type=args.type,
            resource_id=args.id,
            follow=args.follow,
            tail=args.tail,
        ):
            raise SystemExit(1)

    def handle_internal_service_command(self, args):
        success = self.service.run_service_action(
            action=args.action, server_name=args.server
        )
        if not success:
            raise SystemExit(1)

    def handle_internal_listen_command(self, args):
        raise SystemExit(serve_update_listener(logger))

    def handle_internal_metrics_command(self, args):
        raise SystemExit(
            serve_host_metrics(
                port=args.port,
                bind=args.bind,
                interval=args.interval,
            )
        )

    def run(self):
        if len(sys.argv) > 1 and sys.argv[1] == "_service":
            parser = self._create_internal_service_parser()
            args = parser.parse_args(sys.argv[2:])
            self.handle_internal_service_command(args)
            return

        if len(sys.argv) > 1 and sys.argv[1] == "_metrics":
            parser = self._create_internal_metrics_parser()
            args = parser.parse_args(sys.argv[2:])
            self.handle_internal_metrics_command(args)
            return

        if len(sys.argv) > 1 and sys.argv[1] == "_listen":
            parser = self._create_internal_listen_parser()
            args = parser.parse_args(sys.argv[2:])
            self.handle_internal_listen_command(args)
            return

        parser = self.create_parser()
        args = parser.parse_args()
        self._apply_logger_settings(args)

        try:
            if args.command == "login":
                self.handle_login(args)
            elif args.command == "logout":
                self.handle_logout(args)
            elif args.command == "whoami":
                self.handle_whoami(args)
            elif args.command == "install":
                self.handle_install(args)
            elif args.command == "update":
                self.handle_update(args)
            elif args.command == "uninstall":
                self.handle_uninstall(args)
            elif args.command in ["start", "stop"]:
                self.handle_docker_command(args)
            elif args.command == "service":
                self.handle_service_command(args)
            elif args.command == "list":
                self.handle_list_command(args)
            elif args.command == "status":
                self.handle_status_command(args)
            elif args.command == "logs":
                self.handle_logs_command(args)
            else:
                logger.error(f"Unknown command: {args.command}")
        except SystemExit:
            raise
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            raise SystemExit(1)


def main():
    try:
        cli = NovaVisionCLI()
        cli.run()
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        sys.stderr.flush()
        logger.warning("Operation cancelled by user")
        exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
