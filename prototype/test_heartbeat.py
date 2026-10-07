import threading
import unittest
from unittest.mock import patch

from agent import Heartbeat


class HeartbeatTests(unittest.TestCase):
    def test_heartbeat_continues_while_main_work_is_blocked(self):
        sent = threading.Event()
        attempted = threading.Event()
        count = []

        def api(path, method, payload):
            count.append(path)
            if len(count) == 1:
                attempted.set()
                raise OSError('temporary network failure')
            sent.set()

        heartbeat = Heartbeat(api, 'cafe-test', interval=.01)
        heartbeat.start()
        try:
            self.assertTrue(attempted.wait(1))
            self.assertTrue(sent.wait(1))
            self.assertTrue(heartbeat.thread.is_alive())
            self.assertEqual(count[-1], '/api/agents/heartbeat')
        finally:
            heartbeat.stop()

    def test_online_tolerates_slow_inventory_but_expires(self):
        import server
        with patch('server.time.time', return_value=1000):
            self.assertTrue(server.online({'lastSeen': 960}))
            self.assertFalse(server.online({'lastSeen': 900}))
