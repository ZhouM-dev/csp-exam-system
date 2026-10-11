"""Time windows and complete ZIP imports, including HTTP bypasses and upload races."""
import http.client
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import archive, contest_time as ct, grading, importer, judgelocal, problems, security, store, wrapper
from csp_exam.core import scoreboard
from csp_exam.web.server import Handler
from csp_exam.web.multipart import _read_zip


class LegacyInfo(zipfile.ZipInfo):
    def _encodeFilenameFlags(self):
        return self.filename.encode("gbk"), self.flag_bits & ~0x800


def zipped(files, legacy=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        for name, data in files.items():
            z.writestr(LegacyInfo(name) if legacy else name, data)
    return stream.getvalue()


def project(prefix="G01-加法/", answer=".ans", limit=2000):
    return {prefix + "题目.md": "# 加法\n秘密题面".encode(), prefix + "标程.cpp": b"int main(){}",
            prefix + "data/01.IN": b"2 3\n", prefix + "data/01" + answer: b"5\n",
            prefix + "大样例/样例.in": b"2 3\n", prefix + "大样例/样例.out": b"5\n",
            prefix + "problem.json": json.dumps({"name": "sum", "time_ms": limit,
                                                   "memory_mb": 512, "case_count": 1}).encode()}


class TimeAndZip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, "DATA_DIR", self.tmp.name)
        self.pd = patch.object(judgelocal, "PROBLEMS_DIR", str(Path(self.tmp.name) / "problems"))
        self.data.start(); self.pd.start()

    def tearDown(self):
        self.pd.stop(); self.data.stop(); self.tmp.cleanup()

    def test_exact_boundaries_and_manual_flag_ignored(self):
        c = {"start_at": 100, "end_at": 200, "open": False}
        self.assertEqual([ct.phase(c, t) for t in (99.999, 100, 199.999, 200)],
                         ["pending", "running", "running", "ended"])
        self.assertEqual(ct.phase({"open": True, "created_at": 1}), "unscheduled")
        for value in (True, float("nan"), float("inf"), "wrong"):
            self.assertEqual(ct.timestamp(value), 0)

    def test_beijing_time_independent_of_server_timezone(self):
        self.assertEqual(ct.timestamp("2026-10-11T09:00"), ct.timestamp("2026-10-11T01:00:00+00:00"))
        for f in ({}, {"start_at": 200, "end_at": 100}, {"start_at": 100}):
            with self.assertRaises(ValueError): ct.parse_schedule(f)
        with self.assertRaises(ValueError):
            ct.parse_schedule({"start_at": 2, "end_at": 300}, {"start_at": 1, "end_at": 300})

    def test_legacy_zip_keeps_chinese_person_txt(self):
        files = {"GD-J12345/张三.TXT": b"information", "GD-J12345/sum/sum.cpp": b"int main(){}"}
        for legacy in (False, True):
            got = _read_zip(zipped(files, legacy))
            self.assertEqual(got, files)
            self.assertEqual(wrapper.find_person_file(list(got), "张三"), "GD-J12345/张三.TXT")

    def test_zip_path_quota_collision_and_corruption(self):
        for name in ("../1.in", "/1.in", "C:/1.in", "a/../1.in", "a//b"):
            with self.assertRaises(ValueError): _read_zip(zipped({name: b"x"}))
        with self.assertRaises(ValueError): _read_zip(zipped({"1.in": b"12"}), max_bytes=1)
        with self.assertRaises(ValueError): _read_zip(b"invalid")
        with self.assertRaises(ValueError):
            problems.unpack({"a.zip": zipped({"1.in": b"a"}), "b.zip": zipped({"1.in": b"b"})})
        with self.assertRaises(ValueError):
            _read_zip(zipped({"a\\b.txt": b"a", "a/b.txt": b"b"}))

    def test_csp_sources_require_exact_kaohao_directory(self):
        kh = "GD-J12345"
        problem = [{"no": 1, "name": "sum"}]
        for name in ("sum/sum.cpp", "sum.cpp", "GD-J99999/sum/sum.cpp",
                     "gd-J12345/sum/sum.cpp", kh + "/sum.cpp", kh + "/sum/main.cpp",
                     kh + "/Sum/sum.cpp", kh + "/sum/Sum.cpp", kh + "/sum/sum.CPP",
                     kh + "/sum/sum.cc", "extra/" + kh + "/sum/sum.cpp"):
            with self.subTest(name=name):
                picked, missing = wrapper.pick_sources([name], problem, strict=True, strict_layout=True, kaohao=kh)
                self.assertEqual(picked, {})
                self.assertTrue(missing)
        good = kh + "/sum/sum.cpp"
        self.assertEqual(wrapper.pick_sources([good], problem, strict=True, strict_layout=True, kaohao=kh)[0], {1: good})

    def test_csp_person_txt_requires_direct_kaohao_child(self):
        kh = "GD-J12345"
        for name in ("张三.txt", kh + "/sum/张三.txt", "GD-J99999/张三.txt", "extra/" + kh + "/张三.txt"):
            with self.subTest(name=name):
                self.assertFalse(wrapper.find_person_file([name], "张三", strict=True, kaohao=kh))
                _, missing = wrapper.pick_sources([name], [], student_name="张三", strict=True, strict_layout=True, kaohao=kh)
                self.assertTrue(any(m.startswith("缺个人信息文件") for m in missing))
        for ext in ("txt", "TXT"):
            name = kh + "/张三." + ext
            self.assertEqual(wrapper.find_person_file([name], "张三", strict=True, kaohao=kh), name)
            self.assertFalse(wrapper.person_file_hint([name], "张三", strict=True, kaohao=kh))

    def test_root_project_and_container_readme(self):
        self.assertEqual(problems.detect_bundle(project(""))["count"], 1)
        files = {"README.md": b"container documentation", **project(), **project("G02-另一题/", limit=3000)}
        report = problems.create_bundle({"bundle.zip": zipped(files, True)})
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(report["items"]), 2)
        self.assertEqual(judgelocal.limits_of("G01"), (2000, 512))
        self.assertEqual(judgelocal.limits_of("G02"), (3000, 512))
        self.assertEqual(judgelocal.cases_of("G01")[0]["out"], b"5\n")
        self.assertEqual(sum(len(x["files"]) for x in problems.load_samples("G01")["items"]), 2)

    def test_orphans_fail_before_writing(self):
        files = project()
        files["G01-加法/data/02.in"] = b"orphan"
        got = problems.create_problem("G01", "加法", files)
        self.assertFalse(got["ok"])
        self.assertIn("缺少答案", got["error"])
        self.assertNotIn("G01", judgelocal.known_pids())
        files = project()
        files["G01-加法/data/01.out"] = b"other answer"
        self.assertFalse(problems.create_problem("G01", "加法", files)["ok"])
        files = project()
        files["G01-加法/data/01.in"] = b"duplicate input"
        self.assertFalse(problems.create_problem("G01", "加法", files)["ok"])

    def test_named_extreme_data_is_not_mistaken_for_sample(self):
        files = project()
        files.pop("G01-加法/problem.json")
        files["G01-加法/data/extreme.in"] = b"large"
        files["G01-加法/data/extreme.ans"] = b"answer"
        self.assertEqual(len(problems.detect_bundle(files)["problems"][0]["cases"]), 2)

    def test_declared_count_and_unknown_scoring_never_silently_ignored(self):
        for config in ({"case_count": 2}, {"scores": [100]}, {"time_ms": -1}):
            files = project()
            files["G01-加法/problem.json"] = json.dumps(config).encode()
            report = problems.create_problem("G01", "加法", files)
            self.assertFalse(report["ok"])
            self.assertIn("problem.json", report["error"])

    def test_cli_zip_and_web_zip_use_same_names(self):
        files = project()
        path = Path(self.tmp.name) / "legacy.zip"
        path.write_bytes(zipped(files, True))
        b = importer.Bundle(str(path))
        try: self.assertEqual({n: b.read(n) for n in b.names()}, files)
        finally: b.close()
        report = importer.import_problemset(str(path), dry_run=True, log=lambda *a: None)
        self.assertTrue(report["ok"], report)


