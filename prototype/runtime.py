import sys


def configure_console():
    for output in (sys.stdout, sys.stderr):
        if output is not None and hasattr(output, 'reconfigure'):
            output.reconfigure(encoding='utf-8', errors='backslashreplace')
