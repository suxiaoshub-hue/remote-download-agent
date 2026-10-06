import os
import io
import tempfile
import unittest

import run_agent_config
from unittest.mock import patch


class ConfigTests(unittest.TestCase):
    def test_console_handles_chinese_when_windows_default_is_ascii(self):
        from runtime import configure_console
        buffer = io.BytesIO()
        output = io.TextIOWrapper(buffer, encoding='cp1252')
        with patch('sys.stdout', output):
            configure_console()
            print('服务已启动')
        output.flush()
        self.assertEqual(buffer.getvalue().decode('utf-8').strip(), '服务已启动')

    def test_relative_adapter_resolves_against_config_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {'cafeId': 'test', 'server': 'http://127.0.0.1:8765', 'agentToken': 'a-secret'}
            arguments = run_agent_config.agent_arguments(config, folder)
            self.assertEqual(arguments[arguments.index('--adapter') + 1], os.path.join(folder, 'PcstoryAdapter.exe'))
            self.assertIn('--agent-token', arguments)
            self.assertNotIn('--inventory-file', arguments)

    def test_missing_credentials_fails_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, 'agentToken'):
            run_agent_config.agent_arguments({'cafeId': 'test', 'server': 'http://example.test'}, '.')

    def test_rejects_invalid_server_scheme(self):
        with self.assertRaisesRegex(ValueError, 'http'):
            run_agent_config.agent_arguments({'cafeId': 'test', 'server': 'file:///test', 'agentToken': 'secret'}, '.')
