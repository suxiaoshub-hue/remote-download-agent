import base64
import configparser
import hashlib
import hmac
import os
import shutil
import sqlite3
import struct
import tempfile

EXPECTED_HASH = '05b9927164f7b3a842f48ffc3bcfd464ae4052e2cdeec4f54902925f2178cdb6'


def decode_pcstory_path(path):
    if not path:
        return ''
    try:
        decoded = base64.b64decode(path, validate=True).decode('utf-8')
    except (ValueError, UnicodeDecodeError):
        return path
    if ':\\' in decoded or decoded.startswith('\\\\') or '/' in decoded:
        return decoded
    return path


def probe_path(path):
    decoded = decode_pcstory_path(path)
    wildcard = min((index for index in (decoded.find('*'), decoded.find('?')) if index >= 0), default=-1)
    return decoded[:wildcard].rstrip('\\/') if wildcard >= 0 else decoded


def stream_xor(key, nonce, counter, content):
    words = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
    words += list(struct.unpack('<8I', key)) + [counter] + list(struct.unpack('<3I', nonce[:12]))
    result = bytearray()
    for position in range(0, len(content), 64):
        state = words.copy()
        for _ in range(10):
            for first, second, third, fourth in ((0,4,8,12),(1,5,9,13),(2,6,10,14),(3,7,11,15),(0,5,10,15),(1,6,11,12),(2,7,8,13),(3,4,9,14)):
                for target, source, rotated, bits in ((first,second,fourth,16),(third,fourth,second,12),(first,second,fourth,8),(third,fourth,second,7)):
                    state[target] = (state[target] + state[source]) & 0xffffffff
                    value = state[rotated] ^ state[target]
                    state[rotated] = ((value << bits) | (value >> (32 - bits))) & 0xffffffff
        block = struct.pack('<16I', *((value + initial) & 0xffffffff for value, initial in zip(state, words)))
        result.extend(value ^ mask for value, mask in zip(content[position:position + 64], block))
        words[12] = (words[12] + 1) & 0xffffffff
    return bytes(result)


def poly_tag(key, content):
    multiplier = int.from_bytes(key[:16], 'little') & 0x0ffffffc0ffffffc0ffffffc0fffffff
    accumulator = 0
    for position in range(0, len(content), 16):
        block = content[position:position + 16]
        value = int.from_bytes(block, 'little') + (1 << (8 * len(block)))
        accumulator = ((accumulator + value) * multiplier) % ((1 << 130) - 5)
    return ((accumulator + int.from_bytes(key[16:], 'little')) % (1 << 128)).to_bytes(16, 'little')


def decode(source):
    if len(source) < 4096 or len(source) > 32 * 1024 * 1024 or len(source) % 4096:
        raise ValueError('PCStory 数据库大小不支持')
    if source[16:18] != b'\x10\x00' or source[20] != 32:
        raise ValueError('PCStory 数据库格式不匹配')
    password = hashlib.md5(base64.b64encode(b'http://pcstory.ml')).hexdigest().encode('utf-16le')[:32]
    master = hashlib.pbkdf2_hmac('sha256', password, source[:16], 64007, 32)
    result = bytearray(source)
    for offset in range(0, len(source), 4096):
        page = source[offset:offset + 4096]
        number = offset // 4096 + 1
        nonce = page[-32:-16]
        counter = int.from_bytes(nonce[12:], 'little') ^ number
        derived = stream_xor(master, nonce, counter, bytes(64))
        if not hmac.compare_digest(poly_tag(derived[:32], page[:-16]), page[-16:]):
            raise ValueError('PCStory 数据库页认证失败：%d' % number)
        start = 24 if number == 1 else 0
        result[offset + start:offset + 4064] = stream_xor(derived[32:], nonce, (counter + 1) & 0xffffffff, page[start:-32])
    result[:16] = b'SQLite format 3\0'
    return result


