import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
from packaging.version import InvalidVersion, Version

from novavision import __version__

PACKAGE_NAME = "novavision-cli"
PYPI_URL = "https://pypi.org/pypi/novavision-cli/json"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
CHECK_TIMEOUT_SECONDS = 1.0
SELF_UPDATE_TIMEOUT_SECONDS = 5.0
CHECK_JOIN_SECONDS = 1.0


def cache_path():
    return Path.home() / ".novavision" / "cli-update.json"


def stdout_is_tty():
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def version_is_newer(latest, current=__version__):
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def _valid_version(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        Version(value)
    except InvalidVersion:
        return None
    return value


def read_cache():
    try:
        payload = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    latest = _valid_version(payload.get("latest"))
    checked_at = payload.get("checked_at")
    if latest is None or isinstance(checked_at, bool):
        return None
    if not isinstance(checked_at, (int, float)):
        return None
    return {"latest": latest, "checked_at": checked_at}


def cache_is_fresh(payload, now=None):
    if not payload:
        return False
    now = time.time() if now is None else now
    age = now - payload["checked_at"]
    return 0 <= age < CHECK_INTERVAL_SECONDS


def write_cache(latest, checked_at=None):
    latest = _valid_version(latest)
    if latest is None:
        return False
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "checked_at": time.time() if checked_at is None else checked_at,
        "latest": latest,
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)
    return True


def fetch_latest_version(timeout=CHECK_TIMEOUT_SECONDS):
    response = requests.get(
        PYPI_URL,
        timeout=timeout,
        headers={"Accept": "application/json", "User-Agent": PACKAGE_NAME},
    )
    response.raise_for_status()
    info = response.json().get("info") or {}
    latest = _valid_version(info.get("version"))
    if latest is None:
        return None
    try:
        write_cache(latest)
    except OSError:
        pass
    return latest


def running_outside_site_packages(package_file=None):
    path = Path(package_file or __file__).resolve()
    return not any(
        part.lower() in ("site-packages", "dist-packages") for part in path.parts
    )


def direct_url_marks_editable():
    try:
        import importlib.metadata as importlib_metadata

        dist = importlib_metadata.distribution(PACKAGE_NAME)
    except Exception:
        return False
    raw = dist.read_text("direct_url.json")
    if not raw:
        return False
    try:
        payload = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    return bool((payload.get("dir_info") or {}).get("editable"))


def is_pipx_install(prefix=None):
    path = Path(prefix or sys.prefix).resolve()
    parts = [part.lower() for part in path.parts]
    return "pipx" in parts and "venvs" in parts


def install_kind():
    if direct_url_marks_editable() or running_outside_site_packages():
        return "editable"
    if is_pipx_install():
        return "pipx"
    return "pip"


def upgrade_command(kind):
    if kind == "pipx":
        pipx = shutil.which("pipx")
        if pipx:
            return [pipx, "upgrade", PACKAGE_NAME], "pipx"
    return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME], "pip"


def format_notice(latest, current=__version__):
    return (
        f"A new novavision release is available: {current} -> {latest}\n"
        "Run: novavision self-update\n"
    )


class UpdateCheck:
    def __init__(self):
        self.latest = None
        self.thread = None


def begin_update_check(json_mode=False):
    if json_mode or not stdout_is_tty():
        return None
    if install_kind() == "editable":
        return None
    check = UpdateCheck()
    cached = read_cache()
    if cache_is_fresh(cached):
        check.latest = cached["latest"]
        return check

    def worker():
        try:
            fetched = fetch_latest_version()
        except Exception:
            fetched = None
        if fetched:
            check.latest = fetched
        elif cached:
            check.latest = cached["latest"]

    check.thread = threading.Thread(
        target=worker,
        name="novavision-update-check",
        daemon=True,
    )
    check.thread.start()
    return check


def finish_update_check(check, stream=None):
    if check is None:
        return
    if check.thread is not None:
        check.thread.join(CHECK_JOIN_SECONDS)
    latest = check.latest
    if not latest or not version_is_newer(latest):
        return
    output = stream or sys.stderr
    try:
        output.write(format_notice(latest))
        output.flush()
    except Exception:
        return


def self_update(logger):
    kind = install_kind()
    if kind == "editable":
        logger.error(
            "This copy is a development install. "
            "Update it from the source checkout instead of novavision self-update."
        )
        return False

    latest = None
    try:
        latest = fetch_latest_version(timeout=SELF_UPDATE_TIMEOUT_SECONDS)
    except Exception:
        latest = None
    if latest and not version_is_newer(latest):
        logger.success(f"{PACKAGE_NAME} {__version__} is already up to date.")
        return True

    command, tool = upgrade_command(kind)
    if kind == "pipx" and tool == "pip":
        logger.warning(
            "pipx was not found on PATH. Upgrading with pip inside this environment."
        )
    logger.info(f"Upgrading {PACKAGE_NAME} with {tool}.")
    try:
        result = subprocess.run(command)
    except OSError as exc:
        logger.error(f"Could not start the upgrade: {exc}")
        return False
    if result.returncode != 0:
        logger.error("Upgrade failed.")
        return False
    logger.success(
        f"{PACKAGE_NAME} was upgraded. Start a new command to use the new version."
    )
    return True
