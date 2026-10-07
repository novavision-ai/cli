import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

PING_SECONDS = 30
RETRY_SECONDS = 5
MQTT_KEYS = ("mqtt_host", "mqtt_port", "mqtt_token", "mqtt_channel")


def cli_topic(channel):
    return str(channel).rstrip("/") + "/cli"


def read_env_file(path):
    from novavision.installer import _read_env_file

    return _read_env_file(path)


def load_listen_targets(server_name=None, home=None):
    root = Path(home) if home else Path.home()
    root = root / ".novavision"
    metadata_path = root / "servers.json"
    metadata = {}
    if metadata_path.is_file():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                metadata = loaded
        except (OSError, ValueError):
            metadata = {}

    targets = []
    for folder_name, meta in metadata.items():
        if not isinstance(meta, dict):
            continue
        same_folder = folder_name == server_name
        same_device = str(meta.get("id_device")) == str(server_name)
        if server_name and not same_folder and not same_device:
            continue
        server_folder = root / "Server" / folder_name
        env = {}
        env.update(read_env_file(server_folder.parent / ".env"))
        env.update(read_env_file(server_folder / ".env"))
        targets.append(
            {
                "folder_name": folder_name,
                "id_device": meta.get("id_device"),
                "host": meta.get("host"),
                "server_folder": server_folder,
                "mqtt_host": env.get("MQTT_SERVICE_HOST"),
                "mqtt_port": env.get("MQTT_SERVICE_PORT"),
                "mqtt_token": env.get("MQTT_TOKEN"),
                "mqtt_channel": env.get("MQTT_CHANNEL"),
                "web_api": env.get("WEB_API"),
                "device_access_token": env.get("DEVICE_ACCESS_TOKEN"),
            }
        )
    return targets


def split_listen_targets(targets):
    ready = []
    waiting = []
    for target in targets:
        if all(target.get(key) for key in MQTT_KEYS):
            ready.append(target)
        else:
            waiting.append(target)
    return ready, waiting


def group_listen_targets(targets):
    groups = {}
    for target in targets:
        key = (target["mqtt_host"], str(target["mqtt_port"]), target["mqtt_token"])
        groups.setdefault(key, []).append(target)
    return groups


def subscription_signature(targets):
    ready, _waiting = split_listen_targets(targets)
    groups = group_listen_targets(ready)
    signature = []
    for key, group in sorted(groups.items(), key=lambda item: item[0][0]):
        topics = tuple(sorted(cli_topic(target["mqtt_channel"]) for target in group))
        signature.append((key, topics))
    return tuple(signature)


def decode_message(message):
    if not isinstance(message, dict) or message.get("type") != "message":
        return None
    raw = message.get("data")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def dispatch_update_message(payload, targets, acquire_lock, release_lock, run_update, report):
    if not isinstance(payload, dict) or payload.get("mode") != "server-update":
        return "ignored"
    device_id = payload.get("id_device")
    target = next(
        (item for item in targets if str(item.get("id_device")) == str(device_id)),
        None,
    )
    if target is None:
        return "ignored"

    request_uuid = payload.get("requestUUID")
    package_id = payload.get("server_package")
    if not request_uuid or package_id in (None, ""):
        report(
            target,
            request_uuid,
            {
                "status_code": 500,
                "status": "error",
                "message": "Update message is missing requestUUID or server_package.",
            },
        )
        return "error"
    if not target.get("device_access_token") or not target.get("host"):
        report(
            target,
            request_uuid,
            {
                "status_code": 500,
                "status": "error",
                "message": "Server is missing DEVICE_ACCESS_TOKEN or Suite host.",
            },
        )
        return "error"
    if not acquire_lock():
        report(
            target,
            request_uuid,
            {
                "status_code": 409,
                "status": "error",
                "message": "An update is already running.",
            },
        )
        return "conflict"
    try:
        try:
            result = run_update(target, package_id)
        except Exception as e:
            report(
                target,
                request_uuid,
                {"status_code": 500, "status": "error", "message": str(e)},
            )
            return "error"
        report(target, request_uuid, (result or {}).get("report"))
        return "updated"
    finally:
        release_lock()


