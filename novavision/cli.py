import argparse
from pathlib import Path
from datetime import datetime
from novavision.logger import ConsoleLogger
from novavision.installer import Installer
from novavision.docker_manager import DockerManager

logger = ConsoleLogger()

class NovaVisionCLI:
    def __init__(self):
        self.docker = DockerManager(logger=logger)
        self.installer = None  # will be created per install with file logger

    def create_parser(self):
        parser = argparse.ArgumentParser(description="NovaVision CLI Tool")
        subparsers = parser.add_subparsers(dest="command", help="Available commands")
        subparsers.required = True

        self._add_install_parser(subparsers)
        self._add_start_parser(subparsers)
        self._add_stop_parser(subparsers)

        return parser

    def _add_install_parser(self, subparsers):
        install_parser = subparsers.add_parser(
            "install",
            help="Creates device and installs server")
        install_parser.add_argument(
            "device_type",
            choices=["edge", "local", "cloud"],
            help="Select and Configure Device Type"
        )
        install_parser.add_argument(
            "token",
            help="User Authentication Token"
        )
        install_parser.add_argument(
            "--host",
            default="https://alfa.suite.novavision.ai",
            help="Host Url"
        )
        install_parser.add_argument(
            "--workspace",
            default=None,
            help="Workspace Name"
        )

    def _add_start_parser(self, subparsers):
        start_parser = subparsers.add_parser(
            "start",
            help="Starts server | app")
        start_parser.add_argument(
            "type",
            choices=["server", "app"],
            help="Type of service to start"
        )
        start_parser.add_argument(
            "--id",
            help="AppID for App Choice",
            required=False
        )

    def _add_stop_parser(self, subparsers):
        stop_parser = subparsers.add_parser(
            "stop",
            help="Stops server | app")
        stop_parser.add_argument(
            "type",
            choices=["server", "app"],
            help="Type of service to stop"
        )
        stop_parser.add_argument(
            "--id",
            help="AppID for App Choice",
            required=False
        )

    def handle_install(self, args):
        log_dir = Path.home() / ".novavision"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"install-{datetime.now().strftime('%Y-%m-%d_%H-%M')}.log"
        install_logger = ConsoleLogger(log_file_path=str(log_file))
        install_logger.info(f"Logging installation to {log_file}")
        self.installer = Installer(logger=install_logger)
        self.installer.install(
            device_type=args.device_type,
            token=args.token,
            host=args.host,
            workspace=args.workspace
        )

    def handle_docker_command(self, args):
        if (args.type == "app" and args.id) or args.type == "server":
            self.docker.manage_docker(
                command=args.command,
                type=args.type,
                app_name=args.id if args.type == "app" else None
            )
        else:
            logger.error("Invalid arguments!")

    def run(self):
        parser = self.create_parser()
        args = parser.parse_args()

        try:
            if args.command == "install":
                self.handle_install(args)
            elif args.command in ["start", "stop"]:
                self.handle_docker_command(args)
            else:
                logger.error(f"Unknown command: {args.command}")
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")


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