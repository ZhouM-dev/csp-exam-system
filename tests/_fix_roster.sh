#!/bin/bash
# 恢复 students.json（老名册的姓名/密码来源），并把迁移落盘、验证账号能登录
set -u
cd /root/csp-exam || exit 1

echo "=== 1. 把 students.json 放回 data/（它不是垃圾，是老名册的姓名/密码来源）==="
if [ -f "data/_backup_旧单场/students.json" ]; then
  cp "data/_backup_旧单场/students.json" "data/students.json" && echo "  已放回"
else
  echo "  !! 备份里没有 students.json"
fi
python3 -c "
import json, io
d = json.load(io.open('data/students.json', encoding='utf-8'))
print('  学生池内容：', {k: v.get('name') for k, v in d.items()})
"

echo
echo "=== 2. 触发一次名册迁移（现在会落盘）==="
python3 - <<'PY'
import json, io, os, store
for cid in ('c1', 'c2', 'c3'):
    path = 'data/contests/%s/roster.json' % cid
    raw = json.load(io.open(path, encoding='utf-8'))
    shape = '旧格式(列表)' if isinstance(raw, list) else '新格式(字典)'
    r = store.load_roster(cid)          # 这次会把结果写回磁盘
    after = json.load(io.open(path, encoding='utf-8'))
    print('  %s：读前 %s，读后 %s，%d 人，GD-0001=%s' % (
        cid, shape, '新格式(字典)' if isinstance(after, dict) else '旧格式(列表)',
        len(r), (r.get('GD-0001') or {}).get('name')))
PY

echo
echo "=== 3. 现在名册自带姓名/密码了吗（不再依赖 students.json）==="
python3 - <<'PY'
import store
for cid in ('c1', 'c2', 'c3'):
    r = store.load_roster(cid).get('GD-0001') or {}
    print('  %s: name=%s uname=%s pw=%.6s… uid=%s' % (
        cid, r.get('name'), r.get('uname'), r.get('pw') or '', r.get('uid')))
PY

echo
echo "=== 4. 用名册里的账号密码登录一次（验证修复）==="
python3 - <<'PY'
import time, store, hydro_client as h
r = store.load_roster('c1').get('GD-0001') or {}
try:
    h.login(r['uname'], r['pw'])
    print("  ✓ 登录成功")
except h.HydroError as e:
    print("  ✗ 登录失败：", e)
PY

echo
echo "=== 5. 把刚被挪走的那几个文件"复制回来"（保留留档，不动 data/ 根）==="
ls data/ | sed 's/^/  data\//'
python3 -c "
import store
print('  c1 名单人数：', len(store.load_roster('c1')))
print('  c1 学生姓名：', store.student_name('c1', 'GD-0001'))
"
