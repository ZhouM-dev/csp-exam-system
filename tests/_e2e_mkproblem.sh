#!/bin/bash
# 验收：管理端建题目（选文件夹/zip → 浏览器里就地识别 → 点「建题」才上传 → 加进比赛判分）
#      + 坏标程靠「自己测试」发现（建题时**不再**自动自测）
#
# 本轮改造后的口径：
#   * 新建题目**只有一个表单**：选出题文件夹（或 zip）后**在浏览器里就地读文件、自动填标题/题面/标程**，
#     文件**只在点「建题」时才上传**（POST /admin/problem，multipart）——没有「先识别一遍」这一步；
#     `POST /admin/scan` 不再接 multipart，`scan_token` 那套（_save_scan/_load_scan/_drop_scan）整个删了
#   * 建题**不再自动拿标程自测**，回执里不再有「标程满分」这类文案；表单也不再收 selftest 字段（传了忽略）。
#     老师要验数据/时限去「题目列表」点「自己测试」（POST /admin/selftest，**返回 JSON**）——
#     本脚本第 2 节拿坏标程走的就是这条路
#   * 建题**不再支持覆盖**：站点上已有同标识题目时直接失败并提示；表单也不再收 overwrite 字段（传了忽略）。
#     所以本脚本开跑前先清掉上次残留的测试题目（第 0 节），否则重跑会卡在建题这一步
#   * 题目编号（学生建文件夹/写 freopen 用的名字）建题时分配，跟着题走；
#     **题库标识（pid/分类号）老师不再接触**，页面提示里也换成题目编号
#   * 三种赛制都交考号文件夹
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }

# 清掉本脚本的测试数据（测试专用标识 T9101 / T9101b + 测试比赛）。
# 开跑前与跑完后各调一次：本轮**建题不再支持覆盖**，上次跑到一半留下的 T9101
# 会让这一次建题直接失败（「站点上已经有题目」），所以必须先清干净。
cleanup_data() {
python3 - <<PY
import os
import shutil
from csp_exam.core import store, importer as ip, problems as mp
for c in store.list_contests():
    if c['title'] in ('新题判分测试',):
        shutil.rmtree(os.path.join('data', 'contests', c['id']), ignore_errors=True)
        store.delete_contest(c['id'])
# c1 恢复成原来那三题
e = store.load_exam('c1')
e['problems'] = [p for p in e.get('problems', []) if p['pid'] != '$PID']
store.save_exam('c1', e)
for pid in ('$PID', '${PID}b'):
    print('  删除题目', pid, ip.hydro_delete_problem(pid))
# 连题目编号登记表一起清掉（否则会留下指向已删题目的编号）
codes = mp.load_codes()
gone = [p for p in ('$PID', '${PID}b') if codes.pop(p, None)]
mp.save_codes(codes)
print('  清掉编号登记：', gone or '无')
# 题库元信息登记（题目列表页的「测试点/时限/内存」用它）也一起清，
# 否则题目列表里会留一行指向已删题目的「加法测试」
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
info = load_problem_info()
gone2 = [p for p in ('$PID', '${PID}b') if info.pop(p, None)]
save_problem_info(info)
print('  清掉题库元信息：', gone2 or '无')
from csp_exam.core import hydro_client
store.save_catalog(hydro_client.list_problems())
print('  c1 题目：', [(p['no'], p['pid']) for p in store.load_exam('c1').get('problems', [])])
print('  剩余比赛：', [c['title'] for c in store.list_contests()])
PY
}

PID=T9101
echo "=== 0. 先清掉上次跑剩的测试数据（本轮没有「覆盖已有题目」了，残留会让建题直接失败）==="
cleanup_data

echo "=== 准备：造一份"出题包"（2 组数据 + 标程 + 题面）==="
W=$T/mkprob; rm -rf $W; mkdir -p $W
printf '1 2\n' > $W/1.in
printf '3\n'   > $W/1.out
printf '0 0\n' > $W/2.in
printf '0\n'   > $W/2.out
cat > $W/std.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }
EOF
printf '# 加法测试\n\n输入两个整数，输出它们的和。\n' > $W/题目.md
ls -l $W | sed 's/^/   /'

