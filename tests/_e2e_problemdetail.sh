#!/bin/bash
# 验收：题目详情页（/admin/problem-detail）的保存 —— 题面 / 题目名 / 英文名 / 时限 / 内存
#       五样都能改、都能核对、都能还原。
#
# 为什么单独一套：这一页是**写**老师的题目数据的（题面文件 + 编号登记 + 题目信息），
# 冒烟只请求页面（GET 200）完全代表不了"保存真的落盘了"。这里改一遍、逐样核对、
# 再改回原样，最后确认"还原以后和原来一字不差"——不然这套验收本身就把题库改坏了。
#
# 挑哪道题：优先挑**假删除**里的那道（它不参与任何比赛，改坏了也没人看见）；
# 没有就挑列表里第一道。不管挑到谁，跑完一定还原。
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "=== 0. 挑一道题，记下它现在的五样 ==="
curl -s --max-time 30 "$BASE/admin/problems?key=$KEY" -o $T/pd_list.html
PID=$(python3 - <<'PY'
import json, os, re
html = open('/root/csp-exam/tests/tmp/pd_list.html', encoding='utf-8').read()
pids = re.findall(r'href="/admin/problem-detail\?[^"]*?pid=([A-Za-z0-9_\-]+)', html)
# 挑一道**题目名/时限/内存都有值**的：这一套要验"改完能原样还原"，
# 挑到空壳题（老残留、什么都没记的）就验不出还原那一步
info = json.load(open('/root/csp-exam/data/problem_info.json', encoding='utf-8')).get('items', {})
def full(pid):
    r = info.get(pid) or {}
    return bool(str(r.get('title') or '').strip()) and int(r.get('time_ms') or 0) > 0 \
        and int(r.get('memory_mb') or 0) > 0
print(next((p for p in pids if full(p)), ''))
PY
)
if [ -z "$PID" ]; then
  fail "题目列表里没有一道带完整信息（题目名/时限/内存）的题，没法验还原"
  echo "  结果：通过 $PASS 项 / 失败 $FAIL 项"
  exit 1
fi
echo "  选的题目：$PID"

# 记下原来的五样（题面原文 + 元信息），存成 JSON，最后照它还原
python3 - "$PID" <<'PY' > $T/pd_before.json
import json, os, sys
from csp_exam.core import problems as mp
pid = sys.argv[1]
info = mp.load_problem_info().get(pid) or {}
codes = mp.load_codes().get(pid) or {}
stmt_path = os.path.join('data', 'statements', pid + '.md')
stmt = open(stmt_path, encoding='utf-8').read() if os.path.isfile(stmt_path) else ''
json.dump({'title': str(info.get('title') or ''), 'name': str(codes.get('name') or ''),
           'time_ms': int(info.get('time_ms') or 0),
           'memory_mb': int(info.get('memory_mb') or 0),
           'statement': stmt}, sys.stdout, ensure_ascii=False)
PY
python3 - <<PY
import json
b = json.load(open('$T/pd_before.json', encoding='utf-8'))
print('   原来：题目名 %r · 英文名 %r · %d 毫秒 / %d MB · 题面 %d 字'
      % (b['title'], b['name'], b['time_ms'], b['memory_mb'], len(b['statement'])))
PY

# 改一版：五样全换（题面加一行标记）
NEW_TITLE='详情页验收题名'
NEW_NAME='pdcheck'
python3 - "$PID" <<'PY' > $T/pd_new.txt
import json, sys
b = json.load(open('/root/csp-exam/tests/tmp/pd_before.json', encoding='utf-8'))
b['title'] = '详情页验收题名'
b['name'] = 'pdcheck'
b['time_ms'] = 4321
b['memory_mb'] = 321
b['statement'] = b['statement'].rstrip('\n') + '\n\n详情页验收标记 PD-MARK-OK\n'
open('/root/csp-exam/tests/tmp/pd_new.json', 'w', encoding='utf-8').write(
    json.dumps(b, ensure_ascii=False))
PY

