import json
import os
import threading
import urllib.parse
import time

from progress import WindowsListReader


class ProgressReporter:
    def __init__(self, api, cafe_id, interval=1, reader=None, diagnostic_path='pcstory-progress.json'):
        self.api = api
        self.cafe_id = cafe_id
        self.interval = interval
        self.reader = reader
        self.diagnostic_path = diagnostic_path
        self.stopped = threading.Event()
        self.last_error = None
        self.diagnostic = {}
        self.thread = threading.Thread(target=self.run, name='pcstory-progress', daemon=True)

    def save_diagnostic(self, result):
        path = self.diagnostic_path
        with open(path + '.tmp', 'w', encoding='utf-8') as file:
            json.dump(dict(result, capturedAt=time.time()), file, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)

    def poll(self):
        self.diagnostic = {}
        response = self.api('/api/tasks/active/' + urllib.parse.quote(self.cafe_id, safe=''))
        tasks = response['tasks']
        if not tasks:
            return
        if self.reader is None:
            self.reader = WindowsListReader()
        result = self.reader.read([task['gameId'] for task in tasks])
        self.diagnostic = result
        self.save_diagnostic(result)
        samples = {sample['gameId']: sample for sample in result['samples']}
        missing = []
        for task in tasks:
            sample = samples.get(task['gameId'])
            if sample is None and task['gameId'] in result.get('absentGameIds', []):
                sample = {'gameId': task['gameId'], 'source': 'pcstory-listview', 'sampledAt': time.time(),
                          'downloadState': 'absent', 'progress': None, 'listFields': []}
            if sample is None:
                missing.append(str(task['gameId']))
                continue
            self.api('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample)
        if missing:
            raise ValueError('PCStory 下载列表未返回 GID ' + ','.join(missing) + '；详情已写入 pcstory-progress.json')
        if self.last_error:
            print('PCStory 实时进度采集已恢复')
        self.last_error = ''

    def run(self):
        while not self.stopped.is_set():
            started = time.monotonic()
            try:
                self.poll()
            except Exception as error:
                message = str(error)
                if message != self.last_error:
                    print('PCStory 实时进度采集失败：', message)
                    self.last_error = message
                try:
                    self.api('/api/agents/progress-error', 'POST', {'cafeId': self.cafe_id, 'error': message})
                except Exception:
                    pass
                try:
                    self.save_diagnostic(dict(self.diagnostic, error=message))
                except OSError:
                    pass
            self.stopped.wait(max(0, self.interval - (time.monotonic() - started)))

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=16)
