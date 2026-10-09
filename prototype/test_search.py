import unittest
from urllib.parse import quote

import test_integration


class SearchTests(unittest.TestCase):
    setUp = test_integration.IntegrationTests.setUp
    tearDown = test_integration.IntegrationTests.tearDown
    call = test_integration.IntegrationTests.call
    provision = test_integration.IntegrationTests.provision

    def test_chinese_initials_full_pinyin_english_and_gid(self):
        cafe = self.provision()
        games = [{'gameId': 100, 'name': '红色警戒2', 'status': 'not_installed'},
                 {'gameId': 101, 'name': '红色警戒3', 'status': 'installed'},
                 {'gameId': 102, 'name': 'Roblox', 'status': 'not_installed'},
                 {'gameId': 103, 'name': 'CSGO反恐精英', 'status': 'installed'},
                 {'gameId': 104, 'name': '英雄联盟', 'status': 'not_installed'}]
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': games, 'complete': True}, token=cafe['agentToken'])
        expected = {'hs': {100, 101}, 'HS': {100, 101}, 'hsjj': {100, 101},
                    'hongse': {100, 101}, 'hong se': {100, 101}, '红色': {100, 101},
                    'rOb': {102}, 'csg': {103}, 'fkjy': {103}, '101': {101},
                    'yxlm': {104}, '没有这个游戏': set()}
        for query, matched in expected.items():
            with self.subTest(query=query):
                result = self.call('/api/cafes/' + cafe['id'] + '/inventory?query=' + quote(query))
                self.assertEqual({game['gameId'] for game in result['games']}, matched)

    def test_renamed_game_updates_search_and_catalog_matches(self):
        cafe = self.provision()
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': [{'gameId': 100, 'name': '红色警戒', 'status': 'not_installed'}]}, token=cafe['agentToken'])
        self.assertTrue(self.call('/api/cafes/' + cafe['id'] + '/inventory?query=hs')['games'])
        self.call('/api/agents/inventory', 'POST', {'cafeId': cafe['id'], 'games': [{'gameId': 100, 'name': '英雄联盟', 'status': 'not_installed'}]}, token=cafe['agentToken'])
        self.assertEqual(self.call('/api/cafes/' + cafe['id'] + '/inventory?query=hs')['games'], [])
        other = self.provision('其他网吧')
        self.assertEqual(self.call('/api/cafes/' + other['id'] + '/inventory?query=yxlm')['games'][0]['gameId'], 100)
