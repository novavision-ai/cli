import hashlib
import json
import os
import shutil
import tempfile
import time
import zipfile
import requests
import subprocess

from datetime import datetime
from pathlib import Path
from novavision.credentials import canonical_host, hosts_match
from novavision.host_metrics import start_host_metrics
from novavision.logger import ConsoleLogger
from novavision.utils import get_system_info
from novavision.docker_manager import DockerManager
from novavision.service_manager import ServiceManager


def _pid_alive(pid):
    try:
        import psutil

        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def _read_env_file(path):
    values = {}
    path = Path(path)
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value
    return values


def _merge_env_text(existing, incoming):
    """Add keys from incoming that are missing in existing. Keep existing values."""
    seen = set()
    for raw in (existing or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            seen.add(line.split("=", 1)[0].strip())

    additions = []
    for raw in (incoming or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key in seen:
            continue
        seen.add(key)
        additions.append(line)
    if not additions:
        return existing or ""

    text = existing or ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(additions) + "\n"
    return text


class Installer:
    DEVICE_TYPE_CLOUD = 1
    DEVICE_TYPE_EDGE = 2
    DEVICE_TYPE_LOCAL = 3
    REBUILD_TIMEOUT_SECONDS = 120
    STATUS_TIMEOUT_SECONDS = 60
    STATUS_INTERVAL_SECONDS = 2

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
                choice = self.log.ask_index(
                    "Please select a GPU to continue", len(device_info["gpu"])
                )
                device_info["gpu"] = device_info["gpu"][choice]
            else:
                device_info["gpu"] = (
                    device_info["gpu"][0] if device_info["gpu"] else "No GPU Detected"
                )

    def format_host(self, host):
        return canonical_host(host) + "/"

    def request_to_endpoint(
        self, method, endpoint, data=None, auth_token=None, timeout=None
    ):
        # Genel API istek fonksiyonu
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        request_kwargs = {"headers": headers}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        response = None
        try:
            if method == "get":
                response = requests.get(endpoint, **request_kwargs)
            elif method == "post":
                response = requests.post(endpoint, data=data, **request_kwargs)
            elif method == "put":
                response = requests.put(endpoint, data=data, **request_kwargs)
            elif method == "delete":
                response = requests.delete(endpoint, **request_kwargs)
            else:
                self.log.error(f"Invalid HTTP method: {method}")
                return None
            return response
        except requests.exceptions.RequestException as e:
            return e

    def _response_error_text(self, response, fallback="Request failed"):
        if response is None:
            return fallback
        if isinstance(response, Exception):
            return str(response)

        status = getattr(response, "status_code", None)
        message = self._response_message(response)
        if message and status is not None:
            return f"{message} (HTTP {status})"
        if message:
            return message
        if status is not None:
            return f"{fallback} (HTTP {status})"
        return fallback

    def _response_message(self, response):
        try:
            data = response.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        message = data.get("message") or data.get("error") or data.get("detail")
        if isinstance(message, (dict, list)):
            message = str(message)
        elif message is not None:
            message = str(message).strip()
        return message or None

    def install(
        self, device_type, token, host, workspace, port=None, non_interactive=False
    ):
        self.non_interactive = bool(non_interactive)
        host = self.format_host(host)
        os.chdir(os.path.expanduser("~"))
        total_steps = 8

        self.log.step(1, total_steps, "Checking Docker")
        if not self.docker._check_docker_available():
            return False
        self.log.step(2, total_steps, "Cleaning previous installations")
        self.docker._cleanup_previous_docker_installations()

        self.log.step(3, total_steps, "Detecting hardware")
        device_info = get_system_info()
        if "error" in device_info:
            self.log.error(f"Error getting system info: {device_info['error']}")
            return False

        self._select_gpu(device_info)

        self.log.step(4, total_steps, "Selecting workspace")
        workspace_selection = self._get_workspace_id(host, token, workspace)
        if not workspace_selection:
            return False

        workspace_user_id, workspace_name = workspace_selection
        self.log.step(5, total_steps, "Setting workspace")
        if not self._set_workspace(host, token, workspace_user_id):
            return False

        self.log.step(6, total_steps, "Selecting port")
        selected_port = self._select_port(port)
        if not selected_port:
            return False

        device_data = self._prepare_device_data(device_type, device_info, selected_port)
        if not device_data:
            return False

        self.log.step(7, total_steps, "Registering device")
        register_response = self._register_device(device_data, token, host, device_info)
        if register_response is None:
            return False

        pending_key = str(register_response.get("id_device") or "pending")
        self._save_server_metadata(
            Path(pending_key), register_response, host, workspace_name
        )

        self.log.step(8, total_steps, "Downloading and building server")
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
        if getattr(self, "_installed_package_sha256", None):
            self._store_package_hash(server_folder.name, self._installed_package_sha256)
        from novavision.update_listener import start_update_listener

        start_update_listener(self.log)
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
                    "Workspace list request failed. "
                    f"{self._response_error_text(workspace_list_response)}"
                )
                return None
        except Exception as e:
            self.log.error(f"Error occurred while getting workspace list: {e}")
            return None

        try:
            workspace_list = self._unique_workspaces(workspace_list_response.json())
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
                self.log.info(f"{idx + 1}. {workspace_name}")

            choice = self.log.ask_index(
                "Please select a workspace to continue", len(workspace_list)
            )
            selected_workspace = workspace_list[choice]
            workspace_name = selected_workspace.get("workspace", {}).get(
                "name", "Unknown"
            )
            return selected_workspace["id_workspace_user"], workspace_name
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

    def _unique_workspaces(self, workspace_list):
        if not isinstance(workspace_list, list):
            return workspace_list

        unique = []
        seen = set()
        for item in workspace_list:
            if not isinstance(item, dict):
                unique.append(item)
                continue
            workspace_id = item.get("id_workspace")
            if workspace_id is None:
                nested = item.get("workspace")
                if isinstance(nested, dict):
                    workspace_id = nested.get("id_workspace", nested.get("id"))
            if workspace_id is None:
                unique.append(item)
                continue
            key = str(workspace_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    def _set_workspace(self, host, token, workspace_user_id):
        if workspace_user_id is None:
            self.log.error("Workspace user_id not found.")
            return False

        set_workspace_endpoint = f"{host}api/workspace/default/set-workspace"
        workspace_data = {"id": workspace_user_id}

        set_workspace_response = self.request_to_endpoint(
            method="post",
            endpoint=set_workspace_endpoint,
            data=workspace_data,
            auth_token=token,
        )

        try:
            if set_workspace_response.status_code in (200, 201):
                self.log.success("Workspace set successfully!")
                return True
            else:
                self.log.error(
                    f"Workspace set failed. {self._response_error_text(set_workspace_response)}"
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

        if self.log.confirm("Use default port 7001?", default=True):
            return "7001"

        while True:
            entered = self.log.question("Please enter desired port").strip()
            if entered.isdigit():
                port_int = int(entered)
                if 1 <= port_int <= 65535:
                    return str(port_int)
                self.log.warning("Port must be between 1 and 65535.")
            else:
                self.log.warning("Port must be a number.")

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
                elif not self.log.confirm("Use detected WAN HOST?", default=True):
                    wan_host = self.log.question("Enter WAN HOST").strip()

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
                    f"Invalid response format received while fetching devices: "
                    f"{self._response_error_text(device_response)}"
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

                                choice = self.log.ask_index(
                                    "Please select a device to remove",
                                    len(device_response),
                                )
                                device_id_to_delete = device_response[choice][
                                    "id_device"
                                ]
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
                        "Unexpected error occurred during registration. "
                        f"{self._response_error_text(register_response)}"
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
            f"Device removal failed. {self._response_error_text(delete_response)}"
        )
        return False

    def _login_host_mismatch_message(self, server_host, login_host):
        if not server_host:
            return (
                "Server metadata has no host, so it cannot be matched to the saved login."
            )
        return (
            f"This server is registered on {canonical_host(server_host)}, "
            f"but the saved login is for {canonical_host(login_host)}."
        )

    def _saved_login_matches_host(self, server_host, login_host):
        if login_host is None:
            return True
        if server_host and hosts_match(server_host, login_host):
            return True
        self.log.error(self._login_host_mismatch_message(server_host, login_host))
        return False

    def uninstall(self, token, server_name=None, assume_yes=False, login_host=None):
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
        if not self._saved_login_matches_host(server_meta.get("host"), login_host):
            return False
        if not self._confirm_uninstall(folder_name, server_meta, assume_yes):
            return False
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

        if not metadata:
            from novavision.update_listener import stop_update_listener

            stop_update_listener(self.log)
        return True

    def update(self, token, server_name=None, assume_yes=False, login_host=None):
        if not self._acquire_update_lock():
            self.log.error("An update is already running.")
            return False
        try:
            return self._run_server_update(
                token,
                server_name=server_name,
                assume_yes=assume_yes,
                login_host=login_host,
            )["ok"]
        finally:
            self._release_update_lock()

    def _run_server_update(
        self,
        token,
        server_name=None,
        assume_yes=False,
        package_id=None,
        login_host=None,
    ):
        """Download a server package and rebuild that server in place.

        A manual update asks Suite to build a new package. A listener update
        already has the package id Suite published.
        """
        if not self.docker._check_docker_available():
            return self._finish_update(
                False,
                self._error_report("Docker is not available."),
            )

        server_folder, folder_name, server_meta = self._resolve_server_target(
            server_name
        )
        if not server_folder:
            return self._finish_update(
                False, self._error_report("Server was not found.")
            )
        if not server_folder.is_dir():
            self.log.error(f"Server folder not found: {folder_name}")
            return self._finish_update(
                False, self._error_report(f"Server folder not found: {folder_name}")
            )

        compose_file = server_folder / "docker-compose.yml"
        if not compose_file.exists():
            self.log.error(f"No docker-compose.yml found in {server_folder}!")
            return self._finish_update(
                False,
                self._error_report(f"No docker-compose.yml found in {server_folder}."),
            )

        id_device = (server_meta or {}).get("id_device")
        host = (server_meta or {}).get("host")
        if not id_device or not host:
            message = (
                "Server metadata is missing host or device id. "
                "Reinstall the server to refresh it."
            )
            self.log.error(message)
            return self._finish_update(False, self._error_report(message))

        if not self._saved_login_matches_host(host, login_host):
            mismatch = self._login_host_mismatch_message(host, login_host)
            return self._finish_update(False, self._error_report(mismatch))

        if not self._confirm_update(folder_name, server_meta, assume_yes):
            return {"ok": False, "report": None}

        self.log.step(1, 3, "Downloading server package")
        if package_id is None:
            package = self._download_server_package(host, token, id_device)
        else:
            package = self._download_package_file(host, token, package_id)
            self._downloaded_package_id = package_id
        if not package:
            return self._finish_update(
                False,
                self._error_report("Failed to download the server package."),
            )

        resolved_package_id = getattr(self, "_downloaded_package_id", None) or package_id
        package_sha256 = self._package_sha256(package)
        saved_sha256 = (server_meta or {}).get("package_sha256")
        running_now = self.docker._server_is_running(server_folder)
        if saved_sha256 and saved_sha256 == package_sha256:
            self.log.warning(
                "Suite returned the same server package as the one already installed. "
                "Server contents will not change with this update."
            )
            return self._finish_update(
                True,
                {
                    "status_code": 200,
                    "status": "success",
                    "changed": False,
                    "server_package": resolved_package_id,
                    "hash": package_sha256,
                    "running": running_now,
                    "message": "Server package unchanged",
                },
            )
        if not saved_sha256:
            self.log.info(
                "No package hash is stored for this server. "
                "Updating it and saving a hash for the next update."
            )

        if running_now:
            self.log.info(f"Stopping server {folder_name} before update.")
            try:
                self.docker.run_docker_compose(compose_file, "stop")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                message = f"Could not stop server before update: {e}"
                self.log.error(message)
                return self._finish_update(False, self._error_report(message))

        self.log.step(2, 3, "Applying package")
        if not self._apply_package_to_server(package, server_folder):
            self._restart_server_after_update(compose_file, running_now)
            return self._finish_update(
                False, self._error_report("Failed to apply the server package.")
            )

        self.log.step(3, 3, "Rebuilding server")
        try:
            with self.log.loading("Building server"):
                self.docker.run_docker_compose(compose_file, "build", "--no-cache")
        except subprocess.CalledProcessError as e:
            message = self._compose_error_message(e)
            self.log.error(message)
            self._restart_server_after_update(compose_file, running_now)
            return self._finish_update(False, self._error_report(message))
        except FileNotFoundError as e:
            self.log.error(str(e))
            self._restart_server_after_update(compose_file, running_now)
            return self._finish_update(False, self._error_report(str(e)))

        if running_now:
            if not self._restart_server_after_update(compose_file, True):
                return self._finish_update(
                    False,
                    self._error_report("Server was updated but could not be restarted."),
                )
            if not self._wait_for_server_status(server_folder):
                detail = getattr(self, "_last_status_error", "") or "no response"
                message = f"Server updated but /status did not return 200. {detail}"
                self.log.error(message)
                self._store_package_hash(folder_name, package_sha256)
                from novavision.update_listener import start_update_listener

                start_update_listener(self.log)
                return self._finish_update(
                    False,
                    self._error_report(
                        message,
                        server_package=resolved_package_id,
                        package_hash=package_sha256,
                    ),
                )
            self.log.success(f"Server {folder_name} updated and restarted.")
            running = True
        else:
            self.log.success(
                f"Server {folder_name} updated. Start it to use the new build."
            )
            running = False
        self._store_package_hash(folder_name, package_sha256)
        return self._finish_update(
            True,
            {
                "status_code": 200,
                "status": "success",
                "changed": True,
                "server_package": resolved_package_id,
                "hash": package_sha256,
                "running": running,
                "message": "Server updated",
            },
        )

    def _package_sha256(self, content):
        return hashlib.sha256(content).hexdigest()

    def _store_package_hash(self, folder_name, package_sha256):
        if not folder_name or not package_sha256:
            return

        metadata = self._load_server_metadata()
        server_metadata = metadata.get(folder_name)
        if not isinstance(server_metadata, dict):
            server_metadata = {}
        server_metadata["package_sha256"] = package_sha256
        metadata[folder_name] = server_metadata
        try:
            with open(self._metadata_path(), "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
        except Exception as e:
            self.log.warning(f"Could not save package hash: {e}")

    def _finish_update(self, ok, report):
        if ok:
            from novavision.update_listener import start_update_listener

            start_update_listener(self.log)
        return {"ok": ok, "report": report}

    def _error_report(self, message, server_package=None, package_hash=None):
        report = {"status_code": 500, "status": "error", "message": message}
        if server_package is not None:
            report["server_package"] = server_package
        if package_hash:
            report["hash"] = package_hash
        return report

    def _compose_error_message(self, error):
        message = f"Docker Compose failed with error code {error.returncode}"
        detail = getattr(error, "stderr", None) or getattr(error, "output", None) or ""
        tail = "\n".join(str(detail).splitlines()[-20:])
        if tail:
            return f"{message}\n{tail}"
        return message

    def _update_lock_path(self):
        return self.agent_dir / "update.lock"

    def _acquire_update_lock(self):
        path = self._update_lock_path()
        if path.exists():
            owner = self._update_lock_owner(path)
            if owner == os.getpid():
                return True
            if owner and _pid_alive(owner):
                return False
            try:
                path.unlink()
            except OSError:
                return False
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._update_lock_fd = fd
        return True

    def _release_update_lock(self):
        fd = getattr(self, "_update_lock_fd", None)
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            self._update_lock_fd = None
        path = self._update_lock_path()
        try:
            if self._update_lock_owner(path) == os.getpid():
                path.unlink()
        except OSError:
            pass

    def _update_lock_owner(self, path):
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def _confirm_update(self, folder_name, server_meta, assume_yes):
        if self.non_interactive or assume_yes:
            return True

        workspace = (server_meta or {}).get("workspace", "Unknown")
        host = (server_meta or {}).get("host", "Unknown")
        summary = (
            f"Update server {folder_name} (workspace: {workspace}, host: {host}). "
            "This asks Suite to build a new server package and rebuilds the server. "
            "Local apps stay. Existing .env values stay, and new keys are added."
        )
        if self.log.confirm(summary, default=False):
            return True
        self.log.error("Update cancelled.")
        return False

    def _resolve_server_target(self, server_name):
        metadata = self._load_server_metadata()
        if not server_name:
            if self.non_interactive:
                self.log.error("Server id is required in non-interactive mode.")
                return None, None, None
            server_folder = self.docker.get_server_folder()
            if not server_folder:
                return None, None, None
            server_name = server_folder.name

        folder_name = server_name
        server_meta = metadata.get(server_name, {})
        if not server_meta:
            for name, data in metadata.items():
                if str(data.get("id_device")) == str(server_name):
                    folder_name = name
                    server_meta = data
                    break
        server_folder = Path.home() / ".novavision" / "Server" / folder_name
        return server_folder, folder_name, server_meta

    def _device_type_value(self, device):
        value = (device or {}).get("device_type")
        if value in (
            self.DEVICE_TYPE_CLOUD,
            self.DEVICE_TYPE_EDGE,
            self.DEVICE_TYPE_LOCAL,
        ):
            return value
        names = {
            "cloud": self.DEVICE_TYPE_CLOUD,
            "edge": self.DEVICE_TYPE_EDGE,
            "local": self.DEVICE_TYPE_LOCAL,
        }
        return names.get(str(value).lower(), self.DEVICE_TYPE_LOCAL)

    def _device_refresh_payload(self, device, device_info):
        """Hardware fields install posts when a device is created.

        The device write does not build a server package. Update sends the
        current machine details, then asks Suite to rebuild the package.
        """
        port = (device or {}).get("os_api_port") or "7001"
        payload = {
            "name": (device or {}).get("name") or device_info["device_name"],
            "serial": device_info["serial"],
            "processor": device_info["processor"],
            "cpu": device_info["cpu"],
            "gpu": device_info["gpu"],
            "os": device_info["os"],
            "disk": device_info["disk"],
            "memory": device_info["memory"],
            "architecture": device_info["architecture"],
            "platform": device_info["platform"],
            "os_api_port": str(port),
            "device_type": self._device_type_value(device),
        }
        if payload["device_type"] == self.DEVICE_TYPE_CLOUD and (device or {}).get(
            "wan_host"
        ):
            payload["wan_host"] = device["wan_host"]
        return payload

    def _update_device_hardware(self, host, token, id_device, device):
        device_info = get_system_info()
        if "error" in device_info:
            self.log.error(f"Error getting system info: {device_info['error']}")
            return None
        self._select_gpu(device_info)
        payload = self._device_refresh_payload(device, device_info)
        endpoint = f"{host}api/device/default/{id_device}?expand=user"
        with self.log.loading("Updating device details"):
            response = self.request_to_endpoint(
                "put", endpoint=endpoint, data=payload, auth_token=token
            )
        if not response or getattr(response, "status_code", None) not in (200, 201):
            self.log.error(
                "Failed to update device details. "
                f"{self._response_error_text(response, fallback='No response')}"
            )
            return None
        try:
            refreshed = response.json()
        except Exception as e:
            self.log.error(f"Failed to parse device response: {e}")
            return None
        if not isinstance(refreshed, dict):
            self.log.error("Device response was not an object.")
            return None
        return refreshed

    def _rebuild_server_package(self, host, token, id_device):
        endpoint = (
            f"{host}api/device/default/rebuild-server?id={id_device}&expand=user"
        )
        with self.log.loading("Building server package"):
            response = self.request_to_endpoint(
                "post",
                endpoint=endpoint,
                auth_token=token,
                timeout=self.REBUILD_TIMEOUT_SECONDS,
            )
        if isinstance(response, requests.exceptions.Timeout):
            self.log.error(
                "Server package build timed out before the new package was saved."
            )
            return None
        status = getattr(response, "status_code", None)
        if status == 200:
            try:
                rebuilt = response.json()
            except Exception as e:
                self.log.error(f"Failed to parse rebuilt device: {e}")
                return None
            if not isinstance(rebuilt, dict):
                self.log.error("Rebuilt device response was not an object.")
                return None
            if not rebuilt.get("server_package"):
                self.log.error("Rebuild finished without a server package id.")
                return None
            self.log.success("Server package rebuilt.")
            return rebuilt

        code = self._response_code(response)
        detail = self._response_error_text(response, fallback="No response")
        if status == 504 or code == "timeout":
            self.log.error(
                "Server package build timed out before the new package was saved. "
                f"{detail}"
            )
        elif status == 502 or code in ("build_failed", "no_agent"):
            self.log.error(f"Server package build failed. {detail}")
        else:
            self.log.error(f"Failed to rebuild the server package. {detail}")
        return None

    def _response_code(self, response):
        try:
            data = response.json()
        except Exception:
            return None
        if isinstance(data, dict):
            return data.get("code")
        return None

    def _download_server_package(self, host, token, id_device):
        host = self.format_host(host)
        device_endpoint = f"{host}api/device/default/{id_device}?expand=user"
        device_response = self.request_to_endpoint(
            "get", endpoint=device_endpoint, auth_token=token
        )
        if not device_response or getattr(device_response, "status_code", None) != 200:
            self.log.error(
                "Failed to get device. "
                f"{self._response_error_text(device_response, fallback='No response')}"
            )
            return None

        try:
            server_data = device_response.json()
        except Exception as e:
            self.log.error(f"Failed to parse server response: {e}")
            return None
        if not isinstance(server_data, dict):
            self.log.error("Device response was not an object.")
            return None

        refreshed = self._update_device_hardware(host, token, id_device, server_data)
        if not refreshed:
            return None
        access_token = (refreshed.get("user") or {}).get("access_token") or (
            (server_data.get("user") or {}).get("access_token")
        ) or token
        rebuilt = self._rebuild_server_package(host, access_token, id_device)
        if not rebuilt:
            return None
        server_package = rebuilt.get("server_package")
        access_token = (rebuilt.get("user") or {}).get("access_token") or access_token
        self._downloaded_package_id = server_package
        return self._download_package_file(host, access_token, server_package)

    def _download_package_file(self, host, token, server_package):
        host = self.format_host(host)
        agent_endpoint = f"{host}api/storage/default/get-file?id={server_package}"
        agent_response = self.request_to_endpoint(
            "get", endpoint=agent_endpoint, auth_token=token
        )
        if not agent_response or getattr(agent_response, "status_code", None) != 200:
            self.log.error(
                "Failed to download server package. "
                f"{self._response_error_text(agent_response, fallback='No response')}"
            )
            return None
        content = getattr(agent_response, "content", None)
        if not content:
            self.log.error("Failed to download server package")
            return None
        return content

    def _apply_package_to_server(self, content, server_folder):
        try:
            with tempfile.TemporaryDirectory(prefix="novavision-update-") as temp_dir:
                zip_path = Path(temp_dir) / "server.zip"
                zip_path.write_bytes(content)
                extract_root = Path(temp_dir) / "extract"
                extract_root.mkdir()
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    zip_ref.extractall(extract_root)

                package_root = self._locate_package_server(
                    extract_root, server_folder.name
                )
                if not package_root:
                    self.log.error("No server folder found in the downloaded package.")
                    return False

                self._copy_package_tree(package_root, server_folder)
                parent_env = package_root.parent / ".env"
                if parent_env.is_file():
                    self._merge_env_file(parent_env, server_folder.parent / ".env")
        except zipfile.BadZipFile:
            self.log.error("Error: The downloaded file is not a valid zip file")
            return False
        except Exception as e:
            self.log.error(f"Error applying server package: {e}")
            return False

        self._ensure_root_path_env(server_folder.parent)
        self.log.success("Server package applied.")
        return True

    def _locate_package_server(self, extract_root, preferred_name):
        matches = [
            compose.parent
            for compose in extract_root.rglob("docker-compose.yml")
            if compose.is_file()
        ]
        if not matches:
            return None
        named = [path for path in matches if path.name == preferred_name]
        if named:
            return min(named, key=lambda path: len(path.parts))
        return min(matches, key=lambda path: len(path.parts))

    def _copy_package_tree(self, source, destination):
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            target = destination / relative
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if item.name == ".env" and target.exists():
                self._merge_env_file(item, target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)

    def _merge_env_file(self, source, destination):
        incoming = Path(source).read_text(encoding="utf-8")
        existing = ""
        if destination.exists():
            existing = destination.read_text(encoding="utf-8")
        merged = _merge_env_text(existing, incoming)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if merged != existing:
            destination.write_text(merged, encoding="utf-8")
            self.log.info(f"Merged new settings into {destination.name}")
        else:
            self.log.info(f"Keeping existing {destination.name}")

    def _wait_for_server_status(self, server_folder):
        url = self._server_status_url(server_folder)
        deadline = time.time() + self.STATUS_TIMEOUT_SECONDS
        last_error = "no response"
        while time.time() < deadline:
            try:
                response = requests.get(url, timeout=5)
                if getattr(response, "status_code", None) == 200:
                    self._last_status_error = ""
                    return True
                last_error = f"HTTP {response.status_code}"
            except requests.exceptions.RequestException as e:
                last_error = str(e)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(self.STATUS_INTERVAL_SECONDS, remaining))
        self._last_status_error = last_error
        return False

    def _server_status_url(self, server_folder):
        env = _read_env_file(server_folder / ".env")
        port = env.get("DIGINOVA_WSL_SERVICE_PORT") or "7001"
        scheme = "https" if env.get("SERVER_SSL") == "1" else "http"
        return f"{scheme}://127.0.0.1:{port}/status"

    def _ensure_root_path_env(self, server_path):
        env_file = Path(server_path) / ".env"
        key, value = "ROOT_PATH", str(server_path)
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
            lines = [
                f"{key}={value}\n" if line.startswith(f"{key}=") else line
                for line in lines
            ]
            if not any(line.startswith(f"{key}=") for line in lines):
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] = lines[-1] + "\n"
                lines.append(f"{key}={value}\n")
        else:
            lines = [f"{key}={value}\n"]
        env_file.write_text("".join(lines), encoding="utf-8")

    def _restart_server_after_update(self, compose_file, was_running):
        if not was_running:
            return True
        try:
            start_host_metrics(self.log)
            self.docker.run_docker_compose(compose_file, "up", "-d", "--force-recreate")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.log.error(f"Server was updated but could not be restarted: {e}")
            return False

    def _confirm_uninstall(self, folder_name, server_meta, assume_yes):
        if self.non_interactive or assume_yes:
            return True

        workspace = (server_meta or {}).get("workspace", "Unknown")
        host = (server_meta or {}).get("host", "Unknown")
        summary = (
            f"Uninstall server {folder_name} (workspace: {workspace}, host: {host}). "
            "This deletes the Suite device and the local folder."
        )
        if self.log.confirm(summary, default=False):
            return True
        self.log.error("Uninstall cancelled.")
        return False

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

        if not self.log.confirm(
            f"Server {folder_name} has a boot service enabled. "
            "Disable it now so uninstall can continue?",
            default=True,
        ):
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
                    "Failed to get server package. "
                    f"{self._response_error_text(server_response, fallback='No response')}"
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
            self._installed_package_sha256 = None
            agent_endpoint = f"{host}api/storage/default/get-file?id={server_package}"
            agent_response = self.request_to_endpoint(
                "get", endpoint=agent_endpoint, auth_token=access_token
            )

            if not agent_response or not getattr(agent_response, "content", None):
                self.log.error("Failed to download server package")
                return

            self._installed_package_sha256 = self._package_sha256(
                agent_response.content
            )

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
                        "Failed to update deployment status. "
                        f"{self._response_error_text(deploy_response)}"
                    )
            else:
                self.log.error("Deployment status update request failed.")
                return
        except Exception as e:
            self.log.error(f"Error sending deployment status: {e}")
