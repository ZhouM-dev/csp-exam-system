#!/bin/bash
# 验收：管理端能看到学生"实际传上来的目录结构"（提交详情里的目录树 + 每题用的是哪个文件）
#
# 本轮改造后的口径：
#   * 题目编号由系统给（题库登记，如 T1；题库里没登记的老题目按位置兜底 T{题号}）——
#     不再叫「英文名」，所以**不能假设第 1 题叫 candy**，要从 exam.json 里读真实编号
#   * 提交不再做目录结构校验（不再退回），改成"尽量对上题"：
#     对不上的题在详情页列进「没匹配到代码的题目」
#   * 提交详情删了「目录结构校验」，目录树改成**文件名可点**、并标出个人信息文件
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=== 准备：建一场临时考试（CSP），导入一名学生 ==="
curl -s -o /dev/null --max-time 20 -X POST "$BASE/admin/new?key=$KEY" \
  --data-urlencode "title=目录树测试" --data-urlencode "rule=CSP"
CID=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '目录树测试'][0])")
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/exam?key=$KEY&c=$CID" \
  --data-urlencode 'problems_json=[{"pid":"P1001"},{"pid":"P1002"},{"pid":"P1003"}]' \
  --data-urlencode 'full=100'
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/roster?key=$KEY&c=$CID" \
  --data-urlencode "names=测试学生" --data-urlencode "prefix=GD"
