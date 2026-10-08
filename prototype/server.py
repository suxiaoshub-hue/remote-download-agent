import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse
from runtime import configure_console

lock = threading.RLock()
cafes, tasks, inventories, catalog, controls = {}, {}, {}, {}, {}
DB_PATH = 'remote_download.db'
ADMIN_TOKEN = ''
FRESH_SECONDS = 120
ONLINE_SECONDS = 75
GAME_STATUSES = {'installed', 'not_installed', 'missing', 'pending', 'unknown'}
ACTIVE_STATUSES = ('accepted', 'waiting', 'downloading', 'uncertain')
CONTROL_PHASES = ('queued', 'delivering', 'accepted')


def public_task(task):
    result = dict(task)
    if task.get('telemetrySource') != 'pcstory-listview':
        result['progress'] = None
    result['progressFresh'] = bool(task.get('telemetrySource') == 'pcstory-listview' and online(cafes[task['cafeId']]) and
                                  time.time() - (task.get('telemetryUpdatedAt') or 0) < 8 and
                                  0 <= time.time() - (task.get('sampledAt') or 0) < 8)
    if not result['progressFresh']:
        result['etaSeconds'] = None
        result['speedBytesPerSecond'] = None
    result['displayState'] = task['status']
    if task['status'] in ACTIVE_STATUSES:
        result['displayState'] = task.get('downloadState', 'unknown') if result['progressFresh'] else 'unknown'
        if result['displayState'] == 'absent':
            result['displayState'] = 'unknown'
    related = [command for command in controls.values() if command['taskId'] == task['id']]
    result['control'] = max(related, key=lambda command: command['createdAt']) if related else None
    return result


def clean_progress(data):
    if data.get('source') != 'pcstory-listview':
        raise ValueError('仅接受 PCStory 下载列表采样')
    state = data.get('downloadState')
    if state not in ('downloading', 'paused', 'waiting', 'checking', 'completed', 'failed', 'unknown', 'absent', 'removed'):
        raise ValueError('下载状态不合法')
    fields = {}
    for name, maximum in (('progress', 1), ('remainingBytes', 2**63 - 1), ('speedBytesPerSecond', 2**63 - 1), ('updateBytes', 2**63 - 1)):
        value = data.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= maximum):
            raise ValueError('进度字段不合法：' + name)
        fields[name] = value
    sampled_at = data.get('sampledAt')
    if isinstance(sampled_at, bool) or not isinstance(sampled_at, (int, float)) or not math.isfinite(sampled_at) or abs(time.time() - sampled_at) > 120:
        raise ValueError('进度样本时间不合法或已过期')
    remaining, speed = fields['remainingBytes'], fields['speedBytesPerSecond']
    estimate = remaining / speed if state == 'downloading' and remaining is not None and remaining > 0 and speed is not None and speed > 0 else None
    eta = math.ceil(estimate) if estimate is not None and math.isfinite(estimate) else None
    fields.update(downloadState=state, etaSeconds=eta, telemetryUpdatedAt=time.time(), sampledAt=sampled_at,
                  telemetrySource='pcstory-listview')
    raw = data.get('listFields', [])
    if not isinstance(raw, list) or len(raw) > 24:
        raise ValueError('下载列表字段过多或不合法')
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not isinstance(item.get('value'), str) or len(item['title']) > 200 or len(item['value']) > 2048:
            raise ValueError('下载列表单元格不合法')
    fields['listFields'] = [{'title': item['title'], 'value': item['value']} for item in raw]
    return fields


def apply_progress(task, data, confirmed_remove=False):
    if data.get('gameId', task['gameId']) != task['gameId']:
        raise ApiError('采样 GID 与任务不匹配', 409)
    fields = clean_progress(data)
    if fields['downloadState'] == 'removed' and not confirmed_remove:
        raise ApiError('删除状态须连续读取缺失或操作确认', 409)
    if fields['downloadState'] == 'absent':
        now = time.time()
        if now - fields['sampledAt'] > 8:
            raise ApiError('缺失证据已经过期', 409)
        if now - task.get('acceptedAt', 0) < 15 and not task.get('seenInList'):
            return
        task['absenceSince'] = task.get('absenceSince') or now
        task['absenceCount'] = task.get('absenceCount', 0) + 1
        fields.update(progress=task.get('progress'), speedBytesPerSecond=None, etaSeconds=None)
        if task['absenceCount'] >= 3 and now - task['absenceSince'] >= 2:
            fields['downloadState'] = 'removed'
    else:
        task.update(absenceCount=0, absenceSince=None, seenInList=True)
    task.update(fields, updatedAt=time.time())
    if fields['downloadState'] == 'removed':
        game = inventories.get(task['cafeId'], {}).get('games', {}).get(task['gameId'], {})
        task['status'] = 'completed' if fresh(task['cafeId']) and game.get('status') == 'installed' else 'removed'


