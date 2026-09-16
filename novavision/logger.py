import json
import os
from pathlib import Path
from typing import Optional
from datetime import datetime
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text


class ConsoleLogger:
    ICONS = {
        "info": "[INFO]",
        "success": "[OK]",
        "warning": "[WARNING]",
        "error": "[ERROR]",
        "question": "[?]",
        "process": "[...]",
    }

    COLORS = {
        "info": "blue",
        "success": "green",
        "warning": "yellow",
        "error": "red",
        "question": "cyan",
        "process": "magenta",
    }

    def __init__(
        self,
        log_file_path: Optional[str] = None,
        append: bool = False,
        quiet: bool = False,
        json_mode: bool = False,
        no_color: Optional[bool] = None,
    ):
        self.log_file_path = log_file_path
        self.quiet = quiet
        self.json_mode = json_mode
        self.no_color = self._resolve_no_color(no_color)
        self.console = Console(no_color=self.no_color, highlight=False)
        self._active_loading = None
        self._fh = None
        if log_file_path:
            try:
                path_obj = Path(log_file_path)
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                mode = "a" if append else "w"
                self._fh = open(path_obj, mode, encoding="utf-8")
            except Exception:
                self._fh = None

    def configure(self, quiet=False, json_mode=False, no_color=None):
        self.quiet = quiet
        self.json_mode = json_mode
        self.no_color = self._resolve_no_color(no_color)
        self.console = Console(no_color=self.no_color, highlight=False)

    def copy_settings(self, log_file_path=None, append=False):
        return ConsoleLogger(
            log_file_path=log_file_path,
            append=append,
            quiet=self.quiet,
            json_mode=self.json_mode,
            no_color=self.no_color,
        )

    def _resolve_no_color(self, no_color):
        if no_color:
            return True
        return bool(os.environ.get("NO_COLOR"))

    def _timestamp(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _format_message(self, level, message):
        icon = self.ICONS.get(level, "")
        color = self.COLORS.get(level, "")
        return f"[{color}]{icon}[/{color}] {message}"

    def _plain_message(self, level, message):
        icon = self.ICONS.get(level, "")
        return f"{self._timestamp()} {icon} {self._plain_text(message)}"

    def _plain_text(self, message):
        try:
            return Text.from_markup(str(message)).plain
        except Exception:
            return str(message)

    def _write_file(self, level, message):
        if self._fh:
            try:
                self._fh.write(self._plain_message(level, message) + "\n")
                self._fh.flush()
            except Exception:
                pass

    def write_raw(self, text):
        if not text or not self._fh:
            return
        try:
            self._fh.write(text if text.endswith("\n") else text + "\n")
            self._fh.flush()
        except Exception:
            pass

    def _should_print(self, level):
        if self.json_mode and level in ("info", "success", "process"):
            return False
        if self.quiet and level in ("info", "success", "process"):
            return False
        return True

    def info(self, message):
        if self._should_print("info"):
            self.console.print(self._format_message("info", message))
        self._write_file("info", message)

    def success(self, message):
        if self._should_print("success"):
            self.console.print(self._format_message("success", message))
        self._write_file("success", message)

    def warning(self, message):
        if self._should_print("warning"):
            self.console.print(self._format_message("warning", message))
        self._write_file("warning", message)

    def error(self, message):
        self.console.print(self._format_message("error", message))
        self._write_file("error", message)

    def question(self, message):
        self._write_file("question", message)
        return Prompt.ask(self._format_message("question", message), console=self.console)

    def confirm(self, message, default=True):
        self._write_file("question", message)
        return Confirm.ask(
            self._format_message("question", message),
            default=default,
            console=self.console,
        )

    def ask_index(self, prompt, count):
        while True:
            try:
                choice = IntPrompt.ask(
                    self._format_message("question", prompt),
                    console=self.console,
                )
            except Exception:
                self.warning("Please enter a number.")
                continue
            if 1 <= choice <= count:
                return choice - 1
            self.warning("Invalid selection. Please enter a valid number.")

    def step(self, current, total, message):
        label = f"Step {current}/{total}: {message}"
        self._write_file("info", label)
        if not self._should_print("info"):
            return
        self.console.rule(f"Step {current}/{total}", style="blue")
        self.info(message)

    def table(self, headers, rows, title=None):
        self._write_file(
            "info",
            (title + " " if title else "")
            + " | ".join(headers)
            + " :: "
            + " ; ".join(" | ".join(str(cell) for cell in row) for row in rows),
        )
        if not self._should_print("info"):
            return
        table = Table(title=title, show_header=True, header_style="bold")
        for header in headers:
            table.add_column(str(header))
        for row in rows:
            table.add_row(*[str(cell) for cell in row])
        self.console.print(table)

    def emit_json(self, payload):
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        self._write_file("info", text)
        self.console.print(text)

    def loading(self, message):
        self._write_file("process", f"START: {message}")
        return LoadingContext(self, message)

    def print_stream(self, text):
        if not self._should_print("process"):
            return
        kwargs = {
            "markup": False,
            "highlight": False,
            "overflow": "fold",
            "crop": False,
            "soft_wrap": True,
        }
        loading = self._active_loading
        if loading is not None and loading.progress is not None:
            loading.progress.console.print(text, **kwargs)
            return
        self.console.print(text, **kwargs)

    def close(self):
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass

    def __del__(self):
        self.close()


class LoadingContext:
    def __init__(self, logger, message):
        self.logger = logger
        self.message = message
        self.progress = None

    def __enter__(self):
        if self.logger.quiet or self.logger.json_mode:
            return self
        self.progress = Progress(
            SpinnerColumn("line"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            transient=True,
            console=self.logger.console,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        self.progress.start()
        self.task = self.progress.add_task(description=self.message, total=None)
        self.logger._active_loading = self
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if getattr(self.logger, "_active_loading", None) is self:
            self.logger._active_loading = None
        if self.progress:
            self.progress.stop()
        status = "OK" if exc_type is None else f"ERROR: {exc_val}"
        self.logger._write_file("process", f"END: {self.message} -> {status}")
