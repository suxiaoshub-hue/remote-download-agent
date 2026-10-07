import ctypes
import subprocess
import sys
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
    buffer = ctypes.create_unicode_buffer(len(message) + 1)
    count = wintypes.DWORD()
    assert kernel32.ReadConsoleOutputCharacterW(handle, buffer, len(message), Coordinate(0, 0), ctypes.byref(count))
    assert buffer.value == message, repr(buffer.value)


if __name__ == '__main__':
    if len(sys.argv) == 2:
        check_console(int(sys.argv[1]))
    else:
        for code_page in (936, 65001):
            subprocess.run([sys.executable, __file__, str(code_page)], creationflags=subprocess.CREATE_NEW_CONSOLE, timeout=15, check=True)
        print('PASS actual Windows console Chinese Unicode output under CP936 and UTF-8')
