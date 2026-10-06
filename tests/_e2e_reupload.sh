#!/bin/bash
# 验收：题目详情页的「重新上传题目文件夹（覆盖这道题）」
#   /admin/problem-reupload?pid=<题库标识>（multipart，字段名 folder，文件名带相对路径）
#
# 为什么单独一套：这是**写**老师题目数据的第二条路（第一条是建题），而且是**覆盖**式的 ——
# 覆盖错了等于把一道好题的数据换掉。所以这里逐样核对：
#   换掉的：题名（取 题目.md 的一级标题）、题面、标程、测试数据
#   不动  ：题目编号、英文名、时限、内存
# 跑完把自己造的那道题清干净（上一轮跑剩的也先清）。
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }
PID=UP01

echo "=== 0. 清掉上次跑剩的（题目 + 编号 + 元信息 + 临时文件夹）==="
python3 - <<PY
import os, shutil
from csp_exam.core import judgelocal, localoj, problems as mp, store
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
junk = [p for p in localoj.problem_pids() if str(p).startswith('$PID')]
codes, info = mp.load_codes(), load_problem_info()
for p in junk:
    judgelocal.drop_problem_data(p)
    shutil.rmtree(mp._samples_root(p), ignore_errors=True)
    codes.pop(p, None)
    info.pop(p, None)
mp.save_codes(codes)
save_problem_info(info)
store.save_catalog(localoj.list_problems())
shutil.rmtree('$T/uptest', ignore_errors=True)
print('   清掉：', junk or '无（本来就是干净的）')
PY

echo
echo "=== 1. 先按正常建题造一道（时限 1234 / 333，待会验证覆盖不会改它们）==="
python3 - <<'PY'
import io, os, shutil, urllib.parse, urllib.request, uuid

W = '/root/csp-exam/tests/tmp/uptest'
TOP = os.path.join(W, 'UP01-覆盖自检')
os.makedirs(TOP + '/data', exist_ok=True)


def write_folder(version):
    open(TOP + '/data/1.in', 'w').write('1 2\n' if version == 1 else '3 4\n')
    open(TOP + '/data/1.out', 'w').write('3\n' if version == 1 else '7\n')
    open(TOP + '/data/2.in', 'w').write('10 20\n')
    open(TOP + '/data/2.out', 'w').write('30\n')
    io.open(TOP + '/标程.cpp', 'w', encoding='utf-8').write(
        '#include <cstdio>\nint main(){int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);return 0;}\n')
    io.open(TOP + '/题目.md', 'w', encoding='utf-8').write(
        '# 覆盖自检\n\n## 题目描述\n\n第一版题面。\n' if version == 1 else
        '# 覆盖自检（改过）\n\n## 题目描述\n\n第二版题面，带标记 UP-MARK-OK。\n')


def multipart(fields):
    bd = '----up' + uuid.uuid4().hex
    body = b''
    for k, v in fields.items():
        body += ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                 % (bd, k, v)).encode()
    for dp, _dn, fns in os.walk(W):
        for f in fns:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, W).replace('\\', '/')
            body += ('--%s\r\nContent-Disposition: form-data; name="folder"; filename="%s"\r\n'
                     'Content-Type: application/octet-stream\r\n\r\n'
                     % (bd, rel)).encode() + open(p, 'rb').read() + b'\r\n'
    body += ('--%s--\r\n' % bd).encode()
    return body, 'multipart/form-data; boundary=' + bd


def post(url, body, ctype, timeout=300):
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': ctype})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl()


write_folder(1)
KEY = open('/root/csp-exam/data/admin_key.txt').read().strip()
body, ctype = multipart({'pid': 'UP01', 'title': '', 'statement': '',
                         'time_ms': '1234', 'memory_mb': '333'})
post('http://127.0.0.1:8080/admin/problem?key=' + urllib.parse.quote(KEY), body, ctype)
from csp_exam.core import problems as mp
info = mp.load_problem_info().get('UP01') or {}
open('/root/csp-exam/tests/tmp/up_number.txt', 'w').write(str(info.get('code') or ''))
print('   建好：编号 %s，%s 毫秒 / %s MB，%s 组数据'
      % (info.get('code'), info.get('time_ms'), info.get('memory_mb'), info.get('cases')))
PY
NUMBER=$(cat $T/up_number.txt 2>/dev/null || echo "")
[ -n "$NUMBER" ] && pass "造好那道题（编号 $NUMBER）" || fail "建题没成功"

echo
echo "=== 2. 改文件夹（题名/题面/数据都换）→ /admin/problem-reupload 覆盖 ==="
# 题名取**文件夹名**的题名部分（`分类号-题名`，与建题同一套规范）——
# 所以这里把顶层文件夹改名，覆盖后题名就该跟着变
python3 - <<'PY'
import io, os, shutil, urllib.parse, urllib.request, uuid

W = '/root/csp-exam/tests/tmp/uptest'
OLD = os.path.join(W, 'UP01-覆盖自检')
NEW = os.path.join(W, 'UP01-覆盖自检改名')
shutil.move(OLD, NEW)
io.open(os.path.join(NEW, '题目.md'), 'w', encoding='utf-8').write(
    '# 随便写的标题\n\n## 题目描述\n\n第二版题面，带标记 UP-MARK-OK。\n')
