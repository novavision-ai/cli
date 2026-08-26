import json
import os
import re
import yaml
import shutil
import subprocess
import time

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from novavision.logger import ConsoleLogger


class DockerManager:
    MONTHS = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]

    def __init__(self, logger):
        self.log = logger or ConsoleLogger()

    def _metadata_path(self):
        return Path.home() / ".novavision" / "servers.json"

    def _load_server_metadata(self):
        metadata_path = self._metadata_path()
        if not metadata_path.exists():
            return {}

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.log.warning(f"Could not read server metadata: {e}")
            return {}

    def _host_label(self, host):
        if not host:
            return "Unknown"

        netloc = urlparse(host).netloc or host
        hostname = netloc.lower()
        if hostname.startswith("alfa."):
            return "alfa"
        if hostname.startswith("dev."):
            return "dev"
        if "suite.novavision.ai" in hostname:
            return "suite"
        return netloc

    def _format_created_at(self, created_at):
        if not created_at or created_at == "Unknown":
            return "Unknown"

        value = str(created_at).strip()
        normalized = value.replace("Z", "+00:00")

        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            for date_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    dt = datetime.strptime(value, date_format)
                    break
                except ValueError:
                    dt = None
            if dt is None:
                return value

        month = self.MONTHS[dt.month - 1]
        return f"{dt.day} {month} {dt.year}, {dt:%H:%M}"

    def _format_server_details(self, folder, metadata):
        server_metadata = metadata.get(folder.name, {})
        created_at = self._format_created_at(
            server_metadata.get("created_at", "Unknown")
        )
        workspace = server_metadata.get("workspace", "Unknown")
        host = server_metadata.get("host")
        host_label = self._host_label(host)
        service = server_metadata.get("service", {})
        service_status = "enabled" if service.get("enabled") else "disabled"

        return (
            f"{folder.name} | Created: {created_at} | Workspace: {workspace} "
            f"| Host: {host_label} | Service: {service_status}"
        )

    def choose_server_folder(self, server_path):
        server_folders = [item for item in server_path.iterdir() if item.is_dir()]
        visible_folders = [f for f in server_folders if not f.name.startswith(".")]
        metadata = self._load_server_metadata()

        if not server_folders:
            self.log.error("No server folders found!")
            return None

        if len(visible_folders) == 1:
            self.log.info(
                f"Selected server: {self._format_server_details(visible_folders[0], metadata)}"
            )
            return visible_folders[0]
        elif len(visible_folders) > 1:
            self.log.info("Multiple server folders found. Please select one")
            for idx, folder in enumerate(visible_folders):
                self.log.info(
                    f"{idx + 1}. {self._format_server_details(folder, metadata)}"
                )
            while True:
                try:
                    choice = int(
                        self.log.question(
                            "Enter the number of the server you want to select"
                        )
                    )
                    if 1 <= choice <= len(visible_folders):
                        return visible_folders[choice - 1]
                    else:
                        self.log.warning(
                            "Invalid selection. Please enter a valid number."
                        )
                except ValueError:
                    self.log.warning("Invalid input. Please enter a number.")
        return server_folders[0]

    def get_server_folder(self, server_name=None):
        server_path = Path.home() / ".novavision" / "Server"
        if server_name:
            server_folder = server_path / server_name
            if not server_folder.is_dir():
                self.log.error(f"Server folder not found: {server_name}")
                return None
            return server_folder
        return self.choose_server_folder(server_path)

    def start_server_folder(self, server_folder):
        if not server_folder:
            return False

        docker_compose_file = server_folder / "docker-compose.yml"
        if not docker_compose_file.exists():
            self.log.error(f"No docker-compose.yml found in {server_folder}!")
            return False

        return self._start_server(docker_compose_file)

    def stop_server_folder(self, server_folder):
        if not server_folder:
            return False

        docker_compose_file = server_folder / "docker-compose.yml"
        if not docker_compose_file.exists():
            self.log.error(f"No docker-compose.yml found in {server_folder}!")
            return False

        try:
            self.run_docker_compose(docker_compose_file, "down", "--volumes")
            self.log.success("Server stopped.")
            if self.remove_network():
                self.log.success("Server network removed successfully.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.log.error(f"Error stopping server: {e}")
            return False

    def remove_network(self):
        try:
            result = subprocess.run(
                ["docker", "network", "ls", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            network_names = result.stdout.strip().split("\n")
            for net in network_names:
                if net.endswith("-novavision"):
                    try:
                        subprocess.run(["docker", "network", "rm", net], check=True)
                        self.log.success(f"Removed network: {net}")
                    except subprocess.CalledProcessError:
                        self.log.warning(
                            f"Failed to remove network (maybe already removed): {net}"
                        )
            return True
        except subprocess.CalledProcessError as e:
            self.log.error(f"Error listing networks: {e}")
            return False

    def get_docker_build_info(self, compose_file):
        try:
            with open(compose_file, "r") as file:
                compose_data = yaml.safe_load(file)

            services = compose_data.get("services", {})
            build_info = {}

            for service, config in services.items():
                image_name = config.get("image")
                build_context = config.get("build", {}).get("context")
                if image_name and build_context:
                    build_info[service] = {
                        "image": image_name,
                        "context": build_context,
                    }

            if not build_info:
                self.log.error("No buildable services found in docker-compose.yml!")
                return None
            return build_info

        except Exception as e:
            self.log.error(f"Failed to read docker-compose.yml: {e}")
            return None

    def manage_docker(
        self, command, type, app_name=None, select_server=True, close_apps=False
    ):
        default_path = Path.home() / ".novavision"
        server_path = default_path / "Server"

        if command == "start":
            if type == "server":
                server_folder = (
                    self.choose_server_folder(server_path) if select_server else None
                )
                if server_folder is None and not select_server:
                    server_folders = [
                        item for item in server_path.iterdir() if item.is_dir()
                    ]
                    for folder in server_folders:
                        docker_compose_file = folder / "docker-compose.yml"
                        if docker_compose_file.exists():
                            try:
                                self.run_docker_compose(docker_compose_file, "up", "-d")
                            except subprocess.CalledProcessError as e:
                                self.log.error(
                                    f"Error starting server {folder.name}: {e}"
                                )
                else:
                    server_folder = server_folder or self.choose_server_folder(
                        server_path
                    )
                    self.start_server_folder(server_folder)
            elif type == "app":
                self._start_app(app_name)

        elif command == "stop":
            if type == "server":
                self._stop_server(server_path, select_server, close_apps=close_apps)
            elif type == "app":
                self._stop_app(app_name)

    def _docker_compose_command(self):
        if shutil.which("docker"):
            return ["docker", "compose"]
        if shutil.which("docker-compose"):
            return ["docker-compose"]
        return None

    def run_docker_compose(self, compose_file, *args):
        compose_command = self._docker_compose_command()
        if not compose_command:
            raise FileNotFoundError("Docker Compose is not available.")

        subprocess.run(
            compose_command + ["-f", str(compose_file)] + list(args),
            check=True,
        )

    def wait_for_docker(self, timeout_seconds=300, interval_seconds=5):
        if not shutil.which("docker"):
            self.log.error("Docker is not installed. Please install Docker first.")
            return False

        self.log.info("Checking Docker availability")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            result = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                self.log.success("Docker is available.")
                return True
            time.sleep(interval_seconds)

        self.log.error(
            f"Docker did not become available within {timeout_seconds} seconds."
        )
        return False

    def _start_server(self, docker_compose_file, label="server"):
        previous_containers = set(
            subprocess.run(["docker", "ps", "-q"], capture_output=True, text=True)
            .stdout.strip()
            .split("\n")
        )
        self.log.info(f"Starting {label}")
        try:
            self.run_docker_compose(docker_compose_file, "up", "-d")
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Ports}}"],
                capture_output=True,
                text=True,
            )
            self._display_new_containers(result.stdout, previous_containers)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.log.error(f"Error starting {label}: {e}")
            return False

    def _server_app_compose_files(self, server_folder):
        if not server_folder:
            return {}

        server_compose = server_folder / "docker-compose.yml"
        apps = {}
        for compose_file in server_folder.rglob("docker-compose.yml"):
            if compose_file.resolve() == server_compose.resolve():
                continue
            apps[compose_file.parent.name] = compose_file
        return apps

    def _find_app(self, app_name):
        server_path = Path.home() / ".novavision" / "Server"
        if not server_path.is_dir():
            self.log.error("No server folder found.")
            return None, None

        matches = []
        for folder in server_path.iterdir():
            if not folder.is_dir():
                continue
            apps = self._server_app_compose_files(folder)
            if app_name in apps:
                matches.append((folder, apps[app_name]))

        if not matches:
            self.log.error(f"App folder not found: {app_name}")
            return None, None
        if len(matches) > 1:
            servers = ", ".join(folder.name for folder, _ in matches)
            self.log.warning(
                f"App {app_name} was found on multiple servers ({servers}). "
                f"Using server {matches[0][0].name}."
            )
        return matches[0]

    def _server_is_running(self, server_folder):
        if not server_folder:
            return False

        compose_file = server_folder / "docker-compose.yml"
        compose_command = self._docker_compose_command()
        if not compose_command or not compose_file.exists():
            return False

        result = subprocess.run(
            compose_command + ["-f", str(compose_file), "ps", "-q"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return any(line.strip() for line in result.stdout.splitlines())

    def _require_running_server_for_app(self, app_name):
        server_folder, compose_file = self._find_app(app_name)
        if not compose_file:
            return None

        if not self._server_is_running(server_folder):
            self.log.error(
                f"Server {server_folder.name} is not running. "
                f"Start the server before starting or stopping app {app_name}."
            )
            return None
        return compose_file

    def start_server_apps(self, server_folder, app_names=None):
        if not app_names:
            return True

        apps = self._server_app_compose_files(server_folder)
        if not apps:
            self.log.info("No apps found for this server.")
            return True

        if "*" in app_names:
            selected = apps
        else:
            selected = {}
            for app_name in app_names:
                if app_name in apps:
                    selected[app_name] = apps[app_name]
                else:
                    self.log.warning(f"App not found for this server: {app_name}")

        if not selected:
            self.log.info("No matching apps to start.")
            return True

        started = True
        for app_name, compose_file in selected.items():
            if not self._start_server(compose_file, label=f"app {app_name}"):
                started = False
        return started

    def _start_app(self, app_name):
        compose_file = self._require_running_server_for_app(app_name)
        if not compose_file:
            return False
        return self._start_server(compose_file, label=f"app {app_name}")

    def _compose_container_ids(self, compose_file):
        compose_command = self._docker_compose_command()
        if not compose_command or not compose_file or not compose_file.exists():
            return set()

        result = subprocess.run(
            compose_command + ["-f", str(compose_file), "ps", "-a", "-q"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _running_containers(self):
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            container_id, _, container_name = line.partition("\t")
            if container_id and container_name:
                containers.append((container_id.strip(), container_name.strip()))
        return containers

    def close_server_apps(self, server_folder):
        if not server_folder:
            return False

        server_compose = server_folder / "docker-compose.yml"
        stopped = []

        try:
            for app_name, compose_file in self._server_app_compose_files(
                server_folder
            ).items():
                try:
                    self.run_docker_compose(compose_file, "stop")
                    self.log.info(f"Stopped app compose: {app_name}")
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    self.log.warning(f"Could not stop app compose {compose_file}: {e}")

            server_container_ids = self._compose_container_ids(server_compose)
            for container_id, container_name in self._running_containers():
                if container_id in server_container_ids:
                    continue
                if server_folder.name not in container_name:
                    continue
                subprocess.run(["docker", "stop", container_id], check=True)
                stopped.append(container_name)

            if stopped:
                self.log.success("Stopped apps: " + ", ".join(stopped))
            else:
                self.log.info("No running apps found for this server.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.log.error(f"Error stopping server apps: {e}")
            return False

    def _stop_server(self, server_path, select_server=True, close_apps=False):
        if select_server:
            server_folder = self.choose_server_folder(server_path)
            if close_apps:
                self.close_server_apps(server_folder)
            self.stop_server_folder(server_folder)
        else:
            server_folders = [item for item in server_path.iterdir() if item.is_dir()]
            for folder in server_folders:
                docker_compose_file = folder / "docker-compose.yml"
                if docker_compose_file.exists():
                    if close_apps:
                        self.close_server_apps(folder)
                    self.run_docker_compose(docker_compose_file, "down", "--volumes")
                    self.log.success(f"Server {folder.name} stopped.")
                    if self.remove_network():
                        self.log.success(
                            f"Server {folder.name} network removed successfully."
                        )

    def _stop_app(self, app_name):
        compose_file = self._require_running_server_for_app(app_name)
        if not compose_file:
            return False

        try:
            self.run_docker_compose(compose_file, "stop")
            self.log.success(f"App {app_name} stopped.")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.log.error(f"Error stopping app {app_name}: {e}")
            return False

    def _display_new_containers(self, output, previous_containers):
        current_containers = output.strip().split("\n")
        new_containers = []
        for container in current_containers:
            parts = container.split(" ", 2)
            container_id = parts[0]
            container_name = parts[1]
            container_ports = parts[2] if len(parts) > 2 else "No ports"
            if container_id not in previous_containers:
                ports = []
                for mapping in container_ports.split(", "):
                    if "->" in mapping:
                        ports.append(mapping.split("->")[1].split("/")[0].strip())
                port_display = ", ".join(ports) if ports else "Not Exposed to Host"
                new_containers.append((container_name, port_display))

        if new_containers:
            self.log.info("Started containers:")
            for name, ports in new_containers:
                self.log.info(f"- {name} -> Ports: {ports}")
        else:
            self.log.warning("No containers started.")

    def _check_docker_available(self):
        try:
            subprocess.run(
                ["docker", "info"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return True
        except subprocess.CalledProcessError:
            self.log.error(
                "Docker is not available or not running. Please activate docker first."
            )
            return None
        except FileNotFoundError:
            self.log.error("Docker is not installed. Please install docker first.")
            return None

    def _delete_old_containers(self, key):
        server_folder = Path.home() / ".novavision" / "Server" / key

        if not server_folder.is_dir():
            self.log.info(f"No server folder for key={key}, skipping.")
            return True

        try:
            # Tüm compose dosyalarını bul ve ilgili containerları listele
            containers = set()
            for compose_file in server_folder.rglob("docker-compose.yml"):
                build_info = self.get_docker_build_info(compose_file)
                if build_info:
                    for image_name in build_info:
                        result = subprocess.run(
                            [
                                "docker",
                                "ps",
                                "-a",
                                "--filter",
                                f"ancestor={image_name}",
                                "--format",
                                "{{.Names}}",
                            ],
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        containers.update(
                            name
                            for name in result.stdout.strip().splitlines()
                            if name and key in name
                        )

            # Containerları sil
            for container_name in containers:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                self.log.success(f"Container {container_name} removed.")
            return True
        except Exception as e:
            self.log.error(f"Failed to remove old containers: {e}")
            return None

    def _cleanup_previous_docker_installations(self):
        server_path = Path.home() / ".novavision" / "Server"
        if os.path.exists(server_path):
            self._stop_server(server_path, select_server=False)

            try:
                pattern = re.compile(r"^[A-Za-z0-9]{6}$")
                for server_name in os.listdir(server_path):
                    entry = server_path / server_name
                    if entry.is_dir() and pattern.match(server_name):
                        self._delete_old_containers(server_name)
            except Exception as e:
                self.log.error(f"Error during docker cleanup: {e}")
                return None

        else:
            self.log.warning("No server folder found.")
