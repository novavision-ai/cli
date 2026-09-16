import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

from novavision.host_metrics import (
    DEFAULT_PORT,
    HostMetricsCollector,
    _nvidia_smi_gpus,
    is_agent_running,
    make_handler,
    metrics_url,
    start_host_metrics,
    stop_host_metrics,
)


def _sample():
    return {
        "source": "host",
        "host": "device",
        "platform": "Linux",
        "timestamp": 1.0,
        "cpu": {"percent": 12.5, "count": 8, "freq_mhz": 3200.0, "load": None},
        "memory": {
            "total_bytes": 16,
            "used_bytes": 4,
            "available_bytes": 12,
            "percent": 25.0,
        },
        "swap": {"total_bytes": 0, "used_bytes": 0, "percent": 0.0},
        "disk": {"total_bytes": 100, "used_bytes": 40, "percent": 40.0},
        "gpu": [
            {
                "name": "GPU-A",
                "percent": 9.0,
                "memory_used_bytes": 1024,
                "memory_total_bytes": 8192,
                "backend": "nvidia-smi",
            }
        ],
    }


def test_metrics_url_uses_host_docker_internal():
    assert metrics_url(18765) == "http://host.docker.internal:18765/metrics"


def test_collector_serves_cached_snapshot():
    calls = {"count": 0}

    def collect():
        calls["count"] += 1
        sample = _sample()
        sample["timestamp"] = calls["count"]
        return sample

    collector = HostMetricsCollector(collect_fn=collect)
    first = collector.refresh()
    second = collector.snapshot()
    assert first["timestamp"] == 1
    assert second["timestamp"] == 1
    assert calls["count"] == 1


def test_http_metrics_returns_cached_json():
    collector = HostMetricsCollector(collect_fn=_sample)
    collector.refresh()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(collector))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/metrics")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 200
        assert payload["source"] == "host"
        assert payload["cpu"]["percent"] == 12.5
        assert payload["gpu"][0]["percent"] == 9.0

        conn = HTTPConnection(host, port, timeout=2)
        conn.request("GET", "/health")
        health = json.loads(conn.getresponse().read().decode("utf-8"))
        conn.close()
        assert health["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_nvidia_smi_parser():
    output = "NVIDIA RTX 3080, 12, 2048, 10240\n"
    with patch("novavision.host_metrics.shutil.which", return_value="nvidia-smi"):
        with patch(
            "novavision.host_metrics.subprocess.check_output",
            return_value=output.encode("utf-8"),
        ):
            gpus = _nvidia_smi_gpus()
    assert gpus[0]["name"] == "NVIDIA RTX 3080"
    assert gpus[0]["percent"] == 12.0
    assert gpus[0]["memory_used_bytes"] == 2048 * 1024 * 1024
    assert gpus[0]["backend"] == "nvidia-smi"


def test_macos_ioreg_parser():
    from novavision.host_metrics import _macos_ioreg_gpus

    output = """
+-o IOAccelerator  <class IOAccelerator>
  "model" = "Apple M4"
  "PerformanceStatistics" = {"Device Utilization %"=17}
"""
    with patch("novavision.host_metrics.shutil.which", return_value="ioreg"):
        with patch(
            "novavision.host_metrics.subprocess.check_output",
            return_value=output.encode("utf-8"),
        ):
            gpus = _macos_ioreg_gpus()
    assert gpus[0]["name"] == "Apple M4"
    assert gpus[0]["percent"] == 17.0
    assert gpus[0]["backend"] == "ioreg"


def test_start_host_metrics_is_idempotent(fake_logger, nv_home):
    with patch("novavision.host_metrics.is_agent_running", return_value=False):
        with patch("novavision.host_metrics.port_is_available", return_value=True):
            with patch("novavision.host_metrics.subprocess.Popen") as popen:
                popen.return_value = Mock(pid=4321, poll=Mock(return_value=None))
                first = start_host_metrics(fake_logger)
                with patch(
                    "novavision.host_metrics.is_agent_running", return_value=True
                ):
                    second = start_host_metrics(fake_logger)
    assert first["pid"] == 4321
    assert first["url"] == metrics_url(DEFAULT_PORT)
    assert first["port"] == DEFAULT_PORT
    assert second["pid"] == 4321
    assert popen.call_count == 1
    assert (nv_home / ".novavision" / "host-metrics.json").exists()


def test_start_host_metrics_stays_on_fixed_port(fake_logger, nv_home):
    with patch("novavision.host_metrics.is_agent_running", return_value=False):
        with patch("novavision.host_metrics.port_is_available", return_value=False):
            assert start_host_metrics(fake_logger) is None
    assert any("18765" in message for message in fake_logger.messages_of("warning"))


def test_stop_host_metrics_kills_agent(fake_logger, nv_home):
    state_dir = nv_home / ".novavision"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "host-metrics.json").write_text(
        json.dumps({"pid": 99, "port": DEFAULT_PORT}),
        encoding="utf-8",
    )
    (state_dir / "host-metrics.pid").write_text("99", encoding="utf-8")
    fake_proc = Mock()
    fake_proc.cmdline.return_value = ["novavision", "_metrics", "serve"]
    alive = {"value": True}

    def pid_exists(pid):
        return alive["value"]

    def mark_dead(*args, **kwargs):
        alive["value"] = False
        return Mock(returncode=0)

    with patch("novavision.host_metrics.psutil.pid_exists", side_effect=pid_exists):
        with patch("novavision.host_metrics.psutil.Process", return_value=fake_proc):
            with patch("novavision.host_metrics.os.kill", side_effect=mark_dead):
                with patch(
                    "novavision.host_metrics.subprocess.run", side_effect=mark_dead
                ):
                    assert stop_host_metrics(fake_logger) is True
    assert alive["value"] is False
    assert not (state_dir / "host-metrics.json").exists()
    assert not is_agent_running()
