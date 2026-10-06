"""历史重测只用临时资料；验证缺资料、迁移、旧分与恢复上下文。"""
import copy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import grading, judgelocal, security, store, submissions
from csp_exam.web.admin_pages import AdminPages
from csp_exam.web.server import Handler


class Rejudge(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="csp-rejudge-")
        self.addCleanup(self.temp.cleanup)
        for p in [patch.object(store, "DATA_DIR", self.temp.name),
                  patch.object(judgelocal, "PROBLEMS_DIR", str(Path(self.temp.name) / "problems")),
                  patch.object(grading, "log", lambda *_: None),
                  patch.object(grading, "reserve_submission", return_value=True),
                  patch.object(grading, "release_submission")]:
            p.start(); self.addCleanup(p.stop)
        self.enqueue_patch = patch.object(grading, "enqueue_submission")
        self.enqueue = self.enqueue_patch.start(); self.addCleanup(self.enqueue_patch.stop)
        self.cid, self.kh = "test", "GD-S12345"
        self.exam = {"released": True, "problems": [{"no": 1, "pid": "P", "name": "candy", "full": 100}]}
        store._save(store._path("contests.json"), [{"id": self.cid, "title": "测试", "rule": "CSP", "released": True}])
        store._save(store._path("problem_info.json"), {"items": {"P": {"time_ms": 1000, "memory_mb": 256}}})
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b""}])
        store.save_exam(self.cid, self.exam)
        self.student(self.kh, {"candy/candy.cpp": b"int main(){}"})

    def student(self, kh, files, *, uname="", total=100, file="candy/candy.cpp"):
        roster = store.load_roster(self.cid)
        roster[kh] = {"name": kh, "uname": uname}
        store._save(store._path("contests", self.cid, "roster.json"), roster)
        base = Path(store.upload_dir(self.cid, kh)); base.mkdir(parents=True, exist_ok=True)
        for name, contents in files.items():
            p = base / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(contents)
        store.put_result(self.cid, kh, {"submitted_at": "before", "problems": {"T1": {"file": file, "score": total, "tries": 2}}})

    def test_missing_one_student_does_not_block_others_or_become_zero(self):
        self.student("GD-S22222", {}, total=75)
        self.assertEqual(grading.rejudge_contest(self.cid), 1)
        bad = store.load_results(self.cid)["GD-S22222"]
        self.assertEqual(store.entry_total(bad), 75)
        self.assertEqual(bad["submission_state"], "error")
        self.assertIn("源码已丢失", bad["rejudge_error"])
        self.assertFalse(store.load_exam(self.cid)["released"])
        self.assertTrue(Path(bad["rejudge_backup"], "results.json").is_file())

    def test_legacy_kaohao_alias_comes_only_from_roster(self):
        self.student("GD-S22222", {"GD-0002/candy/candy.cpp": b"original"}, uname="test-GD-0002",
                     file="GD-0002/candy/candy.cpp")
        grading.rejudge_contest(self.cid)
        entry = store.load_results(self.cid)["GD-S22222"]
        self.assertIn("GD-S22222/candy/candy.cpp", entry["picked"]["1"])
        self.assertEqual(entry["rejudge_renamed_paths"], {"GD-S22222/candy/candy.cpp": "GD-0002/candy/candy.cpp"})
        base = Path(store.upload_dir(self.cid, "GD-S22222"))
        self.assertEqual((base / entry["picked"]["1"]).read_bytes(), b"original")
        self.assertEqual((base / "GD-0002/candy/candy.cpp").read_bytes(), b"original")

    def test_arbitrary_outer_directory_is_not_renamed(self):
        self.student("GD-S22222", {"OTHER/candy/candy.cpp": b"original"}, file="OTHER/candy/candy.cpp")
        grading.rejudge_contest(self.cid)
        self.assertEqual(store.load_results(self.cid)["GD-S22222"]["picked"], {})

    def test_unused_huge_executable_does_not_prevent_source_rejudge(self):
        base = Path(store.upload_dir(self.cid, self.kh))
        with (base / "huge.exe").open("wb") as f: f.truncate(34 * 1024 * 1024)
        self.assertEqual(grading.rejudge_contest(self.cid), 1)
        entry = store.load_results(self.cid)[self.kh]
        snapshot = base / "__submissions" / entry["submission_id"]
        self.assertFalse((snapshot / "huge.exe").exists())
        self.assertTrue((base / "huge.exe").exists())

    def test_obsolete_problem_score_is_archived_and_removed(self):
        entry = store.load_results(self.cid)[self.kh]
        entry["problems"]["T9"] = {"score": 100}
        store.put_result(self.cid, self.kh, entry)
        grading.rejudge_contest(self.cid)
        entry = store.load_results(self.cid)[self.kh]
        self.assertNotIn("T9", entry["problems"])
        old = store._load(str(Path(entry["rejudge_backup"]) / "results.json"), dict)
        self.assertEqual(old[self.kh]["problems"]["T9"]["score"], 100)

    def test_wrong_historical_exam_is_not_silently_zeroed(self):
        entry = store.load_results(self.cid)[self.kh]
        entry["problems"]["T1"]["file"] = "other/other.cpp"
        store.put_result(self.cid, self.kh, entry)
        self.assertEqual(grading.rejudge_contest(self.cid), 0)
        entry = store.load_results(self.cid)[self.kh]
        self.assertIn("配置不一致", entry["rejudge_error"])
        self.assertEqual(store.entry_total(entry), 100)

    def test_ioi_incomplete_history_not_replaced_with_latest_source(self):
        store.update_contest(self.cid, rule="IOI")
        self.assertEqual(grading.rejudge_contest(self.cid), 1)
        entry = store.load_results(self.cid)[self.kh]
        grading._apply_result(self.cid, self.kh, 1, {"status": 2, "score": 50}, best_of=True,
                              code="candy", full=100, submission_id=entry["submission_id"])
        grading._clear_judging(self.cid, self.kh, entry["submission_id"])
        entry = store.load_results(self.cid)[self.kh]
        self.assertIn("完整历史", entry["rejudge_error"])
        self.assertEqual(entry["submission_state"], "error")

    def test_ioi_full_score_is_proven_upper_bound_without_missing_history(self):
        store.update_contest(self.cid, rule="IOI")
        grading.rejudge_contest(self.cid)
        entry = store.load_results(self.cid)[self.kh]
        grading._apply_result(self.cid, self.kh, 1, {"status": 1, "score": 100}, best_of=True,
                              code="candy", full=100, submission_id=entry["submission_id"])
        grading._clear_judging(self.cid, self.kh, entry["submission_id"])
        entry = store.load_results(self.cid)[self.kh]
        self.assertEqual(entry["submission_state"], "done")
        self.assertIn("upper_bound", entry["rejudge_proof"]["method"])
        self.assertNotIn("rejudge_error", entry)

    def test_restart_does_not_retry_missing_history_as_a_transient_error(self):
        store.update_contest(self.cid, rule="IOI")
        grading.rejudge_contest(self.cid)
        entry = store.load_results(self.cid)[self.kh]
        grading._apply_result(self.cid, self.kh, 1, {"status": 2, "score": 50}, best_of=True,
                              code="candy", full=100, submission_id=entry["submission_id"])
        grading._clear_judging(self.cid, self.kh, entry["submission_id"])
        self.enqueue.reset_mock()
        with patch.object(grading, "_QUEUE_SLOTS"):
            grading.recover_submissions()
        self.enqueue.assert_not_called()
        self.assertEqual(store.load_results(self.cid)[self.kh]["submission_state"], "error")

    def test_ioi_transient_error_keeps_upper_bound_check_for_retry(self):
        store.update_contest(self.cid, rule="IOI")
        grading.rejudge_contest(self.cid)
        entry = store.load_results(self.cid)[self.kh]
        grading._clear_judging(self.cid, self.kh, entry["submission_id"], error=True)
        self.assertTrue(store.load_results(self.cid)[self.kh]["rejudge_best_unverified"])
        grading._apply_result(self.cid, self.kh, 1, {"status": 2, "score": 50}, best_of=True,
                              code="candy", full=100, submission_id=entry["submission_id"])
        grading._clear_judging(self.cid, self.kh, entry["submission_id"])
        self.assertIn("完整历史", store.load_results(self.cid)[self.kh]["rejudge_error"])

    def test_finishing_an_attempt_cannot_erase_unresolved_history(self):
        entry = store.load_results(self.cid)[self.kh]
        entry.update(rejudge_error="IOI 缺完整历史")
        store.put_result(self.cid, self.kh, entry)
        grading._clear_judging(self.cid, self.kh)
        self.assertEqual(store.load_results(self.cid)[self.kh]["submission_state"], "error")

    def test_limits_or_answers_missing_leave_published_data_untouched(self):
        before = copy.deepcopy(store.load_results(self.cid))
        store._save(store._path("problem_info.json"), {"items": {}})
        with self.assertRaises(ValueError): grading.rejudge_contest(self.cid)
        self.assertTrue(store.load_exam(self.cid)["released"])
        self.assertEqual(store.load_results(self.cid), before)
        store._save(store._path("problem_info.json"), {"items": {"P": {"time_ms": 1000, "memory_mb": 256}}})
        Path(judgelocal.cases_dir("P"), "1.out").unlink()
        with self.assertRaises(ValueError): grading.rejudge_contest(self.cid)
        self.assertEqual(store.load_results(self.cid), before)

    def test_running_queue_or_capacity_failure_does_not_withdraw_or_modify(self):
        before = store.load_results(self.cid)
        with patch.object(grading, "reserve_submission", return_value=False):
            with self.assertRaises(ValueError): grading.rejudge_contest(self.cid)
        self.assertEqual(store.load_results(self.cid), before)
        self.assertTrue(store.load_exam(self.cid)["released"])
        entry = store.load_results(self.cid)[self.kh]
        entry["submission_state"] = "queued"; entry["judging"] = False
        store.put_result(self.cid, self.kh, entry)
        with self.assertRaises(ValueError): grading.rejudge_contest(self.cid)

    def test_durable_exam_and_rule_for_restart_recovery(self):
        grading.rejudge_contest(self.cid)
        saved = store.load_results(self.cid)[self.kh]
        changed = {"problems": [{"no": 1, "pid": "OTHER", "name": "other", "full": 200}]}
        store.save_exam(self.cid, changed)
        self.enqueue.reset_mock()
        with patch.object(grading, "_QUEUE_SLOTS"):
            grading.recover_submissions()
        self.assertEqual(self.enqueue.call_args.args[3], saved["submission_exam"])
        self.assertEqual(saved["judge_rule"]["key"], "CSP")

    def test_empty_immutable_last_submission_is_valid_zero_not_missing_archive(self):
        sid, _ = submissions.archive(self.cid, self.kh, {})
        entry = store.load_results(self.cid)[self.kh]
        entry.update(submission_id=sid, picked={})
        store.put_result(self.cid, self.kh, entry)
        self.assertEqual(grading.rejudge_contest(self.cid), 1)
        self.assertEqual(store.load_results(self.cid)[self.kh]["picked"], {})

    def test_null_legacy_submission_id_is_accepted(self):
        entry = store.load_results(self.cid)[self.kh]
        entry["submission_id"] = None
        store.put_result(self.cid, self.kh, entry)
        self.assertEqual(grading.rejudge_contest(self.cid), 1)

    def test_grade_freezes_all_limits_and_uses_saved_rule(self):
        exam = {"problems": self.exam["problems"] + [{"no": 2, "pid": "Q", "name": "other", "full": 100}]}
        sid, snap = submissions.archive(self.cid, self.kh, {"candy/candy.cpp": b"int main(){}", "other/other.cpp": b"int main(){}"})
        entry = store.load_results(self.cid)[self.kh]
        entry.update(submission_id=sid, judge_rule=store.rule_of(store.get_contest(self.cid)))
        store.put_result(self.cid, self.kh, entry)
        store.update_contest(self.cid, rule="OI")
        changed, seen = [1000], []
        def judge(*args, **kwargs):
            seen.append((kwargs["time_ms"], kwargs["io_mode"]))
            changed[0] = 3000
            return {"status": 1, "score": 100}
        with patch.object(judgelocal, "limits_of", side_effect=lambda pid: (changed[0], 256)), \
                patch.object(judgelocal, "judge_source", side_effect=judge):
            grading.judge_csp(self.cid, self.kh, {1: snap+"/candy/candy.cpp", 2: snap+"/other/other.cpp"}, exam, sid)
        self.assertEqual(seen, [(1000, "file"), (1000, "file")])
        entry = store.load_results(self.cid)[self.kh]
        self.assertEqual(entry["judge_limits"], {"P": [1000, 256], "Q": [1000, 256]})
        self.assertTrue(entry["judge_revision"])
        self.assertTrue(entry["evaluation_completed_at"])

    def test_no_submissions_does_not_change_release(self):
        store._save(store._path("contests", self.cid, "results.json"), {})
        self.assertEqual(grading.rejudge_contest(self.cid), 0)
        self.assertTrue(store.load_exam(self.cid)["released"])

    def test_changed_exam_or_rule_cannot_publish_finished_old_context(self):
        class Publisher(AdminPages):
            def _query(inner): return {"c": self.cid}
            def _form(inner): return {"released": "1"}
            def _check_admin(inner, key): return True
            def _redirect(inner, url): inner.redirect = url
        entry = store.load_results(self.cid)[self.kh]
        entry.update(submission_exam=copy.deepcopy(self.exam), judge_rule=store.rule_of(store.get_contest(self.cid)),
                     submission_state="done")
        store.put_result(self.cid, self.kh, entry)
        store.save_exam(self.cid, {"released": False, "problems": [{"no": 1, "pid": "Q", "name": "other", "full": 100}]})
        Publisher()._admin_release()
        self.assertFalse(store.load_exam(self.cid)["released"])
        store.save_exam(self.cid, {**self.exam, "released": False})
        store.update_contest(self.cid, rule="OI")
        Publisher()._admin_release()
        self.assertFalse(store.load_exam(self.cid)["released"])
        store.update_contest(self.cid, rule="CSP")
        Publisher()._admin_release()
        self.assertTrue(store.load_exam(self.cid)["released"])

    def test_blocked_teacher_detail_and_api_are_explicit_and_publicly_hidden(self):
        entry = store.load_results(self.cid)[self.kh]
        entry.update(submission_state="error", rejudge_error="缺少历史资料")
        store.put_result(self.cid, self.kh, entry)
        store.save_exam(self.cid, {**self.exam, "released": False})
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def get(path, admin=True):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            conn.request("GET", path, headers={"Cookie": security.ADMIN_COOKIE+"="+security.make_admin_cookie() if admin else ""})
            res = conn.getresponse(); data=res.read(); conn.close(); return res.status, data.decode()
        try:
            with patch.object(Handler, "log_message", lambda *a: None):
                status, text = get("/admin/student?c=test&k="+self.kh)
                self.assertEqual(status, 200)
                self.assertIn("<b>总分</b>—", text)
                self.assertIn("待补资料", text)
                self.assertIn("未作为本次重测成绩公布", text)
                _, text = get("/admin/scoreboard?c=test")
                self.assertIn("待补资料", text)
                _, text = get("/api/scoreboard?c=test&private=1")
                self.assertEqual(json.loads(text)["rows"][0]["state"], "blocked")
                _, text = get("/api/scoreboard?c=test", admin=False)
                self.assertNotIn("rows", json.loads(text))
                outside = Path(self.temp.name) / "outside.txt"
                outside.write_text("HOST_FILE_MUST_NOT_APPEAR", encoding="utf-8")
                entry["problems"]["T1"]["file"] = str(outside)
                store.put_result(self.cid, self.kh, entry)
                _, text = get("/admin/student?c=test&k="+self.kh)
                self.assertNotIn("HOST_FILE_MUST_NOT_APPEAR", text)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__": unittest.main()
