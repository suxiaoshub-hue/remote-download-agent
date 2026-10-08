import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import server


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        server.DB_PATH = os.path.join(self.folder.name, 'test.db')
        server.ADMIN_TOKEN = 'integration-admin-secret'
        server.cafes.clear()
        server.tasks.clear()
        server.inventories.clear()
        if hasattr(server, 'catalog'):
            server.catalog.clear()
        server.load_db()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.folder.cleanup()

    def call(self, path, method='GET', payload=None, token='integration-admin-secret', expected=200):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
        try:
            response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expected)
            return json.loads(response.read())

    def provision(self, name='测试网吧'):
        created = self.call('/api/cafes', 'POST', {'name': name, 'server': self.base}, expected=201)
        self.call('/api/agents/register', 'POST', {'cafeId': created['id'], 'name': 'initial name'}, token=created['agentToken'])
        return created

    def report(self, cafe):
        return self.call('/api/agents/inventory', 'POST', {
            'cafeId': cafe['id'], 'complete': True,
            'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'not_installed', 'sizeBytes': 5000},
                      {'gameId': 8044, 'name': 'CSGO', 'status': 'installed', 'localPath': 'D:\\CSGO'}],
            'disks': [{'path': 'D:\\', 'freeBytes': 9000, 'totalBytes': 10000, 'downloadDisk': True}]
        }, token=cafe['agentToken'])

    def test_auth_required_and_cafe_isolation(self):
        self.call('/api/state', token='', expected=401)
        first = self.provision()
        second = self.provision('第二家网吧')
        self.call('/api/agents/heartbeat', 'POST', {'cafeId': second['id']}, token=first['agentToken'], expected=403)
        self.call('/api/tasks/next/' + second['id'], token=first['agentToken'], expected=403)

    def test_webpage_is_served_before_login(self):
        with urllib.request.urlopen(self.base + '/') as response:
            self.assertEqual(response.status, 200)
            self.assertIn('网吧远程下载'.encode(), response.read())

    def test_cafe_overview_returns_disk_without_full_game_list(self):
        cafe = self.provision()
        self.report(cafe)
        overview = self.call('/api/cafes/' + cafe['id'] + '/overview')
        self.assertTrue(overview['fresh'])
        self.assertEqual(overview['disks'][0]['freeBytes'], 9000)
        self.assertNotIn('games', overview)

    def test_new_inventory_completes_started_task_after_agent_restart(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        server.load_db()
        self.call('/api/agents/inventory', 'POST', {
            'cafeId': cafe['id'], 'complete': True,
            'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'installed', 'localPath': 'F:\\Roblox'}],
            'disks': [{'path': 'F:\\', 'freeBytes': 9000, 'totalBytes': 10000, 'downloadDisk': True}]
        }, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['tasks'][0]['status'], 'completed')
        self.assertEqual(self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken']), {})
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['tasks'][0]['status'], 'completed')

    def test_live_inventory_real_task_and_persistence(self):
        cafe = self.provision()
        self.report(cafe)
        found = self.call('/api/cafes/' + cafe['id'] + '/inventory?query=rob')
        self.assertEqual(found['games'][0]['status'], 'not_installed')
        self.assertEqual(found['disks'][0]['freeBytes'], 9000)
        self.assertTrue(found['fresh'])
        self.call('/api/cafes/' + cafe['id'] + '/rename', 'POST', {'name': '云端名字'})
        self.call('/api/agents/register', 'POST', {'cafeId': cafe['id'], 'name': 'old'}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['cafes'][0]['name'], '云端名字')
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=409)
        self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 8044}, expected=409)
        accepted = self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.assertEqual(accepted['id'], task['id'])
        replay = self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.assertEqual(replay['id'], task['id'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        self.assertIsNone(self.call('/api/state')['tasks'][0]['progress'])
        server.load_db()
        self.assertEqual(server.inventories[cafe['id']]['games'][5131]['name'], 'Roblox')

    def test_task_telemetry_calculates_eta_from_fresh_real_sample(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        result = self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', {
            'progress': 0.4, 'remainingBytes': 3000, 'speedBytesPerSecond': 1000,
            'sampledAt': time.time(), 'source': 'pcstory-listview', 'downloadState': 'downloading'
        }, token=cafe['agentToken'])
        self.assertEqual(result['etaSeconds'], 3)
        self.assertEqual(result['progress'], 0.4)
        self.assertEqual(self.call('/api/state')['tasks'][0]['speedBytesPerSecond'], 1000)
        delayed = self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', {
            'progress': 0.4, 'remainingBytes': 3000, 'speedBytesPerSecond': 1000,
            'sampledAt': time.time() - 119, 'source': 'pcstory-listview', 'downloadState': 'downloading'
        }, token=cafe['agentToken'])
        self.assertFalse(delayed['progressFresh'])
        self.assertIsNone(delayed['etaSeconds'])
        self.assertEqual(delayed['progress'], .4)
        server.tasks[task['id']].update(telemetrySource='filesystem', progress=.8, etaSeconds=5)
        legacy = self.call('/api/state')['tasks'][0]
        self.assertIsNone(legacy['progress'])
        self.assertIsNone(legacy['etaSeconds'])

    def test_task_telemetry_rejects_fake_or_stale_sample(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', {
            'downloadedBytes': 6000, 'totalBytes': 5000, 'speedBytesPerSecond': 1000,
            'sampledAt': time.time(), 'source': 'guess'
        }, token=cafe['agentToken'], expected=400)

    def test_pending_inventory_does_not_invent_pause_or_zero_progress(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        self.call('/api/agents/inventory', 'POST', {
            'cafeId': cafe['id'], 'complete': True,
            'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'pending', 'rawStatus': 2, 'sizeBytes': 5000}],
            'disks': [{'path': 'D:\\', 'freeBytes': 9000, 'totalBytes': 10000, 'downloadDisk': True}]
        }, token=cafe['agentToken'])
        current = self.call('/api/state')['tasks'][0]
        self.assertEqual(current['status'], 'downloading')
        self.assertIsNone(current['progress'])
        self.assertIsNone(current['etaSeconds'])
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', {
            'progress': 0.4, 'remainingBytes': 3000, 'speedBytesPerSecond': 1000,
            'sampledAt': time.time() - 600, 'source': 'pcstory-listview', 'downloadState': 'downloading'
        }, token=cafe['agentToken'], expected=400)

    def test_native_list_sample_survives_restart_and_inventory_then_pauses(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        sample = {'source': 'pcstory-listview', 'downloadState': 'downloading', 'progress': 0.0297,
                  'remainingBytes': 10000, 'speedBytesPerSecond': 1000, 'sampledAt': time.time()}
        current = self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample, token=cafe['agentToken'])
        self.assertAlmostEqual(current['progress'], 0.0297)
        self.assertEqual(current['etaSeconds'], 10)
        server.load_db()
        self.assertEqual(self.call('/api/tasks/active/' + cafe['id'], token=cafe['agentToken'])['tasks'][0]['id'], task['id'])
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'pending'}]}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['tasks'][0]['downloadState'], 'downloading')
        sample.update(downloadState='paused', sampledAt=time.time())
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample, token=cafe['agentToken'])
        current = self.call('/api/state')['tasks'][0]
        self.assertEqual(current['downloadState'], 'paused')
        self.assertIsNone(current['etaSeconds'])
        self.assertAlmostEqual(current['progress'], 0.0297)
        server.tasks[task['id']]['telemetryUpdatedAt'] -= 60
        self.assertFalse(self.call('/api/state')['tasks'][0]['progressFresh'])

    def test_native_sample_is_bound_to_cafe_and_rejects_invalid_values(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        other = self.provision('第二家网吧')
        sample = {'source': 'pcstory-listview', 'downloadState': 'downloading', 'progress': 0.5,
                  'remainingBytes': 10000, 'speedBytesPerSecond': 1000, 'sampledAt': time.time()}
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample, token=other['agentToken'], expected=403)
        for field, value in [('progress', 1.1), ('sampledAt', float('nan')), ('speedBytesPerSecond', float('inf')), ('remainingBytes', -1)]:
            invalid = dict(sample)
            invalid[field] = value
            self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', invalid, token=cafe['agentToken'], expected=400)
        sample.update(progress=1)
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample, token=cafe['agentToken'])
        self.assertNotEqual(self.call('/api/state')['tasks'][0]['status'], 'completed')

    def test_unknown_and_stale_inventory_cannot_start_download(self):
        cafe = self.provision()
        self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=409)
        self.report(cafe)
        server.inventories[cafe['id']]['updatedAt'] = time.time() - 400
        found = self.call('/api/cafes/' + cafe['id'] + '/inventory')
        self.assertFalse(found['fresh'])
        self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=409)

    def test_disk_shortage_and_queued_cancellation(self):
        cafe = self.provision()
        self.report(cafe)
        server.inventories[cafe['id']]['disks'][0]['freeBytes'] = 100
        self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=409)
        server.inventories[cafe['id']]['disks'][0]['freeBytes'] = 9000
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/' + task['id'] + '/cancel', 'POST', {})
        self.assertEqual(self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken']), {})

    def test_accepted_recovery_still_requires_fresh_inventory(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/agents/error', 'POST', {'cafeId': cafe['id'], 'error': '数据库正在写入'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'], expected=409)


if __name__ == '__main__':
    unittest.main()
