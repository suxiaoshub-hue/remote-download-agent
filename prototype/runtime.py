import sys


def configure_console():
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    for output in (sys.stdout, sys.stderr):
        if output is not None and hasattr(output, 'reconfigure'):
            output.reconfigure(encoding='utf-8', errors='backslashreplace')