KH=$(python3 -c "
from csp_exam.core import store
print(sorted(store.load_roster('$CID'))[0])")
# 本场三道题的**题目编号**（学生用它建文件夹、命名源文件、写 freopen）
CODES=$(python3 -c "
from csp_exam.core import store
e = store.load_exam('$CID')
print(' '.join(store.code_of(p) for p in e['problems']))")
set -- $CODES
A1=$1; A2=$2; A3=$3
echo "  比赛 $CID，考号 $KH，题目编号：$A1 / $A2 / $A3"
J=$T/tree.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST "$BASE/enter" --max-time 15 \
  --data-urlencode "c=$CID" --data-urlencode "kaohao=$KH"

echo
echo "=== 1. 先交一份【有毛病】的：别的题的代码放错文件夹 + 夹带编译产物 ==="
# 第 2 题的文件夹里放第 3 题的代码（改前会被错配到第 2 题，现在要如实报出来），
# 再夹带一个 .exe（编译产物，判分忽略但页面要标出来）
mkdir -p $T/tree_bad/$A1 $T/tree_bad/$A2
printf '#include <bits/stdc++.h>\nusing namespace std;\nint main(){ freopen("%s.in","r",stdin); freopen("%s.out","w",stdout); return 0;}\n' "$A1" "$A1" > $T/tree_bad/$A1/$A1.cpp
printf 'MZ\x90\x00binary\n' > $T/tree_bad/$A1/$A1.exe
printf '#include <bits/stdc++.h>\nusing namespace std;\nint main(){ freopen("%s.in","r",stdin); freopen("%s.out","w",stdout); return 0;}\n' "$A3" "$A3" > $T/tree_bad/$A2/$A3.cpp
curl -s -b $J -o $T/up_bad.html -w '  上传 -> HTTP %{http_code}\n' --max-time 60 \
  -F "files=@$T/tree_bad/$A1/$A1.cpp;filename=$KH/$A1/$A1.cpp" \
  -F "files=@$T/tree_bad/$A1/$A1.exe;filename=$KH/$A1/$A1.exe" \
  -F "files=@$T/tree_bad/$A2/$A3.cpp;filename=$KH/$A2/$A3.cpp" \
  "$BASE/upload?c=$CID"
grep -q '判分用的文件' $T/up_bad.html \
  && pass "交卷回执列出了判分用的文件（不再做目录结构校验、也不退回）" \
  || fail "交卷回执没有列出判分用的文件"
grep -q '目录结构不合格' $T/up_bad.html \
  && fail "还在做目录结构校验（应该已经删掉）" || pass "没有「目录结构不合格」的退回了"

echo
echo "=== 2. 管理端详情页应能看到这份【传上来的结构】+ 哪题没对上 ==="
curl -s -o $T/detail_bad.html -w '  GET /admin/student -> HTTP %{http_code}\n' --max-time 30 \
  "$BASE/admin/student?key=$KEY&c=$CID&k=$KH"
python3 - <<PY
import html
p = html.unescape(open("$T/detail_bad.html", encoding="utf-8").read())
checks = [
    ('上传的目录结构' in p or '学生上传的目录结构' in p, '有「学生上传的目录结构」区块'),
    ("$A1/" in p and "$A1.cpp" in p, '树里有学生真实用的 $A1/$A1.cpp'),
    ("$A1.exe" in p, '树里有夹带上传的 $A1.exe'),
    ('多余文件' in p, '多余文件被标注出来'),
    ('没匹配到代码的题目' in p, '列出没匹配到代码的题目'),
    ("$A3.cpp" in p and '第 3 题' in p, '点明 $A2/ 里的 $A3.cpp 是第 3 题的代码'),
    ('目录结构校验' not in p, '不再显示目录结构校验'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
import re
m = re.search(r'<pre class="tree[^"]*">(.*?)</pre>', p, re.S)
print('   树内容：')
for line in (re.sub(r'<[^>]+>', '', m.group(1)).strip().splitlines() if m else []):
    print('     ', line)
PY

echo
echo "=== 3. 再交一份【规范】的（连个人信息文件一起）→ 树里标出每题用的是哪个文件 ==="
rm -rf $T/tree_ok; mkdir -p $T/tree_ok/$A1 $T/tree_ok/$A2 $T/tree_ok/$A3
for P in $A1 $A2 $A3; do
  printf '#include <bits/stdc++.h>\nusing namespace std;\nint main(){freopen("%s.in","r",stdin);freopen("%s.out","w",stdout);return 0;}\n' "$P" "$P" > $T/tree_ok/$P/$P.cpp
done
printf '姓名：测试学生\n学校：测试中学\n提交的程序：%s.cpp %s.cpp %s.cpp\n' "$A1" "$A2" "$A3" > "$T/tree_ok/测试学生.txt"
curl -s -b $J -o $T/up_ok.html -w '  上传 -> HTTP %{http_code}\n' --max-time 60 \
  -F "files=@$T/tree_ok/$A1/$A1.cpp;filename=$KH/$A1/$A1.cpp" \
  -F "files=@$T/tree_ok/$A2/$A2.cpp;filename=$KH/$A2/$A2.cpp" \
  -F "files=@$T/tree_ok/$A3/$A3.cpp;filename=$KH/$A3/$A3.cpp" \
  -F "files=@$T/tree_ok/测试学生.txt;filename=$KH/测试学生.txt" \
  "$BASE/upload?c=$CID"
grep -q '个人信息文件' $T/up_ok.html \
  && pass "交卷回执检查了个人信息文件（本轮新增）" || fail "回执页没有个人信息文件检查项"
curl -s -o $T/detail_ok.html -w '  GET /admin/student -> HTTP %{http_code}\n' --max-time 30 \
  "$BASE/admin/student?key=$KEY&c=$CID&k=$KH"
python3 - <<PY
import html, re
p = html.unescape(open("$T/detail_ok.html", encoding="utf-8").read())
m = re.search(r'<pre class="tree[^"]*">(.*?)</pre>', p, re.S)
tree = re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ''
print('   树内容：')
for line in tree.splitlines():
    print('     ', line)
for okv, label in [
    ('第 1 题（$A1）' in tree, '标出第 1 题用的是 $A1/$A1.cpp'),
    ('第 2 题（$A2）' in tree, '标出第 2 题用的是 $A2/$A2.cpp'),
    ('第 3 题（$A3）' in tree, '标出第 3 题用的是 $A3/$A3.cpp'),
    ('个人信息文件' in tree, '标出个人信息文件（不参与判分）'),
    ('共 4 个文件' in p, '统计了文件数（4 个）'),
]:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 3b. 等判分写回来，再删比赛 ==="
# 判分跑在后台线程里：如果比赛在判分还没写完时就被删掉，判分线程会把这个比赛目录
# 重新创建出来（judge_csp 开头的 put_result 会 makedirs），留下一个不在索引里的孤儿目录
# ——那会让 tests/_e2e_rules.sh 的「没有孤儿比赛目录」检查变红。
# T2 只有第 3 节这一份提交会匹配到，tries 涨到 1 就说明这一轮判分已经落盘。
WAIT=x
for i in $(seq 1 40); do
  WAIT=$(python3 -c "
import json, os
f = '/root/csp-exam/data/contests/$CID/results.json'
if not os.path.exists(f):
    print('missing'); raise SystemExit
e = (json.load(open(f)).get('$KH') or {})
print(int(((e.get('problems') or {}).get('T2') or {}).get('tries') or 0))
" 2>/dev/null || echo 0)
  [ "$WAIT" = "1" ] && break
  sleep 1.5
done
[ "$WAIT" = "1" ] && pass "判分已写回（第 2 题提交 1 次），可以安全删比赛" \
  || fail "判分没在 60 秒内写回（第 2 题 tries=$WAIT），先别删——不然会留孤儿目录"

echo
echo "=== 4. 清理 ==="
python3 -c "
import shutil
import os
from csp_exam.core import store
for c in store.list_contests():
    if c['title'] == '目录树测试':
        shutil.rmtree(os.path.join('data', 'contests', c['id']), ignore_errors=True)
        store.delete_contest(c['id'])
print('  剩余比赛：', [(c['id'], c['title']) for c in store.list_contests()])
"
rm -rf $T/tree_bad $T/tree_ok
python3 -c "
import os
from csp_exam.core import store
left = [c['id'] for c in store.list_contests() if c['title'] == '目录树测试']
dirs = os.path.isdir(os.path.join('data', 'contests', '$CID'))
print('   [%s] 清理干净（残留比赛 %s / 目录 %s）' % ('PASS' if not (left or dirs) else 'FAIL', left or '无', dirs))
raise SystemExit(0 if not (left or dirs) else 1)
" && pass "测试比赛已删干净" || fail "测试比赛还有残留"

echo
echo "================== 看上面的 [PASS]/[FAIL] 汇总 =================="
