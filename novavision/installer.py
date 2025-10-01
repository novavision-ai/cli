import os
import zipfile
import requests
import subprocess

from pathlib import Path
from novavision.logger import ConsoleLogger
from novavision.utils import get_system_info
from novavision.docker_manager import DockerManager

class Installer:
    DEVICE_TYPE_CLOUD = 1
    DEVICE_TYPE_EDGE = 2
    DEVICE_TYPE_LOCAL = 3

    def __init__(self, logger: ConsoleLogger | None = None):
        self.log = logger if logger else ConsoleLogger()
        self.docker = DockerManager(logger=self.log)
        self.agent_dir = self._create_agent()

    def _create_agent(self):
        agent_dir = Path.home() / ".novavision"
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def _select_gpu(self, device_info):
        if isinstance(device_info['gpu'], list):
            if len(device_info['gpu']) > 1:
                self.log.info("Multiple GPUs detected. Please select one GPU.")
                for idx, gpu in enumerate(device_info['gpu']):
                    self.log.info(f"{idx + 1}. {gpu}")
                while True:
                    try:
                        choice = int(self.log.question("Please select a GPU to continue"))
                        if 1 <= choice <= len(device_info['gpu']):
                            device_info['gpu'] = device_info['gpu'][choice - 1]
                            break
                        else:
                            self.log.warning("Invalid selection. Please select a number from the list.")
                    except ValueError:
                        self.log.warning("Invalid entry. Please enter a number.")
            else:
                device_info['gpu'] = device_info['gpu'][0] if device_info['gpu'] else "No GPU Detected"

    def format_host(self, host):
        host = host.strip()
        if not host.startswith("https://"):
            if host.startswith("http://"):
                host = host[len("http://"):]
            host = "https://" + host
        if not host.endswith("/"):
            host = host + "/"
        return host

    def request_to_endpoint(self, method, endpoint, data=None, auth_token=None):
        headers = {'Authorization': f'Bearer {auth_token}'} if auth_token else {}
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
                self.log.error(f"Invalid HTTP method: {method}")
                return None

            return response
        except Exception as e:
            return e

    def install(self, device_type, token, host, workspace):
        host = self.format_host(host)
        os.chdir(os.path.expanduser("~"))
        device_info = get_system_info()

        if not device_info:
            self.log.error("Failed to retrieve system information.")
            return

        self._select_gpu(device_info)

        # Workspace seçimi
        workspace_user_id = self._get_workspace_id(host, token, workspace)

        self._set_workspace(host, token, workspace_user_id)

        # Port seçimi
        port = self._select_port()
        if not port:
            return

        # Device data hazırlama
        device_data = self._prepare_device_data(device_type, device_info, port)

        if not device_data:
            return

        # Device kaydı
        register_response = self._register_device(device_data, token, host, device_info)
        if not register_response:
            return

        # Server kurulumu
        self._setup_server(register_response, host)

    def _get_workspace_id(self, host, token, workspace):
        host = self.format_host(host)
        get_workspace_endpoint = f"{host}api/workspace/user?expand=workspace"
        workspace_list_response = self.request_to_endpoint(
            "get",
            endpoint=get_workspace_endpoint,
            auth_token=token
        )

        if not workspace_list_response:
            self.log.error("Failed to get workspace list from server")
            return None

        try:
            if workspace_list_response.status_code != 200:
                self.log.error(f"Workspace list request failed. Error: {workspace_list_response.json()['message']}")
                return None
        except Exception as e:
            self.log.error(f"Error occurred while getting workspace list: {e}")
            return None

        try:
            workspace_list = workspace_list_response.json()
        except Exception as e:
            self.log.error(f"Failed to parse workspace response: {e}")
            return None

        if not workspace:
            if not workspace_list:
                self.log.error("There is no workspace available.")
                return None

            if len(workspace_list) == 1:
                self.log.info("There is only one workspace available. Continuing registration.")
                workspace_user_id = workspace_list[0].get("id_workspace_user")
                if not workspace_user_id:
                    self.log.error("Workspace user ID not found in response")
                    return None
                return workspace_user_id

            self.log.info("There are multiple workspaces available for user. Current workspaces available:")
            for idx, workspaces in enumerate(workspace_list):
                workspace_info = workspaces.get('workspace', {})
                workspace_name = workspace_info.get('name', 'Unknown')
                workspace_user_id = workspaces.get('id_workspace_user', 'Unknown')
                self.log.info(f"{idx + 1}. {workspace_name} (Workspace ID: {workspace_user_id})")

            while True:
                try:
                    choice = int(self.log.question("Please select a workspace to continue"))
                    if 1 <= choice <= len(workspace_list):
                        workspace_user_id = workspace_list[choice - 1]['id_workspace_user']
                        break
                    else:
                        self.log.warning("Invalid selection. Please select a number from the list.")
                except ValueError:
                    self.log.warning("Invalid entry. Please enter a number.")
        else:
            workspace_to_select = [workspaces for workspaces in workspace_list if workspaces["workspace"]["name"] == workspace]
            if not workspace_to_select:
                self.log.error(f"Workspace '{workspace}' not found.")
                return None

            workspace_user_id = workspace_to_select[0].get("id_workspace_user")
            if not workspace_user_id:
                self.log.error(f"Workspace '{workspace}' does not have a valid user ID")
                return None

        return workspace_user_id


    def _set_workspace(self, host, token, workspace_user_id):
        if workspace_user_id is None:
            self.log.error("Workspace user_id not found.")
            return None

        set_workspace_endpoint = f"{host}api/workspace/user/{workspace_user_id}"
        workspace_data = {"status": 1}

        set_workspace_response = self.request_to_endpoint(
            method="put",
            endpoint=set_workspace_endpoint,
            data=workspace_data,
            auth_token=token
        )

        if set_workspace_response.status_code == 200:
            self.log.success("Workspace set successfully!")
        else:
            self.log.error(f"Workspace set failed! Error: {set_workspace_response.text}")
            return


    def _select_port(self):
        while True:
            user_port = self.log.question("Default port is 7001. Would you like to use it? (y/n)").strip().lower()
            if user_port == "y":
                return "7001"
            elif user_port == "n":
                return self.log.question("Please enter desired port")
            else:
                self.log.error("Invalid input.")

    def _prepare_device_data(self, device_type, device_info, port):
        base_data = {
            "name": device_info['device_name'],
            "serial": device_info['serial'],
            "processor": device_info['processor'],
            "cpu": device_info['cpu'],
            "gpu": device_info['gpu'],
            "os": device_info['os'],
            "disk": device_info['disk'],
            "memory": device_info['memory'],
            "architecture": device_info['architecture'],
            "platform": device_info['platform'],
            "os_api_port": port
        }

        if device_type == "cloud":
            try:
                response = requests.get("https://api.ipify.org?format=text")
                wan_host = response.text
                self.log.info(f"Detected WAN HOST: {wan_host}")
                user_wan_ip = self.log.question("Would you like to use detected WAN HOST? (y/n)").strip().lower()
                
                if user_wan_ip == "n":
                    wan_host = self.log.question("Enter WAN HOST").strip()
                elif user_wan_ip != "y":
                    self.log.warning("Invalid input. Using detected WAN HOST...")
                
                base_data.update({
                    "device_type": self.DEVICE_TYPE_CLOUD,
                    "wan_host": wan_host
                })
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
                "get",
                endpoint=device_endpoint,
                auth_token=token
            )
            if not device_response:
                self.log.error("Failed to fetch device list.")
                return None

            try:
                device_response = device_response.json()
            except ValueError:
                self.log.error(f"Invalid response format received while fetching devices: {device_response.text}")
                return None

            device_serial = device_info['serial']
            matching_devices = [d for d in device_response if d.get("serial") == device_serial]

            if matching_devices:
                device = matching_devices[0]
                self.log.warning(f"Device named {device['name']} has same serial number as this machine.")
                self.log.warning("In order to continue device must be deleted.")

                while True:
                    remove = self.log.question(f"Would you like to delete {device['name']}? (y/n)").lower()
                    if remove == "y":
                        if not self._delete_device(device['id_device'], host, token):
                            return None
                        break
                    elif remove == "n":
                        self.log.warning("Aborting.")
                        return None
                    else:
                        self.log.warning("Invalid input. Try again.")
            else:
                self.log.info("No matching serial found for device. Continuing.")

            with self.log.loading("Registering device"):
                register_response = self.request_to_endpoint("post", endpoint=register_endpoint, data=data, auth_token=token)

            if not register_response:
                self.log.error("Failed to register device")
                return None

            try:
                register_json = register_response.json()
                if register_response.status_code in [200, 201]:
                    self.log.success("Device registered successfully!")
                    return register_json
                else:
                    self.log.error(f"Device registration failed: {register_json}")
                    return None
            except Exception as e:
                self.log.error(f"Error parsing registration response: {e}")
                return None

    def _delete_device(self, device_id, host, token):
        host = self.format_host(host)
        delete_endpoint = f"{host}api/device/default/{device_id}"
        with self.log.loading("Removing old device"):
            delete_response = self.request_to_endpoint(
                "delete",
                endpoint=delete_endpoint,
                auth_token=token
            )

        if delete_response and delete_response.status_code == 204:
            self.log.success("Old device removed successfully.")
            return True
        else:
            self.log.error("Device removal failed!")
            return False

    def _setup_server(self, register_response, host):
        host = self.format_host(host)
        try:
            # Safe dictionary access with validation
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

            id_deploy_endpoint = f"{host}api/deployment?filter[id_device][eq]={id_device}&sort=id_deploy"
            id_deploy_response = self.request_to_endpoint(
                "get",
                endpoint=id_deploy_endpoint,
                auth_token=access_token
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
                "get",
                endpoint=server_endpoint,
                auth_token=access_token
            )
            
            if not server_response or server_response.status_code != 200:
                self.log.error(f"Failed to get server package: {server_response.text if server_response else 'No response'}")
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
                "get",
                endpoint=agent_endpoint,
                auth_token=access_token
            )
            
            if not agent_response:
                self.log.error("Failed to download server package")
                return

            # Extract and setup server
            self._extract_and_setup_server(agent_response.content)

            if not self._extract_and_setup_server(agent_response.content):
                return

            # Send deployment status
            deploy_data = {"is_deploy": 1}

            # Agent Deploy Status Update
            self.send_deploy_status(
                data=deploy_data,
                access_token=access_token,
                endpoint=f"{host}api/deployment/default/{id_deploy}")

            # Server Deploy Status Update
            self.send_deploy_status(
                data=deploy_data,
                access_token=access_token,
                endpoint=server_endpoint
            )


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
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)

            # Server dizinini ve env dosyasını ayarla
            server_path = extract_path / "Server"
            env_file = server_path / ".env"
            key, value = "ROOT_PATH", str(server_path)

            # Env dosyasını güncelle veya oluştur
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
                self.docker.run_docker_compose(
                    compose_file,
                    "build",
                    "--no-cache")

            self.log.success("Server built successfully!")
            return True

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
                    "put",
                    endpoint=endpoint,
                    data=data,
                    auth_token=access_token
                )
            if deploy_response:
                if deploy_response.status_code == 200:
                    self.log.success("Deployment status updated successfully!")
                else:
                    self.log.error(f"Failed to update deployment status: {deploy_response.text}")
            else:
                self.log.error("Deployment status update request failed.")
                return
        except Exception as e:
            self.log.error(f"Error sending deployment status: {e}")
