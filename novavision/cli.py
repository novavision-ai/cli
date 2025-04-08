import os
import re
import stat
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

DEVICE_TYPE_CLOUD = 1
DEVICE_TYPE_EDGE = 2
DEVICE_TYPE_LOCAL = 3


def request_to_endpoint(method, endpoint, data=None, auth_token=None):
    headers = {'Authorization': f'Bearer {auth_token}'}
    response = None
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

        response = response.response if hasattr(response, 'response') else response
        return response
    except Exception as e:
        if response:
            response = response.response if hasattr(response, 'response') else response
            return response
        else:
            return e

def format_host(host):
    host = host.strip()

    if not host.startswith("https://"):
        if host.startswith("http://"):
            host = host[len("http://"):]
        host = "https://" + host

    if not host.endswith("/"):
        host = host + "/"

    return host

def create_agent():
    agent_dir = Path.home() / ".novavision"
    agent_dir.mkdir(parents=True, exist_ok=True)
    return agent_dir

def remove_readonly(func, path):
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
        log.error(f"Failed to remove {path}: {e}")

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
        return build_info

    except Exception as e:
        log.error(f"Failed to read docker-compose.yml: {e}")
        return None

def choose_server_folder(server_path):
    server_folders = [item for item in server_path.iterdir() if item.is_dir()]
    visible_folders = [f for f in server_folders if not f.name.startswith(".")]

    if not server_folders:
        log.error("No server folders found!")
        return None

    if len(server_folders) == 1 or len(visible_folders) == 1:
        return server_folders[0]

    log.info("Multiple server folders found. Please select one:")

    for idx, folder in enumerate(visible_folders):
        log.info(f"{idx + 1}. {folder.name}")

    while True:
        try:
            choice = int(log.question("Enter the number of the server you want."))
            if 1 <= choice <= len(visible_folders):
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

        server_path = Path.home() / ".novavision" / "Server"
        for folder in server_path.iterdir():
            if folder.is_dir():
                compose_file = folder / "docker-compose.yml"
                if compose_file.exists():
                    for container_name in running_containers:
                        if container_name.startswith(folder.name):
                            return compose_file
        return None
    except Exception as e:
        log.error(f"Error while fetching running container: {e}")
        return None

def delete_old_containers(key):
    containers = set()
    docker_compose_files = []
    server_path = Path.home() / ".novavision" / "Server"
    server_folder = choose_server_folder(server_path)

    for root, dirs, files in os.walk(server_folder):
        if "docker-compose.yml" in files:
            file_path = os.path.join(root, "docker-compose.yml")
            docker_compose_files.append(file_path)

    for compose_file in docker_compose_files:
        docker_info = get_docker_build_info(compose_file)
        for image_name in docker_info:
            container_names = subprocess.run(["docker", "ps", "-a", "--filter", f"ancestor={image_name}", "--format", "{{.Names}}"],capture_output=True, text=True).stdout.strip().split("\n")
            for cname in container_names:
               if cname:
                   containers.add((cname, image_name))

    containers = list(containers)

    try:
        for i in range(len(containers)):
            if key in containers[i][0]:
                subprocess.run(["docker", "container", "rm", "-f", f"{containers[i][0]}"], check=True, stdout=subprocess.DEVNULL)

        log.success("Successfully removed old containers.")
        return True

    except Exception as e:
        log.error(f"Failed to remove old containers: {e}")
        return None

