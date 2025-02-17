import os
import stat
import uuid
import yaml
import shutil
import zipfile
import argparse
import requests
import platform
import subprocess

from pathlib import Path
from novavision.utils import get_system_info
from novavision.logger import ConsoleLogger

log = ConsoleLogger()


def request_to_endpoint(method, endpoint, data=None, auth_token=None):
    headers = {'Authorization': f'Bearer {auth_token}'}
    try:
        if method == 'get':
            response = requests.get(endpoint, headers=headers)
        elif method == 'post':
            response = requests.post(endpoint, data=data, headers=headers)
        elif method == 'put':
            response = requests.put(endpoint, data=data, headers=headers)
        elif method == 'delete':
            response = requests.delete(endpoint, headers=headers)
        else:
            log.error(f"Invalid HTTP method: {method}")
            return None

        response.raise_for_status()
        return response
    except requests.exceptions.RequestException as e:
        log.error(f"HTTP request failed: {e}")
        return None

def create_default_directory():
    default_dir = Path.home() / ".novavision"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir

def remove_readonly(func, path, exc_info):
    os.chmod(path, stat.S_IWRITE)
    func(path)

def remove_directory(path):
    try:
        if platform.system() == "Windows":
            shutil.rmtree(path, onerror=remove_readonly)
        else:
            subprocess.run(
                ["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", path],
                check=True
            )
            subprocess.run(["rm", "-rf", path], check=True)
    except Exception as e:
        print(f"Failed to remove {path}: {e}")

def get_docker_build_info(compose_file):
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
            log.error("No buildable services found in docker-compose.yml!")
            return None

        log.info(f"Found services in compose file: {build_info}")
        return build_info

    except Exception as e:
        log.error(f"Failed to read docker-compose.yml: {e}")
        return None

def choose_server_folder(server_path):
    server_folders = [item for item in server_path.iterdir() if item.is_dir()]

    if not server_folders:
        log.error("No server folders found!")
        return None

    if len(server_folders) == 1:
        return server_folders[0]

    log.info("Multiple server folders found. Please select one:")

    for idx, folder in enumerate(server_folders):
        log.info(f"{idx + 1}. {folder.name}")

    while True:
        try:
            choice = int(log.question("Enter the number of the server you want to start: "))
            if 1 <= choice <= len(server_folders):
                return server_folders[choice - 1]
            else:
                log.warning("Invalid selection. Please enter a valid number.")
        except ValueError:
            log.warning("Invalid input. Please enter a number.")

def get_running_container_compose_file():
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True
        )
        running_containers = result.stdout.strip().split("\n")

        if not running_containers:
            log.warning("No running containers found.")
            return None

        extract_path = Path.home() / ".novavision" / "Server"
        for folder in extract_path.iterdir():
            if folder.is_dir():
                compose_file = folder / "docker-compose.yml"
                if compose_file.exists():
                    for container_name in running_containers:
                        if container_name.startswith(folder.name):
                            log.info(f"Found running container: {container_name} using {compose_file}")
                            return compose_file
        log.warning("No matching running container found in known server folders.")
        return None
    except Exception as e:
        log.error(f"Error while fetching running container: {e}")
        return None