echo
echo "=== 1. 页面能打开（新入口的控件都在）==="
curl -s -o $T/mk1.html -w '  GET /admin/problem -> HTTP %{http_code}\n' --max-time 20 "$BASE/admin/problem?key=$KEY"
grep -q "新建题目" $T/mk1.html && pass "建题页面存在" || fail "建题页面异常"
grep -q 'name="folder"' $T/mk1.html && grep -q 'webkitdirectory' $T/mk1.html \
  && pass "有「选出题文件夹」控件（本轮合并入口）" || fail "缺少选出题文件夹的控件"
grep -q 'name="data"' $T/mk1.html && pass "也可以传一个 zip（name=data）" || fail "缺少 zip 上传控件"
# 本轮改造：识别改在**浏览器里**做、文件只在点「建题」时才上传 —— 页面上不该再有
# 「先识别一遍」，也不该再有自测/覆盖两个勾选（建题不再自动自测、不再支持覆盖）。
grep -q '先识别一遍' $T/mk1.html && fail "页面上还有「先识别一遍」（识别已改在浏览器里做）" \
  || pass "页面上没有「先识别一遍」（选完文件夹就地识别）"
grep -q 'name="selftest"' $T/mk1.html && fail "页面上还有自测勾选（建题不再自动自测）" \
  || pass "页面上没有自测勾选（建题不再自动拿标程自测）"
grep -q 'name="overwrite"' $T/mk1.html && fail "页面上还有覆盖勾选（建题不再支持覆盖）" \
  || pass "页面上没有覆盖勾选（建题不再支持覆盖）"