echo
echo "=== 1. 保存一版改过的（五样全改）==="
python3 - "$PID" <<'PY' > $T/pd_save1.txt
import json, sys, urllib.parse, urllib.request
pid = sys.argv[1]
key = open('/root/csp-exam/data/admin_key.txt').read().strip()
b = json.load(open('/root/csp-exam/tests/tmp/pd_new.json', encoding='utf-8'))
form = {'pid': pid, 'title': b['title'], 'name': b['name'],
        'time_ms': str(b['time_ms']), 'memory_mb': str(b['memory_mb']),
        'statement': b['statement']}
data = urllib.parse.urlencode(form).encode()
req = urllib.request.Request(
    'http://127.0.0.1:8080/admin/problem-detail?key=' + urllib.parse.quote(key),
    data=data, method='POST')
with urllib.request.urlopen(req, timeout=60) as r:
    body = r.read().decode('utf-8', 'replace')
    print(r.geturl())
    print('保存好了' in body)
PY
URL1=$(sed -n 1p $T/pd_save1.txt)
OK1=$(sed -n 2p $T/pd_save1.txt)
[ "$OK1" = "True" ] && pass "保存接口回话说「保存好了」" || fail "保存接口没有回话说成功（$URL1）"
case "$URL1" in *problem-detail*pid=$PID*) pass "保存后跳回这道题的详情页" ;;
                   *) fail "保存后没跳回详情页：$URL1" ;; esac

echo
echo "=== 2. 逐样核对：真的落盘了 ==="
python3 - "$PID" <<'PY'
import json, os, sys
from csp_exam.core import problems as mp
pid = sys.argv[1]
new = json.load(open('/root/csp-exam/tests/tmp/pd_new.json', encoding='utf-8'))
info = mp.load_problem_info().get(pid) or {}
codes = mp.load_codes().get(pid) or {}
stmt = open(os.path.join('data', 'statements', pid + '.md'), encoding='utf-8').read()

def ck(cond, msg):
    print(('   [PASS] ' if cond else '   [FAIL] ') + msg)

ck(info.get('title') == new['title'], '题目名改了（%r）' % info.get('title'))
ck(codes.get('name') == new['name'], '英文名改了（%r）' % codes.get('name'))
ck(int(info.get('time_ms') or 0) == new['time_ms'], '时限改了（%s 毫秒）' % info.get('time_ms'))
ck(int(info.get('memory_mb') or 0) == new['memory_mb'], '内存改了（%s MB）' % info.get('memory_mb'))
ck('PD-MARK-OK' in stmt, '题面落盘了（带标记，%d 字）' % len(stmt))
PY

echo
echo "=== 3. 学生端看到的是改过之后的题面 ==="
# 详情页保存的题面就是学生题面页读的那份文件（`data/statements/<pid>.md`），
# 这里直接按学生页的顺序核一遍，确认"改完立刻生效"。
curl -s --max-time 20 "$BASE/admin/problem-detail?key=$KEY&pid=$PID" -o $T/pd_page.html
grep -q 'PD-MARK-OK' $T/pd_page.html \
  && pass "详情页右边渲染出了新题面" || fail "详情页里看不到新题面"
python3 - "$PID" <<'PY'
import json, sys
sys.path.insert(0, '/root/csp-exam')
from csp_exam.core import localoj
pid = sys.argv[1]
new = json.load(open('/root/csp-exam/tests/tmp/pd_new.json', encoding='utf-8'))
txt = localoj.problem_statement(pid)
print(('   [PASS] ' if 'PD-MARK-OK' in txt else '   [FAIL] ')
      + '题面文件（学生端读的就是它）里是新题面')
PY

echo
echo "=== 4. 改回原样（跑完不留痕迹）==="
python3 - "$PID" <<'PY' > $T/pd_save2.txt
import json, sys, urllib.parse, urllib.request
pid = sys.argv[1]
key = open('/root/csp-exam/data/admin_key.txt').read().strip()
b = json.load(open('/root/csp-exam/tests/tmp/pd_before.json', encoding='utf-8'))
form = {'pid': pid, 'title': b['title'], 'name': b['name'],
        'time_ms': str(b['time_ms']), 'memory_mb': str(b['memory_mb']),
        'statement': b['statement']}
