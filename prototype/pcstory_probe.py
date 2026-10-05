import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def main():
    rows = []
    def process_path(pid):
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ''
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(handle)
        return buf.value if ok else ''
    def callback(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, cls, 256)
        if title.value or cls.value:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rows.append((hwnd, pid.value, cls.value, title.value, process_path(pid.value)))
        return True
    user32.EnumWindows(EnumWindowsProc(callback), 0)
    print('HWND\tPID\tCLASS\tTITLE\tPROCESS')
    for hwnd, pid, cls, title, path in rows:
        print(f'{hwnd}\t{pid}\t{cls}\t{title}\t{path}')
    input('\n记录完成，按回车退出...')

if __name__ == '__main__':
    main()
