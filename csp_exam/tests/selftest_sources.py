#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：「学生上传的文件 → 每题用哪个代码」的宽松匹配（替代原来的目录结构校验）。

用法：python3 selftest_sources.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core import hydro_client, importer, problems, store, wrapper  # noqa: E402
import wrapper  # noqa: E402

PASS = FAIL = 0

#: 一场三题的考试（英文名 candy/road/meal）
EXAM = [{"no": 1, "slug": "candy"}, {"no": 2, "slug": "road"}, {"no": 3, "slug": "meal"}]


def ok(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (label, extra))


def pick(files, problems=None):
    return wrapper.pick_sources(files, EXAM if problems is None else problems)


def main() -> int:
    print("=== 1. 标准 CSP 目录结构 ===")
    p, miss = pick(["GD-0001/candy/candy.cpp", "GD-0001/road/road.cpp", "GD-0001/meal/meal.cpp"])
    ok(p == {1: "GD-0001/candy/candy.cpp", 2: "GD-0001/road/road.cpp",
             3: "GD-0001/meal/meal.cpp"}, "三题都匹配上", p)
    ok(miss == [], "没有缺失", miss)

    print()
    print("=== 2. 宽松档（显式 strict=False）：大小写 / 文件名随意 / 没有外层文件夹 ===")
    # 默认口径已经是严格区分大小写（考场口径，见 wrapper.STRICT_CASE）。
    # 这一节测的是**放宽档**还管不管用，所以显式关掉严格模式，别依赖默认值。
    wrapper.set_strict_case(False)
    try:
        p, _ = pick(["GD-0001/Candy/Candy.CPP", "GD-0001/road/main.cpp", "candy/meal.cpp"])
        ok(p.get(1) == "GD-0001/Candy/Candy.CPP", "大小写不敏感", p.get(1))
        ok(p.get(2) == "GD-0001/road/main.cpp", "文件夹对了、文件名随意", p.get(2))
        ok(p.get(3) == "candy/meal.cpp", "没有考号文件夹也能认（按英文名找）", p.get(3))
    finally:
        wrapper.set_strict_case(True)

    print()
    print("=== 2b. 默认档（严格区分大小写）：只差大小写不算命中 ===")
    p, miss = pick(["GD-0001/Candy/Candy.CPP"])
    ok(p.get(1) != "GD-0001/Candy/Candy.CPP", "Candy/Candy.CPP 不会被当成 candy 那一题", p.get(1))
    ok(any("大小写" in m or "candy" in m for m in miss), "缺失说明里点出了原因", miss)

    print()
    print("=== 3. 只有单个文件（没建文件夹）===")
    p, _ = pick(["candy.cpp", "road.cpp", "meal.cpp"])
    ok(p == {1: "candy.cpp", 2: "road.cpp", 3: "meal.cpp"}, "文件名就是英文名", p)

    print()
    print("=== 4. 找不到就报清楚，而不是退回整份提交 ===")
    p, miss = pick(["GD-0001/candy/candy.cpp", "GD-0001/candy/candy.exe", "GD-0001/README.txt"])
    ok(p == {1: "GD-0001/candy/candy.cpp"}, "只匹配到第 1 题", p)
    ok(len(miss) == 2 and "第 2 题" in miss[0] and "road" in miss[0], "缺的两题有说明", miss[:1])
    ok(all(".exe" not in m for m in miss), "编译产物不算代码")

    print()
    print("=== 5. 同目录多个源码：优先与英文名同名的 ===")
    p, _ = pick(["GD-0001/candy/candy.cpp", "GD-0001/candy/brute.cpp"])
    ok(p.get(1) == "GD-0001/candy/candy.cpp", "选同名的那个", p.get(1))
    p2, _ = pick(["GD-0001/candy/a.cpp", "GD-0001/candy/b.cpp"])
    ok(p2.get(1) in ("GD-0001/candy/a.cpp", "GD-0001/candy/b.cpp"),
       "没有同名文件时也不拒绝（挑一个判，学生自己承担）", p2.get(1))

    print()
    print("=== 6. 一题一文件兜底（只报了一题、只交了一个文件）===")
    p, miss = pick(["GD-0001/solution.cpp"], [{"no": 1, "slug": "candy"}])
    ok(p == {1: "GD-0001/solution.cpp"} and miss == [], "只有一题一个文件时就用它", p)

    print()
    print("=== 7. 不把别人的东西认成自己的题 ===")
    p, _ = pick(["GD-0001/candy/candy.cpp", "GD-0002/road/road.cpp"])
    ok(1 in p and p.get(2) == "GD-0002/road/road.cpp", "多考号混在一起时按英文名各取所需", p)

    print()
    print("=== 8. 中文/奇怪文件名不影响 ===")
    p, _ = pick(["学生01/candy/糖果.cpp", "学生01/road/道路.cpp"])
    ok(p.get(1) == "学生01/candy/糖果.cpp" and p.get(2) == "学生01/road/道路.cpp",
       "文件夹对了就行（文件名可以是中文）", p)

    print()
    print("=== 9. wrapper 自身（包装与 freopen 提示）没被改坏 ===")
    w = wrapper.wrap("int main(){}", "candy")
    ok("candy.in" in w and "candy.out" in w and "int main(){}" in w, "包装包含题名文件与原始代码")
    ok(wrapper.can_wrap(".cpp") and not wrapper.can_wrap(".py"), "只包装 C/C++")
    ok("未检测到 freopen" in wrapper.check_freopen("int main(){}", "candy"), "忘写 freopen 会提示")
    ok(wrapper.check_freopen('freopen("candy.in","r",stdin);', "candy") == "", "写对了就不提示")

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
