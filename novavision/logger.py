from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn

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

    def __init__(self):
        self.console = Console()

    def _format_message(self, level, message):
        icon = self.ICONS.get(level, '')
        color = self.COLORS.get(level, '')
        return f"[{color}]{icon} {message}[/{color}]"

    def info(self, message):
        self.console.print(self._format_message('info', message))

    def success(self, message):
        self.console.print(self._format_message('success', message))

    def warning(self, message):
        self.console.print(self._format_message('warning', message))

    def error(self, message):
        self.console.print(self._format_message('error', message))

    def question(self, message):
        return Prompt.ask(self._format_message('question', message))

    def loading(self, message):
        return LoadingContext(self, message)

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