import time
import unittest

import test_integration


class ControlTests(unittest.TestCase):
    setUp = test_integration.IntegrationTests.setUp
    tearDown = test_integration.IntegrationTests.tearDown
    call = test_integration.IntegrationTests.call
    provision = test_integration.IntegrationTests.provision
    report = test_integration.IntegrationTests.report
    def active_task(self):
        cafe = self.provision()
        self.report(cafe)
        task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        return cafe, task

    def sample(self, state='downloading'):
        return {'source': 'pcstory-listview', 'gameId': 5131, 'downloadState': state,
                'progress': .0297, 'remainingBytes': 60469, 'speedBytesPerSecond': 1024,
                'updateBytes': 62320, 'sampledAt': time.time(),
                'listFields': [{'title': '状态', 'value': '暂停下载' if state == 'paused' else '正在下载'},
                               {'title': '速度(KB/S)', 'value': '1.00'}]}

    def test_full_fields_and_paused_state_allow_control(self):
        cafe, task = self.active_task()
        current = self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', self.sample('paused'), token=cafe['agentToken'])
        self.assertEqual(current['listFields'][1]['value'], '1.00')
        self.assertEqual(current['updateBytes'], 62320)
        self.assertEqual(current['downloadState'], 'paused')
        command = self.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': 'resume'}, expected=201)
        self.assertEqual(command['action'], 'resume')
        self.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': 'resume'}, expected=409)
        other = self.provision('other')
        self.call('/api/controls/next/' + cafe['id'], token=other['agentToken'], expected=403)
        claimed = self.call('/api/controls/next/' + cafe['id'], token=cafe['agentToken'])
        self.assertEqual(claimed['id'], command['id'])
        self.call('/api/controls/' + command['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/controls/next/' + cafe['id'], token=cafe['agentToken']), {})
        self.call('/api/controls/' + command['id'] + '/status', 'POST', {'status': 'confirmed', 'sample': self.sample()}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['tasks'][0]['downloadState'], 'downloading')

    def test_missing_samples_unlock_only_after_repeated_valid_observation(self):
        cafe, task = self.active_task()
        endpoint = '/api/tasks/' + task['id'] + '/telemetry'
        self.call(endpoint, 'POST', self.sample(), token=cafe['agentToken'])
        missing = dict(self.sample('absent'), progress=None, remainingBytes=None, speedBytesPerSecond=None, listFields=[])
        first = self.call(endpoint, 'POST', missing, token=cafe['agentToken'])
        self.assertNotEqual(first['status'], 'removed')
        for index in range(2):
            missing['sampledAt'] = time.time()
            import server
            server.tasks[task['id']]['absenceSince'] = time.time() - 3
            result = self.call(endpoint, 'POST', missing, token=cafe['agentToken'])
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(result['downloadState'], 'removed')
        self.report(cafe)
        replacement = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        self.assertNotEqual(replacement['id'], task['id'])

    def test_control_delete_confirmation_and_history_dismiss(self):
        cafe, task = self.active_task()
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', self.sample(), token=cafe['agentToken'])
        command = self.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': 'remove'}, expected=201)
        self.call('/api/controls/next/' + cafe['id'], token=cafe['agentToken'])
        self.call('/api/controls/' + command['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.call('/api/controls/' + command['id'] + '/status', 'POST', {'status': 'confirmed', 'sample': dict(self.sample('removed'), progress=None)}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/state')['tasks'][0]['status'], 'removed')
        self.call('/api/tasks/' + task['id'] + '/dismiss', 'POST', {})
        self.assertEqual(self.call('/api/state')['tasks'], [])

    def test_stale_progress_does_not_look_running(self):
        cafe, task = self.active_task()
        sample = self.sample()
        sample['sampledAt'] -= 119
        current = self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', sample, token=cafe['agentToken'])
        self.assertFalse(current['progressFresh'])
        self.assertEqual(current['displayState'], 'unknown')
        self.assertIsNone(current['speedBytesPerSecond'])

    def test_removed_game_can_be_started_with_pending_inventory_and_late_result(self):
        cafe, task = self.active_task()
        endpoint = '/api/tasks/' + task['id'] + '/telemetry'
        self.call(endpoint, 'POST', self.sample(), token=cafe['agentToken'])
        missing = dict(self.sample('absent'), progress=None, listFields=[])
        import server
        for index in range(3):
            server.tasks[task['id']]['absenceSince'] = time.time() - 3
            self.call(endpoint, 'POST', missing, token=cafe['agentToken'])
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'pending'}], 'disks': [{'path': 'D:\\', 'totalBytes': 10000, 'freeBytes': 9000, 'downloadDisk': True}]}, token=cafe['agentToken'])
        next_task = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        claimed = self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.assertEqual(claimed['id'], next_task['id'])
        self.call('/api/tasks/' + next_task['id'] + '/status', 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        result = self.call('/api/tasks/' + task['id'] + '/status', 'POST', {'status': 'downloading'}, token=cafe['agentToken'])
        self.assertEqual(result['status'], 'removed')

    def test_missing_report_does_not_remove_just_started_task_or_unhide_running_gid(self):
        cafe, task = self.active_task()
        missing = dict(self.sample('absent'), progress=None, listFields=[])
        endpoint = '/api/tasks/' + task['id'] + '/telemetry'
        for index in range(3):
            current = self.call(endpoint, 'POST', missing, token=cafe['agentToken'])
        self.assertEqual(current['status'], 'downloading')
        self.call('/api/tasks/' + task['id'] + '/dismiss', 'POST', {}, expected=409)
        self.call(endpoint, 'POST', self.sample(), token=cafe['agentToken'])
        self.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': 'resume'}, expected=409)

    def test_removed_evidence_keeps_refreshing_while_replacement_is_queued(self):
        cafe, task = self.active_task()
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', self.sample(), token=cafe['agentToken'])
        import server
        missing = dict(self.sample('absent'), progress=None, listFields=[])
        for index in range(3):
            server.tasks[task['id']]['absenceSince'] = time.time() - 3
            self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', missing, token=cafe['agentToken'])
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': [{'gameId': 5131, 'name': 'Roblox', 'status': 'pending'}], 'disks': [{'path': 'D:\\', 'totalBytes': 10000, 'freeBytes': 9000, 'downloadDisk': True}]}, token=cafe['agentToken'])
        replacement = self.call('/api/tasks', 'POST', {'cafeId': cafe['id'], 'gameId': 5131}, expected=201)
        watched = self.call('/api/tasks/active/' + cafe['id'], token=cafe['agentToken'])
        self.assertIn(task['id'], [item['id'] for item in watched['tasks']])
        server.tasks[task['id']]['sampledAt'] -= 9
        missing['sampledAt'] = time.time()
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', missing, token=cafe['agentToken'])
        claimed = self.call('/api/tasks/next/' + cafe['id'], token=cafe['agentToken'])
        self.assertEqual(claimed['id'], replacement['id'])
        endpoint = '/api/tasks/' + replacement['id'] + '/status'
        self.call(endpoint, 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        server.tasks[task['id']]['sampledAt'] -= 9
        accepted_retry = self.call(endpoint, 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        self.assertEqual(accepted_retry['status'], 'accepted')

    def test_old_confirmation_finishes_operation_without_overwriting_current_progress(self):
        cafe, task = self.active_task()
        self.call('/api/tasks/' + task['id'] + '/telemetry', 'POST', self.sample(), token=cafe['agentToken'])
        command = self.call('/api/tasks/' + task['id'] + '/control', 'POST', {'action': 'pause'}, expected=201)
        self.call('/api/controls/next/' + cafe['id'], token=cafe['agentToken'])
        endpoint = '/api/controls/' + command['id'] + '/status'
        self.call(endpoint, 'POST', {'status': 'accepted'}, token=cafe['agentToken'])
        old = self.sample('paused')
        old['sampledAt'] -= 121
        import server
        server.controls[command['id']]['createdAt'] -= 122
        result = self.call(endpoint, 'POST', {'status': 'confirmed', 'sample': old}, token=cafe['agentToken'])
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(self.call('/api/state')['tasks'][0]['downloadState'], 'downloading')
        self.assertEqual(result['confirmationSampledAt'], old['sampledAt'])
