import shutil
import subprocess
from pathlib import Path
from novavision.logger import ConsoleLogger
import yaml

class DockerManager:
    def __init__(self, logger):
        self.log = logger or ConsoleLogger()

    def choose_server_folder(self, server_path):
        server_folders = [item for item in server_path.iterdir() if item.is_dir()]
        visible_folders = [f for f in server_folders if not f.name.startswith(".")]

        if not server_folders:
            self.log.error("No server folders found!")
            return None

        if len(visible_folders) == 1:
            return visible_folders[0]
        elif len(visible_folders) > 1:
            self.log.info("Multiple server folders found. Please select one")
            for idx, folder in enumerate(visible_folders):
                self.log.info(f"{idx + 1}. {folder.name}")
            while True:
                try:
                    choice = int(self.log.question("Enter the number of the server you want to select"))
                    if 1 <= choice <= len(visible_folders):
                        return visible_folders[choice - 1]
                    else:
                        self.log.warning("Invalid selection. Please enter a valid number.")
                except ValueError:
                    self.log.warning("Invalid input. Please enter a number.")
        return server_folders[0]

    def remove_network(self):
        try:
            result = subprocess.run(
                ["docker", "network", "ls", "--format", "{{.Name}}"],
                capture_output=True, text=True, check=True
            )
            network_names = result.stdout.strip().split("\n")
            for net in network_names:
                if net.endswith("-novavision"):
                    try:
                        subprocess.run(["docker", "network", "rm", net], check=True)
                        self.log.success(f"Removed network: {net}")
                    except subprocess.CalledProcessError:
                        self.log.warning(f"Failed to remove network (maybe already removed): {net}")
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
                    build_info[service] = {"image": image_name, "context": build_context}

            if not build_info:
                self.log.error("No buildable services found in docker-compose.yml!")
                return None
            return build_info

        except Exception as e:
            self.log.error(f"Failed to read docker-compose.yml: {e}")
            return None

    def manage_docker(self, command, type, app_name=None, select_server=True):
        default_path = Path.home() / ".novavision"
        server_path = default_path / "Server"

        if command == "start":
            if type == "server":
                server_folder = self.choose_server_folder(server_path) if select_server else None
                if server_folder is None and not select_server:
                    server_folders = [item for item in server_path.iterdir() if item.is_dir()]
                    for folder in server_folders:
                        docker_compose_file = folder / "docker-compose.yml"
                        if docker_compose_file.exists():
                            try:
                                self.run_docker_compose(docker_compose_file, "up", "-d")
                            except subprocess.CalledProcessError as e:
                                self.log.error(f"Error starting server {folder.name}: {e}")
                else:
                    server_folder = server_folder or self.choose_server_folder(server_path)
                    docker_compose_file = server_folder / "docker-compose.yml"
                    self._start_server(docker_compose_file)

        elif command == "stop":
            if type == "server":
                self._stop_server(server_path, select_server)
            elif type == "app":
                self._stop_app(app_name)

    def run_docker_compose(self, compose_file, *args):
        if shutil.which("docker"):
            subprocess.run(["docker", "compose", "-f", str(compose_file)] + list(args), check=True)
        elif shutil.which("docker-compose"):
            subprocess.run(["docker-compose", "-f", str(compose_file)] + list(args), check=True)

    def _start_server(self, docker_compose_file):
        previous_containers = set(subprocess.run(["docker", "ps", "-q"],
                                              capture_output=True, text=True).stdout.strip().split("\n"))
        self.log.info("Starting server")
        try:
            self.run_docker_compose(docker_compose_file, "up", "-d")
            result = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Ports}}"],
                                 capture_output=True, text=True)
            self._display_new_containers(result.stdout, previous_containers)
        except subprocess.CalledProcessError as e:
            self.log.error(f"Error starting server: {e}")

    def _stop_server(self, server_path, select_server):
        if select_server:
            server_folder = self.choose_server_folder(server_path)
            docker_compose_file = server_folder / "docker-compose.yml"
            self.run_docker_compose(docker_compose_file, "down", "--volumes")
            self.log.success("Server stopped.")
            if self.remove_network():
                self.log.success("Server network removed successfully.")
        else:
            server_folders = [item for item in server_path.iterdir() if item.is_dir()]
            for folder in server_folders:
                docker_compose_file = folder / "docker-compose.yml"
                if docker_compose_file.exists():
                    self.run_docker_compose(docker_compose_file, "down", "--volumes")
                    self.log.success(f"Server {folder.name} stopped.")
                    if self.remove_network():
                        self.log.success(f"Server {folder.name} network removed successfully.")

    def _stop_app(self, app_name):
        with self.log.loading("Stopping App"):
            try:
                result = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Names}}"],
                                     capture_output=True, text=True, check=True)
                if result.returncode != 0:
                    for line in result.stdout.strip().split("\n"):
                        container_id, container_name = line.split(" ", 1)
                        if app_name in container_name:
                            subprocess.run(["docker", "stop", container_id], check=True)

            except subprocess.CalledProcessError as e:
                self.log.error(f"Error stopping app: {e}")

            if self.remove_network():
                self.log.success("App network removed successfully.")
            self.log.success("All apps deployed in server stopped successfully.")

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