def register_device_with_retry(data, token, host, device_info):
    register_endpoint = f"{host}api/device/default?expand=user"
    while True:
        device_endpoint = f"{host}api/device/default"
        device_response = request_to_endpoint(method="get", endpoint=device_endpoint, auth_token=token)

        if not device_response:
            log.error("Failed to fetch device list.")
            return None

        try:
            device_response = device_response.json()
        except ValueError:
            log.error(f"Invalid response format received while fetching devices: {device_response.text}")
            return None

        device_serial = device_info['serial']
        matching_devices = [d for d in device_response if d.get("serial") == device_serial]

        if matching_devices:
            log.warning(f"Device named {matching_devices[0]['name']} has same serial number as this machine. In order to continue device '{matching_devices[0]['name']}' must be deleted.")

            while True:
                remove = log.question(f"Would you like to delete {matching_devices[0]['name']}? (y/n)")
                if remove == "y":
                    delete_endpoint = f"{host}api/device/default/{matching_devices[0]['id_device']}"

                    with log.loading("Removing old device"):
                        delete_response = request_to_endpoint(method="delete", endpoint=delete_endpoint, auth_token=token)

                    if delete_response and delete_response.status_code == 204:
                        log.success("Old device removed successfully.")
                        break

                    else:
                        log.error("Device removal failed!")
                        return None

                elif remove == "n":
                    log.warning("Aborting.")
                    return None

                else:
                    log.warning("Invalid input. Try again.")

        else:
            log.info("No matching serial found for device. Continuing.")

        with log.loading("Registering device"):
            register_response = request_to_endpoint(method="post", endpoint=register_endpoint, data=data, auth_token=token)

        try:
            register_json = register_response.json()
        except Exception as e:
            log.error(f"Error occurred while device registration: {e}")
            return None

        if not isinstance(register_json, dict):
            log.error(f"Unexpected response from server: {register_json}")
            return None

        if register_response.status_code in [200, 201]:
            log.success("Device registered successfully!")
            return register_json

        elif register_response.status_code in [400, 403]:
            error_code = register_json.get("code", None)

            if error_code == 0:
                error_data = register_json.get("error", {})
                if not isinstance(error_data, dict):
                    log.error("The object 'error' cannot be found or is not in dict format.")
                    log.error(f"Full response: {register_json}")
                    return None

                error_message = register_json.get("message", "Unknown error occurred.")
                log.error(f"Device registration failed: {error_message}")
                return None

            else:
                log.warning("User exceeds the maximum limit of device! Device removal is needed.")

                log.info("Current devices:")
                for idx, device in enumerate(device_response):
                    device_type = {1: "cloud", 2: "edge"}.get(device["device_type"], "local")
                    log.info(f"{idx + 1}. {device['name']} (Device type: {device_type})")

                while True:
                    try:
                        choice = int(log.question("Please select a device to remove"))
                        if 1 <= choice <= len(device_response):
                            device_id_to_delete = device_response[choice - 1]['id_device']
                            break
                        else:
                            log.warning("Invalid selection. Please select a number from the list.")
                    except ValueError:
                        log.warning("Invalid entry. Please enter a number.")

                delete_endpoint = f"{host}api/device/default/{device_id_to_delete}"
                with log.loading("Removing device"):
                    delete_response = request_to_endpoint(method="delete", endpoint=delete_endpoint, auth_token=token)

                if delete_response and delete_response.status_code == 204:
                    log.success(f"Device '{device_response[choice - 1]['name']}' removed successfully.")
                    log.info("Trying registration again.")
                    continue
                else:
                    log.error("Device removal failed!")
                    return None
        else:
            log.error(f"Unexpected error occurred. Error: {register_response.text}")
            return None