def post_update_status(target, request_uuid, report, post=None):
    if not report or not request_uuid:
        return False
    web_api = (target.get("web_api") or "").rstrip("/")
    token = target.get("device_access_token")
    if not web_api or not token:
        return False
    sender = post or requests.post
    response = sender(
        f"{web_api}/ide/request/update-status-by-uuid",
        headers={"Authorization": f"Bearer {token}"},
        data={"uuid": request_uuid, "data": json.dumps(report)},
        timeout=30,
    )
    return getattr(response, "status_code", None) in (200, 201, 204)


def _pid_path():
    path = Path.home() / ".novavision"
    path.mkdir(parents=True, exist_ok=True)
    return path / "update-listener.pid"


def _log_path():
    return Path.home() / ".novavision" / "update-listener.log"


def _read_pid():
    path = _pid_path()
    if not path.is_file():
        return None
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _process_is_listener(pid):
    try:
        import psutil

        cmdline = psutil.Process(int(pid)).cmdline()
    except Exception:
        return False
    return "_listen" in cmdline


def listener_is_running():
    pid = _read_pid()
    if not pid or pid == os.getpid():
        return False
    try:
        import psutil

        alive = psutil.pid_exists(pid)
    except Exception:
        alive = False
    return bool(alive and _process_is_listener(pid))


def _listener_command():
    invoked = Path(sys.argv[0]) if sys.argv else Path()
    if invoked.name.lower().startswith("novavision"):
        if invoked.is_absolute():
            command = [str(invoked)]
        else:
            command = [shutil.which(str(invoked)) or str(invoked)]
    else:
        command = [sys.executable, "-m", "novavision.cli"]
    command.append("_listen")
    return command


def start_update_listener(logger=None):
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    if listener_is_running() or _process_is_listener(os.getpid()):
        if logger:
            logger.info("Update listener is already running.")
        return {"pid": _read_pid() or os.getpid()}

    log_file = _log_path()
    log_fh = open(log_file, "a", encoding="utf-8")
    popen_kwargs = {
        "args": _listener_command(),
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.home() / ".novavision"),
        "close_fds": True,
    }
    if os.name == "nt":
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | create_no_window
        )
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(**popen_kwargs)
    except Exception as e:
        log_fh.close()
        if logger:
            logger.warning(f"Could not start update listener: {e}")
        return None
    log_fh.close()

    if process.poll() is not None:
        if logger:
            logger.warning("Update listener exited immediately.")
        return None

    _pid_path().write_text(str(process.pid), encoding="ascii")
    if logger:
        logger.info(f"Update listener started ({process.pid}).")
    return {"pid": process.pid}


def stop_update_listener(logger=None):
    pid = _read_pid()
    if not pid:
        return True
    if not _process_is_listener(pid):
        try:
            _pid_path().unlink()
        except OSError:
            pass
        return True
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError as e:
        if logger:
            logger.warning(f"Could not stop update listener: {e}")
        return False
    try:
        _pid_path().unlink()
    except OSError:
        pass
    if logger:
        logger.info("Update listener stopped.")
    return True


