#!/bin/bash
# 验收：名单分组、每场随机分配考号（新格式）、考号表、登录与考场隔离
#
# 本轮改造后的口径：
#   * 考号 = `<前缀>-<级别字母 J/S><5 位纯随机数>`（如 GD-S10029）——**不再是 1..N 的排列**，
#     所以断言的是「格式对 / 场内不重号 / 不是 1..N」这些**规则**，不是某个具体号
#   * 级别写在考号里，取自比赛字段 level
#   * 同一个考号出现在多场时，登录要求用本场专用链接（考号按场次随机，天然很难撞号，
#     所以这一段用"手工把两场的考号做成一样"来构造这个场景）
set -u
cd /root/csp-exam || exit 1          # python3 -c "from csp_exam.core import store" 需要在这个目录下
BASE=http://127.0.0.1:8080
KEY=$(cat /root/csp-exam/data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }
expect() { [ "$2" = "$3" ] && pass "$1（$2）" || fail "$1：实际 [$2] 期望 [$3]"; }

NAMES="张三
学生17
王五
赵六
钱七
孙八"

echo "=== 0. 清掉之前测试残留的空比赛 ==="
python3 -c "
from csp_exam.core import store
for c in store.list_contests():
    if not store.load_roster(c['id']) and not store.load_exam(c['id']).get('problems') \
       and c['title'].startswith('比赛'):
        store.delete_contest(c['id'])
        print('  已删除空比赛', c['id'], c['title'])
print('  现有比赛：', [(c['id'], c['title']) for c in store.list_contests()])
"

echo
echo "=== 1. 管理端新建分组 ==="
curl -s -o /dev/null -w '  POST /admin/groups -> HTTP %{http_code}\n' --max-time 20 \
  --data-urlencode "action=create" --data-urlencode "group_name=验收测试班" \
  --data-urlencode "names=$NAMES" "$BASE/admin/groups?key=$KEY"
