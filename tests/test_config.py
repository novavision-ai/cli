from pathlib import Path

from novavision.cli import _operation_logger
from novavision.config import DEFAULT_HOST, load_config, logs_dir, resolve_install_defaults
from novavision.host_metrics import log_path
from novavision.update_listener import _log_path


def test_resolve_install_defaults_uses_constant(nv_home):
    host, workspace = resolve_install_defaults(None, None)
    assert host == DEFAULT_HOST
    assert workspace is None


def test_resolve_install_defaults_reads_config(nv_home):
    config_dir = nv_home / ".novavision"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.json").write_text(
        '{"host": "https://alfa.suite.novavision.ai", "workspace": "ci"}',
        encoding="utf-8",
    )
    host, workspace = resolve_install_defaults(None, None)
    assert host == "https://alfa.suite.novavision.ai"
    assert workspace == "ci"
    host, workspace = resolve_install_defaults("https://suite.novavision.ai", "other")
    assert host == "https://suite.novavision.ai"
    assert workspace == "other"
    assert load_config()["workspace"] == "ci"


def test_existing_root_logs_move_into_logs(nv_home):
    root = nv_home / ".novavision"
    root.mkdir()
    old = root / "install-2020-01-01_00-00-00.log"
    old.write_text("old", encoding="utf-8")
    kept = root / "servers.json"
    kept.write_text("{}", encoding="utf-8")

    assert logs_dir() == root / "logs"
    assert (root / "logs" / old.name).read_text(encoding="utf-8") == "old"
    assert not old.exists()
    assert kept.read_text(encoding="utf-8") == "{}"


def test_logs_live_under_their_own_folder(nv_home):
    assert logs_dir() == nv_home / ".novavision" / "logs"
    assert logs_dir().is_dir()
    assert log_path() == logs_dir() / "host-metrics.log"
    assert _log_path() == logs_dir() / "update-listener.log"
    operation_logger = _operation_logger("install", "installation")
    try:
        log_file = Path(operation_logger.log_file_path)
        assert log_file.parent == logs_dir()
        assert log_file.name.startswith("install-")
        assert log_file.suffix == ".log"
    finally:
        handle = getattr(operation_logger, "_fh", None)
        if handle:
            handle.close()
