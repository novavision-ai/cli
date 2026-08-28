import argparse
import sys
from pathlib import Path
from datetime import datetime
from novavision import __version__
from novavision.logger import ConsoleLogger
from novavision.installer import Installer
from novavision.docker_manager import DockerManager
from novavision.service_manager import ServiceManager

logger = ConsoleLogger()


class NovaVisionCLI:
    def __init__(self):
        self.docker = DockerManager(logger=logger)
        self.service = ServiceManager(logger=logger, docker_manager=self.docker)
        self.installer = None  # will be created per install with file logger

    def create_parser(self):
        parser = argparse.ArgumentParser(
            prog="novavision", description="NovaVision CLI Tool"
        )
        parser.add_argument(
            "-v", "--version", action="version", version=f"%(prog)s {__version__}"
        )
        subparsers = parser.add_subparsers(dest="command", help="Available commands")
        subparsers.required = True

        self._add_install_parser(subparsers)
        self._add_uninstall_parser(subparsers)
        self._add_start_parser(subparsers)
        self._add_stop_parser(subparsers)
        self._add_service_parser(subparsers)

        return parser

    def _add_install_parser(self, subparsers):
        install_parser = subparsers.add_parser(
            "install", help="Creates device and installs server"
        )
        install_parser.add_argument(
            "device_type",
            choices=["edge", "local", "cloud"],
            help="Select and Configure Device Type",
        )
        install_parser.add_argument("token", help="User Authentication Token")
        install_parser.add_argument(
            "--host", default="https://suite.novavision.ai", help="Host Url"
        )
        install_parser.add_argument("--workspace", default=None, help="Workspace Name")
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

    def _add_uninstall_parser(self, subparsers):
        uninstall_parser = subparsers.add_parser(
            "uninstall", help="Removes a local server and its registered device"
        )
        uninstall_parser.add_argument(
            "type",
            choices=["server"],
            help="Type of resource to uninstall",
        )
        uninstall_parser.add_argument(
            "token", help="User Authentication Token used to delete the device"
        )
        uninstall_parser.add_argument(
            "--id", help="Server folder ID or device ID", required=True
        )

    def _add_start_parser(self, subparsers):
        start_parser = subparsers.add_parser("start", help="Starts server | app")
        start_parser.add_argument(
            "type", choices=["server", "app"], help="Type of service to start"
        )
        start_parser.add_argument(
            "--id",
            help="Server folder ID, or App ID when starting an app",
            required=False,
        )

    def _add_stop_parser(self, subparsers):
        stop_parser = subparsers.add_parser("stop", help="Stops server | app")
        stop_parser.add_argument(
            "type", choices=["server", "app"], help="Type of service to stop"
        )
        stop_parser.add_argument(
            "--id",
            help="Server folder ID, or App ID when stopping an app",
            required=False,
        )
        stop_parser.add_argument(
            "--close-apps",
            action="store_true",
            help="When stopping a server, also stop apps belonging to that server",
        )

    def _add_service_parser(self, subparsers):
        service_parser = subparsers.add_parser(
            "service", help="Manages server service integration"
        )
        service_parser.add_argument(
            "action", choices=["enable", "disable", "status"], help="Service action"
        )
        service_parser.add_argument(
            "type", choices=["server"], help="Type of service to manage"
        )
        service_parser.add_argument("--id", help="Server folder ID", required=False)
        service_parser.add_argument(
            "--apps",
            nargs="+",
            metavar="APP_ID",
            help='App IDs to start with the server service. Use "*" for all apps.',
            required=False,
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

    def handle_install(self, args):
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"install-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.log"
        install_logger = ConsoleLogger(log_file_path=str(log_file))
        install_logger.info(f"Logging installation to {log_file}")
        self.installer = Installer(logger=install_logger)
        if args.non_interactive and not args.workspace:
            logger.error("--non-interactive requires --workspace.")
            raise SystemExit(1)

        success = self.installer.install(
            device_type=args.device_type,
            token=args.token,
            host=args.host,
            workspace=args.workspace,
            port=args.port,
            non_interactive=args.non_interactive,
        )
        if not success:
            raise SystemExit(1)

    def handle_uninstall(self, args):
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"uninstall-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.log"
        uninstall_logger = ConsoleLogger(log_file_path=str(log_file))
        uninstall_logger.info(f"Logging uninstall to {log_file}")
        self.installer = Installer(logger=uninstall_logger)
        success = self.installer.uninstall(token=args.token, server_name=args.id)
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

    def handle_internal_service_command(self, args):
        success = self.service.run_service_action(
            action=args.action, server_name=args.server
        )
        if not success:
            raise SystemExit(1)

    def run(self):
        if len(sys.argv) > 1 and sys.argv[1] == "_service":
            parser = self._create_internal_service_parser()
            args = parser.parse_args(sys.argv[2:])
            self.handle_internal_service_command(args)
            return

        parser = self.create_parser()
        args = parser.parse_args()

        try:
            if args.command == "install":
                self.handle_install(args)
            elif args.command == "uninstall":
                self.handle_uninstall(args)
            elif args.command in ["start", "stop"]:
                self.handle_docker_command(args)
            elif args.command == "service":
                self.handle_service_command(args)
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
