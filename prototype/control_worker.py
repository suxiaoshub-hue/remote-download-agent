import json
import os
import threading
import time
import urllib.error
import urllib.parse

from progress import WindowsListReader


class ControlWorker:
    def __init__(self, api, cafe_id, reader=None, journal_path='agent-control.json'):
        self.api = api
        self.cafe_id = cafe_id
        self.reader = reader
        self.journal_path = journal_path
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name='pcstory-controls', daemon=True)
        self.record = None
        if os.path.exists(journal_path):
            with open(journal_path, encoding='utf-8') as file:
                self.record = json.load(file)
            if self.record['command']['cafeId'] != cafe_id:
                raise ValueError('agent-control.json 属于其他网吧')
            if self.record['phase'] == 'executing':
                self.save(dict(self.record, phase='result', result={'status': 'uncertain', 'error': 'Agent 重启，操作结果待本地状态确认，不自动重发'}))

    def save(self, record):
        with open(self.journal_path + '.tmp', 'w', encoding='utf-8') as file:
            json.dump(record, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(self.journal_path + '.tmp', self.journal_path)
        self.record = record

    def execute(self, command):
        if self.reader is None:
            self.reader = WindowsListReader()
        game_id, action = command['gameId'], command['action']
        before = self.reader.read([game_id])
        sample = next((item for item in before['samples'] if item['gameId'] == game_id), None)
        expected = {'pause': ('paused',), 'resume': ('downloading', 'waiting', 'checking'), 'remove': ('removed',)}[action]
        if sample and sample['downloadState'] in expected:
            return {'status': 'confirmed', 'sample': sample}
        if game_id in before.get('absentGameIds', []):
            if action == 'remove':
                return {'status': 'confirmed', 'sample': {'gameId': game_id, 'downloadState': 'removed', 'source': 'pcstory-listview', 'sampledAt': time.time()}}
            return {'status': 'failed', 'error': 'PCStory 已无此任务，请重新下发下载'}
        allowed = ('paused',) if action == 'resume' else ('downloading', 'waiting', 'checking', 'paused', 'failed')
        if not sample or sample['downloadState'] not in allowed:
            return {'status': 'failed', 'error': '本地任务状态变化，未发送操作'}
        try:
            self.reader.read([game_id], action=action)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline and not self.stopped.is_set():
                result = self.reader.read([game_id])
                sample = next((item for item in result['samples'] if item['gameId'] == game_id), None)
                if sample and sample['downloadState'] in expected:
                    return {'status': 'confirmed', 'sample': sample}
                if action == 'remove' and game_id in result.get('absentGameIds', []):
                    return {'status': 'confirmed', 'sample': {'gameId': game_id, 'downloadState': 'removed', 'source': 'pcstory-listview', 'sampledAt': time.time()}}
                self.stopped.wait(.25)
        except Exception as error:
            return {'status': 'uncertain', 'error': str(error)}
        return {'status': 'uncertain', 'error': '已发送操作，但尚未读取预期状态；请查看 PCStory，不自动重发'}

    def poll(self):
        if self.record is None:
            command = self.api('/api/controls/next/' + urllib.parse.quote(self.cafe_id, safe=''))
            if not command.get('id'):
                return
            self.save({'command': command, 'phase': 'prepared'})
        command = self.record['command']
        endpoint = '/api/controls/' + command['id'] + '/status'
        if self.record['phase'] == 'prepared':
            try:
                accepted = self.api(endpoint, 'POST', {'status': 'accepted'})
            except urllib.error.HTTPError as error:
                if error.code != 409:
                    raise
                self.save(dict(self.record, phase='result', result={'status': 'failed', 'error': '本地状态变化，操作未执行'}))
            else:
                if accepted['status'] == 'accepted':
                    self.save(dict(self.record, phase='executing'))
                    try:
                        result = self.execute(command)
                    except Exception as error:
                        result = {'status': 'uncertain', 'error': str(error)}
                    self.save(dict(self.record, phase='result', result=result))
                else:
                    os.unlink(self.journal_path)
                    self.record = None
                    return
        if self.record['phase'] == 'result':
            self.api(endpoint, 'POST', self.record['result'])
            print('PCStory 操作结果：%s，GID=%s，%s' % (command['action'], command['gameId'], self.record['result']['status']))
            os.unlink(self.journal_path)
            self.record = None

    def run(self):
        while not self.stopped.is_set():
            try:
                self.poll()
            except Exception as error:
                print('PCStory 操作连接失败，稍后重试：', error)
            self.stopped.wait(1)

    def start(self):
        self.thread.start()
