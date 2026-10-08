import ctypes
import hashlib
import math
import os
import re
import time
import threading
from ctypes import wintypes

import inventory

WINDOW_LOCK = threading.RLock()


def column_key(value):
    return re.sub(r'\s+', '', value).replace('（', '(').replace('）', ')').casefold()


def numeric(value):
    if not re.fullmatch(r'\d+(?:\.\d+)?', value.strip()):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def byte_value(value, header, speed=False):
    matched = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(B|KB|KiB|MB|MiB|GB|GiB)?\s*(?:/s|/秒)?\s*', value, re.IGNORECASE)
    if not matched:
        return None
    unit = matched.group(2)
    if not unit:
        unit_match = re.search(r'\((B|KB|KiB|MB|MiB|GB|GiB)(?:/s|/秒)?\)', header, re.IGNORECASE)
        unit = unit_match.group(1) if unit_match else None
    if not unit:
        return None
    multiplier = {'b': 1, 'kb': 1024, 'kib': 1024, 'mb': 1048576, 'mib': 1048576, 'gb': 1073741824, 'gib': 1073741824}[unit.casefold()]
    number = float(matched.group(1)) * multiplier
    if not math.isfinite(number):
        return None
    return number if speed else round(number)


def parse_row(headers, cells):
    keys = [column_key(value) for value in headers]
    if len(keys) != len(cells) or len(set(keys)) != len(keys):
        return None
    values = dict(zip(keys, cells))
    if not all(key in values for key in ('id', '状态', '进度')):
        return None
    if not values['id'].strip().isdigit():
        return None
    game_id = int(values['id'])
    if not 0 < game_id < 2147483648:
        return None
    percent = values['进度'].strip()
    amount = numeric(percent[:-1]) if percent.endswith('%') else None
    if percent and (amount is None or not 0 <= amount <= 100):
        return None
    raw_state = values['状态'].strip()
    state = {'正在下载': 'downloading', '下载中': 'downloading', '暂停': 'paused', '暂停下载': 'paused',
             '已暂停': 'paused', '等待下载': 'waiting', '排队下载': 'waiting',
             '等待中': 'waiting', '校验中': 'checking', '正在校验': 'checking',
             '下载完成': 'completed', '已完成': 'completed', '下载失败': 'failed'}.get(raw_state, 'unknown')
    if '暂停' in raw_state:
        state = 'paused'
    remaining = next((byte_value(value, key) for key, value in values.items() if key.startswith('剩余')), None)
    speed = next((byte_value(value, key, speed=True) for key, value in values.items() if key.startswith('速度')), None)
    update = next((byte_value(value, key) for key, value in values.items() if key.startswith('更新量')), None)
    return {'gameId': game_id, 'progress': amount / 100 if amount is not None else None,
            'downloadState': state, 'remainingBytes': remaining, 'speedBytesPerSecond': speed,
            'updateBytes': update, 'listFields': [{'title': title, 'value': value} for title, value in zip(headers, cells)],
            'source': 'pcstory-listview', 'sampledAt': time.time()}