# 新流程的关键：文件夹输入框得在**建题表单里**（一个表单直接 POST /admin/problem，
# multipart；不再先往 /admin/scan 传一遍换 scan_token）。
python3 - <<PY
import re
html = open('$T/mk1.html', encoding='utf-8', errors='replace').read()
forms = re.findall(r'<form.*?</form>', html, re.S)
with_folder = [f for f in forms if 'name="folder"' in f]
f = with_folder[0] if len(with_folder) == 1 else ''
checks = [
    (len(forms) == 1, '建题页只有一个表单（不再两段式：先识别换 token 那套没了）'),
    (len(with_folder) == 1, '文件夹输入框在某个表单里'),
    (re.search(r'action="/admin/problem[?"]', f) is not None,
     '文件夹就在**建题表单**里（表单直接 POST /admin/problem）'),
    ('multipart/form-data' in f, '建题表单是 multipart/form-data（文件随建题一起传）'),
    ('name="data"' in f, 'zip 输入框也在同一个表单里'),
    ('name="statement"' in f and 'name="std_text"' in f, '题面/标程也在同一个表单里'),
    ('/admin/scan' not in f, '表单里没有 /admin/scan（不再先识别一遍换 token）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY
grep -q 'name="name"' $T/mk1.html && pass "有「英文名（默认）」输入框（可留空，加进比赛时再填/再改）" || fail "缺少英文名输入框"
grep -q 'name="std_text"' $T/mk1.html && pass "标程是可编辑的代码框" || fail "标程不是代码框"
grep -q 'name="bigsample"' $T/mk1.html && pass "有大样例上传（学生可下载）" || fail "缺少大样例上传"
grep -q '题目标识' $T/mk1.html && fail "页面里还出现「题目标识」（老师不该看到）" \
  || pass "页面里没有「题目标识」（老师只跟题目编号打交道）"

echo
echo "=== 2. 建题不再自测：坏标程照样建得进来，「自己测试」才发现得了 ==="
# (1) 建题这一步只负责导入，**不再拿标程自测**（以前会在这里被拦下）。
#     顺手把 selftest / overwrite 两个老字段传进去 —— 本轮已删掉，传了要被忽略：
#     回执里不该再出现「标程满分」这类自测结论。
cat > $W/bad_std.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long a,b; cin>>a>>b; cout<<a-b<<endl; return 0; }
EOF
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 300 -X POST \
  -F "pid=${PID}b" -F "title=加法测试（坏标程）" -F "time_ms=2000" -F "memory_mb=256" \
  -F "statement=临时测试" -F "selftest=1" -F "overwrite=1" \
  -F "std=@$W/bad_std.cpp;filename=std.cpp" \
  -F "data=@$W/1.in;filename=1.in" -F "data=@$W/1.out;filename=1.out" \
  -F "data=@$W/2.in;filename=2.in" -F "data=@$W/2.out;filename=2.out" \
  "$BASE/admin/problem?key=$KEY")
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(无消息)'
print('  页面提示：', msg)
checks = [
    ('已导入' in msg, '坏标程照样建得进来（建题不再自测）'),
    ('2 组测试数据' in msg, '报到 2 组数据'),
    ('标程满分' not in msg, '回执里没有自测结论（selftest 字段被忽略）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

# (2) 「坏标程能被发现」这个能力改从**「自己测试」**这条路验（题目列表每行的按钮）：
#     POST /admin/selftest 以评测站管理员身份提交代码、跑完这道题的全部测试点，
#     **返回 JSON**（不是跳转），字段：ok/passed/total/cases/verdict。
#     这道坏标程算的是 a-b：第 1 组（1 2 → 3）判错，第 2 组（0 0 → 0）碰巧对，所以不会是满分。
curl -s -o $T/selftest_bad.json -w '  POST /admin/selftest（坏标程）-> HTTP %{http_code}\n' --max-time 300 \
  -X POST --data-urlencode "pid=${PID}b" --data-urlencode "source@$W/bad_std.cpp" \
  "$BASE/admin/selftest?key=$KEY"
python3 - <<PY
import json
d = json.load(open('$T/selftest_bad.json', encoding='utf-8'))
v = d.get('verdict') or ''
print('  回执：ok=%s passed=%s/%s status=%s' % (d.get('ok'), d.get('passed'), d.get('total'), d.get('status_text')))
print('  结论：', v[:200])
if not d.get('ok'):
    print('  错误：', d.get('error'))
checks = [
    (bool(d.get('ok')), '「自己测试」接口正常返回（ok=true）'),
    (int(d.get('total') or 0) == 2, '跑到了 2 个测试点（total=2）'),
    (int(d.get('passed') or 0) < int(d.get('total') or 0), '坏标程不是满分（passed < total）'),
    ('只过了' in v or '有问题' in v, '结论里点出「数据或标程有问题」'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 3. 正式建题（正确标程）==="
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 300 -X POST \
  -F "pid=$PID" -F "title=加法测试" -F "time_ms=2000" -F "memory_mb=256" \
  -F "statement=输入两个整数，输出它们的和。" \
  -F "std=@$W/std.cpp;filename=标程.cpp" \
  -F "data=@$W/1.in;filename=1.in" -F "data=@$W/1.out;filename=1.out" \
  -F "data=@$W/2.in;filename=2.in" -F "data=@$W/2.out;filename=2.out" \
  "$BASE/admin/problem?key=$KEY")
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(无消息)'
print('  页面提示：', msg)
checks = [
    ('已导入' in msg, '题目已导入'),
    ('2 组测试数据' in msg, '报到 2 组数据'),
    ('题目编号' in msg, '提示里给出的是**题目编号**（老师不再接触题库标识）'),
    ('标程满分' not in msg, '建题不再自测（回执里没有自测结论）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY
CODE=$(python3 - <<PY
from csp_exam.core import problems as mp
print(mp.code_of_pid('$PID'))
PY
)
echo "  这道题拿到的题目编号：$CODE"

echo
echo "=== 3b. 建题不再支持覆盖：同标识再建一次会被拒绝（连 overwrite=1 也忽略）==="
# 本轮把「覆盖已有题目」删了：站点上已经有同标识题目时，create_problem 固定
# overwrite=False，直接失败并提示。这里故意再建一次 $PID，并带上老字段 overwrite=1
# —— 应当照样被拒绝（老字段被忽略）。
# 注意：失败是**把建题页重新渲染一遍**（HTTP 200 + 页面上一条红色提示），
# 不是跳转，所以看的是响应正文，不是 redirect_url。
curl -s -o $T/dup.html -w '  POST /admin/problem（同标识再建）-> HTTP %{http_code}\n' --max-time 120 -X POST \
  -F "pid=$PID" -F "title=加法测试（想覆盖）" -F "time_ms=2000" -F "memory_mb=256" \
  -F "statement=想覆盖" -F "overwrite=1" \
  -F "std=@$W/std.cpp;filename=标程.cpp" \
  -F "data=@$W/1.in;filename=1.in" -F "data=@$W/1.out;filename=1.out" \
  "$BASE/admin/problem?key=$KEY"
python3 - <<PY
from csp_exam.core import hydro_client
html = open('$T/dup.html', encoding='utf-8', errors='replace').read()
i = html.find('已经有题目')
print('  页面提示（片段）：', (html[max(0, i - 60): i + 160].replace('\n', ' ')
                            if i >= 0 else '(页面上没有这句话)'))
title = {str(i['pid']): i['title'] for i in hydro_client.list_problems()}.get('$PID')
checks = [
    (i >= 0, '同标识再建被拒绝（页面上提示站点上已经有这道题）'),
    ('id="mk-form"' in html, '失败后把建题页带着提示重新渲染（可以改一处重试，不用跳走）'),
    (title == '加法测试', '站点上那道题没被改动（标题还是「加法测试」，不是「想覆盖」）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 4. 题目确实进了站点（题库缓存也已刷新）==="
python3 - <<PY
import os
from csp_exam.core import store, hydro_client, problems as mp
items = {str(i['pid']): i['title'] for i in (store.load_catalog().get('items') or [])}
live = {str(i['pid']): i['title'] for i in hydro_client.list_problems()}
code = mp.code_of_pid('$PID')
checks = [
    ('$PID' in items, '题库缓存里有 $PID（%s）' % items.get('$PID', '')),
    ('$PID' in live, '站点题库里有 $PID（%s）' % live.get('$PID', '')),
    (bool(code), '编号登记表里已分配题目编号（%s）' % (code or '没分配')),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
# 题目包（题面/标程/testdata）确实落到了评测站的导入目录
root = '/root/hydro/data/backend/import/_mk/$PID'
files = [os.path.relpath(os.path.join(dp, f), root)
         for dp, _dn, fn in os.walk(root) for f in fn]
print('   [%s] 题目包已落盘（%d 个文件：%s）'
      % ('PASS' if len(files) >= 4 else 'FAIL', len(files), '、'.join(sorted(files)[:6])))
if not files:
    print('     （%s 是空的；若导入目录改过位置，这里只是提示，不算失败）' % root)
PY

echo
echo "=== 5. 能直接加进比赛并判分（端到端）==="
CID=c1
curl -s -o /dev/null -w '  把新题加进 c1 -> HTTP %{http_code}\n' --max-time 60 -X POST \
  --data-urlencode 'problems_json=[{"pid":"P1001"},{"pid":"P1002"},{"pid":"P1003"},{"pid":"'"$PID"'"}]' \
  --data-urlencode 'full=100' "$BASE/admin/exam?key=$KEY&c=$CID"
python3 -c "
from csp_exam.core import store
e = store.load_exam('$CID')
print('  c1 加完后：', [(p['no'], p['pid'], store.code_of(p)) for p in e.get('problems', [])])
raise SystemExit(0 if any(p['pid'] == '$PID' for p in e.get('problems', [])) else 1)
" && pass "新题可以加进比赛" || fail "新题加不进比赛"
# 学生交这道新题：三种赛制都交考号文件夹。这里用 **CSP** 赛制跑（要 freopen）——
# 新建的题有 1.in/1.out，正好验「学生交文件夹 → 判分 → 得满分」。
# 建一场 OI 交标准输入输出也行，但那条路现在是坏的（见 tests/_e2e_rules.sh 的 2b），
# 别把两个问题搅在一起。
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/new?key=$KEY" \
  --data-urlencode "title=新题判分测试" --data-urlencode "rule=CSP"
NC=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '新题判分测试'][0])")
curl -s -o /dev/null --max-time 60 -X POST "$BASE/admin/exam?key=$KEY&c=$NC" \
  --data-urlencode 'problems_json=[{"pid":"'"$PID"'"}]' --data-urlencode 'full=100'
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/roster?key=$KEY&c=$NC" \
  --data-urlencode "names=建题测试生" --data-urlencode "prefix=GD"
KH=$(python3 -c "
from csp_exam.core import store
print(sorted(store.load_roster('$NC'))[0])")
NCODE=$(python3 -c "
from csp_exam.core import store
print(store.code_of(store.load_exam('$NC')['problems'][0]))")
J=$T/mkprob.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST "$BASE/enter" --max-time 20 \
  --data-urlencode "c=$NC" --data-urlencode "kaohao=$KH"
rm -rf $T/mksub; mkdir -p $T/mksub/$NCODE
{ printf '#include <bits/stdc++.h>\nusing namespace std;\nint main(){ freopen("%s.in","r",stdin); freopen("%s.out","w",stdout);\n' "$NCODE" "$NCODE"
  printf '  long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }\n'; } > $T/mksub/$NCODE/$NCODE.cpp
curl -s -b $J -o $T/sub.html -w '  学生提交标程 -> HTTP %{http_code}  ' --max-time 300 \
  -X POST -F "files=@$T/mksub/$NCODE/$NCODE.cpp;filename=$KH/$NCODE/$NCODE.cpp" "$BASE/upload?c=$NC"
echo
# 判分是后台线程：等 tries 涨到 1（它每次都真的变），说明这一次判分已经落盘，
# 再去读分数——直接等"分数=100"可能读到还不存在的记录（误判成 0 分）
for i in $(seq 1 40); do
  T1TRY=$(python3 -c "
import json
e = (json.load(open('/root/csp-exam/data/contests/$NC/results.json')).get('$KH') or {})
print(int(((e.get('problems') or {}).get('T1') or {}).get('tries') or 0))")
  [ "$T1TRY" = "1" ] && break
  sleep 1.5
done
python3 -c "
import json
e = (json.load(open('/root/csp-exam/data/contests/$NC/results.json')).get('$KH') or {})
p = (e.get('problems') or {}).get('T1') or {}
print('  成绩：', p.get('score'), '分，', p.get('status_text'), '，提交 %s 次' % p.get('tries'))
raise SystemExit(0 if int(p.get('score') or 0) == 100 else 1)
" && pass "新题判分正常（学生交文件夹交标程得 100）" || fail "新题判分异常"

echo
echo "=== 5c. 删除题目（「题目列表」每行的删除按钮）==="
# (1) 还在比赛里用着的题**不许删**：那一场的 exam.json 指着它，删了那场就判不出分。
#     此刻 $PID 正加在 c1 的题目表里（上面第 5 节加的）。
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 30 -X POST \
  "$BASE/admin/problem?key=$KEY" \
  --data-urlencode "action=delete" --data-urlencode "pid=$PID")
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(无消息)'
print('  页面提示：', msg)
raise SystemExit(0 if '不能删' in msg else 1)
PY
[ $? = 0 ] && pass "在比赛里用着的题拒绝删除（并说明是哪一场）" || fail "在用的题竟然被删了"
python3 -c "
from csp_exam.core import problems as mp
raise SystemExit(0 if '$PID' in mp.load_codes() else 1)
" && pass "被拒之后题目原样还在（编号登记没被清）" || fail "被拒了却把题目清掉了"

# (2) 不在任何比赛里的题删得掉 —— 但**是假删除**：题面、编号、评测站上的题目、
#     历史提交记录全部保留，只是从「全部题目」收进「已删除的题目」里，随时能恢复。
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 60 -X POST \
  "$BASE/admin/problem?key=$KEY" \
  --data-urlencode "action=delete" --data-urlencode "pid=${PID}b")
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(无消息)'
print('  页面提示：', msg)
# 假删除的文案：明确说"只是从列表里收起来了"
raise SystemExit(0 if ('已删除题目' in msg and '题面' in msg) else 1)
PY
[ $? = 0 ] && pass "假删除成功，提示说明题面/记录都还在" || fail "删除没成功"
python3 -c "
from csp_exam.core import problems as mp
from csp_exam.web.admin_pages import load_problem_info
pid = '${PID}b'
codes = mp.load_codes(); info = load_problem_info()
rec = info.get(pid) or {}
ok = (pid in codes                        # 编号登记保留
      and rec.get('deleted') == 1         # 打了假删除记号
      and pid in mp.ip.hydro_problem_pids())   # 评测站上的题目也保留
print('  编号登记在：', pid in codes, '／假删除记号：', rec.get('deleted'),
      '／评测站上在：', pid in mp.ip.hydro_problem_pids())
raise SystemExit(0 if ok else 1)
" && pass "假删除不真删：编号/记号和评测站题目都留着（题面与提交记录还能看）" \
  || fail "假删除把东西真删了"
curl -s "$BASE/admin/problems?key=$KEY" -o $T/pl_del.html
python3 -c "
h = open('$T/pl_del.html', encoding='utf-8', errors='replace').read()
main, sep, tail = h.partition('已删除的题目')
print('  有已删除块：', bool(sep), '／主表格里还有它：', 'pid\" value=\"${PID}b\"' in main,
      '／已删除块里有它：', '${PID}b' in tail)
raise SystemExit(0 if (sep and 'pid\" value=\"${PID}b\"' not in main and '${PID}b' in tail) else 1)
" && pass "列表页把它收进「已删除的题目」里，不再占着主表格" || fail "列表页没收干净"
curl -s "$BASE/admin/scan?key=$KEY" --max-time 60 \
  --data-urlencode "render=1" --data-urlencode "pid=${PID}b" -o $T/stmt_del.json
python3 -c "
import json
d = json.load(open('$T/stmt_del.json', encoding='utf-8'))
h = (d.get('html') or '').strip()
print('  假删除后题面还能取到：', ('有，%d 字节' % len(h)) if h else '空的 ← 不该')
raise SystemExit(0 if h else 1)
" && pass "假删除后题面照旧能看（这正是假删除的意义）" || fail "假删除后题面打不开了"

# (3) 恢复：回到主表格、回到配题候选
curl -s -o /dev/null --max-time 60 -X POST "$BASE/admin/problem?key=$KEY" \
  --data-urlencode "action=restore" --data-urlencode "pid=${PID}b"
curl -s "$BASE/admin/problems?key=$KEY" -o $T/pl_res.html
curl -s "$BASE/api/problems?key=$KEY" -o $T/api_res.json
python3 -c "
import json
h = open('$T/pl_res.html', encoding='utf-8', errors='replace').read()
main, _, _ = h.partition('已删除的题目')
in_main = 'pid\" value=\"${PID}b\"' in main
d = json.load(open('$T/api_res.json', encoding='utf-8'))
in_api = '${PID}b' in [i['pid'] for i in d.get('items') or []]
print('  回到主表格：', in_main, '／回到配题候选：', in_api)
raise SystemExit(0 if (in_main and in_api) else 1)
" && pass "恢复后回到列表与配题候选（题面/编号一直都在，所以是瞬间的）" || fail "恢复没生效"

# (4) 彻底删除：这一步才是真删（评测站题目 + 编号 + 元信息 + 题面缓存一起清）
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 300 -X POST \
  "$BASE/admin/problem?key=$KEY" \
  --data-urlencode "action=purge" --data-urlencode "pid=${PID}b")
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(无消息)'
print('  页面提示：', msg)
raise SystemExit(0 if '已彻底删除' in msg else 1)
PY
[ $? = 0 ] && pass "「彻底删除」能真删掉" || fail "彻底删除没成功"
python3 -c "
from csp_exam.core import problems as mp
from csp_exam.web.admin_pages import load_problem_info
pid = '${PID}b'
codes = mp.load_codes(); info = load_problem_info()
left = [w for w, has in (('编号登记', pid in codes), ('题库元信息', pid in info),
                         ('评测站题目', pid in mp.ip.hydro_problem_pids())) if has]
print('  彻底删完还剩：', left or '全都清掉了')
raise SystemExit(1 if left else 0)
" && pass "彻底删除后各处都不留残留" || fail "彻底删了但留了残留"

echo
echo "=== 6. 清理测试数据 ==="
cleanup_data
rm -rf $T/mkprob $T/mksub
python3 - <<PY
import os
from csp_exam.core import store, importer as ip, problems as mp
left = [c['title'] for c in store.list_contests() if c['title'] == '新题判分测试']
left_p = [p for p in ('$PID', '${PID}b') if p in ip.hydro_problem_pids()]
left_code = [p for p in ('$PID', '${PID}b') if mp.code_of_pid(p)]
okv = not (left or left_p or left_code)
print('   [%s] 清理干净（残留比赛 %s / 题目 %s / 编号 %s）'
      % ('PASS' if okv else 'FAIL', left or '无', left_p or '无', left_code or '无'))
raise SystemExit(0 if okv else 1)
PY
[ $? = 0 ] && pass "测试题目与编号登记都清干净了" || fail "清理没干净：测试题目/编号还有残留"

echo
echo "================== 见上面的 [PASS]/[FAIL] =================="