def token_hash(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def save_db():
    with sqlite3.connect(DB_PATH) as database:
        values = {'cafes': cafes, 'tasks': tasks, 'inventories': inventories, 'catalog': catalog, 'controls': controls}
        for name, value in values.items():
            database.execute('INSERT OR REPLACE INTO app_state VALUES (?,?)', (name, json.dumps(value, ensure_ascii=False)))


def load_db():
    with lock, sqlite3.connect(DB_PATH) as database:
        database.execute('CREATE TABLE IF NOT EXISTS app_state (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        saved = dict(database.execute('SELECT name,value FROM app_state'))
        for name, target in (('cafes', cafes), ('tasks', tasks), ('inventories', inventories), ('catalog', catalog), ('controls', controls)):
            target.clear()
            target.update(json.loads(saved.get(name, '{}')))
        normalized = {int(key): value for key, value in catalog.items()}
        catalog.clear()
        catalog.update(normalized)
        for current in inventories.values():
            current['games'] = {int(key): value for key, value in current['games'].items()}


def online(cafe):
    return time.time() - cafe.get('lastSeen', 0) < ONLINE_SECONDS


def fresh(cafe_id):
    current = inventories.get(cafe_id)
    return bool(current and time.time() - current['updatedAt'] <= FRESH_SECONDS and online(cafes[cafe_id]) and not cafes[cafe_id].get('error'))


def can_download(cafe_id, game_id):
    game = inventories.get(cafe_id, {}).get('games', {}).get(game_id, {})
    if game.get('status') in ('not_installed', 'missing'):
        return True
    return game.get('status') == 'pending' and any(task['cafeId'] == cafe_id and task['gameId'] == game_id and task['status'] == 'removed' and public_task(task)['progressFresh'] for task in tasks.values())


def public_cafe(cafe):
    return {key: value for key, value in dict(cafe, online=online(cafe)).items() if key != 'tokenHash'}


def inventory_view(cafe_id, query=''):
    current = inventories.get(cafe_id, {'games': {}, 'disks': [], 'updatedAt': 0, 'complete': False})
    games = {game_id: dict(game, status='not_installed' if current['complete'] else 'unknown', localPath='') for game_id, game in catalog.items()}
    games.update(current['games'])
    for task in tasks.values():
        if task['cafeId'] == cafe_id and task['gameId'] in games and task['status'] in ('queued', 'delivering') + ACTIVE_STATUSES:
            observed = public_task(task)
            state = task.get('downloadState') if observed['progressFresh'] else None
            games[task['gameId']] = dict(games[task['gameId']], status=state if state in ('downloading', 'paused', 'checking', 'waiting') else 'pending')
    for task in tasks.values():
        if task['cafeId'] == cafe_id and task['status'] == 'removed' and public_task(task)['progressFresh'] and task['gameId'] in games and games[task['gameId']]['status'] != 'installed':
            if not any(other['cafeId'] == cafe_id and other['gameId'] == task['gameId'] and other['status'] in ('queued', 'delivering') + ACTIVE_STATUSES for other in tasks.values()):
                games[task['gameId']]['status'] = 'not_installed'
    query = query.casefold().strip()
    selected = [game for game in games.values() if not query or query in str(game['gameId']) or query in game['name'].casefold()]
    return {'cafeId': cafe_id, 'reported': cafe_id in inventories, 'fresh': fresh(cafe_id),
            'updatedAt': current['updatedAt'], 'complete': current['complete'],
            'games': sorted(selected, key=lambda game: (game['name'], game['gameId'])), 'disks': current['disks']}


def clean_inventory(data):
    now = time.time()
    games = {}
    if not isinstance(data.get('games'), list) or len(data['games']) > 10000:
        raise ValueError('游戏清单必须是最多 10000 条的数组')
    for raw in data['games']:
        game_id = int(raw['gameId'])
        if not 0 < game_id < 2147483648 or raw['status'] not in GAME_STATUSES:
            raise ValueError('游戏编号或状态不合法')
        games[game_id] = {'gameId': game_id, 'name': str(raw['name'])[:200], 'status': raw['status'],
                          'localPath': str(raw.get('localPath', ''))[:4096], 'sizeBytes': max(0, int(raw.get('sizeBytes', 0))),
                          'localVersion': int(raw.get('localVersion', 0)), 'serverVersion': int(raw.get('serverVersion', 0))}
    disks = []
    for raw in data.get('disks', []):
        total, available = int(raw['totalBytes']), int(raw['freeBytes'])
        if total < 0 or available < 0 or available > total:
            raise ValueError('磁盘容量不合法')
        disks.append({'path': str(raw['path'])[:4096], 'totalBytes': total, 'freeBytes': available, 'downloadDisk': raw.get('downloadDisk') is True})
    return {'games': games, 'disks': disks, 'updatedAt': now, 'complete': data.get('complete') is True}


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorize(self, cafe_id=None):
        header = self.headers.get('Authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        if cafe_id is None:
            if not token or not ADMIN_TOKEN or not hmac.compare_digest(token, ADMIN_TOKEN):
                raise ApiError('请先登录后台', 401)
        else:
            if not token:
                raise ApiError('缺少网吧连接凭证', 401)
            cafe = cafes.get(cafe_id)
            if not cafe or not hmac.compare_digest(token_hash(token), cafe['tokenHash']):
                raise ApiError('此凭证不能操作该网吧', 403)

    def dispatch(self, method):
        parsed = urlparse(self.path)
        parts = [unquote(part) for part in parsed.path.strip('/').split('/')]
        if method == 'GET' and parsed.path == '/':
            with open(os.path.join(os.path.dirname(__file__), 'web.html'), 'rb') as file:
                body = file.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
            return
        data = {}
        if method == 'POST':
            length = int(self.headers.get('Content-Length', 0))
            if not 0 <= length <= 8 * 1024 * 1024:
                raise ApiError('请求过大', 413)
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ApiError('请求必须是 JSON 对象')
        with lock:
            if parts[:3] == ['api', 'controls', 'next'] and len(parts) == 4 and method == 'GET':
                cafe_id = parts[-1]
                self.authorize(cafe_id)
                if not online(cafes[cafe_id]) or any(command['cafeId'] == cafe_id and command['status'] == 'accepted' for command in controls.values()):
                    self.send_json({})
                    return
                pending = next((command for command in controls.values() if command['cafeId'] == cafe_id and command['status'] == 'delivering'), None)
                pending = pending or next((command for command in controls.values() if command['cafeId'] == cafe_id and command['status'] == 'queued'), None)
                if pending:
                    pending['status'] = 'delivering'
                    save_db()
                self.send_json(pending or {})
                return
            if parts[:2] == ['api', 'controls'] and len(parts) == 4 and parts[-1] == 'status' and method == 'POST':
                command = controls.get(parts[2])
                if not command:
                    raise ApiError('操作不存在', 404)
                self.authorize(command['cafeId'])
                status = data.get('status')
                if status not in ('accepted', 'confirmed', 'failed', 'uncertain'):
                    raise ApiError('操作结果不合法')
                if command['status'] not in CONTROL_PHASES:
                    self.send_json(command)
                    return
                task = tasks[command['taskId']]
                if status == 'accepted':
                    if command['status'] not in ('delivering', 'accepted'):
                        raise ApiError('操作尚未领取', 409)
                    current = public_task(task)
                    expected = 'paused' if command['action'] == 'resume' else None
                    if task['status'] not in ACTIVE_STATUSES or not current['progressFresh'] or (expected and current['downloadState'] != expected):
                        raise ApiError('本地状态变化，请重新确认操作', 409)
                elif command['status'] != 'accepted' and status == 'confirmed':
                    raise ApiError('操作未确认接收', 409)
                if status == 'confirmed':
                    sample = data.get('sample', {})
                    state = sample.get('downloadState')
                    expected = {'pause': ('paused',), 'resume': ('downloading', 'waiting', 'checking'), 'remove': ('removed',)}[command['action']]
                    if state not in expected:
                        raise ApiError('实际状态尚未确认操作成功', 409)
                    if task['status'] in ACTIVE_STATUSES:
                        apply_progress(task, sample, confirmed_remove=command['action'] == 'remove')
                command.update(status=status, error=str(data.get('error', ''))[:1000], updatedAt=time.time())
                save_db()
                self.send_json(command)
                return
            if parts[:2] == ['api', 'agents'] and method == 'POST':
                cafe_id = str(data['cafeId'])
                self.authorize(cafe_id)
                cafe = cafes[cafe_id]
                cafe['lastSeen'] = time.time()
                action = parts[-1]
                if action == 'inventory':
                    current = clean_inventory(data)
                    inventories[cafe_id] = current
                    cafe['error'] = ''
                    for game_id, game in current['games'].items():
                        catalog[game_id] = {'gameId': game_id, 'name': game['name'], 'sizeBytes': game['sizeBytes']}
                    for task in tasks.values():
                        game = current['games'].get(task['gameId'], {})
                        if task['cafeId'] != cafe_id or task['status'] not in ('accepted', 'waiting', 'downloading', 'uncertain'):
                            continue
                        if game.get('status') == 'installed':
                            task.update(status='completed', updatedAt=time.time())
                elif action == 'error':
                    cafe['error'] = str(data.get('error', ''))[:1000]
                elif action == 'progress-error':
                    cafe['progressError'] = str(data.get('error', ''))[:1000]
                elif action not in ('register', 'heartbeat'):
                    raise ApiError('接口不存在', 404)
                if action != 'heartbeat':
                    save_db()
                self.send_json({'ok': True, 'cafeId': cafe_id})
                return
            if parts[:3] == ['api', 'tasks', 'active'] and method == 'GET' and len(parts) == 4:
                cafe_id = parts[-1]
                self.authorize(cafe_id)
                watched = [dict(task) for task in tasks.values() if task['cafeId'] == cafe_id and (task['status'] in ACTIVE_STATUSES or task['status'] == 'removed' and not task.get('hidden') and not any(other['cafeId'] == cafe_id and other['gameId'] == task['gameId'] and other['status'] in ('queued', 'delivering') + ACTIVE_STATUSES for other in tasks.values()))]
                self.send_json({'tasks': watched})
                return
            if parts[:3] == ['api', 'tasks', 'next'] and method == 'GET' and len(parts) == 4:
                cafe_id = parts[-1]
                self.authorize(cafe_id)
                pending = next((task for task in tasks.values() if task['cafeId'] == cafe_id and task['status'] == 'delivering'), None)
                pending = pending or next((task for task in tasks.values() if task['cafeId'] == cafe_id and task['status'] == 'queued'), None)
                if pending:
                    if not fresh(cafe_id):
                        self.send_json({})
                        return
                    game = inventories[cafe_id]['games'].get(pending['gameId'], {})
                    if not can_download(cafe_id, pending['gameId']):
                        pending.update(status='failed', updatedAt=time.time())
                        save_db()
                        self.send_json({})
                        return
                    pending.update(status='delivering', updatedAt=time.time())
                    save_db()
                self.send_json(pending or {})
                return
            if parts[:2] == ['api', 'tasks'] and len(parts) == 4 and parts[-1] == 'status' and method == 'POST':
                task = tasks.get(parts[2])
                if not task:
                    raise ApiError('任务不存在', 404)
                self.authorize(task['cafeId'])
                status = data['status']
                if status not in ('accepted', 'downloading', 'waiting', 'failed', 'uncertain', 'completed'):
                    raise ApiError('任务状态不合法')
                if status == 'accepted' and task['status'] != 'completed':
                    game = inventories.get(task['cafeId'], {}).get('games', {}).get(task['gameId'], {})
                    if task['status'] not in ('delivering', 'accepted') or not fresh(task['cafeId']) or not can_download(task['cafeId'], task['gameId']):
                        raise ApiError('任务状态或库存变化，停止下发', 409)
                if task['status'] in ('completed', 'removed') or task['status'] == status:
                    self.send_json(task)
                    return
                if task['status'] in ('cancelled', 'completed', 'failed', 'removed'):
                    raise ApiError('此任务已经结束', 409)
                if status == 'completed':
                    local_game = inventories.get(task['cafeId'], {}).get('games', {}).get(task['gameId'], {})
                    if local_game.get('status') != 'installed' or not fresh(task['cafeId']):
                        raise ApiError('尚无新的本地已下载记录', 409)
                task.update(status=status, updatedAt=time.time())
                if status == 'accepted':
                    task['acceptedAt'] = time.time()
                save_db()
                self.send_json(task)
                return
            if parts[:2] == ['api', 'tasks'] and len(parts) == 4 and parts[-1] == 'telemetry' and method == 'POST':
                task = tasks.get(parts[2])
                if not task:
                    raise ApiError('任务不存在', 404)
                self.authorize(task['cafeId'])
                if task['status'] not in ACTIVE_STATUSES + ('removed',):
                    raise ApiError('任务当前不接受进度上报', 409)
                if task['status'] == 'removed':
                    if any(other['id'] != task['id'] and other['cafeId'] == task['cafeId'] and other['gameId'] == task['gameId'] and other['status'] in ('queued', 'delivering') + ACTIVE_STATUSES for other in tasks.values()):
                        self.send_json(public_task(task))
                        return
                    if data.get('downloadState') not in ('absent', 'removed'):
                        task['status'] = 'waiting'
                apply_progress(task, data)
                cafes[task['cafeId']]['progressError'] = ''
                save_db()
                self.send_json(public_task(task))
                return
            self.authorize()
            if parsed.path == '/api/state' and method == 'GET':
                self.send_json({'cafes': [public_cafe(cafe) for cafe in cafes.values()], 'tasks': [public_task(task) for task in tasks.values() if not task.get('hidden')]})
            elif parsed.path == '/api/cafes' and method == 'POST':
                name = str(data['name']).strip()[:100]
                if not name:
                    raise ApiError('网吧名称不能为空')
                cafe_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
                cafe = {'id': cafe_id, 'name': name, 'lastSeen': 0, 'error': '', 'tokenHash': token_hash(token)}
                cafes[cafe_id] = cafe
                save_db()
                config = {'cafeId': cafe_id, 'name': name, 'server': str(data.get('server', '')), 'agentToken': token,
                          'pcstoryFolder': '', 'adapter': 'PcstoryAdapter.exe', 'inventoryInterval': 30}
                self.send_json(dict(public_cafe(cafe), agentToken=token, config=config), 201)
            elif parts[:2] == ['api', 'cafes'] and len(parts) == 4:
                cafe_id = parts[2]
                if cafe_id not in cafes:
                    raise ApiError('网吧不存在', 404)
                if parts[-1] == 'inventory' and method == 'GET':
                    self.send_json(inventory_view(cafe_id, parse_qs(parsed.query).get('query', [''])[0]))
                elif parts[-1] == 'overview' and method == 'GET':
                    current = inventories.get(cafe_id, {})
                    self.send_json({'cafeId': cafe_id, 'reported': bool(current), 'fresh': fresh(cafe_id),
                                    'updatedAt': current.get('updatedAt', 0), 'disks': current.get('disks', [])})
                elif parts[-1] == 'rename' and method == 'POST':
                    name = str(data['name']).strip()[:100]
                    if not name:
                        raise ApiError('名称不能为空')
                    cafes[cafe_id]['name'] = name
                    save_db()
                    self.send_json(public_cafe(cafes[cafe_id]))
                else:
                    raise ApiError('接口不存在', 404)
            elif parsed.path == '/api/tasks' and method == 'POST':
                cafe_id, game_id = str(data['cafeId']), int(data['gameId'])
                if cafe_id not in cafes or not fresh(cafe_id):
                    raise ApiError('网吧离线或库存未更新，请等待 Agent 上报', 409)
                game = inventories[cafe_id]['games'].get(game_id)
                if not game or not can_download(cafe_id, game_id):
                    raise ApiError('游戏已下载或状态未确认，不能下发', 409)
                if any(task['cafeId'] == cafe_id and task['gameId'] == game_id and task['status'] in ('queued','delivering','accepted','waiting','downloading','uncertain') for task in tasks.values()):
                    raise ApiError('已有此游戏的任务，不能重复下发', 409)
                disks = [disk for disk in inventories[cafe_id]['disks'] if disk['downloadDisk']]
                if not disks:
                    raise ApiError('尚未获取 PCStory 下载盘容量', 409)
                if game['sizeBytes'] and max(disk['freeBytes'] for disk in disks) < game['sizeBytes']:
                    raise ApiError('PCStory 下载盘剩余容量不足', 409)
                task = {'id': uuid.uuid4().hex, 'cafeId': cafe_id, 'gameId': game_id, 'name': game['name'],
                        'status': 'queued', 'progress': None, 'downloadedBytes': None,
                        'totalBytes': int(game.get('sizeBytes', 0) or 0),
                        'speedBytesPerSecond': None, 'etaSeconds': None,
                        'telemetryUpdatedAt': 0, 'telemetrySource': None,
                        'updatedAt': time.time()}
                tasks[task['id']] = task
                save_db()
                self.send_json(task, 201)
            elif parts[:2] == ['api', 'tasks'] and len(parts) == 4 and parts[-1] in ('control', 'dismiss') and method == 'POST':
                task = tasks.get(parts[2])
                if not task:
                    raise ApiError('任务不存在', 404)
                if parts[-1] == 'dismiss':
                    if task['status'] in ('queued', 'delivering') + ACTIVE_STATUSES:
                        raise ApiError('请先删除本地任务或取消未下发任务', 409)
                    task['hidden'] = True
                    save_db()
                    self.send_json({'ok': True})
                    return
                action = data.get('action')
                current = public_task(task)
                if action not in ('pause', 'resume', 'remove'):
                    raise ApiError('操作不合法')
                if task['status'] not in ACTIVE_STATUSES or not current['progressFresh'] or current['downloadState'] in ('absent', 'removed', 'unknown', 'completed'):
                    raise ApiError('请等待新鲜的本地下载状态', 409)
                if action == 'resume' and current['downloadState'] != 'paused':
                    raise ApiError('仅暂停任务可以继续', 409)
                if action == 'pause' and current['downloadState'] not in ('downloading', 'waiting', 'checking'):
                    raise ApiError('该任务当前不能暂停', 409)
                if any(command['taskId'] == task['id'] and command['status'] in CONTROL_PHASES for command in controls.values()):
                    raise ApiError('此任务已有操作正在执行', 409)
                command = {'id': uuid.uuid4().hex, 'taskId': task['id'], 'cafeId': task['cafeId'], 'gameId': task['gameId'],
                           'action': action, 'status': 'queued', 'createdAt': time.time(), 'updatedAt': time.time()}
                controls[command['id']] = command
                save_db()
                self.send_json(command, 201)
            elif parts[:2] == ['api','tasks'] and len(parts) == 4 and parts[-1] == 'cancel' and method == 'POST':
                task = tasks.get(parts[2])
                if not task:
                    raise ApiError('任务不存在', 404)
                if task['status'] != 'queued':
                    raise ApiError('命令已下发，请在 PCStory 中暂停；本版只取消未下发任务', 409)
                task.update(status='cancelled', updatedAt=time.time())
                save_db()
                self.send_json(task)
            else:
                raise ApiError('接口不存在', 404)

    def handle_api(self, method):
        try:
            self.dispatch(method)
        except ApiError as error:
            self.send_json({'error': str(error)}, error.status)
        except (ValueError, KeyError, TypeError) as error:
            self.send_json({'error': '请求参数不合法：' + str(error)}, 400)

    def do_GET(self):
        self.handle_api('GET')

    def do_POST(self):
        self.handle_api('POST')


def main():
    global DB_PATH, ADMIN_TOKEN
    configure_console()
    if getattr(sys, 'frozen', False):
        os.chdir(os.path.dirname(sys.executable))
    path = os.path.abspath('server-config.json')
    if not os.path.exists(path):
        with open(path, 'x', encoding='utf-8') as file:
            json.dump({'host': '0.0.0.0', 'port': 8765, 'adminToken': secrets.token_urlsafe(32), 'database': 'remote_download.db'}, file, indent=2)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path, encoding='utf-8-sig') as file:
        config = json.load(file)
    ADMIN_TOKEN = os.environ.get('REMOTE_ADMIN_TOKEN') or config['adminToken']
    if len(ADMIN_TOKEN) < 16:
        raise ValueError('后台登录密钥至少需要 16 个字符')
    DB_PATH = config.get('database', 'remote_download.db')
    load_db()
    http = ThreadingHTTPServer((config.get('host','0.0.0.0'), int(config.get('port',8765))), Handler)
    print('服务已启动，后台登录密钥保存在 server-config.json。浏览器打开 http://127.0.0.1:%d/' % http.server_port)
    http.serve_forever()


if __name__ == '__main__':
    main()
