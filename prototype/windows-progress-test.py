import ctypes
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from progress import WindowsListReader
from progress_reporter import ProgressReporter
from test_integration import IntegrationTests


def main():
    executable = Path(sys.argv[1]).resolve()
    case = IntegrationTests('test_native_list_sample_survives_restart_and_inventory_then_pauses')
    case.setUp()
    with tempfile.TemporaryDirectory() as folder:
        ready = Path(folder) / 'ready.txt'
        fixture = subprocess.Popen([str(executable), str(ready)])
        try:
            deadline = time.time() + 10
            while not ready.exists():
                if fixture.poll() is not None or time.time() > deadline:
                    raise RuntimeError('Progress fixture did not start')
                time.sleep(.05)
            window = int(ready.read_text())
            reader = WindowsListReader()
            try:
                reader.read([5131], window=window)
                raise AssertionError('Production hash guard accepted fixture')
            except ValueError as error:
                assert '版本不匹配' in str(error), error
            result = reader.read([5131], window=window, test_executable=str(executable))
            sample = result['samples'][0]
            assert sample['gameId'] == 5131
            assert sample['progress'] == .0297
            assert sample['remainingBytes'] == round(604.69 * 1048576)
            assert sample['speedBytesPerSecond'] == 1048576
            assert len(result['samples']) == 1
            cafe = case.provision()
            case.report(cafe)
            task = case.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
            case.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
            case.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])

            class FixtureReader:
                def read(self, game_ids):
                    return reader.read(game_ids, window=window, test_executable=str(executable))

            def api(path, method='GET', payload=None):
                return case.call(path, method, payload, token=cafe['agentToken'])

            reporter = ProgressReporter(api, cafe['id'], reader=FixtureReader(), diagnostic_path=os.path.join(folder, 'pcstory-progress.json'))
            reporter.poll()
            current = case.call('/api/state')['tasks'][0]
            assert current['progress'] == .0297, current
            assert current['etaSeconds'] == math.ceil(604.69), current
            response = ctypes.c_size_t()
            assert reader.user.SendMessageTimeoutW(window, 0x8001, 0, 0, 2, 1000, ctypes.byref(response))
            reporter.poll()
            current = case.call('/api/state')['tasks'][0]
            assert current['downloadState'] == 'paused', current
            assert current['progress'] == .1725, current
            assert current['etaSeconds'] is None, current
            assert reader.user.SendMessageTimeoutW(window, 0x8002, 0, 0, 2, 1000, ctypes.byref(response))
            reporter.poll()
            current = case.call('/api/state')['tasks'][0]
            assert current['progress'] is None
            assert current['downloadState'] == 'checking'
            saved = json.loads((Path(folder) / 'pcstory-progress.json').read_text(encoding='utf-8'))
            assert saved['lists'][0]['headers'][1] == 'ID'
            print('PASS native cross-process hidden ListView -> Agent reporter -> HTTP task progress, ETA, paused and unknown states; hash guard rejects fixture')
        finally:
            fixture.terminate()
            fixture.wait(timeout=10)
            case.tearDown()


if __name__ == '__main__':
    main()
