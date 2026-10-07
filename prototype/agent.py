import argparse
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.error
import urllib.request

import inventory


def filesystem_bytes(path):
    """Return bytes currently present at an existing download path.

    PCStory can keep a path template until the first file is created. In that
    case there is no defensible progress sample yet, so return None.
    """
    if not path:
        return None
    if '*' in path or '?' in path:
        path = path[:min(index for index in (path.find('*'), path.find('?')) if index >= 0)].rstrip('\\/')
    if '%' in path:
        return None
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return None
    if not os.path.isdir(path):
        return None
    total = 0
    try:
        for root, _, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    except OSError:
        return None
    return total


def request(url, method='GET', payload=None, token=''):
    body = json.dumps(payload).encode('utf-8') if payload is not None else None
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read() or b'{}')


class PcstoryAdapter:
    def __init__(self, executable='PcstoryAdapter.exe'):
        self.executable = os.path.abspath(executable)
        self.process = None
        self.task = None

    def start(self, task):
        if self.process is not None:
            raise ValueError('仍有下载命令正在执行')
        self.task = task
        output = os.path.abspath('pcstory-task-' + task['id'] + '.txt')
        self.process = subprocess.Popen([self.executable, '--game-id', str(int(task['gameId'])), '--output', output], cwd=os.path.dirname(self.executable))

    @staticmethod
    def result_status(code):
        return {0: 'downloading', 2: 'waiting', 3: 'failed', 4: 'uncertain', 5: 'uncertain'}.get(code, 'failed')

    def poll(self):
        if self.process is None:
            return None
        code = self.process.poll()
        if code is None:
            return None
        self.process = None
        return self.result_status(code)


class TaskJournal:
    def __init__(self, path='agent-task.json'):
        self.path = path
        self.record = None
        if os.path.exists(path):
            with open(path, encoding='utf-8') as file:
                self.record = json.load(file)
            if self.record['phase'] == 'executing':
                self.save(dict(self.record, phase='result', status='uncertain'))

    def save(self, record):
        with open(self.path + '.tmp', 'w', encoding='utf-8') as file:
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(self.path + '.tmp', self.path)
        self.record = record

    def clear(self):
        os.unlink(self.path)
        self.record = None


class Heartbeat:
    def __init__(self, api, cafe_id, interval=5):
        self.api = api
        self.cafe_id = cafe_id
        self.interval = interval
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name='agent-heartbeat', daemon=True)

    def run(self):
        while not self.stopped.is_set():
            try:
                self.api('/api/agents/heartbeat', 'POST', {'cafeId': self.cafe_id})
            except Exception:
                pass
            self.stopped.wait(self.interval)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=16)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cafe-id', required=True)
    parser.add_argument('--name', default='网吧')
    parser.add_argument('--server', required=True)
    parser.add_argument('--agent-token', required=True)
    parser.add_argument('--pcstory-folder', default='')
    parser.add_argument('--adapter', default='PcstoryAdapter.exe')
    parser.add_argument('--inventory-interval', type=int, default=30)
    args = parser.parse_args()
    base = args.server.rstrip('/')
    cafe_path = urllib.parse.quote(args.cafe_id, safe='')
    adapter = PcstoryAdapter(args.adapter)
    registered = False
    last_inventory = 0
    latest_inventory = None
    telemetry = None
    last_telemetry = 0
    journal = TaskJournal()
    if journal.record and journal.record['task']['cafeId'] != args.cafe_id:
        raise ValueError('agent-task.json 属于其他网吧，请恢复原配置处理此任务')

    def api(path, method='GET', payload=None):
        return request(base + path, method, payload, args.agent_token)

    heartbeat = Heartbeat(api, args.cafe_id)
    heartbeat.start()
    while True:
        try:
            if not registered:
                api('/api/agents/register', 'POST', {'cafeId': args.cafe_id, 'name': args.name})
                registered = True
            result = adapter.poll()
            if result:
                journal.save({'task': adapter.task, 'phase': 'result', 'status': result})
                if result in ('downloading', 'waiting'):
                    game = (latest_inventory or {}).get('games', [])
                    game = next((item for item in game if item.get('gameId') == adapter.task.get('gameId')), {})
                    path = game.get('localPath', '')
                    telemetry = {'task': adapter.task, 'baseline': filesystem_bytes(path),
                                 'previous': None, 'path': path}
            if journal.record and journal.record['phase'] == 'result':
                api('/api/tasks/' + journal.record['task']['id'] + '/status', 'POST', {'status': journal.record['status']})
                journal.clear()
            if telemetry and time.time() - last_telemetry >= 5:
                task = telemetry['task']
                game = next((item for item in (latest_inventory or {}).get('games', [])
                             if item.get('gameId') == task.get('gameId')), {})
                if game.get('status') == 'installed':
                    telemetry = None
                else:
                    current = filesystem_bytes(telemetry['path'])
                    total = int(task.get('sizeBytes') or task.get('totalBytes') or game.get('sizeBytes') or 0)
                    if current is not None and telemetry['baseline'] is not None and total > 0:
                        downloaded = max(0, current - telemetry['baseline'])
                        now = time.time()
                        previous = telemetry['previous']
                        speed = 0.0
                        if previous:
                            elapsed = now - previous[0]
                            if elapsed > 0:
                                speed = max(0.0, (downloaded - previous[1]) / elapsed)
                        try:
                            api('/api/tasks/' + task['id'] + '/telemetry', 'POST', {
                                'downloadedBytes': min(downloaded, total), 'totalBytes': total,
                                'speedBytesPerSecond': speed, 'sampledAt': now, 'source': 'filesystem'
                            })
                            telemetry['previous'] = (now, downloaded)
                        except Exception as error:
                            print('进度上报失败，等待下一次真实样本：', error)
                last_telemetry = time.time()
            if time.time() - last_inventory >= max(10, args.inventory_interval):
                try:
                    folder = args.pcstory_folder or inventory.locate_folder()
                    current = inventory.snapshot(folder)
                    latest_inventory = current
                    api('/api/agents/inventory', 'POST', dict(current, cafeId=args.cafe_id))
                    print('清单上报：%d 个游戏，%d 个下载磁盘' % (len(current['games']), len(current['disks'])))
                except Exception as error:
                    print('清单读取失败，保留上次结果：', error)
                    api('/api/agents/error', 'POST', {'cafeId': args.cafe_id, 'error': str(error)})
                last_inventory = time.time()
            if adapter.process is None and journal.record is None:
                task = api('/api/tasks/next/' + cafe_path)
                if task.get('id'):
                    journal.save({'task': task, 'phase': 'prepared'})
            if journal.record and journal.record['phase'] == 'prepared':
                task = journal.record['task']
                try:
                    response = api('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'})
                    if response['status'] != 'accepted':
                        journal.clear()
                    else:
                        journal.save(dict(journal.record, phase='executing'))
                        try:
                            adapter.start(task)
                        except Exception as error:
                            print('下载命令失败：', error)
                            journal.save({'task': task, 'phase': 'result', 'status': 'failed'})
                except urllib.error.HTTPError as error:
                    if error.code == 409:
                        journal.save({'task': task, 'phase': 'result', 'status': 'failed'})
                    else:
                        raise
            time.sleep(2)
        except Exception as error:
            print('服务器连接失败，自动重试：', error)
            registered = False
            time.sleep(5)


if __name__ == '__main__':
    main()