class HTTPWindows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, "DATA_DIR", self.tmp.name); self.data.start()
        self.pd = patch.object(judgelocal, "PROBLEMS_DIR", str(Path(self.tmp.name) / "problems")); self.pd.start()
        self.logs = patch.object(Handler, "log_message", lambda *a: None); self.logs.start()
        self.now = int(time.time())
        c = store.create_contest("时间验收", level="J", start_at=self.now + 60, end_at=self.now + 3600)
        self.cid, self.kh = c["id"], "GD-J12345"
        store._save(store._path("contests", self.cid, "roster.json"), {self.kh: {"name": "张三"}})
        store.save_exam(self.cid, {"problems": [{"no": 1, "pid": "SECRET_PID", "name": "secret_slug", "full": 100}], "released": False})
        self.cookie = "csp=" + security.make_cookie(self.cid, self.kh)
        self.admin = "csp_admin=" + security.make_admin_cookie()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.logs.stop(); self.pd.stop(); self.data.stop(); self.tmp.cleanup()

    def request(self, path, method="GET", body=None, admin=False, origin=None, ctype="application/x-www-form-urlencoded", cookie=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Cookie": cookie if cookie is not None else self.admin if admin else self.cookie, "Content-Type": ctype}
        if origin: headers["Origin"] = origin
        conn.request(method, path + ("&" if "?" in path else "?") + "c=" + self.cid, body, headers)
        r = conn.getresponse(); result = r.status, dict(r.getheaders()), r.read().decode("utf-8")
        conn.close(); return result

    def test_pre_and_post_exam_cannot_read_resources_or_submit(self):
        for ended in (False, True):
            if ended: store.update_contest(self.cid, start_at=self.now - 600, end_at=self.now - 1)
            for path in ("/hall", "/problem?p=1", "/sample?p=1", "/submit", "/file?f=a.cpp", "/help", "/score"):
                status, _, text = self.request(path)
                self.assertEqual(status, 200 if path in ("/hall", "/help") else 403, path)
                self.assertNotIn("secret_slug", text); self.assertNotIn("SECRET_PID", text)
                self.assertNotIn('action="/upload', text)
            self.assertEqual(self.request("/upload", "POST", b"")[0], 403)

    def test_running_ignores_manual_flag_and_cannot_publish(self):
        store.update_contest(self.cid, start_at=self.now - 60, open=False)
        status, _, text = self.request("/hall")
        self.assertEqual(status, 200); self.assertIn("secret_slug", text)
        self.assertIn('action="/upload', text)
        with self.assertRaises(ValueError): store.set_released(self.cid, True)
        self.assertEqual(self.request("/admin/release", "POST", b"open=1", admin=True)[0], 400)

    def test_student_logout_is_visible_in_every_phase_and_returns_to_exam_login(self):
        for start, end in ((0, 0), (self.now + 60, self.now + 120),
                           (self.now - 60, self.now + 120), (self.now - 120, self.now - 60)):
            store.update_contest(self.cid, start_at=start, end_at=end)
            status, _, text = self.request("/hall")
            self.assertEqual(status, 200)
            self.assertIn('href="/logout?c=' + self.cid + '"', text)
            self.assertIn("退出登录", text)
            self.assertNotIn('href="/scoreboard?', text)
            self.assertNotIn('href="/score?', text)
        status, headers, _ = self.request("/logout")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/enter?c=" + self.cid)
        self.assertIn("csp=;", headers["Set-Cookie"])
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        status, _, text = self.request("/enter", cookie="")
        self.assertEqual(status, 200)
        self.assertIn('name="kaohao"', text)
        self.assertIn('name="c" value="' + self.cid + '"', text)
        status, headers, _ = self.request("/hall", cookie="")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/enter?c=" + self.cid)

    def test_waiting_page_shows_public_details_countdown_and_opens_at_server_start(self):
        status, _, text = self.request("/hall")
        self.assertEqual(status, 200)
        for expected in ('id="wait-countdown"', '距离考试开始', '开考后将自动进入考试',
                         '开始时间（北京时间）', '结束时间（北京时间）', '考试时长',
                         '张三', '退出登录', 'window.initContestWindow(', '"phase": "pending"'):
            self.assertIn(expected, text)
        self.assertNotIn('action="/upload', text)
        self.assertNotIn('secret_slug', text)
        self.assertNotIn('SECRET_PID', text)
        self.assertNotIn('time.is/', text)
        with patch('csp_exam.core.contest_time.time.time', return_value=self.now + 60):
            status, _, text = self.request('/hall')
            self.assertEqual(status, 200)
            self.assertIn('action="/upload', text)
            self.assertIn('secret_slug', text)
            self.assertNotIn('id="wait-countdown"', text)
        status, _, text = self.request('/api/contest-state')
        self.assertEqual(status, 200)
        self.assertEqual(set(json.loads(text)), {'phase', 'server_now', 'start_at', 'end_at'})

    def test_submission_receipt_hides_rank_until_ended_and_published(self):
        store.update_contest(self.cid, start_at=self.now - 60, end_at=self.now + 120)
        exam = store.load_exam(self.cid)
        exam["released"] = True
        store.save_exam(self.cid, exam)
        store.put_result(self.cid, self.kh, {"submitted_at": "test", "total": 100,
                                           "problems": {"T1": {"score": 100}}})
        # 回执真实渲染路径；保留成功提交后的排名防泄漏检查，不触发沙箱评测。
        with patch.object(Handler, "_hall_page", lambda h, kh, c, cid:
                          h._receipt_page(kh, c, cid, {}, {}, [])):
            status, _, text = self.request("/hall")
        self.assertEqual(status, 200)
        self.assertIn("收到你交的", text)
        self.assertIn("退出登录", text)
        self.assertNotIn('href="/scoreboard?', text)
        self.assertNotIn('href="/score?', text)
        for path in ("/scoreboard", "/api/scoreboard"):
            self.assertEqual(self.request(path)[0], 403)
        store.update_contest(self.cid, end_at=self.now - 1)
        store.save_exam(self.cid, {**exam, "released": False})
        self.assertNotIn('href="/scoreboard?', self.request("/hall")[2])
        store.set_released(self.cid, True)
        self.assertIn('href="/scoreboard?', self.request("/hall")[2])
        self.assertEqual(self.request("/api/scoreboard")[0], 200)

    def test_logout_without_session_stays_local_and_can_log_in_as_another_student(self):
        status, headers, _ = self.request("/logout", cookie="")
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/enter?c=" + self.cid)
        roster = store.load_roster(self.cid)
        roster["GD-J54321"] = {"name": "李四"}
        store._save(store._path("contests", self.cid, "roster.json"), roster)
        status, headers, _ = self.request("/enter", "POST", b"kaohao=GD-J54321&c=" + self.cid.encode(), cookie="")
        self.assertEqual(status, 302)
        new_cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(security.parse_cookie(new_cookie.split("=", 1)[1]), (self.cid, "GD-J54321"))

    def test_deadline_crossed_while_receiving_upload_leaves_no_snapshot_or_queue_leak(self):
        store.update_contest(self.cid, start_at=self.now - 60)
        def crossing(handler):
            store.update_contest(self.cid, end_at=self.now - 1)
            return {self.kh + "/张三.txt": b"x", self.kh + "/secret_slug/secret_slug.cpp": b"int main(){}"}
        with patch.object(Handler, "_read_upload", crossing), patch.object(grading, "reserve_submission", return_value=True), patch.object(grading, "release_submission") as release, patch.object(grading, "enqueue_submission") as enqueue:
            self.assertEqual(self.request("/upload", "POST", b"")[0], 403)
            release.assert_called_once(); enqueue.assert_not_called()
        self.assertEqual(store.load_results(self.cid), {})
        self.assertFalse(Path(store.upload_dir(self.cid, self.kh)).exists())

    def test_scoreboard_clock_transition_and_reschedule_withdraws(self):
        store.update_contest(self.cid, start_at=self.now - 60, end_at=self.now + 60)
        exam = store.load_exam(self.cid); exam["released"] = True; store.save_exam(self.cid, exam)
        self.assertNotIn("problems", scoreboard.snapshot(self.cid))
        with patch("csp_exam.core.contest_time.time.time", return_value=self.now + 60):
            self.assertIn("problems", scoreboard.snapshot(self.cid))
        store.update_contest(self.cid, end_at=self.now + 120)
        self.assertFalse(store.load_exam(self.cid)["released"])

    def test_invalid_schedule_and_cross_origin_post_are_rejected(self):
        before = len(store.list_contests())
        self.assertEqual(self.request("/admin/new", "POST", b"title=test", admin=True)[0], 400)
        self.assertEqual(len(store.list_contests()), before)
        self.assertEqual(self.request("/admin/release", "POST", b"released=0", admin=True, origin="https://example.invalid")[0], 403)

    def zip_post(self, path, files, admin=False):
        data = zipped(files, legacy=True)
        body = (b'--csp-test\r\nContent-Disposition: form-data; name="data"; filename="bundle.zip"\r\n'
                b'Content-Type: application/zip\r\n\r\n' + data + b'\r\n--csp-test--\r\n')
        return self.request(path, "POST", body, admin=admin, ctype="multipart/form-data; boundary=csp-test")

    def test_teacher_zip_http_preserves_individual_limits(self):
        status, _, _ = self.zip_post("/admin/problem", {**project(), **project("G02-乙题/", limit=3000)}, admin=True)
        self.assertEqual(status, 302)
        self.assertEqual(judgelocal.limits_of("G01"), (2000, 512))
        self.assertEqual(judgelocal.limits_of("G02"), (3000, 512))
        self.assertTrue(problems.load_samples("G02"))
        self.assertEqual(problems.load_problem_info()["G01"]["std"], "int main(){}")

    def test_student_legacy_zip_http_recognizes_txt_and_archives_bytes(self):
        store.update_contest(self.cid, start_at=self.now - 60)
        files = {self.kh + "/张三.TXT": b"identity", self.kh + "/secret_slug/secret_slug.cpp": b"int main(){}"}
        with patch.object(grading, "enqueue_submission", side_effect=lambda *a, **k: grading.release_submission()):
            status, _, text = self.zip_post("/upload", files)
        self.assertEqual(status, 200)
        self.assertNotIn("没找到个人信息文件", text)
        result = store.load_results(self.cid)[self.kh]
        self.assertFalse(result["missing_sources"])
        source = Path(store.upload_dir(self.cid, self.kh)) / result["picked"]["1"]
        self.assertEqual(source.read_bytes(), b"int main(){}")

    def test_student_zip_without_kaohao_root_does_not_select_code(self):
        store.update_contest(self.cid, start_at=self.now - 60)
        files = {"张三.txt": b"identity", "secret_slug/secret_slug.cpp": b"int main(){}"}
        with patch.object(grading, "enqueue_submission", side_effect=lambda *a, **k: grading.release_submission()):
            status, _, text = self.zip_post("/upload", files)
        self.assertEqual(status, 200)
        result = store.load_results(self.cid)[self.kh]
        self.assertFalse(result["picked"])
        self.assertIn("没检测到以考号命名的文件夹", text)
        self.assertIn("没找到个人信息文件", text)

    def test_student_zip_misplaced_person_file_receipt_matches_validation(self):
        store.update_contest(self.cid, start_at=self.now - 60)
        files = {self.kh + "/secret_slug/张三.txt": b"identity", self.kh + "/secret_slug/secret_slug.cpp": b"int main(){}"}
        with patch.object(grading, "enqueue_submission", side_effect=lambda *a, **k: grading.release_submission()):
            status, _, text = self.zip_post("/upload", files)
        self.assertEqual(status, 200)
        result = store.load_results(self.cid)[self.kh]
        self.assertIn("1", result["picked"])
        self.assertTrue(any(m.startswith("缺个人信息文件") for m in result["missing_sources"]))
        self.assertIn("没找到个人信息文件", text)


if __name__ == "__main__": unittest.main()