class UpdateListener:
    def __init__(self, logger, server_name=None, redis_factory=None, poll_timeout=PING_SECONDS):
        self.log = logger
        self.server_name = server_name
        self.redis_factory = redis_factory
        self.poll_timeout = poll_timeout
        self._stop = threading.Event()
        self._reported_waiting = None

    def stop(self):
        self._stop.set()

    def _load_targets(self):
        return load_listen_targets(server_name=self.server_name)

    def _log_waiting(self, waiting):
        names = tuple(sorted(target["folder_name"] for target in waiting))
        if names == self._reported_waiting:
            return
        self._reported_waiting = names
        if not names:
            return
        self.log.info(
            "Update listener is waiting for MQTT settings for "
            + ", ".join(names)
            + ". A manual update adds those keys from the new server package."
        )

    def _connect(self, host, port, password):
        if self.redis_factory:
            return self.redis_factory(host, port, password)
        import redis

        return redis.Redis(
            host=host,
            port=int(port),
            password=password or None,
            socket_connect_timeout=10,
            socket_timeout=None,
        )

    def _run_update(self, target, package_id):
        from novavision.installer import Installer

        installer = Installer(logger=self.log)
        return installer._run_server_update(
            token=target.get("device_access_token"),
            server_name=target["folder_name"],
            assume_yes=True,
            package_id=package_id,
        )

    def _report(self, target, request_uuid, report):
        if not report:
            return
        try:
            posted = post_update_status(target, request_uuid, report)
        except requests.exceptions.RequestException as e:
            self.log.error(f"Could not report update status: {e}")
            return
        if posted:
            self.log.info(f"Reported update status for {request_uuid}.")
        else:
            self.log.error(f"Suite did not accept the update status for {request_uuid}.")

    def _acquire_lock(self):
        from novavision.installer import Installer

        self._lock_installer = Installer(logger=self.log)
        return self._lock_installer._acquire_update_lock()

    def _release_lock(self):
        installer = getattr(self, "_lock_installer", None)
        if installer:
            installer._release_update_lock()

    def _handle_payload(self, payload, targets):
        return dispatch_update_message(
            payload,
            targets,
            acquire_lock=self._acquire_lock,
            release_lock=self._release_lock,
            run_update=self._run_update,
            report=self._report,
        )

    def _consume_group(self, redis_key, targets, external_stop=None):
        host, port, password = redis_key
        topics = [cli_topic(target["mqtt_channel"]) for target in targets]
        signature = subscription_signature(self._load_targets())
        client = self._connect(host, port, password)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(*topics)
        self.log.info("Update listener subscribed to " + ", ".join(topics))
        try:
            while not self._stop.is_set():
                if external_stop is not None and external_stop.is_set():
                    return
                message = pubsub.get_message(timeout=self.poll_timeout)
                if message is None:
                    pubsub.ping()
                    if subscription_signature(self._load_targets()) != signature:
                        return
                    continue
                payload = decode_message(message)
                if payload is None:
                    continue
                self._handle_payload(payload, targets)
        finally:
            for closer in (getattr(pubsub, "close", None), getattr(client, "close", None)):
                if closer:
                    try:
                        closer()
                    except Exception:
                        pass

    def serve(self):
        self._write_pid()
        try:
            while not self._stop.is_set():
                targets = self._load_targets()
                ready, waiting = split_listen_targets(targets)
                self._log_waiting(waiting)
                if not ready:
                    self._stop.wait(PING_SECONDS)
                    continue
                groups = group_listen_targets(ready)
                try:
                    if len(groups) == 1:
                        key, group_targets = next(iter(groups.items()))
                        self._consume_group(key, group_targets)
                    else:
                        self._consume_groups(groups)
                except Exception as e:
                    self.log.warning(f"Update listener disconnected: {e}. Reconnecting.")
                    self._stop.wait(RETRY_SECONDS)
        finally:
            self._clear_pid()

    def _consume_groups(self, groups):
        signature = subscription_signature(self._load_targets())
        stop_groups = threading.Event()
        errors = []

        def run(key, group_targets):
            try:
                self._consume_group(key, group_targets, external_stop=stop_groups)
            except Exception as e:
                errors.append(e)
                stop_groups.set()

        threads = [
            threading.Thread(
                target=run,
                args=(key, group_targets),
                name="novavision-update-listener",
                daemon=True,
            )
            for key, group_targets in groups.items()
        ]
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            if self._stop.is_set() or subscription_signature(self._load_targets()) != signature:
                stop_groups.set()
            if stop_groups.is_set():
                break
            self._stop.wait(1)
        stop_groups.set()
        for thread in threads:
            thread.join(timeout=self.poll_timeout + 1)
        if errors:
            raise errors[0]

    def _write_pid(self):
        _pid_path().write_text(str(os.getpid()), encoding="ascii")

    def _clear_pid(self):
        path = _pid_path()
        try:
            if _read_pid() == os.getpid():
                path.unlink()
        except OSError:
            pass


def serve_update_listener(logger, server_name=None):
    if listener_is_running():
        logger.info("Update listener is already running.")
        return 0

    listener = UpdateListener(logger, server_name=server_name)

    def _shutdown(signum, frame):
        listener.stop()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _shutdown)
    listener.serve()
    return 0
