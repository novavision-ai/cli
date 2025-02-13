import sys
import time
import threading
from itertools import cycle

class ConsoleLogger:
    COLORS = {
        'BLUE': '\033[94m',
        'GREEN': '\033[92m',
        'YELLOW': '\033[93m',
        'RED': '\033[91m',
        'MAGENTA': '\033[95m',
        'CYAN': '\033[96m',
        'ENDC': '\033[0m',
        'BOLD': '\033[1m'
    }

    ICONS = {
        'info': 'ℹ️',
        'success': '✅',
        'warning': '⚠️',
        'error': '❌',
        'question': '❓',
        'process': '⏳ '
    }

    def __init__(self):
        self.is_loading = False
        self.loading_thread = None
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def _hide_cursor(self):
        sys.stdout.write('\033[?25l')
        sys.stdout.flush()

    def _show_cursor(self):
        sys.stdout.write('\033[?25h')
        sys.stdout.flush()

    def _format_message(self, level, message):
        colors = {
            'info': self.COLORS['BLUE'],
            'success': self.COLORS['GREEN'],
            'warning': self.COLORS['YELLOW'],
            'error': self.COLORS['RED'],
            'question': self.COLORS['CYAN'],
            'process': self.COLORS['MAGENTA']
        }
        return f"{colors[level]}[{self.ICONS[level]}]{self.COLORS['ENDC']} {message}"

    def info(self, message):
        print(self._format_message('info', message))

    def success(self, message):
        print(self._format_message('success', message))

    def warning(self, message):
        print(self._format_message('warning', message))

    def error(self, message):
        print(self._format_message('error', message))

    def question(self, message):
        return input(self._format_message('question', message))

    def _animate(self, message):
        self._hide_cursor()
        formatted_msg = self._format_message('process', message)
        for frame in cycle(self.frames):
            if not self.is_loading:
                break
            sys.stdout.write(f'\r{formatted_msg} {frame}')
            sys.stdout.flush()
            time.sleep(0.2)
        self._show_cursor()

    def start_loading(self, message):
        self.is_loading = True
        self.loading_thread = threading.Thread(target=self._animate, args=(message,))
        self.loading_thread.start()

    def stop_loading(self):
        self.is_loading = False
        if self.loading_thread:
            self.loading_thread.join()
        sys.stdout.write('\r' + ' ' * 100 + '\r')  # Clear line
        sys.stdout.flush()

    def loading(self, message):
        return LoadingContext(self, message)


class LoadingContext:
    def __init__(self, logger, message):
        self.logger = logger
        self.message = message

    def __enter__(self):
        self.logger.start_loading(self.message)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logger.stop_loading()

log = ConsoleLogger()