def game_status(raw_status, path):
    if raw_status in (2, 3, 5, 6):
        return 'pending'
    if raw_status == 0 and not path:
        return 'not_installed'
    if raw_status == 1 and path:
        return 'installed' if os.path.isdir(probe_path(path)) else 'missing'
    return 'unknown'


def configured_disks(folder):
    parser = configparser.ConfigParser(interpolation=None)
    with open(os.path.join(folder, 'config.ini'), encoding='utf-8-sig') as file:
        parser.read_file(file)
    disk = parser.get('config', 'disk', fallback='').strip()
    if len(disk) == 1 and disk.isascii() and disk.isalpha():
        return [disk.upper() + ':\\']
    if len(disk) == 2 and disk[1] == ':' and disk[0].isascii() and disk[0].isalpha():
        return [disk.upper() + '\\']
    raise ValueError('无法识别 PCStory 下载磁盘：' + disk)


def locate_folder():
    import ctypes
    from ctypes import wintypes
    if os.name != 'nt':
        raise ValueError('自动定位 PCStory 需要 Windows，可填写 pcstoryFolder')
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    window = user32.FindWindowW('Global\\{4F7961BA-AD65-4018-BDEA-1BA1FF77CD66}', None)
    pid = wintypes.DWORD()
    if not window or not user32.GetWindowThreadProcessId(window, ctypes.byref(pid)):
        raise ValueError('请先运行 PCStory，或填写 pcstoryFolder')
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        raise ValueError('无法读取 PCStory 路径，请以相同管理员权限运行 Agent')
    try:
        size = wintypes.DWORD(32768)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            raise ValueError('读取 PCStory 程序路径失败')
        return os.path.dirname(path.value)
    finally:
        kernel32.CloseHandle(handle)


def snapshot(folder):
    with open(os.path.join(folder, 'pcstory.exe'), 'rb') as file:
        if hashlib.sha256(file.read()).hexdigest() != EXPECTED_HASH:
            raise ValueError('PCStory 程序版本不匹配，库存未刷新')
    path = os.path.join(folder, 'pcstory.dat')
    def journal_check():
        for suffix in ('-wal', '-journal'):
            journal = path + suffix
            if os.path.exists(journal) and os.path.getsize(journal):
                raise ValueError('数据库正在写入，稍后自动重试')
    journal_check()
    with open(path, 'rb') as file:
        source = file.read(32 * 1024 * 1024 + 1)
        file.seek(0)
        if source != file.read(32 * 1024 * 1024 + 1):
            raise ValueError('数据库在读取期间变化，稍后自动重试')
    journal_check()
    decoded = decode(source)
    descriptor, temporary = tempfile.mkstemp(prefix='pcstory-inventory-', suffix='.db')
    try:
        with os.fdopen(descriptor, 'wb') as file:
            file.write(decoded)
        connection = sqlite3.connect(temporary)
        try:
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('清单快照校验失败')
            rows = connection.execute('SELECT gid,gname,status,gsvrpath,idcver,svrver,gsize FROM tGameList').fetchall()
        finally:
            connection.close()
    finally:
        os.unlink(temporary)
    games = []
    for game_id, name, raw_status, local_path, local_version, server_version, size in rows:
        path_value = decode_pcstory_path(local_path or '')
        games.append({'gameId': game_id, 'name': name, 'status': game_status(raw_status, path_value),
                      'rawStatus': raw_status, 'localPath': path_value, 'localVersion': local_version or 0,
                      'serverVersion': server_version or 0, 'sizeBytes': max(0, (size or 0) * 1024)})
    disks = []
    for root in configured_disks(folder):
        try:
            usage = shutil.disk_usage(root)
            disks.append({'path': root, 'freeBytes': usage.free, 'totalBytes': usage.total, 'downloadDisk': True})
        except OSError as error:
            raise ValueError('PCStory 下载盘不可访问：' + root) from error
    return {'games': games, 'disks': disks, 'complete': True, 'source': 'pcstory.dat'}
