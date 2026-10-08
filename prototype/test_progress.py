import unittest

import progress


class ProgressTests(unittest.TestCase):
    def test_download_row_uses_percent_and_remaining_not_catalog_size(self):
        headers = ['ID', '游戏名', '状态', '进度', '更新量(MB)', '剩余(MB)', '速度(KB/s)']
        sample = progress.parse_row(headers, ['8517', '地球帝国1', '正在下载', '2.97%', '623.20', '604.69', '206.00'])
        self.assertEqual(sample['gameId'], 8517)
        self.assertAlmostEqual(sample['progress'], 0.0297)
        self.assertEqual(sample['downloadState'], 'downloading')
        self.assertEqual(sample['remainingBytes'], round(604.69 * 1048576))
        self.assertEqual(sample['speedBytesPerSecond'], 206 * 1024)

    def test_paused_row_keeps_observed_zero_with_no_speed(self):
        sample = progress.parse_row(['ID', '状态', '进度', '剩余(MB)', '速度(KB/s)'], ['5131', '暂停下载', '0.00%', '', ''])
        self.assertEqual(sample['downloadState'], 'paused')
        self.assertEqual(sample['progress'], 0)
        self.assertIsNone(sample['remainingBytes'])
        self.assertIsNone(sample['speedBytesPerSecond'])

    def test_blank_progress_is_unknown_and_invalid_row_is_ignored(self):
        self.assertIsNone(progress.parse_row(['ID', '状态', '进度'], ['5131', '校验中', ''])['progress'])
        self.assertIsNone(progress.parse_row(['ID', '状态', '进度'], ['总计', '', '']))
        self.assertIsNone(progress.parse_row(['ID', '游戏名'], ['5131', 'Roblox']))
        self.assertIsNone(progress.parse_row(['ID', '状态', '进度'], ['5131', '正在下载', '101%']))

    def test_speed_unit_must_be_explicit_and_unknown_state_is_not_running(self):
        sample = progress.parse_row(['ID', '状态', '进度', '剩余(MB)', '速度'], ['5131', '未知状态', '50%', '100', '12'])
        self.assertIsNone(sample['speedBytesPerSecond'])
        self.assertEqual(sample['downloadState'], 'unknown')
        sample = progress.parse_row(['ID', '状态', '进度', '速度'], ['5131', '正在下载', '50%', '12.5 MB/s'])
        self.assertEqual(sample['speedBytesPerSecond'], 12.5 * 1048576)

    def test_duplicate_or_partial_columns_are_not_mapped_by_position(self):
        self.assertIsNone(progress.parse_row(['ID', '状态', '进度', '进度'], ['5131', '正在下载', '50%', '25%']))
        self.assertIsNone(progress.parse_row(['ID', '状态', '进度'], ['5131', '正在下载']))
