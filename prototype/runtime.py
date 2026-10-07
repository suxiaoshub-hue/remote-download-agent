import sys


class WindowsConsoleOutput:
    encoding = 'utf-8'

    def __init__(self, original, kernel32, handle):
        self.original = original
        self.kernel32 = kernel32
        self.handle = handle

    def write(self, value):
        import ctypes
        from ctypes import wintypes
        for start in range(0, len(value), 8192):
            chunk = value[start:start + 8192]
            buffer = ctypes.create_unicode_buffer(chunk)
            length = len(chunk.encode('utf-16-le')) // 2
            written = wintypes.DWORD()
            if not self.kernel32.WriteConsoleW(self.handle, buffer, length, ctypes.byref(written), None):
                raise OSError('Windows console output failed')
            if written.value != length:
                raise OSError('Windows console output was incomplete')
        return len(value)

    def flush(self):
        return None

    def __getattr__(self, name):
        return getattr(self.original, name)


def windows_console(output):
    import ctypes
    import msvcrt
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.WriteConsoleW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    kernel32.WriteConsoleW.restype = wintypes.BOOL
    try:
        handle = msvcrt.get_osfhandle(output.fileno())
    except (AttributeError, OSError, ValueError):
        return output
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return WindowsConsoleOutput(output, kernel32, handle)
    return output


def configure_console():
    for name in ('stdout', 'stderr'):
        output = getattr(sys, name)
        if output is not None and sys.platform == 'win32':
            output = windows_console(output)
            setattr(sys, name, output)
        if output is not None and hasattr(output, 'reconfigure'):
            output.reconfigure(encoding='utf-8', errors='backslashreplace')
