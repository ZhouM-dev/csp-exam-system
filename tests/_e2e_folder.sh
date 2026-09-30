#!/bin/bash
# 验收：上传整个出题文件夹（浏览器里就地识别、点「建题」才上传）→ 大样例单独存档 → 学生可下载
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }

# 只用测试专用的题目标识：以前这里写 G01，跟老师题库里真题的标识撞了，
# 又带着 overwrite=1 上传，结果把真题覆盖了。ZZTEST01 不可能和真题重名。
# 本轮连「覆盖已有题目」都删了（同标识直接建不进去），所以上面那段预清理是必须的：
# 上次跑到一半留下的 ZZTEST01 会让这一次建题直接失败。
PID=ZZTEST01
W=/root/csp-exam/tests/tmp/题目库
echo "=== 准备：造一个出题工程文件夹 ==="
# 上次跑到一半失败会留下测试比赛 / 测试题目 / 大样例存档 —— 先清掉，别越堆越多
python3 - <<PY
import shutil
from csp_exam.core import store, importer as ip, problems as mp
for c in [x for x in store.list_contests() if x.get('title') == '大样例验收']:
    store.delete_contest(c['id'])
    print('  清掉上次残留的测试比赛', c['id'])
print('  清掉上次残留的测试题目 $PID：', ip.hydro_delete_problem('$PID'))
shutil.rmtree(mp._samples_root('$PID'), ignore_errors=True)
# 题目编号登记 / 题库元信息也要清：本轮建题不再支持覆盖，而这两样按 pid 记着，
# 上一轮留下的记录会让「题目列表」多出一行指向已删题目的空壳。
codes = mp.load_codes()
gone = [p for p in ('$PID',) if codes.pop(p, None)]
mp.save_codes(codes)
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
info = load_problem_info()
gone2 = [p for p in ('$PID',) if info.pop(p, None)]
save_problem_info(info)
print('  清掉上次残留的编号登记/元信息：', (gone or []) + (gone2 or []) or '无')
PY
[ $? = 0 ] || fail "准备阶段的清理脚本出错（见上面的 Traceback）"
rm -rf /root/csp-exam/tests/tmp/题目库
mkdir -p "$W/$PID-大样例测试/data" "$W/$PID-大样例测试/大样例"
cat > "$W/$PID-大样例测试/题目.md" <<'EOF'
# 大样例测试

输入两个整数 a b，输出 a+b。

## 样例
输入：
```
1 2
```
输出：
```
3
```
EOF
cat > "$W/$PID-大样例测试/标程.cpp" <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }
EOF
printf '1 2\n' > "$W/$PID-大样例测试/data/01.in";   printf '3\n'     > "$W/$PID-大样例测试/data/01.out"
printf '10 20\n' > "$W/$PID-大样例测试/data/02.in"; printf '30\n'    > "$W/$PID-大样例测试/data/02.out"
printf '1 2\n' > "$W/$PID-大样例测试/样例1.in";     printf '3\n'     > "$W/$PID-大样例测试/样例1.out"
python3 - <<PY
import os
# 造一个"大样例"（故意大一点，模拟真实大样例）
with open("$W/$PID-大样例测试/大样例/大样例.in", "w") as f:
    for i in range(1, 2001):
        f.write("%d %d\n" % (i, i * 2))
with open("$W/$PID-大样例测试/大样例/大样例.out", "w") as f:
    for i in range(1, 2001):
        f.write("%d\n" % (i * 3))
print("  大样例大小：", os.path.getsize("$W/$PID-大样例测试/大样例/大样例.in"), "字节")
PY
find "$W" -type f | sed "s|$W/|  |"

echo
echo "=== 1. 按浏览器的目录上传方式提交（文件名带相对路径）==="
cd "$W/$PID-大样例测试"
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 300 -X POST \
  -F "folder=@题目.md;filename=题目库/$PID-大样例测试/题目.md" \
  -F "folder=@标程.cpp;filename=题目库/$PID-大样例测试/标程.cpp" \
  -F "folder=@data/01.in;filename=题目库/$PID-大样例测试/data/01.in" \
  -F "folder=@data/01.out;filename=题目库/$PID-大样例测试/data/01.out" \
  -F "folder=@data/02.in;filename=题目库/$PID-大样例测试/data/02.in" \
  -F "folder=@data/02.out;filename=题目库/$PID-大样例测试/data/02.out" \
  -F "folder=@样例1.in;filename=题目库/$PID-大样例测试/样例1.in" \
  -F "folder=@样例1.out;filename=题目库/$PID-大样例测试/样例1.out" \
  -F "folder=@大样例/大样例.in;filename=题目库/$PID-大样例测试/大样例/大样例.in" \
  -F "folder=@大样例/大样例.out;filename=题目库/$PID-大样例测试/大样例/大样例.out" \
  "$BASE/admin/problem?key=$KEY")