data = urllib.parse.urlencode(form).encode()
req = urllib.request.Request(
    'http://127.0.0.1:8080/admin/problem-detail?key=' + urllib.parse.quote(key),
    data=data, method='POST')
with urllib.request.urlopen(req, timeout=60) as r:
    r.read()
    print('OK')
PY
python3 - "$PID" <<'PY'
import json, os, sys
from csp_exam.core import problems as mp
pid = sys.argv[1]
before = json.load(open('/root/csp-exam/tests/tmp/pd_before.json', encoding='utf-8'))
info = mp.load_problem_info().get(pid) or {}
codes = mp.load_codes().get(pid) or {}
stmt = open(os.path.join('data', 'statements', pid + '.md'), encoding='utf-8').read()

def ck(cond, msg):
    print(('   [PASS] ' if cond else '   [FAIL] ') + msg)

ck(str(info.get('title') or '') == before['title'], '题目名还原了')
ck(str(codes.get('name') or '') == before['name'], '英文名还原了')
ck(int(info.get('time_ms') or 0) == before['time_ms'], '时限还原了')
ck(int(info.get('memory_mb') or 0) == before['memory_mb'], '内存还原了')
ck(stmt == before['statement'], '题面**逐字节**还原了（%d 字）' % len(stmt))
PY

echo
echo "=== 5. 没带密钥改不动（不能靠猜地址改题面）==="
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST \
  "$BASE/admin/problem-detail" --data-urlencode "pid=$PID" --data-urlencode "statement=黑" )
[ "$CODE" = "403" ] && pass "无密钥保存被拒（403）" || fail "无密钥保存没被拒（HTTP $CODE）"

echo
echo "=== 6. 空值不许静悄悄地改坏题目（题名清空 / 时限 0）==="
# 老师把时限清空再保存是很常见的手滑：写 0 会让下一次判题变成"限时 0 毫秒"全 TLE。
# 口径：**按没改处理，并且明确报出来**（不能默默什么都不做）。
python3 - "$PID" <<'PY'
import json, sys, urllib.parse, urllib.request
from csp_exam.core import problems as mp
pid = sys.argv[1]
key = open('/root/csp-exam/data/admin_key.txt').read().strip()
before = json.load(open('/root/csp-exam/tests/tmp/pd_before.json', encoding='utf-8'))


def post(**kw):
    form = {'pid': pid, 'statement': before['statement'], 'title': '', 'time_ms': '',
            'memory_mb': '', 'name': before['name']}
    form.update(kw)
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:8080/admin/problem-detail?key=' + urllib.parse.quote(key),
        data=data, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        return urllib.parse.unquote(r.geturl())


def ck(cond, msg):
    print(('   [PASS] ' if cond else '   [FAIL] ') + msg)


url = post(name='pd-clear-check')                    # 英文名换成新值，题名/时限/内存留空
info = mp.load_problem_info().get(pid) or {}
codes = mp.load_codes().get(pid) or {}
ck(int(info.get('time_ms') or 0) == before['time_ms'],
   '时限留空没被写成 0（还是 %s 毫秒）' % info.get('time_ms'))
ck(int(info.get('memory_mb') or 0) == before['memory_mb'],
   '内存留空没被写成 0（还是 %s MB）' % info.get('memory_mb'))
ck(str(info.get('title') or '') == before['title'], '题目名留空没被清掉')
ck('没改' in url, '页面上说清了哪些字段没改（不是默默忽略）')
ck(str(codes.get('name') or '') == 'pd-clear-check', '英文名能改（这一样是普通文本）')

post(name=before['name'])                            # 英文名改回来
codes = mp.load_codes().get(pid) or {}
ck(str(codes.get('name') or '') == before['name'], '英文名还原了')
PY

echo
echo "  结果：通过 $PASS 项 / 失败 $FAIL 项"
[ "$FAIL" = "0" ] || exit 1
