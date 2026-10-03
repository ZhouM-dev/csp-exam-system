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

# 清掉本脚本的测试数据（测试题目 + 测试比赛）。
# 开跑前与跑完后各调一次：上次跑到一半留下的题（T9101 等）会让这一次建题建出重复份，
# 所以必须先清干净。
#
# **按标题认，不按 pid 认**：重复建题时标识是自动让位的（T9101 → T9101b/c…），
# 写死 pid 会漏掉它们，站点上就留下永远清不掉的僵尸题（踩过）。
cleanup_data() {
python3 - <<PY
import os
import shutil
from csp_exam.core import store, importer as ip, problems as mp, hydro_client
TEST_TITLES = ('加法测试',)          # 本脚本造的题的标题前缀（含「（坏标程）」「（第二份）」）
for c in store.list_contests():
    if c['title'] in ('新题判分测试',):
        shutil.rmtree(os.path.join('data', 'contests', c['id']), ignore_errors=True)
        store.delete_contest(c['id'])
# c1 恢复成原来那三题
e = store.load_exam('c1')
e['problems'] = [p for p in e.get('problems', []) if p['pid'] != '$PID']
store.save_exam('c1', e)
# 站点上按标题找出本脚本造的题（含让位后的新标识）
junk = []
for it in hydro_client.list_problems():
    pid, title = str(it.get('pid') or ''), str(it.get('title') or '')
    if any(title.startswith(t) for t in TEST_TITLES) or pid in ('$PID', '${PID}b'):
        junk.append(pid)
for pid in junk:
    print('  删除题目', pid, ip.hydro_delete_problem(pid))
print('  站点上认出的测试题：', junk or '无')
# 连题目编号登记表一起清掉（否则会留下指向已删题目的编号）
codes = mp.load_codes()
gone = [p for p in junk if codes.pop(p, None)]
mp.save_codes(codes)
print('  清掉编号登记：', gone or '无')
# 题库元信息登记（题目列表页的「测试点/时限/内存」用它）也一起清，
# 否则题目列表里会留一行指向已删题目的「加法测试」
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
info = load_problem_info()
gone2 = [p for p in junk if info.pop(p, None)]
save_problem_info(info)
print('  清掉题库元信息：', gone2 or '无')
# 题面缓存（3c 改题面那节会写一份）：一起清掉 —— 留着的话，下次这道题重建出来
# 会先看到上一轮那份旧题面（缓存 6 小时内优先）
for p in junk:
    f = os.path.join('data', 'statements', p + '.md')
    if os.path.isfile(f):
        os.remove(f)
        print('  清掉题面缓存', p)
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
    # 本轮新增：上传进度（点「建题」之后不再是一片死等）
    ('id="mk-up"' in html, '有上传进度面板'),
    ('id="mk-up-fill"' in html and 'id="mk-up-pct"' in html, '进度条 + 百分比都在'),
    ('xhr.upload.onprogress' in html, '传输阶段报真实百分比（xhr.upload.onprogress）'),
    ('xhr.upload.onload' in html, '传完之后切到「服务端正在建题」'),
    ('new FormData(' in html, '整个表单原样 XHR 上传（字段名/结构不变）'),
    # f-string 里花括号要写两遍：漏一个 Python 就会去求值，页面要么 500、要么少一段。
    # 这里拿"渲染出来应当是单个 { 的 CSS 规则"反查。
    ('.up-bar.busy > i {' in html, '进度条的 CSS 花括号渲染正确（f-string 里没写坏）'),
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
echo "=== 3b. 建题可以建重复的题：同标识再建一次 → 新的一份换内部标识，旧的留着 ==="
# 老师要的是"能建重复的题"：站点上已经有 $PID 时**不再拒绝**，这一份换个内部标识
# （free_pid 给的是字母后缀，如 T9101→T9101b：**评测站只认字母开头的字母数字**，
# 用 `-2`/`_2` 这种会被它悄悄换成 #N，见下面那条断言）。
# 原来那道 $PID 原样不动（题目编号/题面/数据都还在）。老师界面只看得到题目编号，
# 所以"内部标识让位"这件事对他不可见。顺带传个老字段 overwrite=1，证明它被忽略。
HTTP=$(curl -s -o $T/dup.html -w '%{http_code}' --max-time 240 -X POST \
  -F "pid=$PID" -F "title=加法测试（第二份）" -F "time_ms=2000" -F "memory_mb=256" \
  -F "statement=第二份的题面" -F "overwrite=1" \
  -F "std=@$W/std.cpp;filename=标程.cpp" \
  -F "data=@$W/1.in;filename=1.in" -F "data=@$W/1.out;filename=1.out" \
  "$BASE/admin/problem?key=$KEY")
echo "  POST /admin/problem（同标识再建）-> HTTP $HTTP"
[ "$HTTP" = "302" ] && pass "同标识再建成功（302 跳走；不再是「被拒绝 + 回显表单」）" \
  || fail "同标识再建还是被拒（HTTP $HTTP，应该 302）"
python3 - <<PY
from csp_exam.core import hydro_client, problems as mp
live = {str(i['pid']): i['title'] for i in hydro_client.list_problems()}
code1 = mp.code_of_pid('$PID')
# 第二份落在哪个标识上由 free_pid 决定（G01 → G01b 这种字母后缀），所以按标题找它；
# 找不到、或者标识是 #N，都说明"让位"没落成评测站认的形式（#N 是评测站自己偷偷编的）
second = [p for p, t in live.items() if t == '加法测试（第二份）']
print('  站上 $PID → %r' % live.get('$PID'))
print('  第二份 → 标识 %s' % (second or '没找到'))
code2 = mp.code_of_pid(second[0]) if second else ''
print('  题目编号：第一份 %s ／ 第二份 %s' % (code1 or '没分配', code2 or '没分配'))
checks = [
    (len(second) == 1, '第二份建出来了（自动让位到新标识）'),
    (live.get('$PID') == '加法测试', '原来那道 $PID 没被动（标题还是「加法测试」，不是「第二份」）'),
    (bool(second) and not second[0].startswith('#'),
     '新标识是评测站认的形式（字母开头的字母数字，不是它私编的 #N）'),
    (bool(code2) and code2 != code1, '第二份拿到**自己的**题目编号（两份能区分开）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 3c. 改题面：已有题目不用删也能改（评测站 + 本站缓存一起更新）==="
# 题面的**真身**在评测站（题目文档的 content 字段），本站 data/statements/<pid>.md 只是缓存。
# 所以保存必须两处都写：只写缓存 → 6 小时后被冲掉；只写评测站 → 学生立刻看到的还是旧的。
NEWSTMT="## 改过的题面（验收）

这是一次「改题面」验收，题目 $PID。
标记串：ZM-STATEMENT-OK"
curl -s -o /dev/null -w '  POST /admin/statement -> HTTP %{http_code}\n' --max-time 90 \
  -X POST "$BASE/admin/statement?key=$KEY&pid=$PID" \
  --data-urlencode "pid=$PID" --data-urlencode "statement=$NEWSTMT"
python3 - <<PY
import io
import os
from csp_exam.core import hydro_client as hydro, store
pid = '$PID'
live = hydro.problem_statement(pid, cache_dir='', ttl=0)          # 评测站那份（ttl=0 强取）
cache = os.path.join(store.DATA_DIR, 'statements', pid + '.md')
cached = io.open(cache, encoding='utf-8').read() if os.path.isfile(cache) else ''
print('  评测站那份 %d 字 / 本站缓存 %d 字' % (len(live), len(cached)))
checks = [
    ('ZM-STATEMENT-OK' in live, '评测站上的题面已更新（真身写了）'),
    ('ZM-STATEMENT-OK' in cached, '本站缓存也刷了（不刷的话 6 小时后会被冲掉）'),
    ('改过的题面（验收）' in live and '改过的题面（验收）' in cached, '两边是同一份新题面'),
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

# 改过的题面：学生打开题面看到的就是新的那份（走的是本站缓存那条路 ——
# 只写评测站不刷缓存的话，这里看到的还是旧题面）
curl -s -b $J --max-time 20 "$BASE/problem?c=$NC&p=1" -o $T/stu_stmt.html
grep -q 'ZM-STATEMENT-OK' $T/stu_stmt.html \
  && pass "学生题面页显示的是改过之后的题面（改题面端到端通了）" \
  || fail "学生题面页还是旧题面"

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
# 假删除之后**系统不再认这道题**：配题候选（/api/problems）里不该再有它。
# 题面/记录都还在（上面刚验过），只是不再出现在任何"选题目"的地方。
curl -s "$BASE/api/problems?key=$KEY" -o $T/api_del.json
python3 -c "
import json
d = json.load(open('$T/api_del.json', encoding='utf-8'))
items = [i['pid'] for i in d.get('items') or []]
print('  配题候选里有它吗：', '${PID}b' in items, '（候选共 %d 道）' % len(items))
raise SystemExit(1 if '${PID}b' in items else 0)
" && pass "假删除的题从配题候选里消失（系统不再认它）" || fail "假删除的题还挂在配题候选里"
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
left_p = [p for p in ('$PID', '${PID}b', '$PID-2') if p in ip.hydro_problem_pids()]
left_code = [p for p in ('$PID', '${PID}b', '$PID-2') if mp.code_of_pid(p)]
okv = not (left or left_p or left_code)
print('   [%s] 清理干净（残留比赛 %s / 题目 %s / 编号 %s）'
      % ('PASS' if okv else 'FAIL', left or '无', left_p or '无', left_code or '无'))
raise SystemExit(0 if okv else 1)
PY
[ $? = 0 ] && pass "测试题目与编号登记都清干净了" || fail "清理没干净：测试题目/编号还有残留"

echo
echo "================== 见上面的 [PASS]/[FAIL] =================="