def install(device_type, token, host):
    if device_type == "cloud":
        response = request_to_endpoint("get", "https://api.ipify.org?format=text")
        wan_host = response.text

        log.info(f"Detected WAN HOST: {wan_host}")
        user_wan_ip = log.question("Would you like to use detected WAN HOST? (y/n)").strip().lower()

        if user_wan_ip == "y":
            log.info("Using detected WAN HOST...")
        elif user_wan_ip == "n":
            wan_host = log.question("Enter WAN HOST").strip()
        else:
            print("Invalid input. Using detected WAN HOST...")

        user_port = log.question("Default port is 7001. Would you like to use it? (y/n)").strip().lower()

        if user_port == "y":
            port = "7001"
        elif user_port == "n":
            port = log.question("Please enter desired port")
        else:
            log.error("Invalid input.")


        data = {
        "name": f"{uuid.uuid4()}",
        "os_api_host": "0.0.0.0",
        "os_api_port": f"{port}",
        "os_api_ssl": "DISABLE",
        "wan_host": f"{wan_host}",
        "type": "Cloud"
    }

    elif device_type == "local":
        server_path = Path.home() / ".novavision" / "Server"
        if os.path.exists(server_path):
            delete = log.question(
                "There is already a server installed on this machine. Previous installation will be removed. All unsaved changes will be deleted. Would you like to continue?(y/n)").strip().lower()
            if delete == "y":
                try:
                    remove_directory(server_path)
                except Exception as e:
                    log.error(f"Deletion failed: {e}")
                device_endpoint = f"{host}/api/device/default?filter[device_type][eq]=3"
                response = request_to_endpoint(method="get", endpoint=device_endpoint, auth_token=token)
                device_ids = [device['id_device'] for device in response.json() if device['device_type'] == 3]
                delete_endpoint = f"{host}/api/device/default/{device_ids[0]}"
                with log.loading("Removing device"):
                    response = request_to_endpoint(method="delete", endpoint=delete_endpoint, auth_token=token)

                if response.status_code == 204:
                    log.success("Successfully removed device and server.")
                else:
                    log.error("Failed to removed device.")
                    return
            else:
                log.warning("Aborting.")
                return

        data = {
        "name": f"{uuid.uuid4()}",
        "os_api_host": "0.0.0.0",
        "os_api_port": "7001",
        "os_api_ssl": "DISABLE",
        "type": "Local"
        }

    else:
        log.error("Wrong device type selected!")
        return

    endpoint2 = f"{host}/api/device/data/register-device"
    with log.loading("Registering device"):
        endpoint2_response = request_to_endpoint(method="post", endpoint=endpoint2, data=data, auth_token=token)
    if endpoint2_response.status_code == 200:
        log.success("Device registered successfully!")
        response = endpoint2_response.json()
        access_token = response.get("access-token")
        id_deploy = response.get("id_deploy")

        endpoint3 = f"{host}/api/device/data/initialize-device"

        try:
            device_info = get_system_info()
            with log.loading("Initializing device"):
                endpoint3_response = request_to_endpoint(method="post", endpoint=endpoint3, data=device_info, auth_token=access_token)
        except Exception as e:
            log.error(f"Failed to initialize device: {e}")

        if endpoint3_response.status_code == 200:
            log.success("Device information initialized successfully!")
            server_endpoint = f"{host}/api/device/data/get-server"
            try:
                server_response = request_to_endpoint(method="get", endpoint=server_endpoint, auth_token=access_token)
                file_id = server_response.json()
                file_endpoint = f"{host}/api/storage/default/get-file?id={file_id}"
                file_response = request_to_endpoint(method="get", endpoint=file_endpoint, auth_token=access_token)
            except Exception as e:
                log.error(f"Error while getting server files: {e}")

            extract_path = create_default_directory()

            extract_path.mkdir(parents=True, exist_ok=True)

            zip_path = extract_path / "temp.zip"
            try:
                with open(zip_path, "wb") as f:
                    f.write(file_response.content)

                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)

                log.success(f"Files extracted successfully to: {extract_path}")

                server_path = extract_path / "Server"
                env_file = server_path / ".env"
                key, value = "ROOT_PATH", str(server_path)

                if env_file.exists():
                    with open(env_file, "r") as f:
                        lines = f.readlines()
                    lines = [f"{key}={value}\n" if line.startswith(f"{key}=") else line for line in lines]
                    if not any(line.startswith(f"{key}=") for line in lines):
                        lines.append(f"{key}={value}\n")
                else:
                    lines = [f"{key}={value}\n"]

                with open(env_file, "w") as f:
                    f.writelines(lines)

                try:
                    deploy_endpoint = f"{host}/api/device/deploy/{id_deploy}"
                    image_exist = subprocess.run(["docker", "images", "-q", "diginova-wsl"], capture_output=True, text=True)
                    if not image_exist.stdout.strip():
                        server_path = Path.home() / ".novavision" / "Server"
                        server_folder = [item for item in server_path.iterdir() if item.is_dir()]
                        agent_folder = max(server_folder, key=lambda folder: folder.stat().st_mtime)
                        compose_file = agent_folder / "docker-compose.yml"
                        if not compose_file.exists():
                            log.error(f"No docker-compose.yml found in {agent_folder}!")
                        build_info = get_docker_build_info(compose_file)
                        for service, info in build_info.items():
                            image_name = info["image"]
                            build_context = agent_folder / Path(info["context"])
                            dockerfile = build_context / "Dockerfile.prod"
                            if dockerfile.exists():
                                log.info(f"Building Docker image {image_name} from {dockerfile}...")
                                subprocess.run(
                                    ["docker", "build", "-t", image_name, "-f", str(dockerfile), str(build_context)],
                                    check=True
                                )
                                log.success(f"Docker image {image_name} built successfully!")
                            else:
                                log.error(f"Dockerfile.prod not found in {build_context} for service {service}!")

                    log.success("Server built successfully!")
                    deploy_data = {"is_deploy": 1}
                    try:
                        with log.loading("Sending deployment status"):
                            deploy_response = request_to_endpoint(
                                method="put",
                                endpoint=deploy_endpoint,
                                data=deploy_data,
                                auth_token=access_token
                            )

                        if deploy_response:
                            if deploy_response.status_code == 200:
                                log.success("Deployment status updated successfully!")
                            else:
                                log.error(f"Deployment failed! Status Code: {deploy_response.status_code}, Response: {deploy_response.text}")
                        else:
                            log.error("Deployment request failed: No response received from the server.")
                    except requests.exceptions.RequestException as e:
                        log.error(f"Deployment request failed due to a network error: {e}")
                    except Exception as e:
                        log.error(f"An unexpected error occurred during deployment: {e}")

                except Exception as e:
                    log.error(f"Error during building server: {str(e)}")

            except zipfile.BadZipFile:
                log.error("Error: The downloaded file is not a valid zip file")
            except Exception as e:
                log.error(f"Error during extraction: {str(e)}")
            finally:
                if zip_path.exists():
                    os.remove(zip_path)
        else:
            log.error("Device initialization failed!")


    else:
        log.error(f"Registration failed! Status code: {endpoint2_response.status_code} - Response: {endpoint2_response.text}")


