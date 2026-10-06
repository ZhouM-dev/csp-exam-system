#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：名单分组、按场次随机生成考号、旧数据迁移、总分求和。

用法：python3 selftest_roster.py

> 考号口径在 v2 变过：以前是「前缀 + 4 位连续序号」的随机排列（GD-0001），
> 现在是「前缀 + 级别字母 + 5 位纯随机数」（GD-S48213）。本文件的断言跟着改了，
> 并且**只断言规则、不断言具体号码**（号码是随机的，断言具体值没意义）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core import hydro_client, importer, problems, store, wrapper  # noqa: E402
import store  # noqa: E402

PASS = FAIL = 0

#: 新考号格式：前缀-级别字母+5 位数字
KAOHAO_RE = re.compile(r"^GD-[JS]\d{5}$")


def ok(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (label, extra))


def nums_of(roster: dict) -> list[int]:
    """取考号里的数字部分（新老格式都能取）。"""
    out = []
    for k in roster:
        _, _, digits = store.split_kaohao(k)
        if digits.isdigit():
            out.append(int(digits))
        else:
            tail = k.rsplit("-", 1)[-1]
            tail = re.sub(r"^[JS]", "", tail)
            if tail.isdigit():
                out.append(int(tail))
    return sorted(out)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="csp-roster-")
    store.DATA_DIR = tmp
    try:
        print("=== 1. 名单分组 ===")
        g = store.create_group("初三1班", ["学生01", "学生02", "学生03"])
        ok(g["gid"] == "g1" and len(g["students"]) == 3, "建分组", g["gid"])
        ok(store.get_group("g1")["name"] == "初三1班", "读分组")
        g2 = store.create_group("集训A班", ["学生01", "赵六"])       # 跨组重名允许
        ok(len(store.load_groups()) == 2, "两个分组", g2["gid"])
        dup = store.clean_names(["学生01", "学生01", " 学生02 ", "", "学生02"])[1]
        ok(sorted(dup) == ["学生01", "学生02"], "组内重名会去重", dup)
        store.update_group("g1", names=["学生01", "学生02", "学生03", "周七"])
        ok(len(store.get_group("g1")["students"]) == 4, "改名单")
        ok(store.delete_group("g2") is True, "删分组")
        ok(store.delete_group("g2") is False, "重复删返回 False")

        print()
        print("=== 2. 一场考试的考号生成（纯随机）===")
        c = store.create_contest("随机号测试", "CSP", level="J")
        cid = c["id"]
        ok(c.get("level") == "J" and c.get("duration_min") == 210,
           "新建比赛带级别与默认时长", f"level={c.get('level')} 时长={c.get('duration_min')}")

        names = ["学生%02d" % i for i in range(1, 21)]
        roster = store.assign_kaohaos(cid, names)
        ok(len(roster) == 20, "20 人全部进名单")
        ok(all(KAOHAO_RE.match(k) for k in roster),
           "考号格式是 GD-<级别><5 位数字>", sorted(roster)[:2])
        ok(all(k.startswith("GD-J") for k in roster),
           "级别字母跟着比赛（J 组）", sorted(roster)[0])
        ns = nums_of(roster)
        ok(len(set(ns)) == 20, "场内不重号")
        ok(ns != list(range(1, 21)),
           "数字是纯随机，不是 1..20 的排列", ns[:5])
        ok(max(ns) - min(ns) > 20, "号码跨度大（不是连续小号）", f"{min(ns)}~{max(ns)}")

        # 随机性：换一场，结果应该完全不一样
        cid2 = store.create_contest("随机号测试2", "CSP", level="S")["id"]
        r2 = store.assign_kaohaos(cid2, names)
        ok(all(k.startswith("GD-S") for k in r2), "另一场是 S 组，级别字母跟着变")
        ok(set(roster) != set(r2), "两场考试的号完全不同")

        # 连发 3 场，结果互不相同
        sets = []
        for i in range(3):
            cx = store.create_contest("连发%d" % i, "CSP", level="J")["id"]
            sets.append(tuple(sorted(store.assign_kaohaos(cx, names))))
        ok(len(set(sets)) == 3, "连发 3 场，三次结果互不相同")

        print()
        print("=== 3. 同一场重复导入 ===")
        before = dict(store.load_roster(cid))
        store.assign_kaohaos(cid, names[:10])          # 再导入其中 10 人
        after = store.load_roster(cid)
        ok(all(after[k]["name"] == v["name"] for k, v in before.items()),
           "老学生考号不变")
        ok(set(after) == set(before), "重复导入不会多出考号")
        store.assign_kaohaos(cid, ["新同学甲", "新同学乙"])
        after = store.load_roster(cid)
        ok(len(after) == 22, "新学生追加进来", len(after))
        added = set(after) - set(before)
        ok(len(added) == 2 and all(KAOHAO_RE.match(k) for k in added),
           "新学生拿到合规的新号")
        ok(len(nums_of(after)) == len(set(nums_of(after))), "追加后仍然不重号")

        print()
        print("=== 4. 整场重排 / 重建 ===")
        old_names = {k: v["name"] for k, v in store.load_roster(cid).items()}
        store.reshuffle_kaohaos(cid)
        new = store.load_roster(cid)
        ok(sorted(v["name"] for v in new.values()) == sorted(old_names.values()),
           "重排后学生不变", len(new))
        ok(set(new) != set(old_names), "重排后考号确实换了")
        ok(all(KAOHAO_RE.match(k) for k in new), "重排后的号仍是新格式")
        store.replace_roster(cid, ["只有一个人"])
        ok(len(store.load_roster(cid)) == 1, "重建本场名单")

        print()
        print("=== 5. 账号名带场次 + 找考场 ===")
        one = list(store.load_roster(cid).items())[0]
        ok(one[1]["uname"] == "%s-%s" % (cid, one[0]), "账号名 = 场次-考号", one[1]["uname"])
        kh_here = one[0]
        hits = store.find_contests_of_kaohao(kh_here)
        ok(cid in [h["id"] for h in hits], "按考号能找到自己的考场", [h["id"] for h in hits])

        # 同一批人在两场里各拿各的随机号 —— 号不会串场
        cid3 = store.create_contest("另一场", "OI", level="J")["id"]
        r3 = store.replace_roster(cid3, ["甲", "乙", "丙"])
        ok(not (set(r3) & set(store.load_roster(cid))),
           "两场之间考号不会撞（随机号 + 场次隔离）",
           f"共 {len(set(r3) & set(store.load_roster(cid)))} 个重号")
        # 同一个号只属于一场里的人
        for k in list(r3)[:1]:
            owners = [c["id"] for c in store.find_contests_of_kaohao(k)]
            ok(owners == [cid3], "一个考号只归属一个场次", owners)

        print()
        print("=== 6. 旧数据迁移（考号列表 + 全局学生池）===")
        legacy = os.path.join(tmp, "legacy")
        os.makedirs(os.path.join(legacy, "contests", "c9"), exist_ok=True)
        with open(os.path.join(legacy, "contests.json"), "w", encoding="utf-8") as f:
            json.dump([{"id": "c9", "title": "老比赛", "rule": "CSP", "open": True}], f)
        with open(os.path.join(legacy, "students.json"), "w", encoding="utf-8") as f:
            json.dump({"GD-0001": {"name": "老张", "pw": "pw1", "uid": 7}}, f)
        with open(os.path.join(legacy, "contests", "c9", "roster.json"), "w", encoding="utf-8") as f:
            json.dump(["GD-0001"], f)                       # 老格式：考号列表
        store2_data = legacy
        old_dir, store.DATA_DIR = store.DATA_DIR, store2_data
        try:
            r = store.load_roster("c9")
            ok(r.get("GD-0001", {}).get("name") == "老张", "老名单带出姓名", r)
            ok(r["GD-0001"]["uname"] == "GD-0001", "老账号名沿用考号（历史提交不丢）")
            ok(r["GD-0001"]["pw"] == "pw1", "老密码保留")
            ok(r["GD-0001"]["uid"] == 7, "老 uid 保留")
            ok(store.split_kaohao("GD-0001") == ("", "", ""),
               "老格式考号被识别为老格式（迁移脚本据此改名）")
            ok(store.split_kaohao("GD-S48213") == ("GD", "S", "48213"),
               "新格式考号能拆出前缀/级别/数字")
        finally:
            store.DATA_DIR = old_dir

        print()
        print("=== 7. 总分按每题求和 ===")
        e = {"problems": {"T1": {"score": 17}, "T2": {"score": 0}, "T3": {"score": 70}}}
        ok(store.entry_total(e) == 87, "17+0+70=87", store.entry_total(e))
        ok(store.entry_total({}) == 0, "空记录 0 分")
        ok(store.entry_total({"problems": {}, "total": 300}) == 0, "不信任存储的 total")

        print()
        print("=== 8. 级别 / 时长的兜底（兼：反馈模式已删）===")
        ok(store.level_of({}) == "S", "老比赛缺 level 按 S 兜底")
        ok(store.duration_of({"level": "J"}) == 210, "J 组默认 210 分钟")
        ok(store.duration_of({"level": "S"}) == 240, "S 组默认 240 分钟")
        ok(store.duration_of({"level": "J", "duration_min": 200}) == 200, "自定时长优先")
        # 「反馈模式」（赛制 feedback + 本场 feedback_mode）整套已删：只剩一种行为
        # —— 老师公布成绩之前学生端零反馈。这里钉住删除，免得以后又被加回来。
        ok(not any("feedback" in r for r in store.RULES.values())
           and not any(hasattr(store, n) for n in
                       ("FEEDBACK_MODES", "DEFAULT_FEEDBACK", "feedback_mode_of", "is_full_mode")),
           "「反馈模式」概念已删（RULES 无 feedback 键，四个接口都不在）")

        print()
        print("=== 9. 题目编号（T00001，老师侧）/ 英文名（candy，学生侧）===")
        ok(store.code_of({"name": "candy", "code": "T00001"}) == "candy",
           "英文名优先取 name（本场配题时填的）")
        ok(store.code_of({"code": "candy", "no": 1}) == "candy"
           and store.code_of({"slug": "m02", "no": 2}) == "m02",
           "老数据退回 code / slug（里面存的就是英文名）")
        ok(store.code_of({"no": 3}) == "p3"
           and store.code_of({"code": "T00007", "no": 1}) == "p1",
           "都没填就 p{题号} 兜底（刻意不用 T{题号}，那长得像题目编号）；"
           "code 里是题目编号时不拿它当英文名")
        ok(store.number_of({"code": "T00007"}) == "T00007"
           and store.number_of({"number": "T00012", "code": "T00007"}) == "T00012"
           and store.number_of({"code": "candy"}) == "",
           "题目编号取 number/code（老数据没有编号就是空）")
        ok(store.valid_code("candy") and store.valid_code("t1_x")
           and not store.valid_code("Candy") and not store.valid_code("my-problem")
           and not store.valid_code("中文"), "英文名合法性判定（小写字母/数字/下划线）")

        print()
        print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
