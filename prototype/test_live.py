import hashlib
import base64
import os
import tempfile
import unittest
from unittest.mock import patch

import inventory
from agent import PcstoryAdapter, TaskJournal


class LiveTests(unittest.TestCase):
    def test_journal_recovers_result_but_never_replays_executing_command(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'task.json')
            journal = TaskJournal(path)
            journal.save({'phase': 'prepared', 'task': {'id': 'test', 'gameId': 5131}})
            self.assertEqual(TaskJournal(path).record['phase'], 'prepared')
            journal.save(dict(journal.record, phase='executing'))
            recovered = TaskJournal(path)
            self.assertEqual(recovered.record['phase'], 'result')
            self.assertEqual(recovered.record['status'], 'uncertain')
            recovered.save(dict(recovered.record, status='failed'))
            self.assertEqual(TaskJournal(path).record['status'], 'failed')
            recovered.clear()
            self.assertIsNone(TaskJournal(path).record)

    def test_chacha_known_vector(self):
        expected = '76b8e0ada0f13d90405d6ae55386bd28bdd219b8a08ded1aa836efcc8b770dc7da41597c5157488d7724e03fb8d84a376a43b8f41518a11cc387b669b2ee6586'
        self.assertEqual(inventory.stream_xor(bytes(32), bytes(16), 0, bytes(64)).hex(), expected)

    def test_authenticated_database_snapshot(self):
        with self.assertRaises(ValueError):
            inventory.decode(bytes(4096))

    def test_completed_requires_local_record_and_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch('inventory.os.path.isdir', return_value=True):
                self.assertEqual(inventory.game_status(1, folder), 'installed')
                self.assertEqual(inventory.game_status(2, folder), 'pending')
                self.assertEqual(inventory.game_status(0, folder), 'unknown')
            self.assertEqual(inventory.game_status(1, folder + '-missing'), 'missing')
            self.assertEqual(inventory.game_status(0, ''), 'not_installed')

    def test_pcstory_base64_path_template_is_decoded_for_local_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            encoded = base64.b64encode((folder + os.sep + '*.*').encode()).decode()
            self.assertEqual(inventory.decode_pcstory_path(encoded), folder + os.sep + '*.*')
            with patch('inventory.os.path.isdir', return_value=True):
                self.assertEqual(inventory.game_status(1, inventory.decode_pcstory_path(encoded)), 'installed')

    def test_actual_pcstory_disk_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'config.ini'), 'w') as file:
                file.write('[config]\ndisk=F\n')
            self.assertEqual(inventory.configured_disks(folder), ['F:\\'])

    def test_adapter_reports_start_without_fake_completion(self):
        adapter = PcstoryAdapter('PcstoryAdapter.exe')
        with patch('agent.subprocess.Popen') as start:
            start.return_value.poll.return_value = 0
            adapter.start({'gameId': 5131, 'id': 'task'})
            self.assertEqual(adapter.poll(), 'downloading')
            self.assertNotIn('--force', start.call_args.args[0])
        self.assertEqual(adapter.result_status(2), 'waiting')
        self.assertEqual(adapter.result_status(3), 'failed')
        self.assertEqual(adapter.result_status(5), 'uncertain')

    def test_filesystem_sample_uses_only_an_existing_path(self):
        from agent import filesystem_bytes
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'part.bin'), 'wb') as file:
                file.write(b'x' * 17)
            self.assertEqual(filesystem_bytes(folder), 17)
            self.assertEqual(filesystem_bytes(os.path.join(folder, 'missing')), None)


if __name__ == '__main__':
    unittest.main()
