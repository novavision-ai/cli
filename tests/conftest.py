import os
import subprocess

import pytest
from pathlib import Path


def _current_docker_host():
    existing = os.environ.get("DOCKER_HOST")
    if existing:
        return existing
    try:
        result = subprocess.run(
            [
                "docker",
                "context",
                "inspect",
                "--format",
                "{{.Endpoints.docker.Host}}",
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    host = (result.stdout or "").strip()
    if result.returncode == 0 and host:
        return host
    return None


class DummyLoading:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeLogger:
    def __init__(self, answers=None):
        self.messages = []
        self.answers = list(answers or [])
        self.quiet = False
        self.json_mode = False
        self.no_color = False
        self.log_file_path = None
        self.json_payloads = []
        self.tables = []

    def configure(self, quiet=False, json_mode=False, no_color=None):
        self.quiet = quiet
        self.json_mode = json_mode
        self.no_color = bool(no_color)

    def copy_settings(self, log_file_path=None, append=False):
        clone = FakeLogger(answers=self.answers)
        clone.quiet = self.quiet
        clone.json_mode = self.json_mode
        clone.no_color = self.no_color
        clone.log_file_path = log_file_path
        clone.messages = self.messages
        return clone

    def _record(self, level, message):
        self.messages.append((level, message))

    def info(self, message):
        self._record("info", message)

    def success(self, message):
        self._record("success", message)

    def warning(self, message):
        self._record("warning", message)

    def error(self, message):
        self._record("error", message)

    def question(self, message):
        self._record("question", message)
        if self.answers:
            return self.answers.pop(0)
        return "y"

    def confirm(self, message, default=True):
        self._record("question", message)
        if self.answers:
            answer = self.answers.pop(0)
            if isinstance(answer, bool):
                return answer
            return str(answer).strip().lower() in ("y", "yes", "true", "1")
        return default

    def ask_index(self, prompt, count):
        self._record("question", prompt)
        if self.answers:
            return int(str(self.answers.pop(0)).strip()) - 1
        return 0

    def step(self, current, total, message):
        self._record("info", f"Step {current}/{total}: {message}")

    def table(self, headers, rows, title=None):
        self.tables.append({"headers": headers, "rows": rows, "title": title})
        self._record("info", title or "table")

    def emit_json(self, payload):
        self.json_payloads.append(payload)
        self._record("info", payload)

    def write_raw(self, text):
        self._record("process", text)

    def loading(self, message):
        self._record("process", message)
        return DummyLoading()

    def messages_of(self, level):
        return [text for recorded_level, text in self.messages if recorded_level == level]


@pytest.fixture
def fake_logger():
    return FakeLogger()


@pytest.fixture
def nv_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    docker_host = _current_docker_host()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", lambda *args, **kwargs: home)
    if docker_host:
        monkeypatch.setenv("DOCKER_HOST", docker_host)
    return home
