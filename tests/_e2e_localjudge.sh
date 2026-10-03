#!/bin/bash
# 端到端：判题**完全不经过 Hydro**地跑一遍（造一次性比赛，交一次，看分数，收尾删干净）。
#
#   sudo bash /root/csp-exam/tests/_e2e_localjudge.sh
#
# 覆盖：建比赛 → 配题（用现有题库里一道有数据的题）→ 加名单 → 学生登录 → 交考号文件夹
#       → 本地 go-judge 判分 → 查成绩 → 清理。
# 断言里**一次都不提 Hydro**：判题后端换成本地的之后，这条链路要能自己站住。
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=== 0. 判题后端体检 ==="
sudo env PYTHONPATH=/root/csp-exam PYTHONIOENCODING=utf-8 python3 - <<'PY'
import sys
sys.path.insert(0, "/root/csp-exam")
from csp_exam.core import localoj
h = localoj.health()
ok = h["judge_ok"] and h["problems"] > 0
print("   [%s] 沙箱 %s（go-judge %s）／题目 %d 道（%d 道没数据）"
      % ("PASS" if ok else "FAIL", h["judge_version"], h["judge_version"],
         h["problems"], len(h["no_data"])))
sys.exit(0 if ok else 1)
PY
[ $? = 0 ] && pass "判题沙箱可用、题目库非空" || fail "判题后端不可用"

# 挑一道**本机有数据**的题（优先 2 个点的小题，判得快）
PID=$(python3 - <<'PY'
import sys
sys.path.insert(0, "/root/csp-exam")
from csp_exam.core import judgelocal, localoj
best = None
for p in sorted(localoj.problem_pids()):
    cs = judgelocal.cases_of(p)
    if cs and (best is None or len(cs) < best[1]):
        best = (p, len(cs))
print(best[0] if best else "")
PY
)
[ -n "$PID" ] || { echo "  没有带数据的题目，没法测"; exit 1; }
N=$(python3 -c "
import sys; sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import judgelocal
print(len(judgelocal.cases_of('$PID')))")
echo "  用题目 $PID（$N 个测试点）"

echo
echo "=== 1. 造一次性比赛（CSP 赛制）==="
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/new?key=$KEY" \
  --data-urlencode "title=本地判题验收" --data-urlencode "rule=CSP"
CID=$(python3 -c "
import sys; sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '本地判题验收'][0])")
echo "  比赛 id = $CID"
curl -s -o /dev/null --max-time 60 -X POST "$BASE/admin/exam?key=$KEY&c=$CID" \
  --data-urlencode 'problems_json=[{"pid":"'"$PID"'"}]' --data-urlencode 'full=100'
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/roster?key=$KEY&c=$CID" \
  --data-urlencode "names=本地判题生" --data-urlencode "prefix=GD"
KH=$(python3 -c "
import sys; sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import store
print(sorted(store.load_roster('$CID'))[0])")
CODE=$(python3 -c "
import sys; sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import store
e = store.load_exam('$CID')['problems'][0]
print(store.code_of(e))")
echo "  考号 $KH，题目英文名 $CODE"
[ -n "$CODE" ] && pass "比赛建好、配了题、给了名单" || fail "比赛/配题/名单有问题"

echo
echo "=== 2. 学生登录 + 交文件夹 ==="
J=$T/localjudge.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST "$BASE/enter" --max-time 20 \
  --data-urlencode "c=$CID" --data-urlencode "kaohao=$KH"
rm -rf $T/lj; mkdir -p $T/lj/$CODE
# 写一份**会满分**的代码：按题目英文名 freopen（CSP 的写法）+ 标准输入输出的兼容写法
# 这里故意**不写 freopen**（auto 口径下应当也满分）——正是老师问的那个差异点
{ echo '#include <bits/stdc++.h>'; echo 'using namespace std;'; echo 'int main(){';
  echo '  long long a,b; if(!(cin>>a>>b)) return 0; cout<<a+b<<endl; return 0; }'; } \
  > $T/lj/$CODE/$CODE.cpp
curl -s -b $J -o $T/lj_sub.html -w "  交卷 HTTP %{http_code}\n" --max-time 300 \
  -X POST -F "files=@$T/lj/$CODE/$CODE.cpp;filename=$KH/$CODE/$CODE.cpp" "$BASE/upload?c=$CID"
grep -q "$KH" $T/lj_sub.html && pass "交卷回执认得这个考号" || fail "交卷回执不对"

echo
echo "=== 3. 等本地判分出分（最多 60 秒）==="
SCORE=""
for i in $(seq 1 40); do
  SCORE=$(python3 - <<PY
import json
e = (json.load(open('/root/csp-exam/data/contests/$CID/results.json')).get('$KH') or {})
p = (e.get('problems') or {}).get('T1') or {}
print(p.get('score') if p.get('tries') else "")
PY
)
  [ -n "$SCORE" ] && break
  sleep 1.5
done
echo "  得分：$SCORE"
[ "$SCORE" = "100" ] && pass "不用 freopen 的写法判了满分（auto 口径）" \
  || fail "判分结果异常（得分 $SCORE，期望 100）"

echo
echo "=== 4. 逐点明细落盘了（管理端要看这个）==="
python3 - <<PY
import json, sys
sys.path.insert(0, '/root/csp-exam')
e = (json.load(open('/root/csp-exam/data/contests/$CID/results.json')).get('$KH') or {})
p = (e.get('problems') or {}).get('T1') or {}
cs = p.get('testcases') or []
within = [c for c in cs if (c.get('input') or '').strip()]
print("   [%s] 逐点 %d 条，其中 %d 条带输入内容" % ("PASS" if cs and within else "FAIL",
                                               len(cs), len(within)))
print("   [%s] 状态文字：%s" % ("PASS" if p.get('status_text') else "FAIL", p.get('status_text')))
PY

echo
echo "=== 5. 清理（删掉这场一次性比赛）==="
python3 - <<PY
import shutil, sys
sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import store
shutil.rmtree('/root/csp-exam/data/contests/$CID', ignore_errors=True)
store.delete_contest('$CID')
left = [c['title'] for c in store.list_contests() if c['title'] == '本地判题验收']
print("   [%s] 一次性比赛已删除" % ("PASS" if not left else "FAIL"))
PY
rm -rf $T/lj $T/lj_sub.html

echo
echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
[ "$FAIL" = "0" ] || exit 1
