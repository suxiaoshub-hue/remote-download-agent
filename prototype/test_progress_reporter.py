import json
import io
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from progress_reporter import ProgressReporter


class ReporterTests(unittest.TestCase):
    def test_valid_absence_is_reported_but_unreadable_gid_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            reader = Mock()
            reader.read.return_value = {'samples': [], 'absentGameIds': [5131], 'lists': [{'headers': ['ID', '状态', '进度'], 'rows': 0}]}
            calls = []

            def api(endpoint, method='GET', payload=None):
                if method == 'GET':
                    return {'tasks': [{'id': 'task', 'gameId': 5131}]}
                calls.append(payload)
                return {}

            reporter = ProgressReporter(api, 'cafe', reader=reader, diagnostic_path=os.path.join(folder, 'progress.json'))
            self.assertEqual(reporter.interval, 1)
            reporter.poll()
            self.assertEqual(calls[0]['downloadState'], 'absent')
            reader.read.return_value = {'samples': [], 'absentGameIds': [], 'lists': []}
            with self.assertRaises(ValueError):
                reporter.poll()
            self.assertEqual(len(calls), 1)

    def test_idle_does_not_open_pcstory(self):
        reporter = ProgressReporter(lambda path: {'tasks': []}, 'cafe')
        with patch('progress_reporter.WindowsListReader') as factory:
            reporter.poll()
        factory.assert_not_called()

    def test_missing_gid_keeps_raw_columns_and_other_tasks_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'pcstory-progress.json')
            reader = Mock()
            reader.read.return_value = {'samples': [{'gameId': 5131, 'progress': .0297}],
                                       'lists': [{'headers': ['ID', '状态', '进度'], 'matchedRows': [['5131', '正在下载', '2.97%']]}]}
            calls = []
            reporter = None

            def api(endpoint, method='GET', payload=None):
                calls.append((endpoint, payload))
                if endpoint.startswith('/api/tasks/active/'):
                    return {'tasks': [{'id': 'first', 'gameId': 5131}, {'id': 'missing', 'gameId': 8517}]}
                if endpoint == '/api/agents/progress-error':
                    reporter.stopped.set()
                return {}

            reporter = ProgressReporter(api, 'cafe', reader=reader, diagnostic_path=path)
            output = io.StringIO()
            with redirect_stdout(output):
                reporter.run()
            self.assertIn('8517', output.getvalue())
            reader.read.assert_called_once_with([5131, 8517])
            self.assertIn('/api/tasks/first/telemetry', [endpoint for endpoint, payload in calls])
            with open(path, encoding='utf-8') as file:
                diagnostic = json.load(file)
            self.assertEqual(diagnostic['lists'][0]['matchedRows'][0][-1], '2.97%')
            self.assertIn('8517', diagnostic['error'])


if __name__ == '__main__':
    unittest.main()