def install(device_type, token, host, workspace):
    formatted_host = format_host(host)
    os.chdir(os.path.expanduser("~"))
    device_info = get_system_info()
    server_path = Path.home() / ".novavision" / "Server"

    try:
        subprocess.run(["docker", "info"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pass

    except subprocess.CalledProcessError:
        log.error("Docker is not running. Please activate docker first.")
        return

    except FileNotFoundError:
        log.error("Docker is not installed")
        return

    workspace_endpoint = f"{formatted_host}api/workspace/user?expand=workspace"
    workspace_list_response = request_to_endpoint(method="get", endpoint=workspace_endpoint, auth_token=token)

    try:
        if workspace_list_response.status_code != 200:
            log.error(f"Workspace list request failed. Error: {workspace_list_response.json()['message']}")
            return
    except Exception as e:
        log.error(f"Error occurred while getting workspace list: {e}")
        return

    workspace_list = workspace_list_response.json()

    if not workspace:
        if workspace_list is None:
            log.error("There is no workspace available.")
            return None

        if len(workspace_list) == 1:
            log.info("There is only one workspace available. Continuing registration.")
            workspace_id_to_select = workspace_list[0]["id_workspace_user"]

        else:
            log.info("There are multiple workspaces available for user. Current workspaces available:")
            for idx, workspaces in enumerate(workspace_list):
                log.info(f"{idx + 1}. {workspaces['workspace']['name']} (Workspace ID: {workspaces['id_workspace_user']})")

            while True:
                try:
                    choice = int(log.question("Please select a workspace to continue"))
                    if 1 <= choice <= len(workspace_list):
                        workspace_id_to_select = workspace_list[choice - 1]['id_workspace_user']
                        break

                    else:
                        log.warning("Invalid selection. Please select a number from the list.")

                except ValueError:
                    log.warning("Invalid entry. Please enter a number.")
    else:
        workspace_to_select = [workspaces for workspaces in workspace_list if workspaces["workspace"]["name"] == workspace]
        workspace_id_to_select = workspace_to_select[0]["id_workspace_user"]


    set_workspace_endpoint = f"{formatted_host}api/workspace/user/{workspace_id_to_select}"
    workspace_data = {"status": 1}
    set_workspace_response = request_to_endpoint(method="put", endpoint=set_workspace_endpoint, data=workspace_data, auth_token=token)

    if set_workspace_response.status_code == 200:
        log.success("Workspace set successfully!")

    else:
        log.error(f"Workspace set failed! Error: {set_workspace_response.text}")
        return

    if os.path.exists(server_path):
        app_name = None
        server_folder = choose_server_folder(server_path)
        pattern = re.compile(r'^[A-Za-z0-9]{6}$')

        for item in os.listdir(str(server_folder)):
            item_path = os.path.join(str(server_folder), item)
            if os.path.isdir(item_path) and pattern.match(item):
                app_name = item
                manage_docker("stop", "app", app_name)

        manage_docker("stop", "server")
        if app_name is not None:
            delete_containers = delete_old_containers(key=app_name)

            if delete_containers is None:
                return None

        while True:
            delete = log.question("There is already a server installed on this machine. Previous installation will be removed. All unsaved changes will be deleted. Would you like to continue?(y/n)").strip().lower()
            if delete == "y":
                try:
                    remove_directory(server_path)
                    break
                except Exception as e:
                    log.error(f"Server file deletion failed: {e}")

            elif delete == "n":
                log.warning("Aborting.")
                return
            else:
                log.warning("Invalid input. Try again.")


    if device_type == "cloud":
        response = request_to_endpoint(method="get", endpoint="https://api.ipify.org?format=text")
        wan_host = response.text

        log.info(f"Detected WAN HOST: {wan_host}")
        user_wan_ip = log.question("Would you like to use detected WAN HOST? (y/n)").strip().lower()

        if user_wan_ip == "y":
            log.info("Using detected WAN HOST...")

        elif user_wan_ip == "n":
            wan_host = log.question("Enter WAN HOST").strip()

        else:
            log.warning("Invalid input. Using detected WAN HOST...")

        user_port = log.question("Default port is 7001. Would you like to use it? (y/n)").strip().lower()

        if user_port == "y":
            port = "7001"

        elif user_port == "n":
            port = log.question("Please enter desired port")

        else:
            log.error("Invalid input.")

        data = {
            "name": f"{device_info['device_name']}",
            "serial": f"{device_info['serial']}",
            "device_type": DEVICE_TYPE_CLOUD,
            "processor": f"{device_info['processor']}",
            "cpu": f"{device_info['cpu']}",
            "gpu": f"{device_info['gpu']}",
            "os": f"{device_info['os']}",
            "disk": f"{device_info['disk']}",
            "memory": f"{device_info['memory']}",
            "architecture": f"{device_info['architecture']}",
            "platform": f"{device_info['platform']}",
            "os_api_port": f"{port}",
            "wan_host": f"{wan_host}",
        }

    elif device_type == "local":
        data = {
            "name": f"{device_info['device_name']}",
            "device_type": DEVICE_TYPE_LOCAL,
            "serial": f"{device_info['serial']}",
            "processor": f"{device_info['processor']}",
            "cpu": f"{device_info['cpu']}",
            "gpu": f"{device_info['gpu']}",
            "os": f"{device_info['os']}",
            "disk": f"{device_info['disk']}",
            "memory": f"{device_info['memory']}",
            "architecture": f"{device_info['architecture']}",
            "platform": f"{device_info['platform']}"
        }

    else:
        log.error("Wrong device type selected!")
        return

    register_response = register_device_with_retry(data=data, token=token, host=formatted_host, device_info=device_info)

    if register_response is None:
        return

    try:
        access_token = register_response["user"]["access_token"]
        id_device = register_response["id_device"]

        id_deploy_endpoint = f"{formatted_host}api/deployment?filter[id_device][eq]={id_device}&sort=id_deploy"
        id_deploy_response = request_to_endpoint(method="get", endpoint=id_deploy_endpoint, auth_token=token).json()
        id_deploy = id_deploy_response[0]["id_deploy"]

    except Exception as e:
        log.error(f"Error while getting access token and device id: {e}")
        return

    server_endpoint = f"{formatted_host}api/device/default/{id_device}"

    with log.loading("Building server"):
        try:
            server_response = request_to_endpoint(method="get", endpoint=server_endpoint, auth_token=access_token)
            if server_response.status_code == 200:
                server_package = server_response.json()["server_package"]

            else:
                log.error(f"Failed to get server package: {server_response.text}")
                return

            agent_endpoint = f"{formatted_host}api/storage/default/get-file?id={server_package}"
            agent_response = request_to_endpoint(method="get", endpoint=agent_endpoint, auth_token=access_token)

        except Exception as e:
            log.error(f"Error while getting server package: {e}")
            return

        extract_path = create_agent()

        extract_path.mkdir(parents=True, exist_ok=True)

        zip_path = extract_path / "temp.zip"
    try:
        with open(zip_path, "wb") as f:
            f.write(agent_response.content)

        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_path)

        deploy_data = {"is_deploy": 1}
        try:
            with log.loading("Sending device deployment status"):
                device_deploy_response = request_to_endpoint(method="put", endpoint=server_endpoint, data=deploy_data,
                                                             auth_token=token)

            if device_deploy_response:
                if device_deploy_response.status_code == 200:
                    log.success("Device deployment status updated successfully!")

                else:
                    log.error(f"Device deployment failed! {device_deploy_response.text}")
                    return

            else:
                log.error("Deployment request failed: No response received from the server.")

        except requests.exceptions.RequestException as e:
            log.error(f"Deployment request failed due to a network error: {e}")

        except Exception as e:
            log.error(f"An unexpected error occurred during deployment: {e}")

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
            server_folder = [item for item in server_path.iterdir() if item.is_dir()]
            agent_folder = max(server_folder, key=lambda folder: folder.stat().st_mtime)
            compose_file = agent_folder / "docker-compose.yml"

            if not compose_file.exists():
                log.error(f"No docker-compose.yml found in {agent_folder}!")

            subprocess.run(["docker", "compose", "-f", str(compose_file), "up", "-d"], check=True)

            log.success("Server built successfully!")
            deploy_data = {"is_deploy": 1}
            try:
                agent_deploy_endpoint = f"{formatted_host}api/deployment/default/{id_deploy}"
                with log.loading("Sending agent deployment status"):
                    agent_deploy_response = request_to_endpoint(method="put", endpoint=agent_deploy_endpoint, data=deploy_data, auth_token=token)

                if agent_deploy_response:
                    if agent_deploy_response.status_code == 200:
                        log.success("Agent deployment status updated successfully!")

                    else:
                        log.error(f"Agent deployment failed! {agent_deploy_response.text}")
                        return

                else:
                    log.error("Deployment request failed: No response received from the server.")

            except requests.exceptions.RequestException as e:
                log.error(f"Deployment request failed due to a network error: {e}")

            except Exception as e:
                log.error(f"An unexpected error occurred during deployment: {e}")

        except subprocess.CalledProcessError as e:
            log.error(f"Docker Compose failed with error code {e.returncode}")
            log.error(f"Standard Output:\n{e.stdout}")
            log.error(f"Standard Error:\n{e.stderr}")
        except Exception as e:
            log.error(f"Error during building server: {str(e)}")

    except zipfile.BadZipFile:
        log.error("Error: The downloaded file is not a valid zip file")

    except Exception as e:
        log.error(f"Error during extraction: {str(e)}")

    finally:
        if zip_path.exists():
            os.remove(zip_path)

def deploy(type, id, to):
    # App deployu ve agent'ın içerisine konulması
    pass

def manage_docker(command, type, app_name=None):
    default_path = Path.home() / ".novavision"
    server_path = default_path / "Server"
    docker_compose_files = []

    if type == "server":
        if command == "start":
            server_folder = choose_server_folder(server_path)
            docker_compose_file = server_folder / "docker-compose.yml"

        else:
            docker_compose_file = get_running_container_compose_file()

            if not docker_compose_file:
                log.warning("No running server found to stop.")
                return

    else:
        server_folder = choose_server_folder(server_path)

        for root, dirs, files in os.walk(server_folder):
            if Path(root) == server_folder:
                continue

            if "docker-compose.yml" in files:
                file_path = os.path.join(root, "docker-compose.yml")
                docker_compose_files.append(file_path)

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
                    ports = []

                    for mapping in container_ports.split(", "):
                        if "->" in mapping:
                            ports.append(mapping.split("->")[1].split("/")[0].strip())

                    port_display = ", ".join(ports) if ports else "Not Exposed to Host"
                    new_containers.append((container_name, port_display))

            if new_containers:
                log.info("Started containers:")
                for name, ports in new_containers:
                    log.info(f"- {name} -> Ports: {ports}")

            else:
                log.warning("No containers started.")

        else:
            if type == "server":
                subprocess.run(["docker", "compose", "-f", str(docker_compose_file), "down", "--volumes"], check=True)
                log.success("Server stopped.")
            else:
                with log.loading("Stopping App"):
                    try:
                        result = subprocess.run(["docker", "ps", "--format", "{{.ID}} {{.Names}}"], capture_output=True, text=True, check=True)
                        if result.returncode != 0:
                            for line in result.stdout.strip().split("\n"):
                                container_id, container_name = line.split(" ", 1)
                                if app_name in container_name:
                                    subprocess.run(["docker", "stop", container_id], check=True)

                    except subprocess.CalledProcessError as e:
                        log.error(f"Error stopping app: {e}")

                    log.success("All apps deployed in server stopped successfully.")

    except subprocess.CalledProcessError as e:
        log.error(f"Error while managing docker compose: {e}")


def main():
    parser = argparse.ArgumentParser(description="NovaVision CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.required = True

    install_parser = subparsers.add_parser("install", help="Creates device and installs server")
    install_parser.add_argument("device_type", choices=["edge", "local", "cloud"],
                               help="Select and Configure Device Type")
    install_parser.add_argument("token", help="User Authentication Token")
    install_parser.add_argument("--host", default="https://alfa.suite.novavision.ai", help="Host Url")
    install_parser.add_argument("--workspace", default=None, help="Workspace Name")

    start_parser = subparsers.add_parser("start", help="Starts server | app")
    start_parser.add_argument("type", choices=["server", "app"])
    start_parser.add_argument("--id", help="AppID for App Choice", required=False)

    deploy_parser = subparsers.add_parser("deploy", help="Deploying App")
    deploy_parser.add_argument("type", choices="app")
    deploy_parser.add_argument("--id", help="AppID for Which App Will Be Deployed", required=False)
    deploy_parser.add_argument("token", help="User Authentication Token")

    stop_parser = subparsers.add_parser("stop", help="Stops server | app")
    stop_parser.add_argument("type", choices=["server", "app"])
    stop_parser.add_argument("--id", help="AppID for App Choice", required=False)

    args = parser.parse_args()

    if args.command == "install":
        install(device_type=args.device_type, token=args.token, host=args.host, workspace=args.workspace)

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