import json
import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys

from pathlib import Path
from novavision.docker_manager import DockerManager
from novavision.logger import ConsoleLogger


class ServiceManager:
    def __init__(self, logger=None, docker_manager=None):
        self.log = logger or ConsoleLogger()
        self.service_home = self._resolve_service_home()
        self._apply_service_home()
        self.docker = docker_manager or DockerManager(logger=self.log)

    def enable_server(self, server_name=None, apps=None):
        server_folder = self.docker.get_server_folder(server_name)
        if not server_folder:
            return False

        if not self._validate_service_privileges():
            return False

        if not self._validate_docker_startup():
            return False

        service_apps = self._normalize_service_apps(apps)
        if service_apps and "*" not in service_apps:
            available_apps = self.docker._server_app_compose_files(server_folder)
            for app_name in service_apps:
                if app_name not in available_apps:
                    self.log.warning(
                        f"App {app_name} was not found under server {server_folder.name}."
                    )

        service_name = self._service_name(server_folder.name)
        result = self._enable_native_service(service_name, server_folder.name)
        if not result:
            return False

        self._update_service_metadata(
            server_folder.name,
            enabled=True,
            provider=result["provider"],
            service_name=service_name,
            apps=service_apps,
        )
        if service_apps == ["*"]:
            self.log.info("Service will start all apps for this server.")
        elif service_apps:
            self.log.info("Service will start apps: " + ", ".join(service_apps))
        self.log.success(f"Service enabled for server {server_folder.name}.")
        return True

    def disable_server(self, server_name=None):
        server_folder = self.docker.get_server_folder(server_name)
        if not server_folder:
            return False

        if not self._validate_service_privileges():
            return False

        service_name = self._service_name(server_folder.name)
        provider = self._provider_name()
        if not self._disable_native_service(service_name):
            return False

        current_apps = (
            self._load_metadata()
            .get(server_folder.name, {})
            .get("service", {})
            .get("apps", [])
        )
        self._update_service_metadata(
            server_folder.name,
            enabled=False,
            provider=provider,
            service_name=service_name,
            apps=current_apps,
        )
        self.log.success(f"Service disabled for server {server_folder.name}.")
        return True

    def status_server(self, server_name=None):
        server_folder = self.docker.get_server_folder(server_name)
        if not server_folder:
            return False

        service_name = self._service_name(server_folder.name)
        provider = self._provider_name()
        metadata = self._load_metadata().get(server_folder.name, {})
        service_metadata = metadata.get("service", {})
        enabled = service_metadata.get("enabled", False)

        self.log.info(f"Server: {server_folder.name}")
        self.log.info(f"Service: {'enabled' if enabled else 'disabled'}")
        self.log.info(f"Provider: {service_metadata.get('provider', provider)}")
        self.log.info(f"Name: {service_metadata.get('name', service_name)}")
        apps = service_metadata.get("apps") or []
        if apps == ["*"]:
            self.log.info("Apps: all")
        elif apps:
            self.log.info("Apps: " + ", ".join(apps))
        else:
            self.log.info("Apps: none")
        return self._status_native_service(service_name)

    def run_service_action(self, action, server_name):
        server_folder = self.docker.get_server_folder(server_name)
        if not server_folder:
            return False

        if action == "start-server":
            if not self.docker.wait_for_docker():
                return False
            if not self.docker.start_server_folder(server_folder):
                return False
            apps = (
                self._load_metadata()
                .get(server_name, {})
                .get("service", {})
                .get("apps")
                or []
            )
            self.docker.start_server_apps(server_folder, apps)
            return True
        if action == "stop-server":
            return self.docker.stop_server_folder(server_folder)

        self.log.error(f"Unknown service action: {action}")
        return False

    def _provider_name(self):
        system = platform.system()
        if system == "Linux":
            return "systemd"
        if system == "Darwin":
            return "launchd-agent"
        if system == "Windows":
            return "task-scheduler"
        return system.lower() or "unknown"

    def _metadata_path(self):
        return self.service_home / ".novavision" / "servers.json"

    def _sudo_user(self):
        sudo_user = os.environ.get("SUDO_USER")
        if sudo_user and sudo_user != "root":
            return sudo_user
        return None

    def _resolve_service_home(self):
        if platform.system() in ["Linux", "Darwin"]:
            sudo_user = self._sudo_user()
            if sudo_user:
                try:
                    import pwd

                    return Path(pwd.getpwnam(sudo_user).pw_dir)
                except Exception:
                    pass

        return Path.home()

    def _apply_service_home(self):
        if platform.system() in ["Linux", "Darwin"] and self._sudo_user():
            os.environ["HOME"] = str(self.service_home)

    def _service_uid(self):
        sudo_uid = os.environ.get("SUDO_UID")
        if sudo_uid and sudo_uid.isdigit():
            return int(sudo_uid)
        if hasattr(os, "getuid"):
            return os.getuid()
        return None

    def _service_gid(self):
        sudo_gid = os.environ.get("SUDO_GID")
        if sudo_gid and sudo_gid.isdigit():
            return int(sudo_gid)
        if hasattr(os, "getgid"):
            return os.getgid()
        return None

    def _chown_to_service_user(self, path):
        if not self._is_root() or not self._sudo_user():
            return

        uid = self._service_uid()
        gid = self._service_gid()
        if uid is None or gid is None:
            return

        try:
            os.chown(path, uid, gid)
        except (AttributeError, OSError):
            pass

    def _load_metadata(self):
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

    def _save_metadata(self, metadata):
        metadata_path = self._metadata_path()
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
            return True
        except Exception as e:
            self.log.error(f"Could not save server metadata: {e}")
            return False

    def _normalize_service_apps(self, apps):
        if not apps:
            return []

        normalized = []
        for app_name in apps:
            name = str(app_name).strip()
            if not name or name in normalized:
                continue
            if name == "*":
                return ["*"]
            normalized.append(name)
        return normalized

    def _update_service_metadata(
        self, server_name, enabled, provider, service_name, apps=None
    ):
        metadata = self._load_metadata()
        server_metadata = metadata.setdefault(server_name, {})
        server_metadata["service"] = {
            "enabled": enabled,
            "provider": provider,
            "name": service_name,
            "apps": apps or [],
        }
        self._save_metadata(metadata)

    def _service_name(self, server_name):
        return f"novavision-server-{server_name}"

    def _working_directory(self):
        return Path(__file__).resolve().parent.parent

    def _console_script_command(self):
        invoked_command = Path(sys.argv[0])
        if invoked_command.name.lower().startswith("novavision"):
            if invoked_command.is_absolute():
                return [str(invoked_command)]

            executable = shutil.which(str(invoked_command))
            return [executable or str(invoked_command)]

        return None

    def _service_command(self, action, server_name):
        command = self._console_script_command() or [
            sys.executable,
            "-m",
            "novavision.cli",
        ]
        return command + [
            "_service",
            action,
            "--server",
            server_name,
        ]

    def _command_string(self, args):
        if platform.system() == "Windows":
            return subprocess.list2cmdline(args)
        return " ".join(shlex.quote(str(arg)) for arg in args)

    def _systemd_quote(self, value):
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _server_service_log_paths(self, service_name, server_name):
        log_dir = self.service_home / ".novavision" / "Server" / server_name / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._chown_to_service_user(log_dir)
        return (
            log_dir / f"{service_name}.out.log",
            log_dir / f"{service_name}.err.log",
        )

    def _write_windows_task_script(self, action, server_name):
        service_name = self._service_name(server_name)
        server_folder = self.service_home / ".novavision" / "Server" / server_name
        script_path = server_folder / "start-service.cmd"
        working_directory = subprocess.list2cmdline([str(self._working_directory())])
        service_command = self._command_string(
            self._service_command(action, server_name)
        )
        stdout_log, stderr_log = self._server_service_log_paths(
            service_name,
            server_name,
        )
        script_content = f"""@echo off
set "NV_OUT={stdout_log}"
set "NV_ERR={stderr_log}"
echo [%date% %time%] Starting NovaVision service {service_name} >> "%NV_OUT%"
cd /D {working_directory} >> "%NV_OUT%" 2>> "%NV_ERR%"
if errorlevel 1 exit /B %ERRORLEVEL%
{service_command} >> "%NV_OUT%" 2>> "%NV_ERR%"
exit /B %ERRORLEVEL%
"""

        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_content)

        return script_path

    def _write_windows_task_launcher(self, script_path):
        launcher_path = script_path.with_suffix(".vbs")
        launcher_content = f"""Set shell = CreateObject("WScript.Shell")
comspec = shell.ExpandEnvironmentStrings("%ComSpec%")
command = Chr(34) & comspec & Chr(34) & " /D /C " & Chr(34) & "{script_path}" & Chr(34)
shell.Run command, 0, False
"""

        with open(launcher_path, "w", encoding="utf-8") as f:
            f.write(launcher_content)

        return launcher_path

    def _windows_task_command(self, action, server_name):
        script_path = self._write_windows_task_script(action, server_name)
        launcher_path = self._write_windows_task_launcher(script_path)
        wscript = shutil.which("wscript.exe") or "wscript.exe"
        return subprocess.list2cmdline([wscript, "//B", str(launcher_path)])

    def _run_command(self, args, log_error=True):
        try:
            result = subprocess.run(args, capture_output=True, text=True)
        except FileNotFoundError:
            if log_error:
                self.log.error(f"Command not found: {args[0]}")
            return False

        if result.returncode != 0:
            if log_error:
                message = result.stderr.strip() or result.stdout.strip()
                self.log.error(message or f"Command failed: {' '.join(args)}")
            return False
        return True

    def _run_status_command(self, args):
        try:
            return subprocess.run(args, capture_output=True, text=True)
        except FileNotFoundError:
            self.log.error(f"Command not found: {args[0]}")
            return None

    def _enable_native_service(self, service_name, server_name):
        system = platform.system()
        if system == "Linux":
            return self._enable_systemd_service(service_name, server_name)
        if system == "Darwin":
            return self._enable_launchd_service(service_name, server_name)
        if system == "Windows":
            return self._enable_windows_task(service_name, server_name)

        self.log.error(f"Unsupported operating system: {system}")
        return None

    def _disable_native_service(self, service_name):
        system = platform.system()
        if system == "Linux":
            return self._disable_systemd_service(service_name)
        if system == "Darwin":
            return self._disable_launchd_service(service_name)
        if system == "Windows":
            return self._disable_windows_task(service_name)

        self.log.error(f"Unsupported operating system: {system}")
        return False

    def _status_native_service(self, service_name):
        system = platform.system()
        if system == "Linux":
            return self._status_systemd_service(service_name)
        if system == "Darwin":
            return self._status_launchd_service(service_name)
        if system == "Windows":
            return self._status_windows_task(service_name)

        self.log.error(f"Unsupported operating system: {system}")
        return False

    def _is_root(self):
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def _is_windows_admin(self):
        if platform.system() != "Windows":
            return False

        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _validate_service_privileges(self):
        system = platform.system()
        if system == "Linux" and not self._is_root():
            self.log.error(
                "Managing the server service requires root. "
                "Run this command with sudo or as root."
            )
            return False
        if system == "Windows" and not self._is_windows_admin():
            self.log.error(
                "Managing the server service requires an Administrator terminal. "
                "Reopen the terminal as Administrator and run this command again."
            )
            return False
        return True

    def _is_noninteractive(self):
        if os.environ.get("CI"):
            return True
        try:
            return not sys.stdin.isatty()
        except Exception:
            return True

    def _validate_docker_startup(self):
        if not shutil.which("docker"):
            self.log.error("Docker is not installed. Please install Docker first.")
            return False

        system = platform.system()
        if system == "Linux":
            return self._validate_linux_docker_startup()
        if system in ["Darwin", "Windows"]:
            return self._confirm_docker_desktop_startup(system)

        return True

    def _validate_linux_docker_startup(self):
        result = self._run_status_command(["systemctl", "is-enabled", "docker"])
        if result and result.returncode == 0:
            return True

        self.log.error(
            "Docker is not enabled at startup. "
            "Run 'sudo systemctl enable docker' before enabling the NovaVision service."
        )
        return False

    def _confirm_docker_desktop_startup(self, system):
        os_name = "macOS" if system == "Darwin" else "Windows"
        if self._is_noninteractive():
            self.log.info(
                f"Assuming Docker Desktop starts automatically on {os_name} "
                "(non-interactive session)."
            )
            return True

        answer = (
            self.log.question(
                f"Docker Desktop must be configured to start automatically on {os_name}. "
                "Is Docker Desktop startup enabled? (y/n)"
            )
            .strip()
            .lower()
        )

        if answer == "y":
            return True

        self.log.error(
            "Enable Docker Desktop startup first, then run this command again."
        )
        return False

    def _systemd_scope(self):
        return {
            "provider": "systemd",
            "unit_dir": Path("/etc/systemd/system"),
            "control": ["systemctl"],
            "wanted_by": "multi-user.target",
            "unit_name": None,
        }

    def _enable_systemd_service(self, service_name, server_name):
        if not self._is_root():
            self.log.error(
                "Linux boot startup requires root. "
                "Run this command with sudo or as root."
            )
            return None

        scope = self._systemd_scope()
        unit_name = f"{service_name}.service"
        unit_path = scope["unit_dir"] / unit_name
        start_command = self._command_string(
            self._service_command("start-server", server_name)
        )
        stop_command = self._command_string(
            self._service_command("stop-server", server_name)
        )
        working_directory = shlex.quote(str(self._working_directory()))
        home_environment = self._systemd_quote(f"HOME={self.service_home}")

        unit_content = f"""[Unit]
Description=NovaVision Server {server_name}
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={working_directory}
Environment={home_environment}
ExecStart={start_command}
ExecStop={stop_command}
TimeoutStartSec=0

[Install]
WantedBy={scope["wanted_by"]}
"""

        try:
            scope["unit_dir"].mkdir(parents=True, exist_ok=True)
            with open(unit_path, "w", encoding="utf-8") as f:
                f.write(unit_content)
        except PermissionError:
            self.log.error(
                f"Permission denied while writing {unit_path}. Run as administrator/root."
            )
            return None

        if not self._run_command(scope["control"] + ["daemon-reload"]):
            return None
        if not self._run_command(scope["control"] + ["enable", unit_name]):
            return None

        self.log.info(f"Installed systemd unit: {unit_path}")
        return {"provider": scope["provider"], "path": str(unit_path)}

    def _disable_systemd_service(self, service_name):
        scope = self._systemd_scope()
        unit_name = f"{service_name}.service"
        unit_path = scope["unit_dir"] / unit_name

        self._run_command(scope["control"] + ["disable", unit_name])
        if unit_path.exists():
            try:
                unit_path.unlink()
            except PermissionError:
                self.log.error(f"Permission denied while removing {unit_path}.")
                return False

        return self._run_command(scope["control"] + ["daemon-reload"])

    def _status_systemd_service(self, service_name):
        scope = self._systemd_scope()
        unit_name = f"{service_name}.service"
        enabled = self._run_status_command(scope["control"] + ["is-enabled", unit_name])
        active = self._run_status_command(scope["control"] + ["is-active", unit_name])

        enabled_text = (
            enabled.stdout.strip() if enabled and enabled.stdout else "unknown"
        )
        active_text = active.stdout.strip() if active and active.stdout else "unknown"
        self.log.info(f"Native enabled: {enabled_text}")
        self.log.info(f"Native active: {active_text}")
        return True

    def _launchd_scope(self):
        uid = self._service_uid()
        return {
            "provider": "launchd-agent",
            "plist_dir": self.service_home / "Library" / "LaunchAgents",
            "domain": f"gui/{uid}" if uid is not None else "gui",
        }

    def _enable_launchd_service(self, service_name, server_name):
        scope = self._launchd_scope()
        label = service_name.replace("-", ".")
        plist_path = scope["plist_dir"] / f"{label}.plist"
        stdout_log, stderr_log = self._server_service_log_paths(
            service_name,
            server_name,
        )

        plist_data = {
            "Label": label,
            "ProgramArguments": self._service_command("start-server", server_name),
            "WorkingDirectory": str(self._working_directory()),
            "RunAtLoad": True,
            "StandardOutPath": str(stdout_log),
            "StandardErrorPath": str(stderr_log),
        }

        try:
            scope["plist_dir"].mkdir(parents=True, exist_ok=True)
            self._chown_to_service_user(scope["plist_dir"])
            with open(plist_path, "wb") as f:
                plistlib.dump(plist_data, f)
            self._chown_to_service_user(plist_path)
        except PermissionError:
            self.log.error(f"Permission denied while writing {plist_path}.")
            return None

        self._run_command(
            ["launchctl", "bootout", scope["domain"], str(plist_path)],
            log_error=False,
        )
        if not self._run_command(
            ["launchctl", "bootstrap", scope["domain"], str(plist_path)]
        ):
            return None
        self._run_command(["launchctl", "enable", f"{scope['domain']}/{label}"])

        self.log.info(f"Installed launchd plist: {plist_path}")
        return {"provider": scope["provider"], "path": str(plist_path)}

    def _disable_launchd_service(self, service_name):
        scope = self._launchd_scope()
        label = service_name.replace("-", ".")
        plist_path = scope["plist_dir"] / f"{label}.plist"

        self._run_command(
            ["launchctl", "bootout", scope["domain"], str(plist_path)],
            log_error=False,
        )
        if plist_path.exists():
            try:
                plist_path.unlink()
            except PermissionError:
                self.log.error(f"Permission denied while removing {plist_path}.")
                return False
        return True

    def _status_launchd_service(self, service_name):
        scope = self._launchd_scope()
        label = service_name.replace("-", ".")
        result = self._run_status_command(
            ["launchctl", "print", f"{scope['domain']}/{label}"]
        )
        if result and result.returncode == 0:
            self.log.info("Native status: loaded")
        else:
            self.log.info("Native status: not loaded")
        return True

    def _enable_windows_task(self, service_name, server_name):
        if not self._is_windows_admin():
            self.log.error(
                "Windows boot startup requires an Administrator terminal. "
                "Reopen the terminal as Administrator and run this command again."
            )
            return None

        task_command = self._windows_task_command("start-server", server_name)
        if not self._run_command(
            [
                "schtasks",
                "/Create",
                "/TN",
                service_name,
                "/TR",
                task_command,
                "/SC",
                "ONLOGON",
                "/F",
            ]
        ):
            return None

        return {"provider": "task-scheduler", "path": service_name}

    def _disable_windows_task(self, service_name):
        return self._run_command(["schtasks", "/Delete", "/TN", service_name, "/F"])

    def _status_windows_task(self, service_name):
        result = self._run_status_command(["schtasks", "/Query", "/TN", service_name])
        if result and result.returncode == 0:
            self.log.info("Native status: registered")
        else:
            self.log.info("Native status: not registered")
        return True
