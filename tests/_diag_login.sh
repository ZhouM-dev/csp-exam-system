#!/bin/bash
# 诊断：账号 GD-0001 为什么登录不上
set -u
cd /root/csp-exam || exit 1

echo "=== 1. 名册里 GD-0001 的账号信息 ==="
python3 -c "
import store
for cid in ('c1','c2','c3'):
    r = store.load_roster(cid).get('GD-0001') or {}
    print('  %s: name=%s uname=%s uid=%s pw=%.6s…' % (cid, r.get('name'), r.get('uname'), r.get('uid'), r.get('pw') or ''))
"

echo
echo "=== 2. 评测站里这些账号还在吗 ==="
docker compose -f /root/hydro/docker-compose.yml exec -T oj-mongo mongosh hydro --quiet --eval \
  'print(JSON.stringify(db.user.find({uname:{$in:["GD-0001","GD-0002","GD-0003"]}},{_id:1,uname:1,priv:1}).toArray()))' 2>/dev/null | sed 's/^/  /'

echo
echo "=== 3. 用名册里的密码手工登录一次 ==="
python3 - <<'PY'
import store, hydro_client as h
r = store.load_roster('c1').get('GD-0001') or {}
print("  尝试登录 %s / %s…" % (r.get('uname'), (r.get('pw') or '')[:6]))
try:
    h.login(r['uname'], r['pw'])
    print("  ✓ 登录成功")
except h.HydroError as e:
    print("  ✗ 登录失败：", e)
PY

echo
echo "=== 4. 评测站后端日志（登录相关）==="
cd /root/hydro && docker compose -f docker-compose.yml logs --tail=120 oj-backend 2>&1 | grep -iE "login|invalid|banned|rate|limit|403" | tail -12 | sed 's/^/  /'

echo
echo "=== 5. 判分机在不在 ==="
cd /root/hydro && docker compose -f docker-compose.yml logs --tail=6 oj-judge 2>&1 | tail -5 | sed 's/^/  /'
