"""管理业务边界：失败不损坏题目、改配置撤回成绩、路径与 zip 校验。"""
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import importer,judgelocal,localoj,problems,store

class Management(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="csp-mgmt-unit-")
        self.patches=[patch.object(store,"DATA_DIR",self.temp.name),patch.object(judgelocal,"PROBLEMS_DIR",self.temp.name+"/problems")]
        for p in self.patches:p.start()
        self.cid=store.create_contest("测试","CSP")["id"]
        store.save_exam(self.cid,{"problems":[{"no":1,"pid":"P","name":"sum","full":100}],"released":False})
        self.files={"1.in":b"2 3\n","1.out":b"5\n"}
        self.assertTrue(problems.create_problem("P","加法",self.files,statement="# 原题面",time_ms=1000,memory_mb=256,name="sum")["ok"])
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.temp.cleanup()
    def result(self):
        store.save_results(self.cid,{"K":{"submitted_at":"x","problems":{"T1":{"score":100}},"submission_exam":store.load_exam(self.cid),"judge_rule":{"key":"CSP"}}})
    def test_invalid_overwrite_preserves_original(self):
        before=problems.load_codes()
        result=problems.create_problem("P","坏版本",{"1.in":b"broken"},overwrite=True)
        self.assertFalse(result["ok"])
        self.assertEqual(judgelocal.cases_of("P")[0]["out"],b"5\n")
        self.assertEqual(localoj.problem_statement("P"),"# 原题面")
        self.assertEqual(problems.load_codes(),before)
    def test_limits_change_withdraws_and_blocks_release(self):
        self.result();store.set_released(self.cid,True)
        problems.remember_problem("P",time_ms=1200)
        self.assertFalse(store.load_exam(self.cid)["released"])
        self.assertTrue(store.load_results(self.cid)["K"].get("rejudge_error"))
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
    def test_inflight_limits_change_is_rejected(self):
        self.result();r=store.load_results(self.cid);r["K"]["judging"]=True;store.save_results(self.cid,r)
        with self.assertRaises(OSError):problems.remember_problem("P",time_ms=1200)
        self.assertEqual(judgelocal.limits_of("P"),(1000,256))
        self.assertFalse(problems.create_problem("P","新数据",self.files,overwrite=True)["ok"])
    def test_changed_exam_and_rule_withdraw_publication(self):
        self.result();store.set_released(self.cid,True)
        e=store.load_exam(self.cid);e["problems"][0]["name"]="changed";store.save_exam(self.cid,e)
        self.assertFalse(store.load_exam(self.cid)["released"])
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
        store.update_contest(self.cid,rule="IOI")
        with self.assertRaises(ValueError):store.set_released(self.cid,True)
    def test_replacing_roster_with_results_is_rejected(self):
        self.result()
        with self.assertRaises(ValueError):store.replace_roster(self.cid,["新学生"])
    def test_no_testcase_order_fallback_or_traversal(self):
        judgelocal.store_problem("P",[{"name":"alpha","in":b"input","out":b"answer"}])
        self.assertEqual(localoj.read_problem_case("P","1"),("",""))
        self.assertEqual(localoj.read_problem_case("P","../../outside"),("",""))
        self.assertEqual(localoj.read_problem_case("P","alpha"),("input","answer"))
    def test_zip_rejects_traversal_and_duplicates(self):
        for names in [["../x"],["same","same"]]:
            p=Path(self.temp.name)/"bad.zip"
            with zipfile.ZipFile(p,"w") as z:
                for name in names:z.writestr(name,b"x")
            with self.assertRaises(ValueError):importer.Bundle(str(p))
    def test_empty_import_is_not_success(self):
        p=Path(self.temp.name)/"empty.zip"
        with zipfile.ZipFile(p,"w"):pass
        self.assertFalse(importer.import_problemset(str(p),dry_run=True,log=lambda *args:None)["ok"])

if __name__=="__main__":unittest.main()
