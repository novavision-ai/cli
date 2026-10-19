import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

DEFAULT_PORT = 18765
DEFAULT_BIND = "0.0.0.0"
DEFAULT_INTERVAL = 2.0
HOST_METRICS_HOSTNAME = "host.docker.internal"
STATE_NAME = "host-metrics.json"
PID_NAME = "host-metrics.pid"
LOG_NAME = "host-metrics.log"


def metrics_url(port=DEFAULT_PORT):
    return "http://{0}:{1}/metrics".format(HOST_METRICS_HOSTNAME, int(port))


def state_dir():
    path = Path.home() / ".novavision"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path():
    return state_dir() / STATE_NAME


def pid_path():
    return state_dir() / PID_NAME


def log_path():
    return state_dir() / LOG_NAME


def load_state():
    path = state_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    path = state_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def clear_state():
    for path in (state_path(), pid_path()):
        try:
            path.unlink()
        except OSError:
            pass


def _read_pid():
    state = load_state()
    pid = state.get("pid")
    if pid:
        try:
            return int(pid)
        except (TypeError, ValueError):
            pass
    path = pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_is_metrics_agent(pid):
    try:
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
    except (psutil.Error, OSError):
        return False
    return "_metrics" in cmdline or "host_metrics" in cmdline


def is_agent_running():
    pid = _read_pid()
    if not pid:
        return False
    if not psutil.pid_exists(pid):
        return False
    return _pid_is_metrics_agent(pid)


PUBLISHER_CONTAINER = "novavision-host-metrics"
PUBLISHER_IMAGE = "alpine:3.20"


def metrics_www_dir():
    path = state_dir() / "metrics-www"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_metrics_file(payload):
    directory = metrics_www_dir()
    target = directory / "metrics.json"
    data = json.dumps(payload, separators=(",", ":"))
    temporary = directory / "metrics.json.tmp"
    temporary.write_text(data, encoding="utf-8")
    try:
        os.replace(str(temporary), str(target))
    except OSError:
        target.write_text(data, encoding="utf-8")
        try:
            temporary.unlink()
        except OSError:
            pass
    return target


PUBLISHER_COMMAND = [
    "nc",
    "-lk",
    "-p",
    "18765",
    "-e",
    "/bin/sh",
    "/data/serve.sh",
]


def _publisher_script():
    # Each connection is its own process. read -t stops a client that sends
    # nothing from occupying that process. The body is copied once so the
    # Content-Length matches the bytes that are sent.
    return """#!/bin/sh
i=0
while [ "$i" -lt 40 ]; do
  IFS= read -r -t 2 line || exit 0
  line=$(printf '%s' "$line" | tr -d '\\r')
  [ -z "$line" ] && break
  i=$((i + 1))
done
body=/data/metrics.json
if [ ! -s "$body" ]; then
  body=/data/empty.json
fi
tmp=$(mktemp /tmp/metrics.XXXXXX) || exit 0
cp "$body" "$tmp" || exit 0
size=$(wc -c < "$tmp" | tr -d ' ')
{
  printf 'HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\nContent-Length: %s\\r\\nCache-Control: no-store\\r\\nConnection: close\\r\\n\\r\\n' "$size"
  cat "$tmp"
}
rm -f "$tmp"
"""


def _publisher_is_running():
    if not shutil.which("docker"):
        return False
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", PUBLISHER_CONTAINER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0 and (result.stdout or "").strip().lower() == "true"


