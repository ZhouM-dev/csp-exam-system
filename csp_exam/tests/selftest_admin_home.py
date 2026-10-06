"""比赛列表分页、筛选、新建入口与权限回归；使用内存样本，不写比赛数据。"""
import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from csp_exam.web.admin_pages import AdminPages
from csp_exam.web.server import Handler
from csp_exam.core import store


class PageHarness(AdminPages):
    allowed = True
    status = 200

    def _check_admin(self, key):
        return self.allowed

    def _send(self, body, status=200, cookie=""):
        self.body = body.decode('utf-8') if isinstance(body, bytes) else body
        self.status = status

    def _flash(self, kind, text):
        return text


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.page = PageHarness()
        self.contests = [dict(id=f'c{i}', title=f'Contest-{i:02}',
                              created_at=f'2026-10-{i:02} 12:00:00', rule='OI')
                         for i in range(1, 24)]
        self.groups = [dict(gid='g1', name='Group A', students=['A']),
                       dict(gid='g2', name='Group B', students=['B'])]
        mocks = [patch.object(store, 'list_contests', side_effect=lambda: self.contests),
                 patch.object(store, 'load_groups', return_value=self.groups),
                 patch.object(store, 'load_roster', side_effect=lambda cid: {
                     'k': {'name': 'A' if int(cid[1:]) % 2 else 'B'}}),
                 patch.object(store, 'load_results', return_value={}),
                 patch.object(store, 'load_exam', return_value={})]
        for mock in mocks:
            mock.start()
            self.addCleanup(mock.stop)

    def render(self, **query):
        self.page._admin_home(query)
        return self.page.body

    def titles(self, text):
        return re.findall(r'<b>(Contest-\d+)</b>', text)

    def test_order_and_page_boundaries(self):
        first = self.titles(self.render())
        second = self.titles(self.render(page='2'))
        last = self.titles(self.render(page='999'))
        self.assertEqual(first, [f'Contest-{i:02}' for i in range(23, 13, -1)])
        self.assertEqual(second, [f'Contest-{i:02}' for i in range(13, 3, -1)])
        self.assertEqual(last, ['Contest-03', 'Contest-02', 'Contest-01'])
        self.assertEqual(len(set(first + second + last)), 23)
        for invalid in ('x', '-1', '0', None):
            self.assertEqual(self.titles(self.render(page=invalid)), first)

    def test_filter_before_pagination_and_preserved_links(self):
        body = self.render(g_g1='1', key='test&key')
        self.assertEqual(self.titles(body), [f'Contest-{i:02}' for i in range(23, 3, -2)])
        self.assertIn('page=2&amp;key=test%26key&amp;g_g1=1', body)
        self.assertIn('共 12 场 · 每页 10 场', body)
        self.assertEqual(self.titles(self.render(g_g1='1', page='2')), ['Contest-03', 'Contest-01'])
        self.assertEqual(len(self.titles(self.render(g_g1='1', g_g2='1'))), 10)

    def test_empty_and_unmatched(self):
        body = self.render(g__none='1')
        self.assertEqual(self.titles(body), [])
        self.assertIn('没有符合条件的比赛', body)
        self.contests.clear()
        body = self.render(page='20')
        self.assertIn('还没有比赛', body)
        self.assertIn('第 1 / 1 页', body)

    def test_creation_page_is_separate_and_does_not_create(self):
        body = self.render()
        self.assertIn('href="/admin/new"', body)
        self.assertNotIn('name="title"', body)
        with patch.object(store, 'create_contest') as create:
            self.page._admin_new_page({})
            create.assert_not_called()
        for field in ('title', 'rule', 'level', 'duration'):
            self.assertIn(f'name="{field}"', self.page.body)
        self.assertIn('action="/admin/new"', self.page.body)

    def test_creation_page_auth_and_route(self):
        self.page.allowed = False
        self.page._admin_new_page({})
        self.assertEqual(self.page.status, 403)
        handler = Handler.__new__(Handler)
        handler.path = '/admin/new'
        handler._query = lambda: {}
        with patch.object(handler, '_admin_new_page') as render:
            handler._route_get()
            render.assert_called_once_with({})


if __name__ == '__main__':
    unittest.main()
