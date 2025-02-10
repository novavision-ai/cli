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

def install(device_type, token):
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

    endpoint2 = f"https://alfa.suite.novavision.ai/api/device/data/register-device?access-token={token}"
    print("Sending data to Endpoint2...")
    endpoint2_response = post_to_endpoint(endpoint=endpoint2, data=data)
    if endpoint2_response["status_code"] == 200:
        print(f"Response from Endpoint2: {endpoint2_response['json']}")
        response = endpoint2_response["json"]
        response_uuid = response.get("uuid")
        access_token = response.get("access-token")

        endpoint3 = f"https://alfa.suite.novavision.ai/api/device/data/initialize-device?access-token={access_token}"

        print("Sending device info to Endpoint3...")
        device_info = get_system_info()

        endpoint3_response = post_to_endpoint(endpoint=endpoint3, data=device_info)
        if endpoint3_response["status_code"] == 200:
            print(f"Response from Endpoint3: {endpoint3_response['json']}")

            server_endpoint = f"https://alfa.suite.novavision.ai/api/device/data/get-server?access-token={access_token}"
            server_response = requests.get(server_endpoint)
            file_id = server_response.json()

            file_endpoint = f"https://alfa.suite.novavision.ai/api/storage/default/get-file?access-token={access_token}&id={file_id}"
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

                env_file = extract_path / ".env"
                with open(env_file, "a") as f:
                    f.write(f"ROOT_PATH={extract_path}")

            except zipfile.BadZipFile:
                print("Error: The downloaded file is not a valid zip file")
            except Exception as e:
                print(f"Error during extraction: {str(e)}")
            finally:
                if zip_path.exists():
                    os.remove(zip_path)


def docker_run():
    extract_path = Path.home() / ".novavision"
    folder = [item for item in extract_path.iterdir() if item.is_dir()]
    docker_compose_file = folder[0] / "docker-compose.yml"
    try:
        subprocess.run(["docker", "compose", "-f", docker_compose_file, "up", "-d"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error while starting Docker container: {e}")

def docker_stop():
    extract_path = Path.home() / ".novavision"
    folder = [item for item in extract_path.iterdir() if item.is_dir()]
    docker_compose_file = folder[0] / "docker-compose.yml"
    try:
        subprocess.run(["docker", "compose", "-f", docker_compose_file, "down"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error while stopping Docker container: {e}")

def main():
    parser = argparse.ArgumentParser(description="NovaVision CLI Tool")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    subparsers.required = True

    install_parser = subparsers.add_parser("install", help="Initialize and Download Agent")
    install_parser.add_argument("device_type", choices=["edge", "local", "cloud"],
                               help="Select and Configure Device Type")
    install_parser.add_argument("token", help="Authentication Token")

    start_parser = subparsers.add_parser("start", help="Start Docker Container")
    start_parser.add_argument("type", choices=["server", "app"])
    start_parser.add_argument("--id", help="AppID for App Choice", required=False)

    stop_parser = subparsers.add_parser("stop", help="Stop Docker Container")
    stop_parser.add_argument("type", choices=["server", "app"])
    stop_parser.add_argument("--id", help="AppID for App Choice", required=False)

    # Parse arguments
    args = parser.parse_args()

    if args.command == "install":
        install(args.device_type, args.token)
    if args.command == "start":
        if (args.type == "app" and args.app_id) or args.type == "server":
            docker_run()
        else:
            print("Invalid Arguments")

    if args.command == "stop":
        if (args.type == "app" and args.app_id) or args.type == "server":
            docker_stop()
        else:
            print("Invalid Arguments")

if __name__ == "__main__":
    main()