"""全部教师路由权限矩阵及跨场、越界、成绩失效回归。"""
import json
from pathlib import Path
import sys
import time
import unittest
import urllib.parse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.tests import selftest_problemset_api as fixture
from csp_exam.core import judgelocal, problems, security, store
from csp_exam.web import admin_pages


class APIContract(unittest.TestCase):
    setUp = fixture.ProblemsetAPI.setUp
    tearDown = fixture.ProblemsetAPI.tearDown
    request = fixture.ProblemsetAPI.request

    def contest(self, title="Current-Contest", kh="GD-J12345", name="Current-Student"):
        now = int(time.time())
        cid = store.create_contest(title, "CSP", level="J", start_at=now - 1000, end_at=now - 1)["id"]
        store.save_roster(cid, {kh: {"name": name}})
        if "P" not in judgelocal.known_pids():
            self.assertTrue(problems.create_problem("P", "SECRET-QUESTION", {"1.in": b"2 3\n", "1.out": b"5\n"},
                                                  statement="# SECRET-STATEMENT", std_source="SECRET-SOLUTION", name="sum")["ok"])
        store.save_exam(cid, {"problems": [{"no": 1, "pid": "P", "name": "sum", "full": 100}], "released": False})
        self.result(cid, kh)
        return cid

    def result(self, cid, kh, **fields):
        data = {"submitted_at": "2026-10-11", "submission_state": "done",
                "submission_exam": store.load_exam(cid), "judge_rule": store.rule_of(store.get_contest(cid)),
                "problems": {"T1": {"score": 100, "status": 1, "file": "sum/sum.cpp"}}}
        data.update(fields)
        store.put_result(cid, kh, data)

    def test_all_teacher_mutations_reject_anonymous_and_students(self):
        cid = self.contest()
        student = "csp=" + security.make_cookie(cid, "GD-J12345")
        paths = ["/admin/new", "/admin/delete", "/admin/roster", "/admin/reshuffle", "/admin/groups",
                 "/admin/exam", "/admin/release", "/admin/judge", "/admin/problem", "/admin/statement",
                 "/admin/problem-detail", "/admin/problem-reupload", "/admin/scan", "/admin/selftest", "/api/problemset/import"]
        before = store.list_contests()
        for path in paths:
            for cookie in ("", student):
                with self.subTest(path=path, student=bool(cookie)):
                    self.assertEqual(self.request("POST", path + "?c=" + cid + "&pid=P", {}, cookie=cookie)[0], 403)
        self.assertEqual(store.list_contests(), before)

    def test_all_teacher_reads_hide_sensitive_data(self):
        cid = self.contest()
        paths = ["/admin", "/admin/new", "/admin/groups", "/admin/print", "/admin/scores", "/admin/scoreboard",
                 "/admin/student", "/admin/file", "/admin/problem", "/admin/problems", "/admin/statement", "/admin/problem-detail"]
        for path in paths:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path + "?c=" + cid + "&pid=P&k=GD-J12345", cookie="")
                self.assertIn(status, (200, 302, 403))
                for secret in (b"SECRET-STATEMENT", b"SECRET-SOLUTION", b"Current-Student"):
                    self.assertNotIn(secret, body)
        for path in ("/admin/judge", "/admin/testcase", "/api/problems", "/api/problemset/report", "/api/scoreboard?private=1"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path, cookie="")[0], 403)

    def test_non_ascii_wrong_credentials_are_rejected(self):
        self.assertFalse(security.parse_admin_cookie("错误"))
        self.assertIsNone(security.parse_cookie("c1%3AK.错误"))
        for path in ("/api/problems", "/api/problemset/report"):
            self.assertEqual(self.request("GET", path + "?key=" + urllib.parse.quote("错误"), cookie="")[0], 403)
        self.assertEqual(self.request("POST", "/admin/login", {"key": "错误"}, cookie="")[0], 403)

    def test_same_kaohao_does_not_disclose_other_contest(self):
        a = self.contest()
        b = self.contest("SECRET-OTHER-CONTEST", name="SECRET-OTHER-STUDENT")
        store.set_released(a, True)
        store.set_released(b, True)
        cookie = "csp=" + security.make_cookie(a, "GD-J12345")
        status, _, body = self.request("GET", "/contests", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"Current-Student", body)
        self.assertNotIn(b"SECRET-OTHER", body)

    def test_legacy_file_reference_cannot_read_another_student(self):
        cid = self.contest()
        other = Path(store.upload_dir(cid, "GD-J99999")) / "sum/sum.cpp"
        other.parent.mkdir(parents=True)
        other.write_text("SECRET-OTHER-CODE")
        self.result(cid, "GD-J12345", problems={"T1": {"score": 100, "status": 1, "file": "../GD-J99999/sum/sum.cpp"}})
        store.set_released(cid, True)
        cookie = "csp=" + security.make_cookie(cid, "GD-J12345")
        status, _, body = self.request("GET", "/score?c=" + cid, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn(b"SECRET-OTHER-CODE", body)
        self.assertEqual(self.request("GET", "/file?c=" + cid + "&f=../GD-J99999/sum/sum.cpp", cookie=cookie)[0], 400)

    def test_legacy_own_file_remains_readable(self):
        cid = self.contest()
        own = Path(store.upload_dir(cid, "GD-J12345")) / "sum/sum.cpp"
        own.parent.mkdir(parents=True)
        own.write_text("OWN-CODE-MARKER")
        self.result(cid, "GD-J12345")
        store.set_released(cid, True)
        cookie = "csp=" + security.make_cookie(cid, "GD-J12345")
        self.assertIn(b"OWN-CODE-MARKER", self.request("GET", "/score?c=" + cid, cookie=cookie)[2])

    def test_limits_endpoint_withdraws_scores_and_blocks_inflight_change(self):
        cid = self.contest()
        store.set_released(cid, True)
        path = "/admin/judge?format=json"
        form = {"action": "limits", "pid": "P", "time_ms": "1200", "memory_mb": "256"}
        status, _, body = self.request("POST", path, form)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(judgelocal.limits_of("P"), (1200, 256))
        self.assertFalse(store.load_exam(cid)["released"])
        self.assertTrue(store.load_results(cid)["GD-J12345"].get("rejudge_error"))
        self.result(cid, "GD-J12345", judging=True)
        status, _, body = self.request("POST", path, {**form, "time_ms": "1400"})
        self.assertEqual(status, 400)
        self.assertFalse(json.loads(body)["ok"])
        self.assertEqual(judgelocal.limits_of("P"), (1200, 256))

    def test_save_all_reports_rejected_roster_and_bad_picks(self):
        cid = self.contest()
        form = {"action": "save_all", "names": "New-Student", "mode": "replace", "problems_json": "broken"}
        status, headers, _ = self.request("POST", "/admin/release?c=" + cid, form)
        self.assertEqual(status, 302)
        msg = urllib.parse.unquote(headers["Location"])
        self.assertIn("不能重建名单", msg)
        self.assertIn("选题数据格式不对", msg)
        self.assertEqual(store.student_name(cid, "GD-J12345"), "Current-Student")
        self.assertEqual(len(store.load_exam(cid)["problems"]), 1)

    def test_rejected_level_change_still_saves_other_settings(self):
        cid = self.contest()
        status, headers, _ = self.request("POST", "/admin/release?c=" + cid,
                                         {"action": "settings", "level": "S", "duration": "180", "prefix": "TEST"})
        self.assertEqual(status, 302)
        self.assertIn("级别修改已拒绝", urllib.parse.unquote(headers["Location"]))
        c = store.get_contest(cid)
        self.assertEqual((c["level"], c["prefix"], c["duration_min"]), ("J", "TEST", 180))

    def test_invalid_selection_does_not_modify_or_allocate(self):
        cid = self.contest()
        before, numbers = store.load_exam(cid), problems.load_codes()
        choices = [{}, [{"pid": "MISSING"}], [{"pid": "P", "full": -1}], [{"pid": "P"}, {"pid": "P"}]]
        for choice in choices:
            status, _, _ = self.request("POST", "/admin/exam?c=" + cid, {"problems_json": json.dumps(choice)})
            self.assertEqual(status, 302)
            self.assertEqual(store.load_exam(cid), before)
            self.assertEqual(problems.load_codes(), numbers)

    def test_invalid_prefix_rejected_before_any_settings_change(self):
        cid = self.contest()
        before = store.get_contest(cid)
        status, _, _ = self.request("POST", "/admin/release?c=" + cid,
                                   {"action": "settings", "prefix": "../bad", "level": "S", "duration": "123"})
        self.assertEqual(status, 400)
        self.assertEqual(store.get_contest(cid), before)
        with self.assertRaises(ValueError):
            store.create_contest("Bad", prefix="GD-1")

    def test_rejected_limits_do_not_partially_change_metadata(self):
        cid = self.contest()
        self.result(cid, "GD-J12345", judging=True)
        before = problems.load_codes()
        info = problems.load_problem_info()
        with self.assertRaises(OSError):
            admin_pages.remember_meta("P", title="Changed", name="newname", time_ms=2000, memory_mb=256)
        self.assertEqual(problems.load_codes(), before)
        self.assertEqual(problems.load_problem_info(), info)

    def test_duplicate_long_names_remain_valid(self):
        cid = self.contest()
        self.assertTrue(problems.create_problem("Q", "Another", {"1.in": b"1", "1.out": b"1"})["ok"])
        choice = [{"pid": pid, "name": "a" * 32} for pid in ("P", "Q")]
        status, _, _ = self.request("POST", "/admin/exam?c=" + cid, {"problems_json": json.dumps(choice)})
        self.assertEqual(status, 302)
        names = [p["name"] for p in store.load_exam(cid)["problems"]]
        self.assertEqual(len(set(names)), 2)
        self.assertTrue(all(store.valid_name(name) for name in names))

    def test_display_title_change_keeps_scores_but_contract_change_blocks_publish(self):
        cid = self.contest()
        store.set_released(cid, True)
        before = store.load_results(cid)
        exam = store.load_exam(cid)
        exam["problems"][0]["title"] = "New Story"
        store.save_exam(cid, exam)
        self.assertTrue(store.load_exam(cid)["released"])
        store.set_released(cid, True)
        self.assertEqual(store.load_results(cid), before)
        exam["problems"][0]["name"] = "changed_io"
        store.save_exam(cid, exam)
        self.assertFalse(store.load_exam(cid)["released"])
        with self.assertRaises(ValueError):
            store.set_released(cid, True)

    def test_running_or_inflight_contest_cannot_be_deleted(self):
        cid = self.contest()
        self.result(cid, "GD-J12345", judging=True, submission_state="queued")
        before = store.load_roster(cid)
        self.assertEqual(self.request("POST", "/admin/delete?c=" + cid, {})[0], 400)
        self.assertEqual(store.load_roster(cid), before)
        self.assertIsNotNone(store.get_contest(cid))
        self.result(cid, "GD-J12345", judging=False, submission_state="done")
        now = int(time.time())
        store.update_contest(cid, start_at=now - 1, end_at=now + 100)
        self.assertEqual(self.request("POST", "/admin/delete?c=" + cid, {})[0], 400)
        self.assertIsNotNone(store.get_contest(cid))

    def test_data_components_cannot_traverse(self):
        for component in ("../outside", "..", "/tmp/escape", "a\\b", "a:b", ""):
            with self.subTest(component=component):
                with self.assertRaises(ValueError):
                    store.load_exam(component)
                with self.assertRaises(ValueError):
                    store.upload_dir("safe", component)


if __name__ == "__main__":
    unittest.main()