GID=$(python3 -c "
from csp_exam.core import store
g = [x for x in store.load_groups() if x['name'] == '验收测试班']
print(g[0]['gid'] if g else '')
")
expect "分组已建立" "$([ -n "$GID" ] && echo yes || echo no)" "yes"
expect "分组人数" "$(python3 -c "
from csp_exam.core import store
g = [x for x in store.load_groups() if x['name'] == '验收测试班'][0]
print(len(g['students']))")" "6"
curl -s -o /tmp/groups.html -w '' --max-time 20 "$BASE/admin/groups?key=$KEY"
grep -q "验收测试班" /tmp/groups.html && pass "分组页显示分组" || fail "分组页没显示分组"

echo
echo "=== 2. 建两场考试，导入同一分组 -> 考号各自随机、格式是新的 ==="
for TT in 分号测试甲 分号测试乙 分号测试丙; do
  curl -s -o /dev/null --max-time 20 -X POST "$BASE/admin/new?key=$KEY" \
    --data-urlencode "title=$TT" --data-urlencode "rule=CSP" --data-urlencode "level=S"
done
CA=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '分号测试甲'][0])")
CB=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '分号测试乙'][0])")
CC=$(python3 -c "
from csp_exam.core import store
print([c['id'] for c in store.list_contests() if c['title'] == '分号测试丙'][0])")
echo "  甲=$CA 乙=$CB 丙=$CC（乙用另一种考号前缀，丙用来构造"同一考号出现在两场"）"
curl -s -o /dev/null -w "  导入 $CA (前缀 GD) -> HTTP %{http_code}\n" --max-time 30 \
  --data-urlencode "gid=$GID" --data-urlencode "mode=replace" --data-urlencode "prefix=GD" \
  "$BASE/admin/roster?key=$KEY&c=$CA"
curl -s -o /dev/null -w "  导入 $CB (前缀 XY) -> HTTP %{http_code}\n" --max-time 30 \
  --data-urlencode "gid=$GID" --data-urlencode "mode=replace" --data-urlencode "prefix=XY" \
  "$BASE/admin/roster?key=$KEY&c=$CB"
# 乙场再加一个只属于乙场的学生（用来验证"甲场看不到乙场的人"）
curl -s -o /dev/null -w "  给 $CB 追加一名专属学生 -> HTTP %{http_code}\n" --max-time 30 \
  --data-urlencode "names=乙场专属学生" --data-urlencode "mode=append" --data-urlencode "prefix=XY" \
  "$BASE/admin/roster?key=$KEY&c=$CB"
curl -s -o /dev/null -w "  导入 $CC (前缀 GD) -> HTTP %{http_code}\n" --max-time 30 \
  --data-urlencode "gid=$GID" --data-urlencode "mode=replace" --data-urlencode "prefix=GD" \
  "$BASE/admin/roster?key=$KEY&c=$CC"

python3 - <<PY
import re
from csp_exam.core import store
a = store.load_roster('$CA'); b = store.load_roster('$CB')
na = [a[k]['name'] for k in sorted(a)]
nb = [b[k]['name'] for k in sorted(b)]
def parse(roster, cid):
    out = []
    for k, v in sorted(roster.items()):
        m = re.match(r'^([A-Za-z]+)-([JS])(\d+)$', k)
        out.append((k, v, (m.group(1), m.group(2), m.group(3)) if m else ('', '', '')))
    return out
sa, sb = parse(a, '$CA'), parse(b, '$CB')
lv_a = store.level_of(store.get_contest('$CA'))
lv_b = store.level_of(store.get_contest('$CB'))
src = ['张三', '学生17', '王五', '赵六', '钱七', '孙八']
print('  甲场考号顺序：', [(k, v['name']) for k, v, _ in sa])
print('  乙场考号顺序：', [(k, v['name']) for k, v, _ in sb])
open('$T/reshuffle_before.txt', 'w').write('|'.join(na))
checks = [
    (sorted(na) == sorted(src), '甲场 6 人'),
    (sorted(nb) == sorted(src + ['乙场专属学生']), '乙场 7 人'),
    (all(p == 'GD' for _, _, (p, _, _) in sa), '甲场前缀是导入时指定的 GD'),
    (all(p == 'XY' for _, _, (p, _, _) in sb), '乙场前缀是导入时指定的 XY'),
    (all(l == lv_a for _, _, (_, l, _) in sa), '考号里的级别字母 = 本场级别（%s）' % lv_a),
    (all(l == lv_b for _, _, (_, l, _) in sb), '考号里的级别字母 = 本场级别（%s）' % lv_b),
    (all(len(d) == 5 and d.isdigit() for _, _, (_, _, d) in sa), '号码是 5 位数字'),
    (len({k for k, _, _ in sa}) == len(sa), '甲场内不重号'),
    (len({k for k, _, _ in sb}) == len(sb), '乙场内不重号'),
    (sorted(int(d) for _, _, (_, _, d) in sa) != list(range(1, len(sa) + 1)),
     '甲场号码不是 1..N 的排列（纯随机，学生猜不出别人）'),
    ({k for k, _, _ in sa} != {k for k, _, _ in sb}, '两场的随机结果不同'),
    (all(v['uname'].startswith('$CA-') for _, v, _ in sa), '甲场账号名带场次前缀'),
    (all(v['uname'].startswith('$CB-') for _, v, _ in sb), '乙场账号名带场次前缀'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY

echo
echo "=== 3. 考号表 ==="
curl -s -o /tmp/print.html -w '  GET /admin/print -> HTTP %{http_code}\n' --max-time 20 "$BASE/admin/print?key=$KEY&c=$CA"
grep -q "考号表" /tmp/print.html && pass "考号表页面打开" || fail "考号表页面异常"
grep -q "enter?c=$CA" /tmp/print.html && pass "考号表带本场专用考试链接" || fail "考号表缺考试链接"
python3 - <<'PY'
import re, html
p = html.unescape(open('/tmp/print.html', encoding='utf-8').read())
need = ['张三', '学生17', '王五', '赵六', '钱七', '孙八']
missing = [n for n in need if n not in p]
rows = re.findall(r'<td><b>([A-Za-z]+-[JS]\d{5})</b></td><td>([^<]+)</td>', p)
checks = [
    (not missing, '六个学生都在考号表里 %s' % (missing or '')),
    (len(rows) == 6, '考号表 6 行（实际 %d 行）' % len(rows)),
    ('5 位随机数' in p, '考号表写明了考号是新格式（5 位随机数）'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
print('   考号表前 3 行：', rows[:3])
PY

echo
echo "=== 4. 学生登录与考场隔离 ==="
J=$T/stu_iso.jar; rm -f $J
KH_A=$(python3 -c "from csp_exam.core import store; print(sorted(store.load_roster('$CA'))[0])")
NAME_A=$(python3 -c "from csp_exam.core import store; print(store.student_name('$CA', '$KH_A'))")
KH_B=$(python3 -c "from csp_exam.core import store; print(sorted(store.load_roster('$CB'))[0])")
NAME_B=$(python3 -c "from csp_exam.core import store; print(store.student_name('$CB', '$KH_B'))")
# 丙场做成与甲场**同一套考号**：改造后考号是纯随机的，两场天然撞不上号，
# 而"同一考号出现在多场"这条分支必须能测到（老师手工改数据也会撞），所以这里直接构造。
python3 - <<PY
from csp_exam.core import store
a = store.load_roster('$CA')
store.save_roster('$CC', {k: dict(store.new_account('$CC', k), name=v.get('name', '?'))
                          for k, v in a.items()})
print('  丙场（$CC）已改成与甲场同一套考号：', sorted(store.load_roster('$CC'))[:2], '…')
PY

printf '  乙场考号 %s 拿去甲场登录：' "$KH_B"
curl -s -o $T/e1.html --max-time 15 -X POST "$BASE/enter" \
  --data-urlencode "c=$CA" --data-urlencode "kaohao=$KH_B"
grep -q "不在本场名单里" $T/e1.html && pass "别场考号进本场被拒" || fail "别场考号竟然能进"
printf '  不存在的考号 GD-S99999：'
curl -s -o $T/e1b.html --max-time 15 -X POST "$BASE/enter" \
  --data-urlencode "c=$CA" --data-urlencode "kaohao=GD-S99999"
grep -q "不在本场名单里" $T/e1b.html && pass "不存在的考号被拒" || fail "不存在的考号也能进"

printf '  甲场考号 %s 进甲场：' "$KH_A"
curl -s -o /dev/null -w 'HTTP %{http_code} -> %{redirect_url}\n' --max-time 15 \
  -c $J -X POST "$BASE/enter" --data-urlencode "c=$CA" --data-urlencode "kaohao=$KH_A"
curl -s -b $J -o $T/hall.html --max-time 15 "$BASE/hall?c=$CA"
grep -q "$NAME_A" $T/hall.html && pass "进了甲场并看到自己的姓名（$NAME_A）" || fail "姓名不对"
grep -q "乙场专属学生" $T/hall.html && fail "甲场页面出现了乙场的学生！" || pass "甲场页面看不到乙场的任何学生"

LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 15 -b $J "$BASE/hall?c=$CB")
case "$LOC" in *"/enter?c=$CB"*) pass "甲场会话访问乙场被挡回登录页" ;; *) fail "跨场会话没挡住：$LOC" ;; esac

printf '  乙场考号 %s 不带考场编号登录（只在一场出现）：' "$KH_B"
LOC=$(curl -s -o /dev/null -w '%{http_code}:%{redirect_url}' --max-time 15 -X POST "$BASE/enter" \
  --data-urlencode "kaohao=$KH_B")
case "$LOC" in *302*"$CB"*) pass "唯一匹配时自动进对应考场" ;; *) fail "唯一匹配没自动进：$LOC" ;; esac