def deploy(type, id, to):
    # App deployu ve agent'ın içerisine konulması
    pass

def manage_docker(command, type):
    extract_path = Path.home() / ".novavision"
    server_path = extract_path / "Server"

    if type == "server":
        if command == "start":
            selected_server_folder = choose_server_folder(server_path)
            if not selected_server_folder:
                return
            docker_compose_file = selected_server_folder / "docker-compose.yml"
        else:
            docker_compose_file = get_running_container_compose_file()
            if not docker_compose_file:
                log.error("No running server found to stop.")
                return
    else:
        # App'in compose'u seçilmeli.
        pass

    try:
        if command == "start":
            previous_containers = set(subprocess.run(
                ["docker", "ps", "-q"],
                capture_output=True, text=True
            ).stdout.strip().split("\n"))

            log.info("Starting server")
            subprocess.run(["docker", "compose", "-f", str(docker_compose_file), "up", "-d"], check=True)
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.ID}} {{.Names}} {{.Ports}}"],
                capture_output=True, text=True)

            current_containers = result.stdout.strip().split("\n")
            new_containers = []
            for container in current_containers:
                parts = container.split(" ", 2)
                container_id = parts[0]
                container_name = parts[1]
                container_ports = parts[2] if len(parts) > 2 else "No ports"
                if container_id not in previous_containers:
                    for mapping in container_ports.split(", "):
                        if "->" in mapping:
                            port = mapping.split("->")[1].split("/")[0].strip()

                    port_display = ", ".join(port) if port else "No ports"
                    new_containers.append((container_name, port_display))


            if new_containers:
                log.info("Started containers:")
                for name, ports in new_containers:
                    log.info(f"- {name} -> Port: {port}")
            else:
                log.warning("No containers started.")

        else:
            subprocess.run(["docker", "compose", "-f", str(docker_compose_file), "down"], check=True)
            log.info("Server stopped.")

    except subprocess.CalledProcessError as e:
        log.error(f"Error while managing docker compose: {e}")


def main():
    parser = argparse.ArgumentParser(description="NovaVision CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.required = True

    install_parser = subparsers.add_parser("install", help="Initialize and Download Agent")
    install_parser.add_argument("device_type", choices=["edge", "local", "cloud"],
                               help="Select and Configure Device Type")
    install_parser.add_argument("token", help="User Authentication Token")
    install_parser.add_argument("--host", default="https://alfa.suite.novavision.ai", help="Host Url")

    start_parser = subparsers.add_parser("start", help="Start Docker Container")
    start_parser.add_argument("type", choices=["server", "app"])
    start_parser.add_argument("--id", help="AppID for App Choice", required=False)

    deploy_parser = subparsers.add_parser("deploy", help="Deploying App")
    deploy_parser.add_argument("type", choices="app")
    deploy_parser.add_argument("--id", help="AppID for Which App Will Be Deployed", required=False)
    deploy_parser.add_argument("token", help="User Authentication Token")

    stop_parser = subparsers.add_parser("stop", help="Stop Docker Container")
    stop_parser.add_argument("type", choices=["server", "app"])
    stop_parser.add_argument("--id", help="AppID for App Choice", required=False)

    args = parser.parse_args()

    if args.command == "install":
        install(device_type=args.device_type, token=args.token, host=args.host)
    elif args.command == "start" or args.command == "stop":
        if (args.type == "app" and args.id) or args.type == "server":
            manage_docker(command=args.command, type=args.type)
        else:
            log.error("Invalid arguments!")
    elif args.command == "deploy":
        if args.type and args.id:
            deploy(args.type, args.id, args.token)
    else:
        log.error("Invalid command!")

if __name__ == "__main__":
    main()