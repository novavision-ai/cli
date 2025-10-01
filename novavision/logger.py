from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from datetime import datetime
from pathlib import Path

class ConsoleLogger:
    ICONS = {
        'info': '[INFO]',
        'success': '[OK]',
        'warning': '[WARNING]',
        'error': '[ERROR]',
        'question': '[?]',
        'process': '[...]'
    }

    COLORS = {
        'info': 'blue',
        'success': 'green',
        'warning': 'yellow',
        'error': 'red',
        'question': 'cyan',
        'process': 'magenta'
    }

    def __init__(self, log_file_path: str | None = None, append: bool = False):
        self.console = Console()
        self.log_file_path = log_file_path
        self._fh = None
        if log_file_path:
            try:
                path_obj = Path(log_file_path)
                path_obj.parent.mkdir(parents=True, exist_ok=True)
                mode = 'a' if append else 'w'
                self._fh = open(path_obj, mode, encoding='utf-8')
            except Exception:
                # Fallback: disable file logging silently if cannot open
                self._fh = None

    def _timestamp(self):
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _format_message(self, level, message):
        icon = self.ICONS.get(level, '')
        color = self.COLORS.get(level, '')
        return f"[{color}]{icon} {message}[/{color}]"

    def _plain_message(self, level, message):
        icon = self.ICONS.get(level, '')
        return f"{self._timestamp()} {icon} {message}"

    def _write_file(self, level, message):
        if self._fh:
            self._fh.write(self._plain_message(level, message) + "\n")
            self._fh.flush()

    def info(self, message):
        self.console.print(self._format_message('info', message))
        self._write_file('info', message)

    def success(self, message):
        self.console.print(self._format_message('success', message))
        self._write_file('success', message)

    def warning(self, message):
        self.console.print(self._format_message('warning', message))
        self._write_file('warning', message)

    def error(self, message):
        self.console.print(self._format_message('error', message))
        self._write_file('error', message)

    def question(self, message):
        # Record prompt in log file before asking
        self._write_file('question', message)
        return Prompt.ask(self._format_message('question', message))

    def loading(self, message):
        # record start of loading context
        self._write_file('process', f"START: {message}")
        return LoadingContext(self, message)

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
        self.progress = Progress(
            SpinnerColumn("line"),
            TextColumn("[progress.description]{task.description}"),
            transient=True,
            console=self.logger.console
        )
        self.progress.start()
        self.task = self.progress.add_task(description=self.message, total=None)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.progress.stop()
        # record end of loading context
        status = 'OK' if exc_type is None else f'ERROR: {exc_val}'
        self.logger._write_file('process', f"END: {self.message} -> {status}")