class WindowsListReader:
    def __init__(self):
        if os.name != 'nt' or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise ValueError('PCStory 实时进度读取需要 Windows x64')
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.checked = None
        self.blocked_until = 0
        self.user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.user.FindWindowW.restype = wintypes.HWND
        self.user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.GetDlgCtrlID.argtypes = [wintypes.HWND]
        self.user.GetParent.argtypes = [wintypes.HWND]
        self.user.GetParent.restype = wintypes.HWND
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumChildWindows.argtypes = [wintypes.HWND, self.callback_type, wintypes.LPARAM]
        self.user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.user.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
        self.kernel.VirtualAllocEx.restype = ctypes.c_void_p
        self.kernel.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
        for name in ('ReadProcessMemory', 'WriteProcessMemory'):
            getattr(self.kernel, name).argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

    def read(self, game_ids, window=None, test_executable=None, action=None):
        with WINDOW_LOCK:
            return self._read(game_ids, window, test_executable, action)

    def _read(self, game_ids, window=None, test_executable=None, action=None):
        if action is not None and (action not in ('pause', 'resume', 'remove') or len(game_ids) != 1):
            raise ValueError('控件命令必须指定单一 GID 和已验证操作')
        if time.monotonic() < self.blocked_until:
            raise ValueError('PCStory 控件读取曾超时，稍后重试')
        window = window or self.user.FindWindowW('Global\\{4F7961BA-AD65-4018-BDEA-1BA1FF77CD66}', None)
        if not window:
            raise ValueError('未找到 PCStory 窗口')
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(window, ctypes.byref(pid))
        process = self.kernel.OpenProcess(0x1000 | 0x8 | 0x10 | 0x20, False, pid.value)
        if not process:
            raise ValueError('无法读取 PCStory 列表，请使用与 PCStory 相同的管理员权限')
        remote = None
        safe_to_free = True
        deadline = time.monotonic() + 4

        def send(target, message, first=0, second=0, timeout=200):
            nonlocal safe_to_free
            if time.monotonic() > deadline:
                raise ValueError('PCStory 列表较大，本轮采样超时')
            owner = wintypes.DWORD()
            self.user.GetWindowThreadProcessId(target, ctypes.byref(owner))
            if owner.value != pid.value:
                raise ValueError('PCStory 控件所属进程已变化')
            result = ctypes.c_size_t()
            if not self.user.SendMessageTimeoutW(target, message, first, second, 0x1 | 0x2 | 0x20, timeout, ctypes.byref(result)):
                safe_to_free = False
                self.blocked_until = time.monotonic() + 300
                raise ValueError('PCStory 控件响应超时，已停止采样并保留临时参数')
            return result.value

        def text(target, message, item, structure):
            buffer = ctypes.create_string_buffer(4096)
            transferred = ctypes.c_size_t()
            if not self.kernel.WriteProcessMemory(process, remote, ctypes.byref(buffer), 4096, ctypes.byref(transferred)) or transferred.value != 4096:
                raise ValueError('进度读取参数初始化失败')
            if not self.kernel.WriteProcessMemory(process, remote, ctypes.byref(structure), ctypes.sizeof(structure), ctypes.byref(transferred)) or transferred.value != ctypes.sizeof(structure):
                raise ValueError('进度读取参数写入失败')
            send(target, message, item, remote)
            if not self.kernel.ReadProcessMemory(process, remote + 256, ctypes.byref(buffer), 2048, ctypes.byref(transferred)) or transferred.value != 2048:
                raise ValueError('PCStory 列表文本读取失败')
            return buffer.raw[:2048].decode('utf-16le').split('\0', 1)[0]

        try:
            path = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            if not self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                raise ValueError('PCStory 进程路径读取失败')
            signature = (pid.value, path.value, os.stat(path.value).st_mtime_ns)
            if test_executable:
                if os.path.normcase(os.path.abspath(path.value)) != os.path.normcase(os.path.abspath(test_executable)):
                    raise ValueError('测试窗口所属程序不匹配')
            elif self.checked != signature:
                with open(path.value, 'rb') as file:
                    if hashlib.sha256(file.read()).hexdigest() != inventory.EXPECTED_HASH:
                        raise ValueError('PCStory 进度读取版本不匹配')
                self.checked = signature
            windows = []

            @self.callback_type
            def child(target, parameter):
                name = ctypes.create_unicode_buffer(256)
                if self.user.GetClassNameW(target, name, 256) and name.value == 'SysListView32':
                    windows.append(target)
                return True

            self.user.EnumChildWindows(window, child, 0)
            remote = self.kernel.VirtualAllocEx(process, None, 4096, 0x3000, 4)
            if not remote:
                raise ValueError('进度读取参数分配失败')
            samples = {}
            diagnostics = []
            requested = set(game_ids)
            seen = set()
            candidates = []
            valid_lists = 0
            for listing in windows:
                local_seen = set()
                if self.user.GetDlgCtrlID(listing) != 1003:
                    continue
                header = send(listing, 0x1000 + 31)
                if not header:
                    continue
                count = send(header, 0x1200)
                if not 3 <= count <= 24:
                    continue
                headers = []
                for column in range(count):
                    structure = ctypes.create_string_buffer(72)
                    ctypes.c_uint32.from_buffer(structure, 0).value = 2
                    ctypes.c_void_p.from_buffer(structure, 8).value = remote + 256
                    ctypes.c_int32.from_buffer(structure, 24).value = 1024
                    headers.append(text(header, 0x1200 + 11, column, structure))
                keys = [column_key(value) for value in headers]
                diagnostic = {'headers': headers}
                diagnostics.append(diagnostic)
                if not {'id', '状态', '进度'}.issubset(keys):
                    continue
                if len(set(keys)) != len(keys):
                    raise ValueError('下载列表存在重复标题')
                valid_lists += 1
                row_count = send(listing, 0x1000 + 4)
                diagnostic['rows'] = row_count
                if row_count > 1000:
                    raise ValueError('下载列表超过 1000 行，本轮停止采样')

                def cell(row, column):
                    structure = ctypes.create_string_buffer(88)
                    ctypes.c_int32.from_buffer(structure, 8).value = column
                    ctypes.c_void_p.from_buffer(structure, 24).value = remote + 256
                    ctypes.c_int32.from_buffer(structure, 32).value = 1024
                    return text(listing, 0x1000 + 115, row, structure)

                for row in range(row_count):
                    identity = cell(row, keys.index('id'))
                    if not identity.strip().isdigit() or int(identity) not in requested:
                        continue
                    cells = [cell(row, column) for column in range(count)]
                    if cells[keys.index('id')].strip() != identity.strip():
                        raise ValueError('下载列表正在排序，本轮重新采样')
                    seen.add(int(identity))
                    local_seen.add(int(identity))
                    sample = parse_row(headers, cells)
                    diagnostics[-1].setdefault('matchedRows', []).append(cells)
                    if sample:
                        previous = samples.get(sample['gameId'])
                        if previous and {key: value for key, value in previous.items() if key != 'sampledAt'} != {key: value for key, value in sample.items() if key != 'sampledAt'}:
                            raise ValueError('多个下载列表对同一游戏返回不同进度')
                        samples[sample['gameId']] = sample
                if send(listing, 0x1000 + 4) != row_count:
                    raise ValueError('下载列表行数变化，本轮重新采样')
                if action and self.user.GetDlgCtrlID(listing) == 1003 and requested.issubset(local_seen):
                    candidates.append((listing, headers, row_count))
            if action:
                if len(candidates) != 1:
                    raise ValueError('无法唯一定位 PCStory 下载任务控件')
                listing, headers, row_count = candidates[0]
                id_column = [column_key(value) for value in headers].index('id')
                identities = [cell(row, id_column).strip() for row in range(row_count)]
                target_id = str(game_ids[0])
                if identities.count(target_id) != 1:
                    raise ValueError('目标 GID 行不唯一，停止操作')
                parent = self.user.GetParent(listing)
                parent_class = ctypes.create_unicode_buffer(256)
                self.user.GetClassNameW(parent, parent_class, 256)
                if parent_class.value != '#32770':
                    raise ValueError('下载控件父窗口不是预期的对话框')
                selected = [identity for row, identity in enumerate(identities) if send(listing, 0x102c, row, 2) & 2]

                def select(row, state):
                    structure = ctypes.create_string_buffer(88)
                    ctypes.c_uint32.from_buffer(structure, 12).value = state
                    ctypes.c_uint32.from_buffer(structure, 16).value = 3
                    transferred = ctypes.c_size_t()
                    if not self.kernel.WriteProcessMemory(process, remote, ctypes.byref(structure), 88, ctypes.byref(transferred)) or transferred.value != 88:
                        raise ValueError('原生任务选择参数写入失败')
                    if not send(listing, 0x102b, row, remote):
                        raise ValueError('原生任务选择失败')

                try:
                    select(-1, 0)
                    select(identities.index(target_id), 3)
                    selected_row = send(listing, 0x100c, -1, 2)
                    if selected_row >= row_count or cell(selected_row, id_column).strip() != target_id or send(listing, 0x100c, selected_row, 2) != ctypes.c_size_t(-1).value:
                        raise ValueError('唯一选中 GID 校验失败，未发送操作')
                    deadline = time.monotonic() + 6
                    send(parent, 0x111, {'pause': 0x8016, 'resume': 0x8017, 'remove': 0x8018}[action], timeout=5000)
                finally:
                    if safe_to_free:
                        deadline = time.monotonic() + 2
                        select(-1, 0)
                        for row in range(send(listing, 0x1004)):
                            if cell(row, id_column).strip() in selected:
                                select(row, 2)
            return {'samples': list(samples.values()), 'lists': diagnostics,
                    'absentGameIds': sorted(requested - seen) if valid_lists else []}
        finally:
            if remote and safe_to_free:
                self.kernel.VirtualFreeEx(process, remote, 0, 0x8000)
            self.kernel.CloseHandle(process)