open(os.path.join(NEW, 'data', '1.in'), 'w').write('3 4\n')     # 数据换成另一对（与标程自洽）
open(os.path.join(NEW, 'data', '1.out'), 'w').write('7\n')
KEY = open('/root/csp-exam/data/admin_key.txt').read().strip()
bd = '----up' + uuid.uuid4().hex
body = ('--%s\r\nContent-Disposition: form-data; name="pid"\r\n\r\nUP01\r\n' % bd).encode()
for dp, _dn, fns in os.walk(W):
    for f in fns:
        p = os.path.join(dp, f)
        rel = os.path.relpath(p, W).replace('\\', '/')
        body += ('--%s\r\nContent-Disposition: form-data; name="folder"; filename="%s"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'
                 % (bd, rel)).encode() + open(p, 'rb').read() + b'\r\n'
body += ('--%s--\r\n' % bd).encode()
req = urllib.request.Request(
    'http://127.0.0.1:8080/admin/problem-reupload?key=' + urllib.parse.quote(KEY) + '&pid=UP01',
    data=body, method='POST',
    headers={'Content-Type': 'multipart/form-data; boundary=' + bd})
with urllib.request.urlopen(req, timeout=300) as r:
    url = r.geturl()
msg = urllib.parse.unquote(url.split('m=')[-1]) if 'm=' in url else '(无消息)'
print('   覆盖回话：', msg[:200])
PY

echo
echo "=== 3. 逐样核对：该换的换了、不该动的没动 ==="
python3 - "$NUMBER" <<'PY'
import os, sys
from csp_exam.core import judgelocal, localoj, problems as mp
from csp_exam.web.admin_pages import load_problem_info
want_number = sys.argv[1]
info = load_problem_info().get('UP01') or {}
codes = mp.load_codes().get('UP01') or {}
d = judgelocal.cases_dir('UP01')
names = sorted(os.listdir(d)) if os.path.isdir(d) else []
cin = open(os.path.join(d, '1.in'), 'rb').read() if '1.in' in names else b''
cout = open(os.path.join(d, '1.out'), 'rb').read() if '1.out' in names else b''
stmt = localoj.problem_statement('UP01')


def ck(cond, label):
    print(('   [PASS] ' if cond else '   [FAIL] ') + label)


ck(info.get('title') == '覆盖自检改名', '题名按文件夹名换了（%r）' % info.get('title'))
ck('UP-MARK-OK' in stmt, '题面按文件夹换了（%d 字，带标记）' % len(stmt))
ck(cin == b'3 4\n' and cout == b'7\n', '测试数据按文件夹换了（1.in=%r 1.out=%r）' % (cin, cout))
ck(cin != b'1 2\n', '测试数据确实不是旧的那份了')
ck(len([n for n in names if n.endswith('.in')]) == 2, '测试点还是 2 组（%s）' % names)
ck(str(codes.get('code') or '') == want_number, '题目编号不变（%s）' % codes.get('code'))
ck(int(info.get('time_ms') or 0) == 1234, '时限没被默认值冲掉（%s 毫秒）' % info.get('time_ms'))
ck(int(info.get('memory_mb') or 0) == 333, '内存没被默认值冲掉（%s MB）' % info.get('memory_mb'))
ck(not str(codes.get('name') or ''), '英文名保持原样（文件夹里没有这一项）')
ck(int(info.get('cases') or 0) == 2, '元信息里的测试点数跟着更新（%s）' % info.get('cases'))
ck(bool(str(info.get('std') or '').strip()), '元信息里的标程跟着更新了')
PY

echo
echo "=== 4. 覆盖后的数据能被判（标程跑满 2/2）==="
python3 - <<'PY'
import json, urllib.parse, urllib.request
KEY = open('/root/csp-exam/data/admin_key.txt').read().strip()
src = open('/root/csp-exam/tests/tmp/uptest/UP01-覆盖自检改名/标程.cpp', encoding='utf-8').read()
body = urllib.parse.urlencode({'pid': 'UP01', 'source': src}).encode()
req = urllib.request.Request('http://127.0.0.1:8080/admin/selftest?key=' + urllib.parse.quote(KEY),
                             data=body, method='POST')
with urllib.request.urlopen(req, timeout=300) as r:
    d = json.loads(r.read().decode('utf-8', 'replace'))
ok = d.get('passed') == 2 and d.get('total') == 2
print('   自己测试：passed=%s/%s' % (d.get('passed'), d.get('total')))
print(('   [PASS] ' if ok else '   [FAIL] ') + '覆盖后的数据自洽、标程满分')
PY

echo
echo "=== 5. 没带密钥覆盖不了 ==="
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -X POST \
  "$BASE/admin/problem-reupload?pid=UP01" -F "folder=@/etc/hostname;filename=x/1.in")
[ "$CODE" = "403" ] && pass "无密钥覆盖被拒（403）" || fail "无密钥覆盖没被拒（HTTP $CODE）"

echo
echo "=== 6. 清理 ==="
python3 - <<'PY'
import os, shutil
from csp_exam.core import judgelocal, localoj, problems as mp, store
from csp_exam.web.admin_pages import load_problem_info, save_problem_info
codes, info = mp.load_codes(), load_problem_info()
for p in [x for x in localoj.problem_pids() if str(x).startswith('UP01')]:
    judgelocal.drop_problem_data(p)
    shutil.rmtree(mp._samples_root(p), ignore_errors=True)
    codes.pop(p, None)
    info.pop(p, None)
mp.save_codes(codes)
save_problem_info(info)
store.save_catalog(localoj.list_problems())
shutil.rmtree('/root/csp-exam/tests/tmp/uptest', ignore_errors=True)
left = [p for p in localoj.problem_pids() if str(p).startswith('UP01')]
print('   残留：', left or '已清干净')
PY

echo
echo "  结果：通过 $PASS 项 / 失败 $FAIL 项"
[ "$FAIL" = "0" ] || exit 1
