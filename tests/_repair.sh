#!/bin/bash
# 修复现有名册：从学生池把姓名/密码补回名册（并落盘），再让评测站账号密码与名册一致
set -u
cd /root/csp-exam || exit 1

echo "=== 1. 名册补姓名/密码（以学生池为准）==="
python3 - <<'PY'
import json, io, store
pool = json.load(io.open('data/students.json', encoding='utf-8'))
for cid in ('c1', 'c2', 'c3'):
    roster = store.load_roster(cid)
    fixed = 0
    for k, v in roster.items():
        old = pool.get(k) or {}
        if not old:
            continue
        if v.get('name') != old.get('name') or v.get('pw') != old.get('pw'):
            v['name'] = old.get('name', v.get('name'))
            v['pw'] = old.get('pw', v.get('pw'))
            v['uid'] = v.get('uid') or old.get('uid')
            v['uname'] = v.get('uname') or k
            fixed += 1
    store.save_roster(cid, roster)
    print('  %s：修正 %d 条，现在 %s' % (
        cid, fixed, {k: v.get('name') for k, v in sorted(roster.items())}))
PY

echo
echo "=== 2. 让评测站账号的密码与名册一致（重名会重置密码）==="
python3 - <<'PY'
import store, hydro_client as h
seen = set()
for cid in ('c1', 'c2', 'c3'):
    for k, v in sorted(store.load_roster(cid).items()):
        if k in seen or not v.get('pw'):
            continue
        seen.add(k)
        try:
            uid = h.create_user(v.get('uname') or k, v['pw'])
            roster = store.load_roster(cid)
            if k in roster and not roster[k].get('uid'):
                roster[k]['uid'] = uid
                store.save_roster(cid, roster)
            print('  %s（%s）→ 账号密码已同步 uid=%s' % (k, v.get('name'), uid))
        except h.HydroError as e:
            print('  %s 同步失败：%s' % (k, e))
PY

echo
echo "=== 3. 逐个账号登录验证 ==="
python3 - <<'PY'
import time, store, hydro_client as h
ok = 0
for cid in ('c1',):
    for k, v in sorted(store.load_roster(cid).items()):
        try:
            h.login(v['uname'], v['pw'])
            print('  ✓ %s（%s）登录成功' % (k, v.get('name')))
            ok += 1
        except h.HydroError as e:
            print('  ✗ %s 登录失败：%s' % (k, e))
        time.sleep(2)
print('  共 %d 个账号可用' % ok)
PY

echo
echo "=== 4. 名册现在自带姓名了吗（不再依赖 students.json）==="
python3 - <<'PY'
import store
for cid in ('c1', 'c2', 'c3'):
    print('  %s：%s' % (cid, {k: v.get('name') for k, v in sorted(store.load_roster(cid).items())}))
PY
