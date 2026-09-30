#!/bin/bash
# 全量验收（六套）
set -u
echo "--- 1/6 三种赛制 ---";   bash /tmp/_e2e_rules.sh    2>&1 | grep -E "FAIL|结果："
echo "--- 2/6 新功能 ---";     bash /tmp/_e2e_new.sh      2>&1 | grep -E "FAIL|结果："
echo "--- 3/6 名单分组 ---";   bash /tmp/_e2e_roster.sh   2>&1 | grep -E "FAIL|结果："
echo "--- 4/6 目录结构 ---";   bash /tmp/_e2e_tree.sh     2>&1 | grep -cE "\[PASS\]" | sed 's/^/  通过项：/'
echo "--- 5/6 建题目 ---";     bash /tmp/_e2e_mkproblem.sh 2>&1 | grep -E "FAIL|结果：|\[PASS\]" | tail -12
echo "--- 6/6 本地自测 ---"
cd /root/csp-exam
for T in selftest_roster.py selftest_make_problem.py; do
  printf '  %-26s ' "$T"
  python3 "$T" 2>&1 | grep -E "结果：" | sed 's/^/ /'
done
for T in selftest.py selftest_wrapper.py selftest_slug.py selftest_importer.py; do
  printf '  %-26s ' "$T"
  if python3 "$T" >/dev/null 2>&1; then echo "通过"; else echo "失败"; fi
done