def _remove_metrics_publisher():
    if not shutil.which("docker"):
        return
    subprocess.run(
        ["docker", "rm", "-f", PUBLISHER_CONTAINER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _publisher_inspect():
    if not shutil.which("docker"):
        return None
    result = subprocess.run(
        ["docker", "inspect", PUBLISHER_CONTAINER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    return payload if isinstance(payload, dict) else None


def _publisher_responds(port, timeout=2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", int(port)))
        sock.sendall(b"GET /metrics HTTP/1.0\r\nHost: localhost\r\n\r\n")
        data = b""
        while len(data) < 256 and b"\r\n" not in data:
            chunk = sock.recv(256)
            if not chunk:
                break
            data += chunk
    except OSError:
        return False
    finally:
        sock.close()
    status = data.split(b"\r\n", 1)[0]
    return status.startswith(b"HTTP/1.") and b" 200 " in status


def publisher_is_responding(port=DEFAULT_PORT):
    return _publisher_responds(port)


def _wait_for_publisher(port):
    for _ in range(15):
        if _publisher_responds(port, timeout=0.5):
            return True
        time.sleep(0.2)
    return False


def _publisher_is_current(port):
    info = _publisher_inspect()
    if not info or not (info.get("State") or {}).get("Running"):
        return False
    host_config = info.get("HostConfig") or {}
    policy = (host_config.get("RestartPolicy") or {}).get("Name")
    if policy != "unless-stopped":
        return False
    if list((info.get("Config") or {}).get("Cmd") or []) != PUBLISHER_COMMAND:
        return False
    bindings = (host_config.get("PortBindings") or {}).get("18765/tcp") or []
    host_ports = [
        str(item.get("HostPort"))
        for item in bindings
        if isinstance(item, dict)
    ]
    if str(int(port)) not in host_ports:
        return False
    return _publisher_responds(port)


def ensure_metrics_publisher(port, logger=None):
    """Serve metrics through Docker's own port proxy.

    The websocket container calls host.docker.internal:18765. A host process
    listening there is a new inbound program, which Windows and macOS can block.
    Docker's published port uses the Docker process the user already allowed.
    """
    if _publisher_is_current(port):
        return True
    if not shutil.which("docker"):
        if logger:
            logger.warning("Docker is not available to publish host metrics.")
        return False

    directory = metrics_www_dir()
    script = directory / "serve.sh"
    script.write_bytes(_publisher_script().replace("\r\n", "\n").encode("utf-8"))
    (directory / "empty.json").write_text("{}\n", encoding="utf-8")
    if not (directory / "metrics.json").exists():
        (directory / "metrics.json").write_text("{}\n", encoding="utf-8")

    _remove_metrics_publisher()
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            PUBLISHER_CONTAINER,
            "--restart",
            "unless-stopped",
            "-p",
            "{0}:18765".format(int(port)),
            "-v",
            "{0}:/data:ro".format(str(directory.resolve())),
            PUBLISHER_IMAGE,
            *PUBLISHER_COMMAND,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if logger:
            logger.warning(
                "Could not publish host metrics on port {0}.{1}".format(
                    port, " {0}".format(detail) if detail else ""
                )
            )
        return False
    if not _wait_for_publisher(port):
        _remove_metrics_publisher()
        if logger:
            logger.warning(
                "Host metrics publisher did not respond on port {0}.".format(port)
            )
        return False
    if logger:
        logger.info("Host metrics are published through Docker on port {0}.".format(port))
    return True


def port_is_available(port, bind=DEFAULT_BIND):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((bind, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def choose_port(preferred=DEFAULT_PORT, bind=DEFAULT_BIND):
    selected = int(preferred)
    if is_agent_running():
        port = load_state().get("port", selected)
        try:
            return int(port)
        except (TypeError, ValueError):
            return selected

    if port_is_available(selected, bind=bind):
        return selected
    return None


def _metrics_command(port, bind, interval):
    invoked = Path(sys.argv[0]) if sys.argv else Path()
    if invoked.name.lower().startswith("novavision"):
        if invoked.is_absolute():
            command = [str(invoked)]
        else:
            command = [shutil.which(str(invoked)) or str(invoked)]
    else:
        command = [sys.executable, "-m", "novavision.cli"]
    command.extend(
        [
            "_metrics",
            "serve",
            "--port",
            str(int(port)),
            "--bind",
            str(bind),
            "--interval",
            str(interval),
        ]
    )
    return command


def _nvidia_smi_gpus():
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []
    try:
        output = subprocess.check_output(
            [
                nvidia_smi,
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []

    gpus = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            continue
        gpus.append(
            {
                "name": parts[0],
                "percent": _to_float(parts[1]),
                "memory_used_bytes": _mib_to_bytes(parts[2]),
                "memory_total_bytes": _mib_to_bytes(parts[3]),
                "backend": "nvidia-smi",
            }
        )
    return gpus


def _macos_gpus():
    gpus = _macos_ioreg_gpus()
    if gpus:
        return gpus
    try:
        import Metal

        device = Metal.MTLCreateSystemDefaultDevice()
        if device:
            return [
                {
                    "name": str(device.name()),
                    "percent": None,
                    "memory_used_bytes": None,
                    "memory_total_bytes": None,
                    "backend": "metal",
                }
            ]
    except Exception:
        pass
    return []


def _macos_ioreg_gpus():
    ioreg = shutil.which("ioreg")
    if not ioreg:
        return []
    try:
        output = subprocess.check_output(
            [ioreg, "-r", "-d", "1", "-c", "IOAccelerator", "-w", "0"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return []

    gpus = []
    blocks = re.split(r"\+-o\s+", output)
    for block in blocks[1:] or [output]:
        name = _first_match(r'"model"\s*=\s*"([^"]+)"', block)
        if not name:
            name = _first_match(r'"name"\s*=\s*"([^"]+)"', block)
        percent = _first_match(r'"?Device Utilization %"\s*=\s*(\d+(?:\.\d+)?)', block)
        if name is None and percent is None:
            continue
        gpus.append(
            {
                "name": name or "Apple GPU",
                "percent": _to_float(percent),
                "memory_used_bytes": None,
                "memory_total_bytes": None,
                "backend": "ioreg",
            }
        )
    return gpus


_windows_gpu_name_cache = None


def _windows_gpu_names():
    global _windows_gpu_name_cache
    if _windows_gpu_name_cache is not None:
        return _windows_gpu_name_cache
    powershell = shutil.which("powershell") or shutil.which("powershell.exe")
    if not powershell:
        _windows_gpu_name_cache = []
        return _windows_gpu_name_cache
    try:
        output = subprocess.check_output(
            [
                powershell,
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
            ],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        _windows_gpu_name_cache = []
        return _windows_gpu_name_cache
    _windows_gpu_name_cache = [
        line.strip() for line in output.splitlines() if line.strip()
    ]
    return _windows_gpu_name_cache


def collect_gpus():
    gpus = _nvidia_smi_gpus()
    if gpus:
        return gpus

    system = platform.system()
    if system == "Darwin":
        gpus = _macos_gpus()
        if gpus:
            return gpus
    elif system == "Windows":
        names = _windows_gpu_names()
        if names:
            return [
                {
                    "name": name,
                    "percent": None,
                    "memory_used_bytes": None,
                    "memory_total_bytes": None,
                    "backend": "wmi",
                }
                for name in names
            ]

    return [
        {
            "name": None,
            "percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "backend": "none",
        }
    ]


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mib_to_bytes(value):
    number = _to_float(value)
    if number is None:
        return None
    return int(number * 1024 * 1024)


def _first_match(pattern, text):
    match = re.search(pattern, text)
    if not match:
        return None
    return match.group(1)


def collect_host_metrics():
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage("/")
    try:
        freq = psutil.cpu_freq()
        freq_mhz = freq.current if freq else None
    except Exception:
        freq_mhz = None

    try:
        load1, load5, load15 = os.getloadavg()
        load = {"1": load1, "5": load5, "15": load15}
    except (AttributeError, OSError):
        load = None

    return {
        "source": "host",
        "host": socket.gethostname(),
        "platform": platform.system(),
        "timestamp": time.time(),
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "count": psutil.cpu_count() or 0,
            "freq_mhz": freq_mhz,
            "load": load,
        },
        "memory": {
            "total_bytes": vm.total,
            "used_bytes": vm.used,
            "available_bytes": vm.available,
            "percent": vm.percent,
        },
        "swap": {
            "total_bytes": swap.total,
            "used_bytes": swap.used,
            "percent": swap.percent,
        },
        "disk": {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "percent": disk.percent,
        },
        "gpu": collect_gpus(),
    }


class HostMetricsCollector:
    def __init__(self, collect_fn=collect_host_metrics):
        self._collect_fn = collect_fn
        self._lock = threading.Lock()
        self._snapshot = {
            "source": "host",
            "host": socket.gethostname(),
            "platform": platform.system(),
            "timestamp": 0,
            "cpu": {},
            "memory": {},
            "swap": {},
            "disk": {},
            "gpu": [],
        }
        self._stop = threading.Event()
        self._thread = None

    def snapshot(self):
        with self._lock:
            return dict(self._snapshot)

    def refresh(self):
        sample = self._collect_fn()
        with self._lock:
            self._snapshot = sample
        return sample

    def start(self, interval=DEFAULT_INTERVAL):
        if self._thread and self._thread.is_alive():
            return
        psutil.cpu_percent(interval=None)
        self.refresh()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(float(interval),),
            name="novavision-host-metrics",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout=2):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self, interval):
        while not self._stop.wait(interval):
            try:
                self.refresh()
            except Exception:
                continue


def _json_bytes(payload, status=200):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return status, body


def make_handler(collector):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return

        def _send(self, status, body, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/metrics", "/"):
                status, body = _json_bytes(collector.snapshot())
                self._send(status, body)
                return
            if path == "/health":
                snapshot = collector.snapshot()
                status, body = _json_bytes(
                    {
                        "ok": True,
                        "port": int(self.server.server_address[1]),
                        "timestamp": snapshot.get("timestamp"),
                    }
                )
                self._send(status, body)
                return
            self._send(404, b'{"error":"not found"}')

    return Handler


def serve_host_metrics(port=DEFAULT_PORT, bind=DEFAULT_BIND, interval=DEFAULT_INTERVAL):
    collector = HostMetricsCollector()
    collector.start(interval=interval)
    stop = threading.Event()

    def _shutdown(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _shutdown)

    try:
        write_metrics_file(collector.snapshot())
        while not stop.wait(float(interval)):
            write_metrics_file(collector.snapshot())
    finally:
        collector.stop()
    return 0


def start_host_metrics(
    logger=None, port=None, bind=DEFAULT_BIND, interval=DEFAULT_INTERVAL
):
    if is_agent_running():
        state = load_state()
        try:
            running_port = int(state.get("port", DEFAULT_PORT))
        except (TypeError, ValueError):
            running_port = DEFAULT_PORT
        if not ensure_metrics_publisher(running_port, logger):
            return None
        if logger:
            logger.info(
                "Host metrics already running on port {0}.".format(running_port)
            )
        return state

    selected_port = choose_port(preferred=port or DEFAULT_PORT, bind=bind)
    if selected_port is None and _publisher_is_running():
        selected_port = int(port or DEFAULT_PORT)
    if selected_port is None:
        if logger:
            logger.warning(
                "Host metrics port {0} is in use. "
                "The WSL container expects {1}.".format(
                    port or DEFAULT_PORT, metrics_url(DEFAULT_PORT)
                )
            )
        return None

    if not ensure_metrics_publisher(selected_port, logger):
        return None

    log_file = log_path()
    command = _metrics_command(selected_port, bind, interval)
    log_fh = open(log_file, "a", encoding="utf-8")
    popen_kwargs = {
        "args": command,
        "stdin": subprocess.DEVNULL,
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
        "cwd": str(state_dir()),
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
            logger.warning("Could not start host metrics agent: {0}".format(e))
        return None
    log_fh.close()

    if process.poll() is not None:
        if logger:
            logger.warning("Host metrics agent exited immediately.")
        _remove_metrics_publisher()
        clear_state()
        return None

    state = {
        "pid": process.pid,
        "port": selected_port,
        "bind": bind,
        "url": metrics_url(selected_port),
    }
    save_state(state)
    pid_path().write_text(str(process.pid), encoding="utf-8")
    if logger:
        logger.info("Host metrics published at {0}".format(state["url"]))
    return state


def stop_host_metrics(logger=None):
    _remove_metrics_publisher()
    pid = _read_pid()
    if not pid:
        clear_state()
        return True

    if psutil.pid_exists(pid) and _pid_is_metrics_agent(pid):
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
                logger.warning("Could not stop host metrics agent: {0}".format(e))
            return False

    deadline = time.time() + 3
    while time.time() < deadline and psutil.pid_exists(pid):
        time.sleep(0.05)

    if psutil.pid_exists(pid) and _pid_is_metrics_agent(pid):
        try:
            os.kill(
                pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM
            )
        except OSError:
            pass

    clear_state()
    if logger:
        logger.info("Host metrics agent stopped.")
    return True
