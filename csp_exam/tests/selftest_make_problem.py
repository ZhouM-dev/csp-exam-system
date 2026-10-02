#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：建题目模块（数据配对、题目包生成、标程/题面识别）。

用法：python3 selftest_make_problem.py
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core import hydro_client, importer, problems, store, wrapper  # noqa: E402
import make_problem as mp  # noqa: E402

PASS = FAIL = 0


def ok(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (label, extra))


def main() -> int:
    print("=== 1. 数据配对 ===")
    files = {
        "1.in": b"1 2\n", "1.out": b"3\n",
        "10.in": b"5 5\n", "10.out": b"10\n",
        "2.in": b"2 2\n", "2.out": b"4\n",
        "a.in": b"7 8\n", "a.ans": b"15\n",
        "sample1.in": b"1 1\n", "sample1.out": b"2\n",
    }
    cases, problems = mp.pair_cases(files)
    names = [c["name"] for c in cases]
    ok(len(cases) == 5, "配出 5 组", names)
    ok(names[0] == "1" and names[1] == "2" and names[2] == "10",
       "按名字自然排序（2 在 10 前）", names[:3])
    ok(all(c["out"] for c in cases), "每组都配到了答案")
    ok(problems == [], "没有异常文件", problems)

    print()
    print("=== 2. 缺答案 / 多余文件要报出来 ===")
    cases2, problems2 = mp.pair_cases({
        "1.in": b"1\n", "1.out": b"1\n",
        "2.in": b"2\n",
        "3.out": b"3\n",
        "readme.txt": b"x",
        "weird.dat": b"x",
    })
    ok(len(cases2) == 2, "2 组（第 2 组只有输入）", [c["name"] for c in cases2])
    ok(cases2[1]["out"] is None, "第 2 组没有答案会被标出来")
    joined = " ".join(problems2)
    ok("3.out" in joined, "有答案没输入要被报出来")
    ok("weird.dat" in joined, "不认识的文件要被报出来")

    print()
    print("=== 3. zip 展开 ===")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data/1.in", "1 2\n")
        z.writestr("data/1.out", "3\n")
        z.writestr("data/2.in", "3 4\n")
        z.writestr("data/2.out", "7\n")
        z.writestr("__MACOSX/._1.in", "junk")
    flat, notes = mp.unpack({"tests.zip": buf.getvalue()})
    ok(len(flat) == 4, "zip 里 4 个文件被展开", list(flat))
    ok(all("__MACOSX" not in k for k in flat), "macOS 垃圾文件被忽略")
    cases3, _ = mp.pair_cases(flat)
    ok(len(cases3) == 2 and all(c["out"] for c in cases3), "zip 里的数据也能配对")
    ok(any("zip" in n for n in notes), "提示里说明展开过 zip")

    print()
    print("=== 4. 标程 / 题面识别 + 生成题目包 ===")
    files4 = dict(files)
    files4["标程.cpp"] = b"int main(){}"
    files4["题目.md"] = "# 测试题\n\n描述".encode()
    std, std_name = mp.find_std(files4)
    ok(std_name == "标程.cpp", "认出标程", std_name)
    stmt, stmt_name = mp.take_statement(files4)
    ok("测试题" in stmt, "认出题面", stmt_name)

    tmp = tempfile.mkdtemp(prefix="mk-")
    try:
        cases4, _ = mp.pair_cases({k: v for k, v in files.items()})
        root = mp.build_package(tmp, "T9001", "加法测试", cases4, files,
                                time_ms=1500, memory_mb=128, statement=stmt,
                                std_source=std)
        checks = [
            (os.path.isfile(os.path.join(root, "problem.yaml")), "problem.yaml"),
            (os.path.isfile(os.path.join(root, "problem_zh.md")), "problem_zh.md"),
            (os.path.isfile(os.path.join(root, "testdata", "config.yaml")), "testdata/config.yaml"),
            (os.path.isfile(os.path.join(root, "std", "solution.cpp")), "std/solution.cpp"),
        ]
        for cond, label in checks:
            ok(cond, "生成 " + label)
        cfg = io.open(os.path.join(root, "testdata", "config.yaml"), encoding="utf-8").read()
        ok("time: 1500" in cfg and "memory: 128" in cfg, "时限/内存在 config.yaml 里", cfg.replace("\n", " "))
        yml = io.open(os.path.join(root, "problem.yaml"), encoding="utf-8").read()
        ok("pid: T9001" in yml and "加法测试" in yml, "pid/标题写进了 problem.yaml")
        data = sorted(os.listdir(os.path.join(root, "testdata")))
        ok(data == ["1.in", "1.out", "2.in", "2.out", "3.in", "3.out",
                    "4.in", "4.out", "5.in", "5.out", "config.yaml"],
           "测试数据重排成 1..5 对", data)
        ok(io.open(os.path.join(root, "testdata", "1.in"), encoding="utf-8").read() == "1 2\n",
           "第 1 组内容正确")
        ok(io.open(os.path.join(root, "testdata", "1.out"), encoding="utf-8").read() == "3\n",
           "第 1 组答案正确")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("=== 5. 参数校验 / 缺答案也能建（留空 .out）===")
    bad = mp.create_problem("坏 名字!", "x", {"1.in": b"1\n", "1.out": b"1\n"})
    ok(not bad["ok"] and "题目标识" in bad["error"], "非法题目标识被拒", bad["error"][:30])
    no_case = mp.create_problem("T9002", "x", {"readme.md": b"x"})
    ok(not no_case["ok"] and "测试点" in no_case["error"], "没有数据时给出清楚提示")

    print()
    print("=== 6. 出题工程结构自动识别 ===")
    bundle = {
        "题目库/G01-加边后最小生成树/题目.md": "# 加边后最小生成树\n\n描述".encode(),
        "题目库/G01-加边后最小生成树/标程.cpp": b"int main(){}",
        "题目库/G01-加边后最小生成树/暴力.cpp": b"// ignore",
        "题目库/G01-加边后最小生成树/01.in": b"1\n", "题目库/G01-加边后最小生成树/01.out": b"2\n",
        "题目库/G01-加边后最小生成树/02.in": b"3\n", "题目库/G01-加边后最小生成树/02.out": b"4\n",
        "题目库/G01-加边后最小生成树/大样例/大样例.in": b"9\n" * 10,
        "题目库/G01-加边后最小生成树/大样例/大样例.out": b"8\n" * 10,
        "题目库/G01-加边后最小生成树/样例1.in": b"1 2\n",
        "题目库/G01-加边后最小生成树/样例1.out": b"3\n",
        "题目库/G01-加边后最小生成树/洛谷上传.zip": b"PK\x03\x04junk",
    }
    det = mp.detect_bundle(bundle)
    ok(det["count"] == 1, "识别出 1 道题", det["count"])
    p = det["problems"][0]
    ok(p["pid"] == "G01", "题目标识取分类号", p["pid"])
    ok(p["title"] == "加边后最小生成树", "标题取题名", p["title"])
    ok("加边后最小生成树" in p["statement"], "读到题面")
    ok(bool(p["std"]), "读到标程", p["std"])
    ok(len(p["cases"]) == 2, "2 组评测数据（样例/大样例不算）", [c[0] for c in p["cases"]])
    ok(len(p["big_samples"]) == 1, "识别出 1 组大样例", [b["label"] for b in p["big_samples"]])
    ok(len(p["samples"]) == 1, "识别出 1 组题目样例", [s["label"] for s in p["samples"]])
    ok(all("样例" not in c[1] and "大样例" not in c[1] for c in p["cases"]), "样例没混进评测数据")

    print()
    print("=== 7. 多道题 / 只传题目目录 / 扁平上传 ===")
    multi = dict(bundle)
    multi["题目库/G02-另一题/题目.md"] = "# 另一题".encode()
    multi["题目库/G02-另一题/data/01.in"] = b"1\n"
    multi["题目库/G02-另一题/data/01.out"] = b"1\n"
    det2 = mp.detect_bundle(multi)
    ok(det2["count"] == 2, "识别出 2 道题", [x["pid"] for x in det2["problems"]])
    flat = mp.detect_bundle({"1.in": b"1\n", "1.out": b"1\n"})
    ok(flat["count"] == 0, "纯 .in/.out 上传不会被误判成出题工程")

    print()
    print("=== 8. 大样例存档 / 读取 / 防目录穿越 ===")
    tmp2 = tempfile.mkdtemp(prefix="mk-sample-")
    try:
        info = mp.save_samples("G01", [p["big_samples"], p["samples"]], bundle, base_dir=tmp2)
        ok(info["files"] == 4, "存了 4 个文件（大样例 in/out + 样例 in/out）", info["files"])
        got = mp.load_samples("G01", base_dir=tmp2)
        kinds = [it["kind"] for it in got["items"]]
        ok("big" in kinds and "sample" in kinds, "清单里区分大样例与样例", kinds)
        names = [f["name"] for it in got["items"] for f in it["files"]]
        ok("大样例.in" in names and "样例1.out" in names, "文件名保留", names)
        ok(mp.sample_path("G01", "大样例.in", base_dir=tmp2) is not None, "能取到大样例文件")
        ok(mp.sample_path("G01", "../manifest.json", base_dir=tmp2) is None, "拒绝目录穿越")
        ok(mp.sample_path("G01", "不存在的文件", base_dir=tmp2) is None, "不存在的文件返回 None")
        ok(mp.load_samples("没有这道题", base_dir=tmp2) == {}, "没大样例的题返回空")
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print()
    print("=== 9. 单独上传的大样例（管理端④/单独设置）===")
    grp = mp.multipart_samples({"大样例.in": b"1 2\n", "大样例.out": b"3\n"})
    ok(len(grp) == 1 and grp[0]["in"] and grp[0]["out"], "配对成功", [g["label"] for g in grp])
    grp2 = mp.multipart_samples({"说明.txt": b"x"})
    ok(len(grp2) == 1 and grp2[0]["extra"], "只传附件也能收下")

    print()
    print("=== 10. 重名：标识被占用时自动让位（建题可以建重复的题）===")
    ok(mp.free_pid("G01", set()) == "G01", "标识空着：原样用")
    ok(mp.free_pid("G01", {"G01"}) == "G01b", "被占了：让到 G01b")
    ok(mp.free_pid("G01", {"G01", "G01b"}) == "G01c", "再让一位")
    ok(mp.free_pid("G01", {"G01", "G01c"}) == "G01b", "中间空出来的位置也能用")
    ok(mp.free_pid("G01", ["G01"]) == "G01b", "传 list 也行（内部转 set）")
    ok(mp.free_pid("", set()) == "problem", "空标识兜底，不炸")
    long40 = "y" * 40
    got = mp.free_pid(long40, {long40})
    ok(len(got) == 40 and got.endswith("b"), "顶格 40 字：截一下再加后缀", len(got))
    # 让位出来的标识必须**符合评测站的规矩**：只认字母开头的字母数字，
    # 短横/下划线会让它悄悄换成 #N（踩过：站点上多出一道没标识的僵尸题）
    for base in ("G01", "candy2", "P1000"):
        got = mp.free_pid(base, {base})
        ok(("-" not in got and "_" not in got and "." not in got
            and got[0].isalpha() and got.isalnum()),
           "让位标识 %s 是评测站认的形式" % got)

    print()
    print("=== 11. 「已删除」记号：core 里记、core 里读（页面与导入流程共用）===")
    old_dir = store.DATA_DIR
    tmp3 = tempfile.mkdtemp(prefix="mk-info-")
    store.DATA_DIR = tmp3
    try:
        ok(mp.is_deleted("G01") is False, "没记过 = 没删除")
        mp.set_deleted("G01", True)
        ok(mp.is_deleted("G01") is True, "打上记号后 is_deleted=True")
        ok(mp.deleted_pids() == {"G01"}, "deleted_pids 列得出来", mp.deleted_pids())
        mp.set_deleted("G02", True)
        mp.set_deleted("G01", False)
        ok(mp.deleted_pids() == {"G02"}, "取消记号后就不再算已删除", mp.deleted_pids())
        rec = mp.load_problem_info().get("G01") or {}
        ok("deleted" not in rec and "deleted_at" not in rec, "取消时把两个字段都清掉", sorted(rec))
        ok("deleted_at" in (mp.load_problem_info().get("G02") or {}), "留着的那道有时间戳")
        # 顺带验一下：记号不影响别的字段（题目编号/英文名那些）
        info = mp.load_problem_info()
        info["G02"] = dict(info.get("G02") or {}, code="T00009", name="candy")
        mp.save_problem_info(info)
        mp.set_deleted("G02", False)
        rec2 = mp.load_problem_info().get("G02") or {}
        ok(rec2.get("code") == "T00009" and rec2.get("name") == "candy",
           "取消删除不会碰题目编号/英文名",
           "%s / %s" % (rec2.get("code"), rec2.get("name")))

        print()
        print("=== 12. 作废旧缓存（覆盖重建时才用）===")
        os.makedirs(os.path.join(tmp3, "statements"), exist_ok=True)
        os.makedirs(os.path.join(tmp3, "samples", "G02"), exist_ok=True)
        with open(os.path.join(tmp3, "statements", "G02.md"), "w", encoding="utf-8") as f:
            f.write("# 旧题面")
        gone = mp.drop_problem_cache("G02")
        ok(set(gone) == {"题面缓存", "大样例存档"}, "两处都报了", gone)
        ok(not os.path.isfile(os.path.join(tmp3, "statements", "G02.md")), "题面缓存没了")
        ok(not os.path.isdir(os.path.join(tmp3, "samples", "G02")), "大样例存档没了")
        ok(mp.drop_problem_cache("没有这道题") == [], "没有残留时不报错、返回空")
    finally:
        store.DATA_DIR = old_dir
        shutil.rmtree(tmp3, ignore_errors=True)

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
