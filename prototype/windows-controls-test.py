import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from control_worker import ControlWorker
from progress import WindowsListReader
from test_controls import ControlTests


def main():
    executable = Path(sys.argv[1]).resolve()
    case = ControlTests('test_control_delete_confirmation_and_history_dismiss')
    case.setUp()
    with tempfile.TemporaryDirectory() as folder:
        ready = Path(folder) / 'ready.txt'
        fixture = subprocess.Popen([str(executable), str(ready)])
        try:
            deadline = time.monotonic() + 10
            while not ready.exists():
                if fixture.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Control fixture did not start')
                time.sleep(.05)
            window = int(ready.read_text())
            native = WindowsListReader()

            class FixtureReader:
                def read(self, game_ids, action=None):
                    return native.read(game_ids, window=window, test_executable=str(executable), action=action)

            reader = FixtureReader()
            cafe, task = case.active_task()

            def api(path, method='GET', payload=None):
                return case.call(path, method, payload, token=cafe['agentToken'])

            api('/api/tasks/' + task['id'] + '/telemetry', 'POST', reader.read([5131])['samples'][0])
            worker = ControlWorker(api, cafe['id'], reader=reader, journal_path=os.path.join(folder, 'agent-control.json'))
            for action, expected in [('pause', 'paused'), ('resume', 'downloading'), ('remove', 'removed')]:
                command = case.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': action}, expected=201)
                worker.poll()
                current = case.call('/api/state')['tasks'][0]
                assert current['downloadState'] == expected, current
                assert current['control']['status'] == 'confirmed', current
                assert current['control']['id'] == command['id']
                assert reader.read([9999])['samples'][0]['progress'] == .12
                assert reader.read([9999])['samples'][0]['downloadState'] == 'downloading'
                assert not os.path.exists(worker.journal_path)
            result = reader.read([5131])
            assert result['absentGameIds'] == [5131], result
            assert result['lists'][0]['rows'] == 1, result
            case.report(cafe)
            replacement = case.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
            assert replacement['id'] != task['id']
            print('PASS native GID pause/resume/remove -> durable control worker -> authenticated server; unrelated selected GID unchanged; deletion unlocks new download')
        finally:
            fixture.terminate()
            fixture.wait(timeout=10)
            case.tearDown()


if __name__ == '__main__':
    main()
