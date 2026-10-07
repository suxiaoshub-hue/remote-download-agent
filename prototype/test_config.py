import os
import io
import tempfile
import unittest

import run_agent_config
from unittest.mock import patch
from unittest.mock import Mock


class ConfigTests(unittest.TestCase):
    def test_console_io_failure_does_not_stop_log_or_startup(self):
        console = Mock()
        console.write.side_effect = OSError('legacy console write failed')
        console.flush.side_effect = OSError('legacy console flush failed')
        log_buffer = io.StringIO()
        output = run_agent_config.OutputLog(console, log_buffer)
        output.write('Agent 启动\n')
        output.flush()
        output.write('启动失败：请检查配置\n')
        self.assertEqual(log_buffer.getvalue(), 'Agent 启动\n启动失败：请检查配置\n')

    def test_native_console_write_preserves_chinese_and_utf16_length(self):
        from runtime import WindowsConsoleOutput
        writes = []
        native = Mock()

        def write(handle, buffer, length, written, reserved):
            writes.append((buffer.value, length))
            written._obj.value = length
            return True

        native.WriteConsoleW.side_effect = write
        console = WindowsConsoleOutput(Mock(), native, 123)
        message = '中文启动 🎮\n'
        self.assertEqual(console.write(message), len(message))
        self.assertEqual(writes, [(message, len(message.encode('utf-16-le')) // 2)])

    def test_output_log_does_not_crash_on_legacy_console_encoding(self):
        from run_agent_config import OutputLog
        console = io.TextIOWrapper(io.BytesIO(), encoding='ascii')
        log_buffer = io.StringIO()
        output = OutputLog(console, log_buffer)
        output.write('启动失败：配置错误\n')
        self.assertIn('启动失败：配置错误', log_buffer.getvalue())

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
