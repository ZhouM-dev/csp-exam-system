"""排行榜 HTTP 回归：公开开关、条件请求、鉴权与提交定位。只用临时数据。"""

import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.parse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import security, store, scoreboard as core_scoreboard
from csp_exam.web import scoreboard
from csp_exam.web.server import Handler


class Scoreboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="csp-scoreboard-")
        cls.data_patch = patch.object(store, "DATA_DIR", cls.temp.name)
        cls.log_patch = patch.object(Handler, "log_message", lambda *a: None)
        cls.data_patch.start()
        cls.log_patch.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.log_patch.stop()
        cls.data_patch.stop()
        cls.temp.cleanup()

    def setUp(self):
        self.cid, self.kh = "board", "GD-S12345"
        self.problems = [{"no": 1, "pid": "A", "name": "candy", "full": 100},
                         {"no": 7, "pid": "B", "name": "seat", "full": 50}]
        self.exam = {"problems": self.problems, "released": True}
        store._save(store._path("contests.json"), [{"id": self.cid, "title": "测试比赛", "rule": "CSP", "open": True}])
        store.save_exam(self.cid, self.exam)
        store._save(store._path("contests", self.cid, "roster.json"),
                    {self.kh: {"name": "甲"}, "GD-S23456": {"name": "乙"},
                     "GD-S34567": {"name": "丙"}, "GD-S45678": {"name": "未交"}})
        store._save(store._path("contests", self.cid, "results.json"), {
            self.kh: {"submitted_at": "x", "problems": {"T1": {"score": 50}, "T7": {"score": 25}}, "total": 75},
            "GD-S23456": {"submitted_at": "x", "problems": {"T1": {"score": 75}}, "total": 75},
            "GD-S34567": {"submitted_at": "x", "problems": {"T1": {"score": 20}}, "total": 20}})
        self.admin = security.ADMIN_COOKIE + "=" + security.make_admin_cookie()

    def get(self, path="/api/scoreboard?c=board", *, admin=False, etag="", form=None, cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Cookie": cookie if cookie is not None else self.admin if admin else "", "If-None-Match": etag}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request("POST" if form is not None else "GET", path,
                     body=urllib.parse.urlencode(form) if form is not None else None, headers=headers)
        response = conn.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return status, headers, body

    def test_ties_and_noncontiguous_problem_numbers(self):
        status, _, data = self.get()
        state = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual([r["rank"] for r in state["rows"][:3]], [1, 1, 3])
        self.assertEqual([p["no"] for p in state["problems"]], [1, 7])
        self.assertTrue(all("kaohao" not in r and "state" not in r for r in state["rows"]))

    def test_zero_feedback_in_api_and_html(self):
        store.save_exam(self.cid, {**self.exam, "released": False})
        _, _, data = self.get()
        state = json.loads(data)
        self.assertFalse(state["released"])
        self.assertNotIn("rows", state)
        self.assertNotIn("problems", state)
        _, _, data = self.get("/scoreboard?c=board")
        self.assertNotIn("甲", data.decode())
        self.assertNotIn("candy", data.decode())

    def test_private_authorization_before_conditional_response(self):
        path = "/api/scoreboard?c=board&private=1"
        status, headers, _ = self.get(path, admin=True)
        self.assertEqual(status, 200)
        status, _, data = self.get(path, etag=headers["ETag"])
        self.assertEqual(status, 403)
        self.assertNotIn(self.kh.encode(), data)
        status, _, _ = self.get("/admin/scoreboard?c=board")
        self.assertEqual(status, 403)

    def test_unchanged_projection_returns_304(self):
        _, headers, _ = self.get()
        status, second_headers, data = self.get(etag=headers["ETag"])
        self.assertEqual(status, 304)
        self.assertEqual(data, b"")
        self.assertEqual(second_headers["ETag"], headers["ETag"])
        self.assertEqual(second_headers["Cache-Control"], "no-store")

    def test_withdrawal_never_reuses_published_response(self):
        _, headers, _ = self.get()
        store.save_exam(self.cid, {**self.exam, "released": False})
        status, new_headers, data = self.get(etag=headers["ETag"])
        self.assertEqual(status, 200)
        self.assertNotEqual(headers["ETag"], new_headers["ETag"])
        self.assertNotIn("rows", json.loads(data))

    def test_teacher_tag_cannot_select_teacher_projection_publicly(self):
        _, headers, _ = self.get("/api/scoreboard?c=board&private=1", admin=True)
        status, _, data = self.get(etag=headers["ETag"])
        self.assertEqual(status, 200)
        self.assertNotIn(self.kh.encode(), data)

    def test_changed_score_invalidates_tag(self):
        _, headers, _ = self.get()
        store.put_result(self.cid, self.kh, {"submitted_at": "new", "problems": {"T1": {"score": 100}}})
        status, _, data = self.get(etag=headers["ETag"])
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["rows"][0]["total"], 100)

    def test_single_title_server_render_and_role_links(self):
        _, _, data = self.get("/scoreboard?c=board")
        text = data.decode()
        self.assertEqual(text.count("<h1 "), 1)
        self.assertIn("甲</span>", text)
        self.assertNotIn('href="/admin/student?', text)
        self.assertNotIn('href="/admin/scoreboard?', text)
        self.assertIn('href="/admin/login?', text)
        self.assertNotIn("判题由本机", text)
        self.assertNotIn("不使用 ICPC", text)
        _, _, data = self.get("/scoreboard?c=board", admin=True)
        self.assertIn('href="/admin/student?c=board&amp;k=GD-S12345#T7"', data.decode())
        self.assertIn('private=1', data.decode())

    def test_teacher_can_preview_public_projection(self):
        _, _, data = self.get("/scoreboard?c=board&view=public", admin=True)
        self.assertNotIn('href="/admin/student?', data.decode())
        self.assertIn('href="/admin/scoreboard?', data.decode())
        _, _, data = self.get(admin=True)
        self.assertNotIn(self.kh.encode(), data)

    def test_old_scores_route_and_admin_navigation_share_board(self):
        status, _, data = self.get("/admin/scores?c=board", admin=True)
        self.assertEqual(status, 200)
        self.assertIn('id="board-config"', data.decode())
        self.assertIn('k=GD-S12345#T7', data.decode())
        _, _, data = self.get("/admin?c=board", admin=True)
        self.assertIn('href="/admin/scoreboard?c=board"', data.decode())
        self.assertNotIn('href="/admin/scores?', data.decode())
        status, _, data = self.get("/admin/scores?c=board")
        self.assertEqual(status, 403)
        self.assertIn('action="/admin/login"', data.decode())
        self.assertIn('/admin/scores?c=board', data.decode())

    def test_login_form_bad_key_and_signed_cookie_returns_to_clickable_board(self):
        status, _, data = self.get("/admin/login?c=board")
        self.assertEqual(status, 200)
        self.assertIn('type="password"', data.decode())
        status, headers, data = self.get("/admin/login", form={"key": "wrong", "next": "/scoreboard?c=board"})
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)
        self.assertIn("管理密钥不正确", data.decode())
        status, headers, _ = self.get("/admin/login", form={"key": store.admin_key(), "next": "/scoreboard?c=board"})
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/scoreboard?c=board")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, data = self.get(headers["Location"], cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('k=GD-S12345#T7', data.decode())
        status, headers, _ = self.get("/admin/login?next=%2Fscoreboard%3Fc%3Dboard", cookie=cookie)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/scoreboard?c=board")

    def test_login_return_cannot_redirect_offsite_or_disclose_key(self):
        bad = ["https://example.com", "//example.com", "/\\example.com", "/admin/%2f%2fexample.com",
               "/admin/login", "/admin\r\nX-Test: 1", "http://[invalid", "/other"]
        for target in bad:
            with self.subTest(target=target):
                status, headers, _ = self.get("/admin/login?c=board", form={"key": store.admin_key(), "next": target})
                self.assertEqual(status, 302)
                self.assertEqual(headers["Location"], "/scoreboard?c=board")
        _, headers, _ = self.get("/admin/login", form={"key": store.admin_key(),
            "next": "/admin/student?c=board&key=secret&k=GD-S12345#T7"})
        self.assertEqual(headers["Location"], "/admin/student?c=board&k=GD-S12345#T7")

    def test_private_links_use_actual_problem_number(self):
        _, _, data = self.get("/admin/scoreboard?c=board", admin=True)
        text = data.decode()
        self.assertIn('href="/admin/student?c=board&amp;k=GD-S12345#T7"', text)
        self.assertNotIn('#T2"', text)
        self.assertNotIn('k=GD-S45678#', text)

    def test_unpublished_teacher_preview_is_explicit(self):
        store.save_exam(self.cid, {**self.exam, "released": False})
        _, _, data = self.get("/api/scoreboard?c=board&private=1", admin=True)
        state = json.loads(data)
        self.assertTrue(state["released"])
        self.assertFalse(state["published"])
        _, _, data = self.get("/admin/scoreboard?c=board", admin=True)
        self.assertIn('class="badge">未公布', data.decode())

    def test_hostile_name_is_text_not_markup(self):
        name = '</script><img src=x onerror=alert(1)>'
        roster = store.load_roster(self.cid)
        roster[self.kh]["name"] = name
        store._save(store._path("contests", self.cid, "roster.json"), roster)
        _, _, data = self.get("/admin/scoreboard?c=board", admin=True)
        text = data.decode()
        self.assertNotIn(name, text)
        self.assertIn('&lt;img', text)
        self.assertIn('\\u003c/script>', text)

    def test_pending_state_and_no_false_final_score(self):
        result = store.load_results(self.cid)[self.kh]
        store.put_result(self.cid, self.kh, {**result, "submission_state": "error"})
        snapshot = core_scoreboard.snapshot(self.cid, private=True)
        student = next(r for r in snapshot["rows"] if r["kaohao"] == self.kh)
        self.assertTrue(student["pending"])
        self.assertIsNone(student["rank"])
        self.assertIn('total waiting">待重测</td>', scoreboard._rows(self.cid, snapshot, True, ""))

    def test_cache_does_not_reread_full_results_or_share_mutable_rows(self):
        with patch.object(store, "load_results", wraps=store.load_results) as load:
            one = core_scoreboard.snapshot(self.cid, private=True)
            one["rows"][0]["total"] = 999
            two = core_scoreboard.snapshot(self.cid, private=True)
            self.assertEqual(two["rows"][0]["total"], 75)
            self.assertEqual(load.call_count, 1)

    def test_roster_and_title_changes_invalidate_projection(self):
        _, headers, _ = self.get()
        roster = store.load_roster(self.cid)
        roster[self.kh]["name"] = "更名"
        store._save(store._path("contests", self.cid, "roster.json"), roster)
        store.update_contest(self.cid, title="新标题")
        status, _, data = self.get(etag=headers["ETag"])
        self.assertEqual(status, 200)
        state = json.loads(data)
        self.assertEqual(state["title"], "新标题")
        self.assertIn("更名", [r["name"] for r in state["rows"]])

    def test_problem_link_reaches_retained_source(self):
        base = Path(store.upload_dir(self.cid, self.kh))
        base.mkdir(parents=True, exist_ok=True)
        (base / "seat.cpp").write_text("int main(){return 0;}")
        result = store.load_results(self.cid)[self.kh]
        result["problems"]["T7"]["file"] = "seat.cpp"
        store.put_result(self.cid, self.kh, result)
        status, _, data = self.get("/admin/student?c=board&k=" + self.kh, admin=True)
        self.assertEqual(status, 200)
        text = data.decode()
        self.assertIn('id="T7"', text)
        self.assertIn('class="submission-source"', text)
        self.assertIn('int main(){return 0;}', text)
        self.assertIn('openLinkedSource()', text)
        status, _, data = self.get("/admin/student?c=board&k=" + self.kh)
        self.assertEqual(status, 403)
        self.assertNotIn('int main(){return 0;}', data.decode())
        self.assertIn('action="/admin/login"', data.decode())

    def test_missing_contest_is_404(self):
        status, _, _ = self.get("/api/scoreboard?c=missing")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
