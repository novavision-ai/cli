import json
import os
import shutil
import zipfile
import requests
import subprocess

from datetime import datetime
from pathlib import Path
from novavision.logger import ConsoleLogger
from novavision.utils import get_system_info
from novavision.docker_manager import DockerManager
from novavision.service_manager import ServiceManager


class Installer:
    DEVICE_TYPE_CLOUD = 1
    DEVICE_TYPE_EDGE = 2
    DEVICE_TYPE_LOCAL = 3

    def __init__(self, logger: ConsoleLogger):
        self.log = logger if logger else ConsoleLogger()
        self.docker = DockerManager(logger=self.log)
        self.service = ServiceManager(logger=self.log, docker_manager=self.docker)
        self.agent_dir = self._create_agent()
        self.non_interactive = False

    def _create_agent(self):
        agent_dir = Path.home() / ".novavision"
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def _metadata_path(self):
        return self.agent_dir / "servers.json"

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

    def _extract_created_at(self, register_response):
        for key in (
            "created_at",
            "createdAt",
            "created_date",
            "date_created",
            "insert_date",
        ):
            value = register_response.get(key)
            if value:
                return value
        return datetime.now().isoformat(timespec="seconds")

    def _save_server_metadata(
        self, server_folder, register_response, host, workspace_name
    ):
        if not server_folder:
            return

        metadata = self._load_server_metadata()
        metadata[server_folder.name] = {
            "created_at": self._extract_created_at(register_response),
            "workspace": workspace_name or "Unknown",
            "host": self.format_host(host).rstrip("/"),
            "id_device": register_response.get("id_device"),
            "name": register_response.get("name")
            or register_response.get("device_name")
            or server_folder.name,
        }

        try:
            with open(self._metadata_path(), "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            self.log.success("Server metadata saved.")
        except Exception as e:
            self.log.warning(f"Could not save server metadata: {e}")

    def _select_gpu(self, device_info):
        # Birden fazla GPU varsa kullanıcıdan seçim yapmasını iste
        if isinstance(device_info["gpu"], list):
            if len(device_info["gpu"]) > 1:
                if self.non_interactive:
                    device_info["gpu"] = device_info["gpu"][0]
                    self.log.info(
                        f"Non-interactive mode: using GPU {device_info['gpu']}"
                    )
                    return
                self.log.info("Multiple GPUs detected. Please select one GPU.")
                for idx, gpu in enumerate(device_info["gpu"]):
                    self.log.info(f"{idx + 1}. {gpu}")
                while True:
                    try:
                        choice = int(
                            self.log.question("Please select a GPU to continue")
                        )
                        if 1 <= choice <= len(device_info["gpu"]):
                            device_info["gpu"] = device_info["gpu"][choice - 1]
                            break
                        else:
                            self.log.warning(
                                "Invalid selection. Please select a number from the list."
                            )
                    except ValueError:
                        self.log.warning("Invalid entry. Please enter a number.")
            else:
                device_info["gpu"] = (
                    device_info["gpu"][0] if device_info["gpu"] else "No GPU Detected"
                )

    def format_host(self, host):
        # CLI'da girilen host parametresinin doğru formatta olup olmadığını kontrol et
        host = host.strip()
        if not host.startswith("https://"):
            if host.startswith("http://"):
                host = host[len("http://") :]
            host = "https://" + host
        if not host.endswith("/"):
            host = host + "/"
        return host

    def request_to_endpoint(self, method, endpoint, data=None, auth_token=None):
        # Genel API istek fonksiyonu
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        response = None
        try:
            if method == "get":
                response = requests.get(endpoint, headers=headers)
            elif method == "post":
                response = requests.post(endpoint, data=data, headers=headers)
            elif method == "put":
                response = requests.put(endpoint, data=data, headers=headers)
            elif method == "delete":
                response = requests.delete(endpoint, headers=headers)
            else:
                self.log.error(f"Invalid HTTP method: {method}")
                return None
            return response
        except requests.exceptions.RequestException as e:
            return e

    def install(
        self, device_type, token, host, workspace, port=None, non_interactive=False
    ):
        self.non_interactive = bool(non_interactive)
        host = self.format_host(host)
        os.chdir(os.path.expanduser("~"))

        if not self.docker._check_docker_available():
            return False
        self.docker._cleanup_previous_docker_installations()

        device_info = get_system_info()
        if "error" in device_info:
            self.log.error(f"Error getting system info: {device_info['error']}")
            return False

        self._select_gpu(device_info)

        workspace_selection = self._get_workspace_id(host, token, workspace)
        if not workspace_selection:
            return False

        workspace_user_id, workspace_name = workspace_selection
        if not self._set_workspace(host, token, workspace_user_id):
            return False

        selected_port = self._select_port(port)
        if not selected_port:
            return False

        device_data = self._prepare_device_data(device_type, device_info, selected_port)
        if not device_data:
            return False

        register_response = self._register_device(device_data, token, host, device_info)
        if register_response is None:
            return False

        pending_key = str(register_response.get("id_device") or "pending")
        self._save_server_metadata(
            Path(pending_key), register_response, host, workspace_name
        )

        server_folder = self._setup_server(register_response, host)
        if not server_folder:
            return False

        if server_folder.name != pending_key:
            metadata = self._load_server_metadata()
            if pending_key in metadata:
                metadata[server_folder.name] = metadata.pop(pending_key)
                try:
                    with open(self._metadata_path(), "w", encoding="utf-8") as f:
                        json.dump(metadata, f, indent=2)
                except Exception as e:
                    self.log.warning(f"Could not update server metadata key: {e}")

        self._save_server_metadata(
            server_folder, register_response, host, workspace_name
        )
        return True

    def _get_workspace_id(self, host, token, workspace):
        # Host ve endpoint ayarlama
        host = self.format_host(host)
        get_workspace_endpoint = f"{host}api/workspace/user?expand=workspace"

        # Kullanıcıya ait workspace listesini alma
        workspace_list_response = self.request_to_endpoint(
            "get", endpoint=get_workspace_endpoint, auth_token=token
        )

        if isinstance(workspace_list_response, requests.exceptions.ConnectionError):
            self.log.error(
                "Failed to connect to the server. Please check the host URL and network connection."
            )
            return None
        if workspace_list_response is None:
            self.log.error("Failed to get workspace list from server")
            return None

        try:
            if workspace_list_response.status_code != 200:
                self.log.error(
                    f"Workspace list request failed. Error: {workspace_list_response.json()['message']}"
                )
                return None
        except Exception as e:
            self.log.error(f"Error occurred while getting workspace list: {e}")
            return None

        try:
            workspace_list = workspace_list_response.json()
        except Exception as e:
            self.log.error(f"Failed to parse workspace response: {e}")
            return None

        # Kullanıcı CLI'da eğer workspace belirtmemişse seçim yapmasını sağla
        if not workspace:
            workspace_user_id = None
            if not workspace_list:
                self.log.error("There is no workspace available.")
                return None

            if len(workspace_list) == 1:
                self.log.info(
                    "There is only one workspace available. Continuing registration."
                )
                workspace_user_id = workspace_list[0].get("id_workspace_user")
                if not workspace_user_id:
                    self.log.error("Workspace user ID not found in response")
                    return None
                workspace_name = (
                    workspace_list[0].get("workspace", {}).get("name", "Unknown")
                )
                return workspace_user_id, workspace_name

            if self.non_interactive:
                self.log.error(
                    "Multiple workspaces found. Pass --workspace in non-interactive mode."
                )
                return None

            self.log.info(
                "There are multiple workspaces available for user. Current workspaces available:"
            )
            for idx, workspaces in enumerate(workspace_list):
                workspace_info = workspaces.get("workspace", {})
                workspace_name = workspace_info.get("name", "Unknown")
                workspace_user_id = workspaces.get("id_workspace_user", "Unknown")
                self.log.info(
                    f"{idx + 1}. {workspace_name} (Workspace ID: {workspace_user_id})"
                )

            while True:
                try:
                    choice = int(
                        self.log.question("Please select a workspace to continue")
                    )
                    if 1 <= choice <= len(workspace_list):
                        selected_workspace = workspace_list[choice - 1]
                        workspace_name = selected_workspace.get("workspace", {}).get(
                            "name", "Unknown"
                        )
                        return selected_workspace["id_workspace_user"], workspace_name
                    else:
                        self.log.warning(
                            "Invalid selection. Please select a number from the list."
                        )
                except ValueError:
                    self.log.warning("Invalid entry. Please enter a number.")
        else:
            workspace_to_select = [
                workspaces
                for workspaces in workspace_list
                if workspaces["workspace"]["name"] == workspace
            ]
            if not workspace_to_select:
                self.log.error(f"Workspace '{workspace}' not found.")
                return None

            workspace_user_id = workspace_to_select[0].get("id_workspace_user")
            if not workspace_user_id:
                self.log.error(f"Workspace '{workspace}' does not have a valid user ID")
                return None
            return workspace_user_id, workspace

    def _set_workspace(self, host, token, workspace_user_id):
        if workspace_user_id is None:
            self.log.error("Workspace user_id not found.")
            return False

        set_workspace_endpoint = f"{host}api/workspace/user/{workspace_user_id}"
        workspace_data = {"status": 1}

        set_workspace_response = self.request_to_endpoint(
            method="put",
            endpoint=set_workspace_endpoint,
            data=workspace_data,
            auth_token=token,
        )

        try:
            if set_workspace_response.status_code == 200:
                self.log.success("Workspace set successfully!")
                return True
            else:
                self.log.error(
                    f"Workspace set failed! Error: {set_workspace_response.text}"
                )
                return False
        except Exception as e:
            self.log.error(f"Error occurred while setting workspace: {e}")
            return False

    def _select_port(self, port=None):
        if port is not None:
            port_str = str(port).strip()
            if port_str.isdigit() and 1 <= int(port_str) <= 65535:
                return port_str
            self.log.error("Port must be a number between 1 and 65535.")
            return None

        if self.non_interactive:
            self.log.info("Non-interactive mode: using default port 7001.")
            return "7001"

        while True:
            user_port = (
                self.log.question(
                    "Default port is 7001. Would you like to use it? (y/n)"
                )
                .strip()
                .lower()
            )
            if user_port == "y":
                return "7001"
            elif user_port == "n":
                while True:
                    entered = self.log.question("Please enter desired port").strip()
                    if entered.isdigit():
                        port_int = int(entered)
                        if 1 <= port_int <= 65535:
                            return str(port_int)
                        else:
                            self.log.warning("Port must be between 1 and 65535.")
                    else:
                        self.log.warning("Port must be a number.")
            else:
                self.log.error("Invalid input.")

    def _prepare_device_data(self, device_type, device_info, port):
        base_data = {
            "name": device_info["device_name"],
            "serial": device_info["serial"],
            "processor": device_info["processor"],
            "cpu": device_info["cpu"],
            "gpu": device_info["gpu"],
            "os": device_info["os"],
            "disk": device_info["disk"],
            "memory": device_info["memory"],
            "architecture": device_info["architecture"],
            "platform": device_info["platform"],
            "os_api_port": port,
        }

        if device_type == "cloud":
            try:
                response = requests.get("https://api.ipify.org?format=text")
                wan_host = response.text
                self.log.info(f"Detected WAN HOST: {wan_host}")
                if self.non_interactive:
                    self.log.info("Non-interactive mode: using detected WAN HOST.")
                else:
                    user_wan_ip = (
                        self.log.question(
                            "Would you like to use detected WAN HOST? (y/n)"
                        )
                        .strip()
                        .lower()
                    )

                    if user_wan_ip == "n":
                        wan_host = self.log.question("Enter WAN HOST").strip()
                    elif user_wan_ip != "y":
                        self.log.warning("Invalid input. Using detected WAN HOST...")

                base_data.update(
                    {"device_type": self.DEVICE_TYPE_CLOUD, "wan_host": wan_host}
                )
            except Exception as e:
                self.log.error(f"Error getting WAN host: {e}")
                return None

        elif device_type == "edge":
            base_data["device_type"] = self.DEVICE_TYPE_EDGE

        elif device_type == "local":
            base_data["device_type"] = self.DEVICE_TYPE_LOCAL

        else:
            self.log.error("Wrong device type selected!")
            return None

        return base_data

    def _register_device(self, data, token, host, device_info):
        host = self.format_host(host)
        register_endpoint = f"{host}api/device/default?expand=user"
        device_endpoint = f"{host}api/device/default"

        while True:
            device_response = self.request_to_endpoint(
                "get", endpoint=device_endpoint, auth_token=token
            )
            if not device_response:
                self.log.error("Failed to fetch device list.")
                return None

            try:
                device_response = device_response.json()
            except ValueError:
                self.log.error(
                    f"Invalid response format received while fetching devices: {device_response.text}"
                )
                return None

            # device_serial = device_info['serial']
            # matching_devices = [d for d in device_response if d.get("serial") == device_serial]
            #
            # if matching_devices:
            #     device = matching_devices[0]
            #     self.log.warning(f"Device named {device['name']} has same serial number as this machine.")
            #     self.log.warning("In order to continue device must be deleted.")
            #
            #     while True:
            #         remove = self.log.question(f"Would you like to delete {device['name']}? (y/n)").lower()
            #         if remove == "y":
            #             if not self._delete_device(device['id_device'], host, token):
            #                 return None
            #             break
            #         elif remove == "n":
            #             self.log.warning("Aborting.")
            #             return None
            #         else:
            #             self.log.warning("Invalid input. Try again.")
            # else:
            #     self.log.info("No matching serial found for device. Continuing.")

            with self.log.loading("Registering device"):
                register_response = self.request_to_endpoint(
                    "post", endpoint=register_endpoint, data=data, auth_token=token
                )

            if register_response is None:
                self.log.error("Failed to register device")
                return None

            try:
                register_json = register_response.json()
                if register_response.status_code in [200, 201]:
                    self.log.success("Device registered successfully!")
                    return register_json
                elif register_response.status_code in [400, 403]:
                    error_code = register_json.get("code", None)
                    error = register_json.get("error", None)

                    if error is not None:
                        if isinstance(error, dict):
                            for value in error.values():
                                self.log.error(
                                    f"Device registration failed: {str(value[0])}"
                                )
                        else:
                            self.log.error(f"Device registration failed: {error}")
                        return None

                    try:
                        if error_code is not None:
                            error_data = register_json.get("message", None)
                            if error_code == 0:
                                if not isinstance(error_data, dict):
                                    self.log.error(
                                        "The object 'error' cannot be found or is not in dict format."
                                    )
                                    self.log.error(f"Error Data: {error_data}")
                                    return None

                                error_message = register_json.get(
                                    "message", "Unknown error occurred."
                                )
                                self.log.error(
                                    f"Device registration failed: {error_message}"
                                )
                                return None

                            elif error_code == 1:
                                self.log.warning(
                                    "User exceeds the maximum limit of device! Device removal is needed."
                                )
                                if self.non_interactive:
                                    self.log.error(
                                        "Device limit reached. Uninstall an existing device, then retry."
                                    )
                                    return None

                                self.log.info("Current devices:")
                                for idx, device in enumerate(device_response):
                                    device_type = {1: "cloud", 2: "edge"}.get(
                                        device["device_type"], "local"
                                    )
                                    self.log.info(
                                        f"{idx + 1}. {device['name']} (Device type: {device_type})"
                                    )

                                while True:
                                    try:
                                        choice = int(
                                            self.log.question(
                                                "Please select a device to remove"
                                            )
                                        )
                                        if 1 <= choice <= len(device_response):
                                            device_id_to_delete = device_response[
                                                choice - 1
                                            ]["id_device"]
                                            break
                                        else:
                                            self.log.warning(
                                                "Invalid selection. Please select a number from the list."
                                            )
                                    except ValueError:
                                        self.log.warning(
                                            "Invalid entry. Please enter a number."
                                        )

                                self._delete_device(device_id_to_delete, host, token)

                            else:
                                if error_data is not None:
                                    self.log.error(
                                        f"Unexpected response from server: {error_data}"
                                    )
                                else:
                                    self.log.error(
                                        "Couldn't get response from server. Please contact administrator."
                                    )
                                self.log.error("Please contact system administrator.")
                                return None
                    except Exception as e:
                        self.log.error(f"Error: {e}")

                else:
                    self.log.error(
                        f"Unexpected error occurred during registration. Error:{register_response.text}"
                    )
            except Exception as e:
                self.log.error(f"Error parsing registration response: {e}")
                return None

    def _delete_device(self, device_id, host, token):
        host = self.format_host(host)
        delete_endpoint = f"{host}api/device/default/{device_id}"
        with self.log.loading("Removing old device"):
            delete_response = self.request_to_endpoint(
                "delete", endpoint=delete_endpoint, auth_token=token
            )

        if delete_response is None or isinstance(
            delete_response, requests.exceptions.RequestException
        ):
            self.log.error("Device removal failed!")
            return False
        if delete_response.status_code in (200, 204, 404):
            self.log.success("Device removed successfully.")
            return True

        self.log.error(
            f"Device removal failed! Status: {delete_response.status_code} "
            f"{getattr(delete_response, 'text', '')}"
        )
        return False

    def uninstall(self, token, server_name=None):
        if not server_name:
            if self.non_interactive:
                self.log.error("Server id is required in non-interactive mode.")
                return False
            server_folder = self.docker.get_server_folder()
            if not server_folder:
                return False
            server_name = server_folder.name

        metadata = self._load_server_metadata()
        folder_name = server_name
        server_meta = metadata.get(server_name, {})
        if not server_meta:
            for name, data in metadata.items():
                if str(data.get("id_device")) == str(server_name):
                    folder_name = name
                    server_meta = data
                    break

        server_folder = Path.home() / ".novavision" / "Server" / folder_name
        if not self._confirm_server_service_disabled(folder_name, server_meta):
            return False

        if server_folder.is_dir():
            self.docker.close_server_apps(server_folder)
            self.docker.stop_server_folder(server_folder)

        id_device = server_meta.get("id_device")
        host = server_meta.get("host")
        if id_device and host:
            if not self._delete_device(id_device, host, token):
                return False
        elif id_device:
            self.log.error(
                "Host is missing from server metadata; cannot delete device."
            )
            return False
        else:
            self.log.warning("No device id in metadata; skipping remote device delete.")

        if server_folder.is_dir():
            try:
                shutil.rmtree(server_folder)
                self.log.success(f"Removed local server folder {server_folder.name}.")
            except Exception as e:
                self.log.error(f"Could not remove local server folder: {e}")
                return False

        if folder_name in metadata:
            metadata.pop(folder_name, None)
            try:
                with open(self._metadata_path(), "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2)
            except Exception as e:
                self.log.warning(f"Could not update server metadata: {e}")

        return True

    def _confirm_server_service_disabled(self, folder_name, server_meta):
        service_meta = (server_meta or {}).get("service") or {}
        if not service_meta.get("enabled"):
            return True

        disable_cmd = f"novavision service disable server --id {folder_name}"
        if not self._can_prompt_for_service_disable():
            self.log.error(
                f"Server {folder_name} has a boot service enabled. "
                f"Disable it first, then uninstall again: {disable_cmd}"
            )
            return False

        answer = (
            self.log.question(
                f"Server {folder_name} has a boot service enabled. "
                "Disable it now so uninstall can continue? (y/n)"
            )
            .strip()
            .lower()
        )
        if answer != "y":
            self.log.error(
                f"Uninstall cancelled. Disable the service first: {disable_cmd}"
            )
            return False

        if not self.service._validate_service_privileges():
            return False

        if self.service.disable_server(server_name=folder_name):
            return True

        self.log.error(
            f"Could not disable the OS service for server {folder_name}. "
            f"Run '{disable_cmd}' from an administrator/root terminal, then uninstall again."
        )
        return False

    def _can_prompt_for_service_disable(self):
        if self.non_interactive:
            return False
        return not self.service._is_noninteractive()

    def _setup_server(self, register_response, host):
        host = self.format_host(host)
        try:
            if not register_response:
                self.log.error("Register response is empty or None")
                return

            user = register_response.get("user")
            if not user:
                self.log.error("User data not found in register response")
                return

            access_token = user.get("access_token")
            if not access_token:
                self.log.error("Access token not found in user data")
                return

            id_device = register_response.get("id_device")
            if not id_device:
                self.log.error("Device ID not found in register response")
                return

            id_deploy_endpoint = (
                f"{host}api/deployment?filter[id_device][eq]={id_device}&sort=id_deploy"
            )
            id_deploy_response = self.request_to_endpoint(
                "get", endpoint=id_deploy_endpoint, auth_token=access_token
            )

            if not id_deploy_response:
                self.log.error("Failed to get deployment id.")
                return

            try:
                id_deploy_data = id_deploy_response.json()
                if not id_deploy_data or len(id_deploy_data) == 0:
                    self.log.error("No deployment id found for device.")
                    return
                id_deploy = id_deploy_data[0].get("id_deploy")
                if not id_deploy:
                    self.log.error("Deployment ID not found in response")
                    return
            except Exception as e:
                self.log.error(f"Failed to parse deployment response: {e}")
                return

            # Get server package
            server_endpoint = f"{host}api/device/default/{id_device}"
            server_response = self.request_to_endpoint(
                "get", endpoint=server_endpoint, auth_token=access_token
            )

            if not server_response or server_response.status_code != 200:
                self.log.error(
                    f"Failed to get server package: {server_response.text if server_response else 'No response'}"
                )
                return

            try:
                server_data = server_response.json()
                server_package = server_data.get("server_package")
                if not server_package:
                    self.log.error("Server package not found in response")
                    return
            except Exception as e:
                self.log.error(f"Failed to parse server response: {e}")
                return

            # Download and extract server package
            agent_endpoint = f"{host}api/storage/default/get-file?id={server_package}"
            agent_response = self.request_to_endpoint(
                "get", endpoint=agent_endpoint, auth_token=access_token
            )

            if not agent_response:
                self.log.error("Failed to download server package")
                return

            # Extract and setup server
            server_folder = self._extract_and_setup_server(agent_response.content)
            if not server_folder:
                return

            # Send deployment status
            deploy_data = {"is_deploy": 1}

            # Agent Deploy Status Update
            self.send_deploy_status(
                data=deploy_data,
                access_token=access_token,
                endpoint=f"{host}api/deployment/default/{id_deploy}",
            )

            # Server Deploy Status Update
            self.send_deploy_status(
                data=deploy_data, access_token=access_token, endpoint=server_endpoint
            )

            return server_folder

        except Exception as e:
            self.log.error(f"An error occurred while setting up the server: {e}")
            return

    def _extract_and_setup_server(self, content):
        extract_path = self.agent_dir
        zip_path = extract_path / "temp.zip"

        try:
            # Zip dosyasını kaydet
            with open(zip_path, "wb") as f:
                f.write(content)

            # Zip dosyasını çıkart
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_path)

            # Server dizinini ve env dosyasını ayarla
            server_path = extract_path / "Server"
            env_file = server_path / ".env"
            key, value = "ROOT_PATH", str(server_path)

            # Env dosyasını güncelle veya oluştur
            if env_file.exists():
                with open(env_file, "r") as f:
                    lines = f.readlines()
                lines = [
                    f"{key}={value}\n" if line.startswith(f"{key}=") else line
                    for line in lines
                ]
                if not any(line.startswith(f"{key}=") for line in lines):
                    lines.append(f"{key}={value}\n")
            else:
                lines = [f"{key}={value}\n"]

            with open(env_file, "w") as f:
                f.writelines(lines)

            # Server klasörünü ve docker-compose dosyasını kontrol et
            server_folder = [item for item in server_path.iterdir() if item.is_dir()]
            if not server_folder:
                self.log.error("No server folder found!")
                return False

            agent_folder = max(server_folder, key=lambda folder: folder.stat().st_mtime)
            compose_file = agent_folder / "docker-compose.yml"
            if not compose_file.exists():
                self.log.error(f"No docker-compose.yml found in {agent_folder}!")
                return False

            # Docker compose build işlemini başlat
            with self.log.loading("Building server"):
                self.docker.run_docker_compose(compose_file, "build", "--no-cache")

            self.log.success("Server built successfully!")
            return agent_folder

        except zipfile.BadZipFile:
            self.log.error("Error: The downloaded file is not a valid zip file")
        except subprocess.CalledProcessError as e:
            self.log.error(f"Docker Compose failed with error code {e.returncode}")
            self.log.error(f"Error:\n{e.stderr}")
        except Exception as e:
            self.log.error(f"Error during server setup: {str(e)}")
        finally:
            if zip_path.exists():
                os.remove(zip_path)

        return False

    def send_deploy_status(self, data, access_token, endpoint):
        try:
            with self.log.loading("Sending deploy status"):
                deploy_response = self.request_to_endpoint(
                    "put", endpoint=endpoint, data=data, auth_token=access_token
                )
            if deploy_response:
                if deploy_response.status_code == 200:
                    self.log.success("Deployment status updated successfully!")
                else:
                    self.log.error(
                        f"Failed to update deployment status: {deploy_response.text}"
                    )
            else:
                self.log.error("Deployment status update request failed.")
                return
        except Exception as e:
            self.log.error(f"Error sending deployment status: {e}")
