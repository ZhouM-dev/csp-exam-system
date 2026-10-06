"""已确认评测漏洞的回归，使用临时数据，不访问真实提交。"""

import io
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from csp_exam.core import grading, gojudge, judge_profile, judgelocal, scoreboard, store, submissions, wrapper
from csp_exam.tools.calibrate_judge import compare as compare_benchmark, FLAGS, SOURCE_HASH, WORKLOADS
from csp_exam.web.multipart import _read_zip
from csp_exam.web import student_pages
from csp_exam.web.server import Handler


class Regression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="csp-regression-")
        self.addCleanup(self.temp.cleanup)
        for p in [patch.object(store, "DATA_DIR", self.temp.name),
                  patch.object(judgelocal, "PROBLEMS_DIR", str(Path(self.temp.name) / "problems")),
                  patch.object(grading, "log", lambda *_: None)]:
            p.start()
            self.addCleanup(p.stop)
        self.cid, self.kh = "test", "GD-S12345"
        self.problem = {"no": 1, "pid": "P", "name": "candy", "full": 100}
        self.exam = {"problems": [self.problem], "released": False}
        store._save(store._path("contests.json"), [{"id": self.cid, "title": "测试", "rule": "CSP", "open": True}])
        store.save_exam(self.cid, self.exam)
        store._save(store._path("contests", self.cid, "roster.json"), {self.kh: {"name": "甲"}})
        store.put_result(self.cid, self.kh, {"submitted_at": "before", "submission_id": "new",
                                           "problems": {"T1": {"score": 100}}, "total": 100})

    def apply(self, row, no=1, sid="new", **kw):
        return grading._apply_result(self.cid, self.kh, no, row, best_of=False,
                                     code="candy", full=100, submission_id=sid, **kw)

    def test_paths_and_symlink(self):
        for name in ["../a.cpp", "candy/../../a.cpp", "/a.cpp", "C:\\a.cpp", "a//b", "a/./b"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                submissions.clean_files({name: b"x"})
        base = Path(store.upload_dir(self.cid, self.kh))
        base.mkdir(parents=True, exist_ok=True)
        (base / "escape").symlink_to(Path(self.temp.name))
        with self.assertRaises(ValueError):
            submissions.confined_file(str(base), "escape/outside")

    def test_source_and_archive_bounds(self):
        with self.assertRaises(ValueError):
            submissions.clean_files({"candy/candy.cpp": b"x" * (102400 + 1)})
        with self.assertRaises(ValueError):
            submissions.clean_files({str(i): b"" for i in range(1025)})
        a, ar = submissions.archive(self.cid, self.kh, {"candy/candy.cpp": b"first"})
        b, br = submissions.archive(self.cid, self.kh, {"candy/candy.cpp": b"second"})
        self.assertNotEqual(a, b)
        base = store.upload_dir(self.cid, self.kh)
        self.assertEqual(Path(submissions.confined_file(base, ar + "/candy/candy.cpp")).read_bytes(), b"first")
        self.assertEqual(Path(submissions.confined_file(base, br + "/candy/candy.cpp")).read_bytes(), b"second")

    def test_zip_expansion_limit_and_symlink(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("a", b"x" * 8192)
        with self.assertRaises(ValueError):
            _read_zip(buf.getvalue(), max_bytes=4096)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            info = zipfile.ZipInfo("link")
            info.external_attr = 0o120777 << 16
            z.writestr(info, "../outside")
        with self.assertRaises(ValueError):
            _read_zip(buf.getvalue())

    def test_strict_csp_layout(self):
        names = ["candy.cpp", "random/candy.cpp", "candy/Candy.cpp", "candy/candy.c"]
        picked, _ = wrapper.pick_sources(names, [self.problem], strict=True, strict_layout=True, kaohao=self.kh)
        self.assertEqual(picked, {})
        picked, _ = wrapper.pick_sources([f"{self.kh}/candy/candy.cpp"], [self.problem], strict=True,
                                         strict_layout=True, kaohao=self.kh)
        self.assertEqual(picked[1], f"{self.kh}/candy/candy.cpp")

    def test_text_comparison_is_binary_and_space_specific(self):
        self.assertTrue(judgelocal.compare(b"x  \r\n\r\n", b"x\n"))
        for got in [b" x\n", b"x\t\n", "x\u00a0\n".encode(), b"x\xff\n"]:
            self.assertFalse(judgelocal.compare(got, b"x\n"))
        self.assertFalse(judgelocal.compare(b"\xff", b"\xfe"))

    def test_missing_answer_refuses_import(self):
        with self.assertRaises(OSError):
            judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": None}])
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b"ok"}])
        Path(judgelocal.cases_dir("P"), "1.out").unlink()
        r = judgelocal.judge_source("P", b"int main(){}", ".cpp", code="candy", full=100)
        self.assertEqual(r["status"], 8)

    def test_no_guessed_limits(self):
        with patch("csp_exam.core.problems.load_problem_info", return_value={}), self.assertRaises(ValueError):
            judgelocal.limits_of("P")

    def test_stale_completion_ignored(self):
        self.apply({"status": 2, "score": 0}, sid="old")
        grading._clear_judging(self.cid, self.kh, "old", error=True)
        self.assertEqual(store.load_results(self.cid)[self.kh]["total"], 100)
        self.assertEqual(store.load_results(self.cid)[self.kh]["submission_id"], "new")

    def test_system_error_preserves_score_and_marks_pending(self):
        self.apply({"status": 8, "score": 0, "note": "sandbox down"})
        item = store.load_results(self.cid)[self.kh]["problems"]["T1"]
        self.assertEqual(item["score"], 100)
        self.assertTrue(item["pending"])
        self.apply({"status": 2, "score": 0})
        self.assertNotIn("pending", store.load_results(self.cid)[self.kh]["problems"]["T1"])

    def test_unsupported_source_is_compile_error_and_finishes(self):
        store.update_contest(self.cid, rule="OI")
        path = Path(store.upload_dir(self.cid, self.kh)) / "candy.pas"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"program candy; begin end.")
        self.assertFalse(grading.judge_csp(self.cid, self.kh, {1: "candy.pas"}, self.exam, "new"))
        result = store.load_results(self.cid)[self.kh]
        self.assertEqual(result["problems"]["T1"]["status"], 7)
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["judging"])
        self.assertFalse(result["problems"]["T1"].get("pending"))
        self.assertEqual(result["submission_state"], "done")

    def test_whole_last_submission_missing_task_zero(self):
        self.assertFalse(grading.judge_csp(self.cid, self.kh, {}, self.exam, "new"))
        result = store.load_results(self.cid)[self.kh]
        self.assertEqual(result["total"], 0)
        self.assertFalse(result["judging"])
        self.assertEqual(result["submitted_at"], "before")

    def test_concurrent_updates_and_retry_no_duplicate_attempts(self):
        jobs = [threading.Thread(target=self.apply, args=({"status": 1, "score": 100}, n)) for n in (1, 2)]
        for j in jobs: j.start()
        for j in jobs: j.join()
        self.assertEqual(store.load_results(self.cid)[self.kh]["total"], 200)
        self.apply({"status": 1, "score": 100})
        self.assertEqual(store.load_results(self.cid)[self.kh]["problems"]["T1"]["tries"], 1)

    def test_all_testcases_counted(self):
        rows = [{"status": 1 if i < 100 else 2} for i in range(200)]
        item = self.apply({"status": 2, "testcases": rows})
        self.assertEqual(len(item["testcases"]), 200)
        self.assertEqual(item["score"], 100)  # 既有整数计分约定，余数分配给前面的点。
        rows = [{"status": 1 if i < 100 else 2, "score": 5 if i < 100 else 0} for i in range(200)]
        item = grading._apply_result(self.cid, self.kh, 1, {"status": 2, "testcases": rows},
                                     best_of=False, full=1000, code="candy", submission_id="new")
        self.assertEqual(item["score"], 500)

    def test_zero_feedback_and_tied_ranks(self):
        hidden = scoreboard.snapshot(self.cid)
        self.assertNotIn("rows", hidden)
        self.assertNotIn("problems", hidden)
        roster = store.load_roster(self.cid)
        roster["GD-S23456"] = {"name": "乙"}
        store._save(store._path("contests", self.cid, "roster.json"), roster)
        store.put_result(self.cid, "GD-S23456", {"submitted_at": "x", "problems": {"T1": {"score": 100}}})
        state = scoreboard.snapshot(self.cid, private=True)
        self.assertEqual([r["rank"] for r in state["rows"]], [1, 1])
        store.save_exam(self.cid, {**self.exam, "released": True})
        self.assertNotIn("kaohao", scoreboard.snapshot(self.cid)["rows"][0])

    def test_time_scale_validation_and_freeze(self):
        for value in [0, float("nan"), float("inf"), 9]:
            with self.assertRaises(ValueError): judge_profile.save(value)
        judge_profile.save(1.5)
        self.assertEqual(judge_profile.effective_limit(1000)[0], 1500)
        judgelocal.store_problem("P", [{"in": b"", "out": b"", "name": "1"}])
        def inner(*a, **kw):
            self.assertEqual(kw["time_ms"], 1500)
            judge_profile.save(2)
            return {"status": 1, "score": 100, "time": 900}
        with patch.object(gojudge, "ensure_ready", return_value={}), patch.object(judgelocal, "_judge_inner", side_effect=inner):
            r = judgelocal.judge_source("P", b"", ".cpp", code="candy", full=100)
        self.assertEqual(r["time_scale"], 1.5)
        self.assertEqual(r["official_time_ms"], 1000)
        self.assertEqual(r["effective_time_ms"], 1500)
        self.assertEqual(r["reference_time"], 600)

    def test_case_limits_missing_file_and_cleanup(self):
        r = {"status": "Accepted", "time": 999000000, "memory": 1024, "fileIds": {"stdout": "out"}}
        with patch.object(gojudge, "prepare", return_value="input"), patch.object(gojudge, "run", return_value=r) as run, \
                patch.object(gojudge, "fetch", return_value=b"ok"), patch.object(gojudge, "drop") as drop:
            _, status, _, _, _ = judgelocal._run_case("binary", {"in": b"\xff"}, code="candy", time_ms=500,
                                                    memory_mb=512, io_mode="file")
            self.assertEqual(status, 3)
            self.assertEqual(run.call_args.kwargs["stack_mb"], 512)
            self.assertEqual(run.call_args.kwargs["files"][0], {"content": ""})
            self.assertCountEqual([x.args[0] for x in drop.call_args_list], ["input", "out"])

    def test_benchmark_ratio_direction(self):
        def data(ms):
            return {"source_sha256": SOURCE_HASH, "flags": FLAGS, "compiler": "9.3.0",
                    "benchmarks": {n: {"checksum": "same", "median_ms": ms, "spread": .01} for n in WORKLOADS}}
        self.assertEqual(compare_benchmark(data(1500), data(1000))["time_scale"], 1.5)
        self.assertEqual(compare_benchmark(data(500), data(1000))["time_scale"], .5)

    def test_rejudge_backups_and_withdraws_release(self):
        store._save(store._path("problem_info.json"), {"items": {"P": {"time_ms": 1000, "memory_mb": 256}}})
        judgelocal.store_problem("P", [{"name": "1", "in": b"", "out": b""}])
        sid, snap = submissions.archive(self.cid, self.kh, {"candy/candy.cpp": b"int main(){}"})
        entry = store.load_results(self.cid)[self.kh]
        entry.update(submission_id=sid, picked={"1": snap + "/candy/candy.cpp"})
        store.put_result(self.cid, self.kh, entry)
        store.save_exam(self.cid, {**self.exam, "released": True})
        with patch.object(grading, "enqueue_submission") as enqueue, \
                patch.object(grading, "reserve_submission", return_value=True), patch.object(grading, "release_submission"):
            self.assertEqual(grading.rejudge_contest(self.cid), 1)
        self.assertFalse(store.load_exam(self.cid)["released"])
        entry = store.load_results(self.cid)[self.kh]
        self.assertEqual(entry["total"], 100)
        self.assertTrue(entry["judging"])
        self.assertNotEqual(entry["submission_id"], sid)
        self.assertTrue(Path(entry["rejudge_backup"], "results.json").is_file())
        self.assertEqual(enqueue.call_args.args[4], entry["submission_id"])

    def test_ioi_keeps_source_of_best_submission(self):
        entry = store.load_results(self.cid)[self.kh]
        entry["problems"]["T1"]["file"] = "best.cpp"
        store.put_result(self.cid, self.kh, entry)
        grading._apply_result(self.cid, self.kh, 1, {"status": 2, "score": 0}, best_of=True,
                              code="candy", full=100, submission_id="new", file_rel="latest.cpp")
        self.assertEqual(store.load_results(self.cid)[self.kh]["problems"]["T1"]["file"], "best.cpp")

    def test_official_source_rules_ignore_comments_and_strings(self):
        for text in ['#pragma GCC optimize("Ofast")\nint main(){}', 'int main(){asm("nop");}', '_Pragma("GCC target(avx2)")']:
            self.assertTrue(wrapper.csp_source_violation(text))
        text = '// #pragma GCC optimize("Ofast")\nconst char*s=R"tag(asm("nop"))tag";int main(){}'
        self.assertEqual(wrapper.csp_source_violation(text), "")
        self.assertEqual(wrapper.csp_source_violation('_Pragma("once")\nint main(){}'), "")

    def test_http_length_and_explicit_deadline(self):
        handler = object.__new__(Handler)
        for n in ["-1", "garbage", "33554433"]:
            handler.headers = {"Content-Length": n}
            with self.assertRaises(ValueError): handler._request_size(33554432)
        self.assertEqual(student_pages._deadline_ts({"deadline": 1}), 1)
        self.assertEqual(student_pages._deadline_ts({"started_at": 1, "duration_min": 1}), 61)
        self.assertEqual(student_pages._deadline_ts({"created_at": 1}), 0)

    def test_whole_submission_freezes_profile_across_tasks(self):
        judge_profile.save(1.5)
        _, snap = submissions.archive(self.cid, self.kh, {"candy/candy.cpp": b"int main(){}", "other/other.cpp": b"int main(){}"})
        exam = {"problems": [self.problem, {"no": 2, "pid": "Q", "name": "other", "full": 100}]}
        seen = []
        def run(*a, **kw):
            seen.append(kw["profile"]["time_scale"])
            judge_profile.save(2)
            return {"status": 1, "score": 100}
        with patch.object(judgelocal, "limits_of", return_value=(1000,512)), patch.object(judgelocal, "judge_source", side_effect=run):
            grading.judge_csp(self.cid, self.kh, {1: snap+"/candy/candy.cpp", 2: snap+"/other/other.cpp"}, exam, "new")
        self.assertEqual(seen, [1.5,1.5])
        self.assertEqual(store.load_results(self.cid)[self.kh]["judge_profile"]["time_scale"], 1.5)


if __name__ == "__main__":
    unittest.main()
