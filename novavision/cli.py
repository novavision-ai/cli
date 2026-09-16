import argparse
import sys
from pathlib import Path
from datetime import datetime
from novavision import __version__
from novavision.config import DEFAULT_HOST, resolve_install_defaults
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

logger = ConsoleLogger()

EPILOG = """examples:
  novavision install local TOKEN --workspace my-ws
  novavision list
  novavision start server --id abcdef
  novavision logs server --id abcdef --follow
  novavision stop server --id abcdef --close-apps
  novavision uninstall server TOKEN --id abcdef --yes
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

        self._add_install_parser(subparsers, parent)
        self._add_uninstall_parser(subparsers, parent)
        self._add_start_parser(subparsers, parent)
        self._add_stop_parser(subparsers, parent)
        self._add_service_parser(subparsers, parent)
        self._add_list_parser(subparsers, parent)
        self._add_status_parser(subparsers, parent)
        self._add_logs_parser(subparsers, parent)

        return parser

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
        install_parser.add_argument("token", help="User authentication token")
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
        uninstall_parser.add_argument(
            "token", help="User authentication token used to delete the device"
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

    def _apply_logger_settings(self, args):
        logger.configure(
            quiet=getattr(args, "quiet", False),
            json_mode=getattr(args, "json_mode", False),
            no_color=getattr(args, "no_color", False),
        )

    def handle_install(self, args):
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (
            log_dir / f"install-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        )
        install_logger = logger.copy_settings(log_file_path=str(log_file))
        install_logger.info(f"Logging installation to {log_file}")
        self.installer = Installer(logger=install_logger)
        host, workspace = resolve_install_defaults(args.host, args.workspace)
        if args.non_interactive and not workspace:
            logger.error("--non-interactive requires --workspace.")
            raise SystemExit(1)

        success = self.installer.install(
            device_type=args.device_type,
            token=args.token,
            host=host,
            workspace=workspace,
            port=args.port,
            non_interactive=args.non_interactive,
        )
        if not success:
            raise SystemExit(1)

    def handle_uninstall(self, args):
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = (
            log_dir / f"uninstall-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
        )
        uninstall_logger = logger.copy_settings(log_file_path=str(log_file))
        uninstall_logger.info(f"Logging uninstall to {log_file}")
        self.installer = Installer(logger=uninstall_logger)
        success = self.installer.uninstall(
            token=args.token,
            server_name=args.id,
            assume_yes=getattr(args, "yes", False),
        )
        if not success:
            raise SystemExit(1)

    def handle_docker_command(self, args):
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
        if not self.docker.list_servers():
            raise SystemExit(1)

    def handle_status_command(self, args):
        if not self.docker.show_status(server_name=args.id):
            raise SystemExit(1)

    def handle_logs_command(self, args):
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

        parser = self.create_parser()
        args = parser.parse_args()
        self._apply_logger_settings(args)

        try:
            if args.command == "install":
                self.handle_install(args)
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
        logger.warning("Operation cancelled by user")
        exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
