import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def main():
    rows = []
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(512)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 512)
        user32.GetClassNameW(hwnd, cls, 256)
        if title.value or cls.value:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rows.append((hwnd, pid.value, cls.value, title.value))
        return True
    user32.EnumWindows(EnumWindowsProc(callback), 0)
    print('HWND\tPID\tCLASS\tTITLE')
    for hwnd, pid, cls, title in rows:
        print(f'{hwnd}\t{pid}\t{cls}\t{title}')
    input('\n记录完成，按回车退出...')

if __name__ == '__main__':
    main()
