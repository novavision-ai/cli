import os
import json
import uuid
import zipfile
import argparse
import requests
import subprocess
from pathlib import Path
from novavision.utils import get_system_info

def post_to_endpoint(endpoint, data):
    response = requests.post(endpoint, data=data)
    if response.status_code == 200:
        return {"status_code": response.status_code, "json": response.json()}
    else:
        return {"status_code": response.status_code, "json": None}

def create_default_directory():
    default_dir = Path.home() / ".novavision"
    default_dir.mkdir(parents=True, exist_ok=True)
    return default_dir


def get_running_containers():
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}} {{.Ports}}"],
            capture_output=True, text=True, check=True
        )

        containers = result.stdout.strip().split("\n")
        if not containers or containers == [""]:
            print("No running containers found.")
            return

        print("\nRunning Containers and Ports:")
        for container in containers:
            parts = container.split(" ", 1)
            name = parts[0]

            if len(parts) > 1:
                ports = parts[1]
                for port_mapping in ports.split(", "):
                    if "->" in port_mapping:
                        port = port_mapping.split("->")[0].strip()
                        port = port.split(":")[-1]

            else:
                port = "No ports"

            print(f"{name} -> Port: {port}")

    except subprocess.CalledProcessError as e:
        print(f"Error retrieving running containers: {e}")

def install(device_type, token, host):
    if device_type == "cloud":
        response = requests.get("https://api.ipify.org?format=text")
        wan_host = response.text

        print(f"Detected WAN HOST: {wan_host}")
        user_wan_ip = input("Would you like to use detected WAN HOST? (y/n): ").strip().lower()

        if user_wan_ip == "y":
            print("Using detected WAN HOST...")
        elif user_wan_ip == "n":
            wan_host = input("Enter WAN HOST: ").strip()
        else:
            print("Invalid input. Using detected WAN HOST...")

        user_port = input("Default Port is 7001. Would you like to use it? (y/n):  ").strip().lower()

        if user_port == "y":
            port = "7001"
        elif user_port == "n":
            port = input("Enter Port: ")
        else:
            print("Invalid input.")


        data = {
        "name": f"{uuid.uuid4()}",
        "os_api_host": "0.0.0.0",
        "os_api_port": f"{port}",
        "os_api_ssl": "DISABLE",
        "wan_host": f"{wan_host}",
        "type": "cloud"
    }

    elif device_type == "local":
        data = {
        "name": f"{uuid.uuid4()}",
        "os_api_host": "0.0.0.0",
        "os_api_port": "7001",
        "os_api_ssl": "DISABLE",
        "type": "local"
        }

    else:
        print("Wrong Device Type Selected!")

    endpoint2 = f"{host}api/device/data/register-device?access-token={token}"
    print("Sending data to Endpoint2...")
    endpoint2_response = post_to_endpoint(endpoint=endpoint2, data=data)
    if endpoint2_response["status_code"] == 200:
        print(f"Response from Endpoint2: {endpoint2_response['json']}")
        response = endpoint2_response["json"]
        response_uuid = response.get("uuid")
        access_token = response.get("access-token")

        endpoint3 = f"{host}api/device/data/initialize-device?access-token={access_token}"

        print("Sending device info to Endpoint3...")
        device_info = get_system_info()

        endpoint3_response = post_to_endpoint(endpoint=endpoint3, data=device_info)
        if endpoint3_response["status_code"] == 200:
            print(f"Response from Endpoint3: {endpoint3_response['json']}")

            server_endpoint = f"{host}api/device/data/get-server?access-token={access_token}"
            server_response = requests.get(server_endpoint)
            file_id = server_response.json()

            file_endpoint = f"{host}api/storage/default/get-file?access-token={access_token}&id={file_id}"
            file_response = requests.get(file_endpoint)

            extract_path = create_default_directory()

            extract_path.mkdir(parents=True, exist_ok=True)

            zip_path = extract_path / "temp.zip"
            try:
                with open(zip_path, "wb") as f:
                    f.write(file_response.content)

                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)

                print(f"Files extracted successfully to: {extract_path}")

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

            except zipfile.BadZipFile:
                print("Error: The downloaded file is not a valid zip file")
            except Exception as e:
                print(f"Error during extraction: {str(e)}")
            finally:
                if zip_path.exists():
                    os.remove(zip_path)

def deploy(type, id, to):
    # App deployu ve agent'ın içerisine konulması
    pass

def docker_run(type):
    extract_path = Path.home() / ".novavision"
    server_path = extract_path / "Server"
    server_folder = [item for item in server_path.iterdir() if item.is_dir()]
    if type == "server":
        docker_compose_file = server_folder[0] / "docker-compose.yml"
    else:
        app_folder = [item for item in server_folder[0].iterdir() if item.is_dir()]
        docker_compose_file = app_folder[0] / "docker-compose.yml"
    try:
        subprocess.run(["docker", "compose", "-f", str(docker_compose_file), "up", "-d"], check=True)
        get_running_containers()
    except subprocess.CalledProcessError as e:
        print(f"Error while starting Docker container: {e}")


def docker_stop(type):
    extract_path = Path.home() / ".novavision"
    server_path = extract_path / "Server"
    folder = [item for item in server_path.iterdir() if item.is_dir()]
    docker_compose_file = folder[0] / "docker-compose.yml"
    try:
        subprocess.run(["docker", "compose", "-f", str(docker_compose_file), "down"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error while stopping Docker container: {e}")

def main():
    parser = argparse.ArgumentParser(description="NovaVision CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.required = True

    install_parser = subparsers.add_parser("install", help="Initialize and Download Agent")
    install_parser.add_argument("device_type", choices=["edge", "local", "cloud"],
                               help="Select and Configure Device Type")
    install_parser.add_argument("token", help="User Authentication Token")
    install_parser.add_argument("--host", default="https://alfa.suite.novavision.ai/", help="Host Url")

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

    # Parse arguments
    args = parser.parse_args()

    if args.command == "install":
        install(args.device_type, args.token, args.host)
    elif args.command == "start":
        if (args.type == "app" and args.id) or args.type == "server":
            docker_run(args.type)
        else:
            print("Invalid Arguments")
    elif args.command == "deploy":
        if args.type and args.id:
            deploy(args.type, args.id, args.token)
    elif args.command == "stop":
        if (args.type == "app" and args.id) or args.type == "server":
            docker_stop(args.type)
        else:
            print("Invalid Arguments")
    else:
        print("Invalid Command")

if __name__ == "__main__":
    main()