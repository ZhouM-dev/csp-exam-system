"""自测：题单导入器的难度映射、考点切分、题目目录识别。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core import hydro_client, importer, problems, store, wrapper  # noqa: E402
import import_problemset as ip

ok = bad = 0
print("难度映射（洛谷 7 级 -> 站点数值）：")
for label, want in [("入门", 1), ("普及−", 2), ("普及/提高−", 3), ("普及+/提高", 4),
                    ("提高+/省选−", 5), ("省选/NOI−", 6), ("NOI/NOI+", 7), ("", 3)]:
    got = ip.difficulty_of(label)
    flag = got == want
    ok, bad = ok + flag, bad + (not flag)
    print(f"  {label or '(空)':12s} -> {got}   {'OK' if flag else '错误，期望 %d' % want}")

print()
topics = ip.split_topics("最小生成树｜树上路径最大边｜增量维护")
want = ["最小生成树", "树上路径最大边", "增量维护"]
flag = topics == want
ok, bad = ok + flag, bad + (not flag)
print(f"考点切分（全角竖线）：{topics}  {'OK' if flag else '错误，期望 ' + str(want)}")

topics2 = ip.split_topics("排序|二分查找、双指针")
print(f"考点切分（兼容半角/顿号）：{topics2}")

print()
print(f"结果：{ok} 项通过，{bad} 项失败")
sys.exit(1 if bad else 0)