cd /root/csp-exam        # 回到服务目录（后面几段 python 要 from csp_exam.core import store）
python3 - <<PY
import urllib.parse
m = '''$LOC'''
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(没拿到跳转)'
print('  页面提示：', msg)
checks = [
    # 本轮改造：建题不再自动拿标程自测，「标程满分」这类回执没了；题库标识也不再露给老师
    # （回执里给的是系统分配的**题目编号**）。这一套真正关心的是下面这些：
    ('题目编号' in msg, '回执给出系统分配的题目编号（题库标识不再露给老师）'),
    ('大样例测试' in msg, '标题取到了题名'),
    ('2 组测试数据' in msg, '2 组评测数据（样例/大样例没混进来）'),
    ('自动识别' in msg, '提示是自动识别的'),
    ('大样例' in msg, '提示大样例已存档'),
    ('标程满分' not in msg and '自测' not in msg, '建题不再自测（回执里没有自测结论）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 2. 站点上确实只评测 2 个点（样例不算）==="
python3 - <<PY
import subprocess, json
from csp_exam.core import store
d = [p for p in store.load_catalog().get('items') or [] if str(p['pid']) == '$PID']
print('  题库里有这道题：', d)
js = 'const d=db.document.findOne({docType:10,pid:"$PID"},{docId:1,data:1,content:1}); '\\
     'print(JSON.stringify({n:(d.data||[]).length, names:(d.data||[]).map(x=>x.name), content:(d.content||"").slice(0,40)}))'
p = subprocess.run(["docker","compose","-f","/root/hydro/docker-compose.yml","exec","-T","oj-mongo",
                    "mongosh","hydro","--quiet","--eval",js], cwd="/root/hydro",
                   capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
line = [l for l in (p.stdout or "").splitlines() if l.startswith('{')]
info = json.loads(line[0]) if line else {}
print('  站点上的数据文件：', info.get('names'))
print('  题面开头：', info.get('content'))
names = info.get('names') or []
checks = [
    (all(n in names for n in ('1.in','1.out','2.in','2.out'))
     and not any('大样例' in n for n in names)
     and not any(n.startswith('样例') for n in names),
     '评测数据只有 1..2，样例/大样例都没混进来'),
    ('大样例测试' in (info.get('content') or ''), '题面用了文件夹里的 题目.md'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY
[ $? = 0 ] || fail "第 2 段的检查脚本出错（见上面的 Traceback）"

echo
echo "=== 3. 大样例存在考试服务这边 ==="
python3 - <<PY
import os
from csp_exam.core import problems as mp
info = mp.load_samples('$PID')
print('  存档目录：', info.get('dir'))
for it in info.get('items') or []:
    kind = '大样例' if it['kind'] == 'big' else '题目样例'
    print('   %s「%s」: %s' % (kind, it['label'], [(f['name'], f['size']) for f in it['files']]))
checks = [
    (any(it['kind'] == 'big' for it in info.get('items') or []), '大样例已单独存档'),
    (any(it['kind'] == 'sample' for it in info.get('items') or []), '题目样例也一并存档'),
    (mp.sample_path('$PID', '大样例.in') is not None, '大样例文件可取'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY
[ $? = 0 ] || fail "第 3 段的大样例检查脚本出错（见上面的 Traceback）"

echo
echo "=== 4. 学生端能看到并下载大样例 ==="
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/new?key=$KEY" \
  --data-urlencode "title=大样例验收" --data-urlencode "rule=OI"
CID=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '大样例验收'][0])")
curl -s -o /dev/null --max-time 60 -X POST "$BASE/admin/exam?key=$KEY&c=$CID" \
  --data-urlencode 'problems_json=[{"pid":"'"$PID"'","slug":"sum"}]' --data-urlencode 'full=100'
curl -s -o /dev/null --max-time 30 -X POST "$BASE/admin/roster?key=$KEY&c=$CID" \
  --data-urlencode "names=大样例学生" --data-urlencode "prefix=GD"
KH=$(python3 -c "
from csp_exam.core import store
print(sorted(store.load_roster('$CID'))[0])")
J=/root/csp-exam/tests/tmp/sample.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST "$BASE/enter" --max-time 20 \
  --data-urlencode "c=$CID" --data-urlencode "kaohao=$KH"
curl -s -b $J -o /root/csp-exam/tests/tmp/hall.html --max-time 20 "$BASE/hall?c=$CID"
grep -q "下载大样例" /root/csp-exam/tests/tmp/hall.html \
  && pass "比赛页出现「下载大样例」入口" || fail "比赛页没有大样例入口"
curl -s -b $J -o /root/csp-exam/tests/tmp/sample.html --max-time 20 "$BASE/sample?c=$CID&p=1"
grep -q "大样例.in" /root/csp-exam/tests/tmp/sample.html \
  && pass "大样例页列出了文件" || fail "大样例页没有文件"
curl -s -b $J -o /root/csp-exam/tests/tmp/sample_in.txt -w '  下载大样例.in -> HTTP %{http_code}  ' --max-time 30 \
  "$BASE/sample?c=$CID&p=1&f=%E5%A4%A7%E6%A0%B7%E4%BE%8B.in"
LINES=$(wc -l < /root/csp-exam/tests/tmp/sample_in.txt)
FIRST=$(head -1 /root/csp-exam/tests/tmp/sample_in.txt)
echo "内容：$LINES 行，首行「$FIRST」"
[ "$LINES" = "2000" ] && pass "下载到的内容与上传的一致（2000 行）" || fail "内容不对（$LINES 行）"
# 未登录不能下载
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$BASE/sample?c=$CID&p=1")
[ "$code" = "302" ] && pass "未登录访问大样例被挡（302）" || fail "未登录也能访问（$code）"

echo
echo "=== 5. 清理 ==="
python3 - <<PY
import os
import shutil
from csp_exam.core import store, importer as ip, problems as mp, hydro_client as hydro
shutil.rmtree(os.path.join('data', 'contests', '$CID'), ignore_errors=True)
store.delete_contest('$CID')
print('  删除题目 $PID：', ip.hydro_delete_problem('$PID'))
shutil.rmtree(mp._samples_root('$PID'), ignore_errors=True)
# 题目编号登记 / 题库元信息一起清（否则「题目列表」里留一行指向已删题目的「大样例测试」）
codes = mp.load_codes()
gone = [p for p in ('$PID',) if codes.pop(p, None)]
mp.save_codes(codes)
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
info = load_problem_info()
gone2 = [p for p in ('$PID',) if info.pop(p, None)]
save_problem_info(info)
print('  清掉编号登记/元信息：', (gone or []) + (gone2 or []) or '无')
store.save_catalog(hydro.list_problems())
print('  剩余比赛：', [c['title'] for c in store.list_contests()])
PY
[ $? = 0 ] || fail "清理脚本出错（见上面的 Traceback）"

# 清理必须核对：以前这里的 import 名写错了，脚本每次都崩，于是测试比赛和测试题
# 一场场地堆在老师的比赛列表/题库里，而验收还是"全绿"。
python3 - <<PY
import os
from csp_exam.core import store, importer as ip, problems as mp
left_c = [c['id'] for c in store.list_contests() if c['title'] == '大样例验收']
left_dir = os.path.isdir(os.path.join('data', 'contests', '$CID'))
left_p = '$PID' in ip.hydro_problem_pids()
left_s = os.path.isdir(os.path.join('data', 'samples', '$PID'))
left_code = mp.code_of_pid('$PID')
okv = not (left_c or left_dir or left_p or left_s or left_code)
print('   [%s] 清理干净（残留比赛 %s / 目录 %s / 题目 %s / 大样例 %s / 编号 %s）'
      % ('PASS' if okv else 'FAIL', left_c or '无', left_dir, left_p, left_s, left_code or '无'))
raise SystemExit(0 if okv else 1)
PY
[ $? = 0 ] || fail "清理没干净：测试比赛/测试题/大样例/编号还有残留"

echo
echo "================== 见上面的 [PASS]/[FAIL] =================="