printf '  甲场考号 %s 不带考场编号登录（甲丙两场都有）：' "$KH_A"
curl -s -o $T/e2.html -w 'HTTP %{http_code}\n' --max-time 15 -X POST "$BASE/enter" \
  --data-urlencode "kaohao=$KH_A"
grep -q "要用哪个考试链接" $T/e2.html && pass "多考场时要求用专用链接（避免串人）" || fail "多考场没有提示"

echo "=== 5. 重新随机分配考号 ==="
curl -s -o /dev/null -w '  甲场重排 -> HTTP %{http_code}\n' --max-time 30 \
  -X POST "$BASE/admin/reshuffle?key=$KEY&c=$CA"
python3 - <<PY
from csp_exam.core import store
before = open('$T/reshuffle_before.txt').read().split('|')
a = store.load_roster('$CA')
order = [a[k]['name'] for k in sorted(a)]
print('   重排后考号顺序：', [(k, v['name']) for k, v in sorted(a.items())])
checks = [
    (sorted(order) == sorted(before), '学生还是那 6 个人'),
    (order != before, '考号确实换过了'),
    (all(k.split('-')[1][0] == store.level_of(store.get_contest('$CA')) for k in a),
     '重排后格式仍然是「前缀-级别+5 位随机数」'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
PY
echo "  有提交的场次（c1）重排："
LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 15 -X POST "$BASE/admin/reshuffle?key=$KEY&c=c1")
python3 -c "
import urllib.parse
m = '$LOC'
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(没有提示)'
print('   提示：', msg)
raise SystemExit(0 if '拒绝' in msg else 1)
" && pass "有提交时拒绝重排考号" || fail "有提交时没有拒绝重排"

# 改级别也会重发考号，同样要挡（有提交的场次）
# 注意：「本场设置」提交到 /admin/release（带 action=settings），不是 /admin/exam
# （/admin/exam 是"保存本场题目"，不带 problems_json 会把题目清空）
if python3 -c "
from csp_exam.core import store
raise SystemExit(0 if store.load_results('c1') else 1)"; then
  LOC=$(curl -s -o /dev/null -w '%{redirect_url}' --max-time 15 -X POST "$BASE/admin/release?key=$KEY&c=c1" \
    --data-urlencode "action=settings" --data-urlencode "level=J")
  python3 -c "
import urllib.parse
m = '$LOC'
msg = urllib.parse.unquote(m.split('m=', 1)[1]) if 'm=' in m else '(没有提示)'
print('   改级别提示：', msg)
raise SystemExit(0 if '拒绝' in msg else 1)
" && pass "有提交时拒绝改级别（改级别等于重发考号）" || fail "有提交时改级别没有被拒绝"
else
  fail "c1 没有成绩记录，测不了「有提交时拒绝改级别」（夹具不对）"
fi

echo
echo "=== 6. 清理测试数据 ==="
python3 - <<PY
from csp_exam.core import store
for c in store.list_contests():
    if c['title'] in ('分号测试甲', '分号测试乙', '分号测试丙'):
        store.delete_contest(c['id'])
for x in [g for g in store.load_groups() if g['name'] == '验收测试班']:
    store.delete_group(x['gid'])
print('  剩余比赛：', [(c['id'], c['title']) for c in store.list_contests()])
print('  剩余分组：', [x['name'] for x in store.load_groups()])
PY
python3 - <<PY
import os
from csp_exam.core import store
left = [c['title'] for c in store.list_contests()
        if c['title'] in ('分号测试甲', '分号测试乙', '分号测试丙')]
left_g = [g['name'] for g in store.load_groups() if g['name'] == '验收测试班']
left_d = [d for d in ('$CA', '$CB', '$CC')
          if os.path.isdir(os.path.join('data', 'contests', d))]
okv = not (left or left_g or left_d)
print('   [%s] 清理干净（残留比赛 %s / 分组 %s / 目录 %s）'
      % ('PASS' if okv else 'FAIL', left or '无', left_g or '无', left_d or '无'))
raise SystemExit(0 if okv else 1)
PY
[ $? = 0 ] && pass "测试比赛与分组都清干净了" || fail "清理没干净：测试比赛/分组还有残留"

echo
echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
