import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
from ctypes import wintypes

from runtime import configure_console, WindowsConsoleOutput


class Coordinate(ctypes.Structure):
    _fields_ = [('column', wintypes.SHORT), ('row', wintypes.SHORT)]


def check_console(code_page):
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.SetConsoleCursorPosition.argtypes = [wintypes.HANDLE, Coordinate]
    kernel32.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, Coordinate, ctypes.POINTER(wintypes.DWORD)]
    kernel32.SetConsoleOutputCP(code_page)
    configure_console()
    assert isinstance(sys.stdout, WindowsConsoleOutput)
    handle = kernel32.GetStdHandle(-11 & 0xffffffff)
    assert kernel32.SetConsoleCursorPosition(handle, Coordinate(0, 0))
    message = '服务已启动，测试中文'
    print(message)
    cell_count = len(message) * 2
    buffer = ctypes.create_unicode_buffer(cell_count + 1)
    count = wintypes.DWORD()
    assert kernel32.ReadConsoleOutputCharacterW(handle, buffer, cell_count, Coordinate(0, 0), ctypes.byref(count))
    assert buffer.value.rstrip() == message, repr(buffer.value)


if __name__ == '__main__':
    if len(sys.argv) == 3:
        try:
            check_console(int(sys.argv[1]))
            Path(sys.argv[2]).write_text(json.dumps({'ok': True}), encoding='utf-8')
        except Exception:
            Path(sys.argv[2]).write_text(json.dumps({'ok': False, 'traceback': traceback.format_exc(), 'stdoutType': type(sys.stdout).__name__, 'isatty': sys.stdout.isatty()}), encoding='utf-8')
            sys.exit(1)
    else:
        with tempfile.TemporaryDirectory() as folder:
            for code_page in (936, 65001):
                report = Path(folder) / ('console-' + str(code_page) + '.json')
                child = subprocess.run([sys.executable, __file__, str(code_page), str(report)], creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=15)
                if report.exists():
                    print(report.read_text(encoding='utf-8'))
                if child.returncode:
                    raise RuntimeError('Console check failed for code page ' + str(code_page))
        print('PASS actual Windows console Chinese Unicode output under CP936 and UTF-8')
