import io
import os
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest.mock import Mock

from control_worker import ControlWorker


class WorkerTests(unittest.TestCase):
    def command(self):
        return {'id': 'control-id', 'cafeId': 'cafe', 'gameId': 5131, 'action': 'pause'}

    def test_restart_marks_executing_uncertain_without_resending(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'agent-control.json')
            reader, api = Mock(), Mock(return_value={'status': 'uncertain'})
            first = ControlWorker(api, 'cafe', reader=reader, journal_path=path)
            first.save({'command': self.command(), 'phase': 'executing'})
            restored = ControlWorker(api, 'cafe', reader=reader, journal_path=path)
            with redirect_stdout(io.StringIO()):
                restored.poll()
            reader.read.assert_not_called()
            self.assertEqual(api.call_args[0][2]['status'], 'uncertain')
            self.assertFalse(os.path.exists(path))

    def test_result_upload_retry_never_repeats_native_operation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'agent-control.json')
            reader = Mock()
            sample = {'gameId': 5131, 'downloadState': 'paused', 'sampledAt': time.time()}
            reader.read.side_effect = [{'samples': [dict(sample, downloadState='downloading')]}, {}, {'samples': [sample]}]
            uploaded = []

            def api(endpoint, method='GET', payload=None):
                if endpoint.startswith('/api/controls/next/'):
                    return self.command()
                if payload['status'] == 'accepted':
                    return {'status': 'accepted'}
                uploaded.append(payload)
                if len(uploaded) == 1:
                    raise urllib.error.URLError('offline')
                return {'status': 'confirmed'}

            worker = ControlWorker(api, 'cafe', reader=reader, journal_path=path)
            with self.assertRaises(urllib.error.URLError):
                worker.poll()
            self.assertEqual(worker.record['phase'], 'result')
            with redirect_stdout(io.StringIO()):
                worker.poll()
            reader.read.assert_any_call([5131], action='pause')
            self.assertEqual(reader.read.call_count, 3)
            self.assertEqual(len(uploaded), 2)
            self.assertFalse(os.path.exists(path))
