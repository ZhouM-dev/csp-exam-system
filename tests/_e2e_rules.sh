#!/bin/bash
# 三种赛制端到端验收（按考场登录版）。
#
# 本轮改造后的口径（对照 整改清单.md 与 tasks/T-11）：
#   * 三种赛制**统一交考号文件夹**（POST /upload）；`/submit` 代码提交页已删（302 回比赛页）
#   * 考号格式 `<前缀>-<级别字母><5 位纯随机数>`（如 GD-S10029）——脚本里一律从本场名单里读，
#     **不写死具体号**（写死就变成"断言随机值"了）
#   * 比赛新增 level / duration_min；**没有「反馈模式」开关了**——三种赛制一律赛中零反馈：
#     判分照常跑（老师要看成绩），但老师在管理端点「公布成绩」之前，学生看不到任何判定与
#     分数（连"部分正确"都不给）；公布后可见。IOI 也不再实时给分
#   * 学生端删了「我的提交」页（/result → 404）；新增考生须知（/help?c=）
#   * 计分按测试点（不捆绑）；OI/CSP 按最后一次提交计分，IOI 每题取最高分
#
# 用法：sudo bash /root/csp-exam/tests/_e2e_rules.sh
set -u
cd /root/csp-exam || exit 1
T=/root/csp-exam/tests/tmp; mkdir -p $T
BASE=http://127.0.0.1:8080
KEY=$(cat /root/csp-exam/data/admin_key.txt)
J1=$T/stu_c1.jar; J2=$T/stu_c2.jar; J3=$T/stu_c3.jar
rm -f $J1 $J2 $J3
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }
expect() { [ "$2" = "$3" ] && pass "$1（$2）" || fail "$1：实际 [$2] 期望 [$3]"; }

kh_of() {  # 本场名单里的第一个考号（考号是随机的，从名单里取）
  python3 -c "
from csp_exam.core import store
r = store.load_roster('$1')
print(sorted(r)[0] if r else '')"
}
KH1=$(kh_of c1); KH2=$(kh_of c2); KH3=$(kh_of c3)
if [ -z "$KH1" ] || [ -z "$KH2" ] || [ -z "$KH3" ]; then
  fail "c1/c2/c3 里有比赛没有名单，先跑一次 tests/_cleanup.sh 或检查 data/contests/<id>/roster.json"
  echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
  exit 1
fi
echo "  考号（每场随机分配，从名单里读）：c1=$KH1  c2=$KH2  c3=$KH3"
# 夹具自检：三场都必须配了题（有人手误把题目清空过：POST /admin/exam 不带 problems_json 会清空本场题目）
python3 -c "
from csp_exam.core import store
bad = []
for cid in ('c1', 'c2', 'c3'):
    ps = store.load_exam(cid).get('problems') or []
    print('   %s 本场题目：%s' % (cid, [(p['no'], store.code_of(p)) for p in ps]))
    if not ps:
        bad.append(cid)
raise SystemExit(1 if bad else 0)
" || { fail "c1/c2/c3 有比赛没配题（夹具坏了，先修 data/contests/<id>/exam.json 再跑）"
       echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
       exit 1; }

login() {  # $1=jar $2=cid $3=考号
  curl -s -o /dev/null -c "$1" -X POST "$BASE/enter" --max-time 15 \
    --data-urlencode "c=$2" --data-urlencode "kaohao=$3"
}
# 交考号文件夹（三种赛制**唯一**的提交方式）：$1=jar $2=cid $3=考号，后面跟 <题目编号>:<源码文件> 若干
upload() {
  local jar=$1 cid=$2 kh=$3 spec code F=()
  shift 3
  for spec in "$@"; do
    code=${spec%%:*}
    F+=( -F "files=@${spec#*:};filename=$kh/$code/$code.cpp" )
  done
  curl -s -b "$jar" -o $T/_page.html -w '   HTTP %{http_code}  ' --max-time 240 \
    -X POST "${F[@]}" "$BASE/upload?c=$cid"
  python3 -c "
import re, html
page = open('$T/_page.html', encoding='utf-8').read()
ms = re.findall(r'<div class=\"flash flash-(\w+)\">(.*?)</div>', page, re.S)
txt = ' | '.join(html.unescape(m[1]).strip() for m in ms)
open('$T/_flash.txt','w',encoding='utf-8').write(txt)
print('提示：%s' % (txt or '(无)'))
"
}
field() {  # $1=cid $2=考号 $3=题键(T1) $4=字段
  python3 -c "
import json
e = (json.load(open('/root/csp-exam/data/contests/$1/results.json')).get('$2') or {})
v = ((e.get('problems') or {}).get('$3') or {}).get('$4')
print('' if v is None else v)
"
}
# 判分跑在后台线程里（不是同步返回）：断言前先等它把值写进 results.json，到了就立刻返回
#
# ⚠️ 每交完一次，**先断言 tries**（它每次都真的变），再断言分数/判定——
# `_do_upload` 会先把旧记录原样留在文件里（judging=False），此时读到的字段还是**上一次**的，
# 直接等"分数=期望值"可能一读就中（旧值恰好等于期望值），变成假通过。
# `_apply_result` 一次写进去整条记录，所以只要看到 tries 涨到期望值，
# 同一题后面的 score / status_text 就一定是这一次判分的结果了。
expect_field() {  # $1=说明 $2=cid $3=考号 $4=题键 $5=字段 $6=期望
  local v="" i
  for i in $(seq 1 40); do
    v=$(field "$2" "$3" "$4" "$5")
    [ "$v" = "$6" ] && break
    sleep 1.5
  done
  expect "$1" "$v" "$6"
}
flash_has() { grep -q "$2" $T/_flash.txt && pass "$1" || fail "$1：提示里没有「$2」→ $(cat $T/_flash.txt)"; }
flash_hasnt() { grep -qE "$2" $T/_flash.txt && fail "$1（不该出现「$2」）→ $(cat $T/_flash.txt)" || pass "$1"; }
hall_has() {  # $1=jar $2=cid $3=关键词 $4=说明
  curl -s -b "$1" -o $T/_hall.html --max-time 20 "$BASE/hall?c=$2"
  grep -q "$3" $T/_hall.html && pass "$4" || fail "$4（比赛页里没有「$3」）"
}
hall_hasnt() {  # $1=jar $2=cid $3=正则 $4=说明
  curl -s -b "$1" -o $T/_hall.html --max-time 20 "$BASE/hall?c=$2"
  grep -qE "$3" $T/_hall.html && fail "$4（比赛页里不该出现「$3」）" || pass "$4"
}
release() {  # $1=cid $2=0|1 —— 老师「发布/收回成绩」：学生可见性**唯一**的开关（三种赛制都一样）
  # 只发 released：**不再**顺手带 open=1 —— 那是「提交开关」，是另一件事（老师可能特意关着提交）。
  # 本脚本要交作业，提交开关在第 0 节显式打开、收尾按快照还原。
  curl -s -o /dev/null --max-time 20 -X POST "$BASE/admin/release?key=$KEY&c=$1" \
    --data-urlencode "released=$2"
}
# —— 开关快照 / 还原（第 0 节快照，收尾/trap 还原）--------------------------------
# 本脚本会临时动 c1/c2/c3 的「公布成绩」和「提交开关」，两个开关的**运行前原值**在第 0 节
# 记进 $SW，收尾（含中途被打断）按快照逐字段还原：
#   公布成绩 —— 本场 exam.json 的 released（学生可见性看它）+ contests.json 索引里的 released（镜像）
#   提交开关 —— contests.json 索引里的 open
# 还原必须用**原值**：老师可能本来就公布了成绩、或本来就关着提交，写死 0/1 会把老师的设置冲掉。
# （真实事故：老师「关闭提交 + 公布成绩」之后，任何人跑一次全量验收，成绩被悄悄收回、提交被重新打开。）
SW=$T/_rules_switches_before.json
snapshot_switches() {  # 把 c1/c2/c3 的运行前原值记到 $SW（必须在第一次改动这些开关**之前**调）
  python3 - "$SW" c1 c2 c3 <<'PY'
import io, json, sys


def slot(d, key):
    """原值 + 原来有没有这个键：还原时既不凭空加键，也不漏删"""
    return {'has': key in d, 'val': d.get(key)}


def onoff(v):
    return {True: '开放', False: '关闭'}.get(v, '(无)')


def rel_txt(v):
    return {True: '已公布', False: '未公布'}.get(v, '(无)')


out, data, cids = sys.argv[1], '/root/csp-exam/data', sys.argv[2:]
idx = json.load(io.open(data + '/contests.json', encoding='utf-8'))
snap = {}
for cid in cids:
    c = next((x for x in idx if x.get('id') == cid), None)
    e = json.load(io.open('%s/contests/%s/exam.json' % (data, cid), encoding='utf-8'))
    snap[cid] = {'index_open': slot(c or {}, 'open'),          # 提交开关
                 'index_released': slot(c or {}, 'released'),  # 公布状态（索引里的镜像）
                 'exam_released': slot(e, 'released')}         # 公布状态（学生可见性看它）
json.dump(snap, io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('  开关快照（运行前原值 -> %s）：' % out + '  '.join(
    '%s[提交%s 公布%s]' % (cid, onoff(snap[cid]['index_open']['val']),
                           rel_txt(snap[cid]['exam_released']['val'])) for cid in cids))
PY
}
restore_switches() {  # 按 $SW 还原；幂等/可重入：没改过、重复调用、被杀之后再跑都安全
  [ -f "$SW" ] || return 0
  python3 - "$SW" <<'PY'
import io, json, os, sys

snap, data = json.load(io.open(sys.argv[1], encoding='utf-8')), '/root/csp-exam/data'


def save(path, d):
    """与 store._save 同一套写法；别的键、键序都不动"""
    with io.open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def apply(d, fields):
    """按快照写回：返回真改了几个键（0 = 已经是原值，就不落盘）"""
    n = 0
    for key, sl in fields:
        if sl['has']:
            if d.get(key) != sl['val']:
                d[key] = sl['val']; n += 1
        elif key in d:
            d.pop(key); n += 1
    return n


idx_path = data + '/contests.json'
cs = json.load(io.open(idx_path, encoding='utf-8'))
idx_n = 0
for cid in sorted(snap):
    st = snap[cid]
    c = next((x for x in cs if x.get('id') == cid), None)
    if c is None:                                   # 比赛已被删：没有可还原的对象
        continue
    idx_n += apply(c, [('open', st['index_open']), ('released', st['index_released'])])
    f = '%s/contests/%s/exam.json' % (data, cid)
    if os.path.exists(f):
        e = json.load(io.open(f, encoding='utf-8'))
        if apply(e, [('released', st['exam_released'])]):
            save(f, e)
            print('  已还原 %s' % f)
if idx_n:
    save(idx_path, cs)
    print('  已还原 %s' % idx_path)
cs = json.load(io.open(idx_path, encoding='utf-8'))     # 回读盘上的真实结果
line = []
for cid in sorted(snap):
    c = next((x for x in cs if x.get('id') == cid), None)
    if c is None:
        continue
    f = '%s/contests/%s/exam.json' % (data, cid)
    rel = json.load(io.open(f, encoding='utf-8')).get('released') if os.path.exists(f) else None
    line.append('%s[提交%s 公布%s]' % (cid, {True: '开放', False: '关闭'}.get(c.get('open'), '(无)'),
                                      {True: '已公布', False: '未公布'}.get(rel, '(无)')))
print('  开关已按快照还原：' + '  '.join(line))
PY
}
score_scan() {  # $1=页面文件 $2=expect（该看到分数）|deny（不该看到）
  python3 -c "
import re, sys
p = open('$1', encoding='utf-8').read()
# 学生端看得到分数的写法：比赛页逐题的「N 分」、查成绩页的「N 分」——
# 现在**只有老师公布成绩之后**才可能出现这些数字（公布前一个都不该有）
cells = [x for t in re.findall(r'<b>(\d+)</b>\s*分|</span>\s*(\d+)\s*分|(?:总分|当前得分)\s*[：:]?\s*(\d+)\s*分', p)
         for x in t if x]
prose = re.findall(r'.{0,12}(\d+)\s*分', re.sub(r'<[^>]+>', ' ', p))
print('     成绩单元格：', cells or '(无)')
if len(prose) != len(cells):
    print('     页面文案里提到的分数说法（不计入）：', prose)
sys.exit(0 if bool(cells) == ('$2' == 'expect') else 1)
"
}

# OI / IOI / CSP 的代码怎么写是不一样的（这是三种赛制真正的差别之一）：
#   OI、IOI —— **标准输入输出**（cin/cout），不写 freopen（页面与考生须知都这么告诉学生）
#   CSP     —— 必须 freopen 读写题目编号对应的文件（第 5 节用）
# 所以下面两套源码都备着，别串用：拿 freopen 的代码去测 OI 会读到不存在的文件、拿
# cin/cout 的代码去测 CSP 会因为包装垫片把 stdin 读空而 0 分（两者都是"写错了"的正常结果）。
cat > $T/ab_ok.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }
EOF
cat > $T/ab_wa.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long a,b; cin>>a>>b; cout<<a-b<<endl; return 0; }
EOF
cat > $T/road_ok.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long n; cin>>n; cout<<n*(n+1)/2<<endl; return 0; }
EOF
cat > $T/road_wa.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ long long n; cin>>n; cout<<n*(n-1)/2<<endl; return 0; }
EOF

echo
echo "=== 0. 快照开关原值 + 复位试跑状态（清空本次学生在 c2/c3 的记录 + 三场复位成未公布/可提交）==="
# 先快照**再动**：快照里是老师设过的原值，收尾按它还原（不是想当然写 0/1）
snapshot_switches
trap 'restore_switches' EXIT       # 正常收尾、Ctrl-C、被 kill 都会走还原（幂等，重复跑安全）
trap 'exit 130' INT TERM           # 收到信号先退出，交给上面的 EXIT 兜住
python3 -c "
import json, io
for cid, kh in (('c2', '$KH2'), ('c3', '$KH3')):
    f = '/root/csp-exam/data/contests/%s/results.json' % cid
    r = json.load(io.open(f, encoding='utf-8'))
    e = r.get(kh)
    if e:
        for k in ('T1', 'T2'):
            (e.get('problems') or {}).pop(k, None)
        e['total'] = sum(int(v.get('score') or 0)
                         for v in (e.get('problems') or {}).values())
    json.dump(r, io.open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
# 本脚本要提交作业（第 2/3/5 节），先把**这三场**的提交开关打开；三场同时复位成未公布
# （第 4 节要验「公布前零反馈」，这是前提）。两者收尾都按快照还原。
# 只动本脚本自己的三场：原先这里是遍历**所有比赛**写 released=False，会把别的比赛
# 已公布的成绩也收回，而且收尾只归还这三场 —— 同一口径收紧。
cids = ('c1', 'c2', 'c3')
idx = '/root/csp-exam/data/contests.json'
cs = json.load(io.open(idx, encoding='utf-8'))
for c in cs:
    if c.get('id') not in cids:
        continue
    c['open'] = True            # 提交开关（contests.json 索引）
    c['released'] = False       # 公布状态（索引里的镜像）
    f = '/root/csp-exam/data/contests/%s/exam.json' % c['id']
    e = json.load(io.open(f, encoding='utf-8'))
    e['released'] = False       # 公布状态（学生可见性看这里）
    json.dump(e, io.open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
json.dump(cs, io.open(idx, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('  c2/c3 本次学生的记录已清空；%s 复位成「未公布 + 提交开放」（收尾按快照还原原值）' % list(cids))
"

echo
echo "=== 0b. 学生入口（GET 路径 + 按考场登录）==="
code=$(curl -s -o $T/entry.html -w '%{http_code}' --max-time 10 "$BASE/")
[ "$code" = "200" ] && pass "GET / 返回 200（未登录显示考号输入页）" || fail "GET / 返回 $code"
grep -q 'name="kaohao"' $T/entry.html && pass "GET / 页面里有考号输入框" || fail "GET / 页面里没有考号输入框"
grep -q 'GD-S' $T/entry.html && pass "入口页的考号提示已用新格式（GD-S…）" || fail "入口页还在用旧考号格式"
n=$(curl -s -o /dev/null -L --max-redirs 6 -w '%{num_redirects}' --max-time 15 "$BASE/")
[ "$n" = "0" ] && pass "GET / 不产生跳转（没有重定向循环）" || fail "GET / 跳了 $n 次"
code2=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/enter?c=c1")
[ "$code2" = "200" ] && pass "GET /enter?c=c1 可用（老师发的考试链接）" || fail "GET /enter?c=c1 返回 $code2"
# 不在名单里的考号：格式合法但本场没发过（考号是随机的，现挑一个名单里没有的）
BAD=$(python3 -c "
from csp_exam.core import store
r = store.load_roster('c1')
print(next('GD-S%05d' % n for n in range(1, 100000) if 'GD-S%05d' % n not in r))")
curl -s -o $T/e_bad.html -w '' --max-time 10 -X POST "$BASE/enter" \
  --data-urlencode "c=c1" --data-urlencode "kaohao=$BAD"
grep -q "不在本场名单里" $T/e_bad.html && pass "不在名单里的考号被拒（$BAD）" || fail "不在名单里的考号也能进"
login $J1 c1 "$KH1"
loc=$(curl -s -o /dev/null -b $J1 -w '%{http_code}:%{redirect_url}' --max-time 10 "$BASE/")
case "$loc" in 302*"/hall?c=c1") pass "已登录后 GET / 直接进本场考试" ;; *) fail "已登录后 GET / 异常：$loc" ;; esac
loc=$(curl -s -o /dev/null -b $J1 -w '%{redirect_url}' --max-time 10 "$BASE/hall?c=c3")
case "$loc" in *"/enter?c=c3"*) pass "c1 的会话进不了 c3（考场隔离）" ;; *) fail "跨场没挡住：$loc" ;; esac

echo
echo "=== 0c. 入口口径：/submit 已删、/result 已删、考生须知新增 ==="
loc=$(curl -s -b $J1 -o /dev/null -w '%{http_code}:%{redirect_url}' --max-time 15 "$BASE/submit?c=c1&p=1")
case "$loc" in 302*"/hall?c=c1") pass "GET /submit 跳回比赛页（代码提交页已删）" ;; *) fail "GET /submit 异常：$loc" ;; esac
code=$(curl -s -b $J1 -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/result?c=c1")
[ "$code" = "404" ] && pass "GET /result 已删（404）" || fail "GET /result 返回 $code（应该 404）"
code=$(curl -s -o $T/help.html -w '%{http_code}' --max-time 15 "$BASE/help?c=c1")
[ "$code" = "200" ] && grep -q "考生须知" $T/help.html \
  && pass "考生须知页 /help?c=c1 可用（本轮新增）" || fail "考生须知页异常（HTTP $code）"
# 须知整页换成**广东考区官方通告的原文**（改写版 8 节整段撤了），
# 只有后半段「附：本场信息」是本平台按学生算出来的。
python3 -c "
p = open('$T/help.html', encoding='utf-8').read()
# 不管哪种部署都该成立的：整页换成通告之后，「附：本场信息」和旧版撤掉这两件事
common = [
    ('位随机数' in p, '「本场信息」写了考号是纯随机发号'),
    ('个人信息文件' in p, '「本场信息」写了个人信息文件'),
    ('考生须知' in p, '页面标题是考生须知'),
    ('八、考试期间能看到什么' not in p, '旧版改写须知（第八节）已整段撤掉'),
    ('按测试点给分' not in p, '旧版改写的口径已撤（改由通告原文说话）'),
]
# 通告原文那几条：文件是考区给的、且**不进公开仓库**（版权声明），
# 没放这份文件的部署这里整段跳过，不算失败。
notice = [
    ('广东省认证考生注意事项' in p, '页面是考区官方通告（标题对得上）'),
    ('严格遵从监考教师的指引完成认证机考' in p, '通告第 1 条在'),
    ('以自己的考号命名的目录' in p, '通告第 2 条（考号目录）在'),
    ('以自己名字为文件名的文本文件' in p, '通告第 4 条（个人信息文件）在'),
    ('task3.cpp' in p, '通告第 5 条的目录结构图在'),
    ('每15分钟保存一次程序' in p, '通告第 11 条在'),
    ('未经NOI竞赛办公室书面授权' in p, '通告自带的版权声明也在（原文照录）'),
]
if '广东省认证考生注意事项' in p:
    checks = notice + common
else:
    print('   （本部署没放考区通告原文，跳过通告那几条）')
    checks = [('以考点下发的纸质通告为准' in p, '没放通告时给兜底提示，不 500')] + common
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
"
hall_hasnt $J1 c1 "我的提交" "比赛页没有「我的提交」入口（整页已删）"

echo
echo "=== 1. 三种赛制都在学生端可见（赛制/级别/模式徽章 + 倒计时 + 统一提交区）==="
login $J2 c2 "$KH2"
login $J3 c3 "$KH3"
for C in c1 c2 c3; do
  J=$J1; [ "$C" = "c2" ] && J=$J2; [ "$C" = "c3" ] && J=$J3
  curl -s -b $J -o $T/h_$C.html --max-time 15 "$BASE/hall?c=$C"
done
python3 - <<PY
# 本轮比赛页新增/删掉的元素（整改清单 2.1）
import re
p = {cid: open("$T/h_%s.html" % cid, encoding="utf-8").read() for cid in ("c1", "c2", "c3")}
badge = {"c1": "tag-csp", "c2": "tag-oi", "c3": "tag-ioi"}
checks = []
for cid in ("c1", "c2", "c3"):
    checks += [
        ('<span class="tag %s">' % badge[cid] in p[cid], "%s 有赛制徽章" % cid),
        ('<span class="tag tag-level">' in p[cid], "%s 有级别徽章（J/S）" % cid),
        # 「反馈模式」概念已删：不该再有全真/训练徽章。
        # 带引号是刻意的：CSS 里 `.tag-full {` 后面是空格，徽章 HTML 里是 `class="tag tag-full"`，
        # 而 ui.py 的 CSS 是内联进每一页的，不带引号的写法会打到 CSS 上。
        (not re.search(r'tag-(full|train)"', p[cid]), "%s 没有全真/训练徽章（反馈模式已删）" % cid),
        ('class="timer-bar"' in p[cid], "%s 有倒计时条" % cid),
        ('提交我的文件夹' in p[cid], "%s 的提交区是「交考号文件夹」（三种赛制统一）" % cid),
        ('我的提交' not in p[cid], "%s 比赛页没有「我的提交」入口" % cid),
        # 注意：ui.py 把 CSS 内联进了每一页，CSS 注释里还留着「交卷前检查清单」这些字样，
        # 所以只能拿**内容层**的标记判断（这里用页面里有没有指向已删页面的链接）
        ('/result' not in p[cid], "%s 比赛页没有指向已删的「我的提交」(/result) 的链接" % cid),
    ]
checks += [
    ('你要读写的文件' in p["c1"], "c1（CSP）标出每题「你要读写的文件」"),
    ('命名红线' in p["c1"], "c1（CSP）有命名红线提示"),
    ('个人信息文件' in p["c1"], "c1（CSP）提示个人信息文件"),
    ('不需要 freopen' in p["c2"], "c2（OI）说明用标准输入输出（不需要 freopen）"),
    ('不需要 freopen' in p["c3"], "c3（IOI）说明用标准输入输出（不需要 freopen）"),
]
# 「全真模式 / 训练模式 / 反馈模式」这套说法要彻底消失（学生端所有页面；考生须知也一起看）
help_page = open("$T/help.html", encoding="utf-8").read()
for who, txt in (("c1", p["c1"]), ("c2", p["c2"]), ("c3", p["c3"]), ("考生须知", help_page)):
    checks.append((not re.search(r'全真模式|训练模式|反馈模式', txt),
                   "%s 页面上没有「全真模式 / 训练模式 / 反馈模式」字样" % who))
for okv, label in checks:
    print("   [%s] %s" % ("PASS" if okv else "FAIL", label))
PY

echo
echo "=== 2. OI 赛制（c2）：覆盖式计分（先交对、再交错，只认最后一次）==="
# 代码用**标准输入输出**（OI 的写法），不带 freopen——页面也是这么告诉学生的
upload $J2 c2 $KH2 candy:$T/ab_ok.cpp road:$T/road_ok.cpp
expect_field "OI 提交次数" c2 $KH2 T1 tries 1
expect_field "OI 提交后计分（第一次就对）" c2 $KH2 T1 status_text 答案正确
expect_field "OI 答对拿满分" c2 $KH2 T1 score 100
hall_has $J2 c2 "按最后一次提交计分" "OI 比赛页声明了计分方式（按最后一次提交）"
upload $J2 c2 $KH2 candy:$T/ab_wa.cpp road:$T/road_ok.cpp
expect_field "OI 提交次数累加" c2 $KH2 T1 tries 2
expect_field "OI 先对后错 → 覆盖成答案错误" c2 $KH2 T1 status_text 答案错误
expect_field "OI 计分等于最后一次（不是取最高）" c2 $KH2 T1 score 0
upload $J2 c2 $KH2 candy:$T/ab_wa.cpp road:$T/road_wa.cpp
# 每次都是交**整份文件夹**，所以每道题都跟着涨一次提交次数（第 2 题这次是第 3 次）
expect_field "OI 第 2 题跟着整份提交也计数" c2 $KH2 T2 tries 3
upload $J2 c2 $KH2 candy:$T/ab_wa.cpp road:$T/road_ok.cpp
expect_field "OI 第 2 题提交次数" c2 $KH2 T2 tries 4
expect_field "OI 反向（错→对）也认最后一次" c2 $KH2 T2 status_text 答案正确
# 唯一口径：赛中的交卷回执不给任何判定/分数（公布前连"部分正确"都不给，三种赛制都一样）
flash_hasnt "交卷回执不提判定（连部分正确都没有）" "答案正确|答案错误|部分正确"

echo
echo "=== 2b. OI 的「不写 freopen 也能拿分」这条规则单独钉一次 ==="
# 页面（比赛页 + 考生须知）明写着 OI「用标准输入输出，不需要 freopen」，
# 判分必须按赛制决定要不要做 CSP 的 freopen 包装（整改清单 1.4）。
# （第 2 节已经用标准输入输出跑过覆盖式计分，这里把这条规则显式断言一次：
#   它曾经因为 judge_csp 无条件套垫片而失败，只拿到 17/100。）
upload $J2 c2 $KH2 candy:$T/ab_ok.cpp road:$T/road_ok.cpp
expect_field "OI 又提交了一次" c2 $KH2 T1 tries 5
expect_field "OI 标准输入输出代码应得满分（100）" c2 $KH2 T1 score 100
expect_field "OI 标准输入输出代码的状态应是答案正确" c2 $KH2 T1 status_text 答案正确
python3 -c "
import json
e = (json.load(open('/root/csp-exam/data/contests/c2/results.json')).get('$KH2') or {})
cs = ((e.get('problems') or {}).get('T1') or {}).get('testcases') or []
n = sum(1 for c in cs if c.get('status') == 1)
print('   [%s] 逐点明细：%d/%d 个点通过' % ('PASS' if cs and n == len(cs) else 'FAIL', n, len(cs)))
"

echo
echo "=== 3. IOI 赛制（c3）：每题取最高分 ==="
# 先交一份**满分**的（标准输入输出），再交一份错的：IOI 应该把最高分留着
# （分数只落在 results.json 里，学生端要等老师公布成绩才看得到 —— 见第 4 节）
upload $J3 c3 $KH3 candy:$T/ab_ok.cpp road:$T/road_ok.cpp
expect_field "IOI 首交次数" c3 $KH3 T2 tries 1
expect_field "IOI 首交（满分）计分" c3 $KH3 T2 score 100
upload $J3 c3 $KH3 candy:$T/ab_ok.cpp road:$T/road_wa.cpp
expect_field "IOI 较差提交也计数" c3 $KH3 T2 tries 2
expect_field "IOI 本次结果如实记录" c3 $KH3 T2 attempt_status_text 答案错误
expect_field "IOI 较差提交不覆盖计分 / 每题取最高分" c3 $KH3 T2 score 100
hall_has $J3 c3 "每题取最高分" "IOI 比赛页声明了计分方式（每题取最高分）"

echo
echo "=== 4. 学生端可见性：三种赛制一律「公布前零反馈」 ==="
# 没有「反馈模式」开关了（IOI 也不再实时给分）：公布之前，学生只看到「已提交 / 未提交」——
# 没有分数、没有逐题判定（连"部分正确"都不给），也不提"正在判分"。
for C in c1 c2 c3; do
  J=$J1; [ "$C" = "c2" ] && J=$J2; [ "$C" = "c3" ] && J=$J3
  curl -s -b $J -o $T/vis_$C.html --max-time 15 "$BASE/hall?c=$C"
  score_scan $T/vis_$C.html deny && pass "$C 公布前不给分数" || fail "$C 公布前就泄露了分数"
  grep -q '已提交' $T/vis_$C.html \
    && pass "$C 公布前看得到「已提交 / 未提交」" || fail "$C 连「已提交」都没有"
  grep -qE '部分正确|答案正确|答案错误|编译错误' $T/vis_$C.html \
    && fail "$C 公布前泄露逐题判定" || pass "$C 公布前不给逐题判定"
  grep -qE '(正在|还在|开始)判分|判分中' $T/vis_$C.html \
    && fail "$C 公布前还在提「正在判分」" || pass "$C 公布前连「正在判分」都不提"
done

echo
echo "=== 4b. 公布 / 撤回：三种赛制都一样生效（IOI 不再是例外）==="
# 公布 = 学生可见性**唯一**的开关；成绩在「查成绩」页（/score）也要能查到
for CID in c1 c2 c3; do
  J=$J1; KH=$KH1
  [ "$CID" = "c2" ] && { J=$J2; KH=$KH2; }
  [ "$CID" = "c3" ] && { J=$J3; KH=$KH3; }
  release $CID 1
  curl -s -b $J -o $T/hall_rel.html --max-time 15 "$BASE/hall?c=$CID"
  score_scan $T/hall_rel.html expect && pass "$CID 公布后比赛页能看到分数" || fail "$CID 公布后仍看不到分数"
  curl -s -b $J -o $T/score_rel.html --max-time 15 "$BASE/score?c=$CID&kaohao=$KH"
  score_scan $T/score_rel.html expect && pass "$CID 公布后「查成绩」页能查到分数" || fail "$CID 公布后「查成绩」页查不到分数"
  release $CID 0
  curl -s -b $J -o $T/hall_unrel.html --max-time 15 "$BASE/hall?c=$CID"
  score_scan $T/hall_unrel.html deny && pass "$CID 撤回公布后又看不到分数" || fail "$CID 撤回公布未生效"
  curl -s -b $J -o $T/score_unrel.html --max-time 15 "$BASE/score?c=$CID&kaohao=$KH"
  score_scan $T/score_unrel.html deny && pass "$CID 撤回公布后「查成绩」页也不给分" || fail "$CID 撤回后「查成绩」页还在给分"
done

echo
echo "=== 4d. 比赛列表里就有「发布成绩」按钮（老师考完不用点进比赛里找）==="
curl -s -o $T/adm_list.html --max-time 15 "$BASE/admin?key=$KEY"
NROW=$(grep -c 'action="/admin/release' $T/adm_list.html)
NBTN=$(grep -c '>发布成绩<' $T/adm_list.html)
[ "$NROW" -ge 1 ] && [ "$NBTN" -ge 1 ] \
  && pass "比赛列表每行都有发布成绩按钮（发布入口 $NBTN 个）" \
  || fail "比赛列表没有发布成绩按钮（找到 $NBTN 个）"
release c2 1
curl -s -b $J2 -o $T/hall_btn.html --max-time 15 "$BASE/hall?c=c2"
score_scan $T/hall_btn.html expect \
  && pass "发布成绩后学生立刻看到自己的分数" || fail "发布成绩后学生仍看不到分数"
curl -s -o $T/adm_list2.html --max-time 15 "$BASE/admin?key=$KEY"
grep -q '>收回成绩<' $T/adm_list2.html \
  && pass "发布之后按钮变成「收回成绩」（状态跟着变）" || fail "按钮状态没跟着变"
release c2 0

echo
echo "=== 4c. 管理端成绩总表：分数 + 判定 + 未交考生（用 c1 的迁移数据）==="
# c1 的迁移数据正好覆盖三种情形：全对（张三）、部分正确/答案错误（学生17）、
# 有名册记录但一题没交（王五 → 每题显示「未交」）
curl -s -o $T/adm_scores.html "$BASE/admin/scores?key=$KEY&c=c1"
python3 -c "
import re
p = open('$T/adm_scores.html', encoding='utf-8').read()
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', p))
checks = [
    ('candy' in text and 'road' in text and 'meal' in text, '表头用的是本场题目编号'),
    ('部分正确' in text, '有部分分（按点给分）'),
    ('答案错误' in text, '有判分结果'),
    ('未交' in text, '交了但没这题的学生显示「未交」'),
    ('$KH1' in text, '列出考号（$KH1）'),
    ('/admin/student' in p, '每行能进提交详情'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
i = text.find('名次')
print('     ', text[i:i+220] if i >= 0 else text[:220])
"

echo
echo "=== 5. CSP 赛制（c1）：文件夹 + freopen 流程 ==="
# 自己生成学生要上传的文件夹（重启会清空 /tmp，不能依赖预置文件）
S=$T/stu_slug
rm -rf $S
mkdir -p $S/$KH1/candy $S/$KH1/road $S/$KH1/meal
cat > $S/$KH1/candy/candy.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ freopen("candy.in","r",stdin); freopen("candy.out","w",stdout);
  long long a,b; cin>>a>>b; cout<<a+b<<endl; return 0; }
EOF
cat > $S/$KH1/road/road.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ freopen("road.in","r",stdin); freopen("road.out","w",stdout);
  long long n; cin>>n; cout<<n*(n+1)/2<<endl; return 0; }
EOF
cat > $S/$KH1/meal/meal.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){ freopen("meal.in","r",stdin); freopen("meal.out","w",stdout);
  long long n; cin>>n; bool ok=n>=2;
  for(long long i=2;i*i<=n&&ok;i++) if(n%i==0) ok=false;
  cout<<(ok?"Yes":"No")<<endl; return 0; }
EOF
curl -s -b $J1 -o $T/csp1.html -w "   POST /upload?c=c1（考号 $KH1）-> HTTP %{http_code}\n" --max-time 240 \
  -F "files=@$S/$KH1/candy/candy.cpp;filename=$KH1/candy/candy.cpp" \
  -F "files=@$S/$KH1/road/road.cpp;filename=$KH1/road/road.cpp" \
  -F "files=@$S/$KH1/meal/meal.cpp;filename=$KH1/meal/meal.cpp" \
  "$BASE/upload?c=c1"
grep -q '判分用的文件' $T/csp1.html \
  && pass "CSP 提交后显示每题用了哪个文件（不再做结构校验）" || fail "CSP 提交页没有列出判分用的文件"
grep -q '目录结构不合格' $T/csp1.html \
  && fail "还在做目录结构校验（应该已经删掉）" || pass "提交不再被目录结构退回"
grep -q '个人信息文件' $T/csp1.html \
  && pass "交卷回执检查了个人信息文件（本轮新增）" || fail "回执页没有个人信息文件检查项"
grep -q '文件结构检查' $T/csp1.html \
  && pass "交卷回执是「文件结构检查」清单（本轮重做）" || fail "回执页不是文件结构检查清单"

echo
echo "=== 5b. 学生能看题面 ==="
for C in c1 c2 c3; do
  J=$J1; [ "$C" = "c2" ] && J=$J2; [ "$C" = "c3" ] && J=$J3
  curl -s -b $J -o $T/stmt_$C.html -w "  GET /problem?c=$C&p=1 -> HTTP %{http_code}  " --max-time 30 "$BASE/problem?c=$C&p=1"
  python3 -c "
import re
p = open('$T/stmt_$C.html', encoding='utf-8').read()
text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', p))
has = ('第 1 题' in text) and ('题面' in text or '题目描述' in text or len(text) > 800)
print(('有内容' if has else '内容为空'))
"
done
curl -s -b $J1 "$BASE/hall?c=c1" -o $T/hall_link.html --max-time 20
grep -q '/problem?c=c1&p=1' $T/hall_link.html && pass "比赛页题目可点开题面" || fail "比赛页没有题面入口"

echo
echo "=== 5c. 管理端能看测试点明细（点状态 → 输入 / 学生输出 / 标准答案）==="
curl -s -o $T/tp1.json -w '  GET /admin/testcase?n=1&t=01 -> HTTP %{http_code}\n' --max-time 30 \
  "$BASE/admin/testcase?key=$KEY&c=c1&k=$KH1&n=1&t=01"
python3 -c "
import json
d = json.load(open('$T/tp1.json', encoding='utf-8'))
got = (json.load(open('/root/csp-exam/data/contests/c1/results.json')).get('$KH1') or {})
cs = ((got.get('problems') or {}).get('T1') or {}).get('testcases') or []
checks = [
    (d.get('ok') is True, '逐点详情接口可用'),
    ('input' in d and 'output' in d and 'answer' in d, '返回了输入 / 学生输出 / 标准答案三块内容'),
    (bool(d.get('input')) or bool(d.get('answer')), '输入或标准答案有内容（取自判分记录或题目数据文件）'),
    (bool(d.get('verdict')), '给了这一点的结论（verdict）'),
    (len(cs) > 0, 'results.json 里落了逐点明细（testcases）'),
    (all(('no' in c) for c in cs), '每条逐点记录都有点号'),
]
for okv, label in checks:
    print('   [%s] %s' % ('PASS' if okv else 'FAIL', label))
print('     逐点：%s 条；第 1 点 输入 %r 标准答案 %r' % (len(cs), (d.get('input') or '')[:20], (d.get('answer') or '')[:20]))
"

echo
echo "=== 6. 等判分收尾，看三张成绩表 ==="
busy=x
for i in $(seq 1 40); do
  busy=$(python3 -c "
import json,glob,os
# 只看索引里还在的比赛：被删掉的比赛可能留下孤儿目录（判分线程在删除后才写回），
# 那不是"卡住"，不该让这一项失败——孤儿目录由下面单独一项检查。
idx = {c['id'] for c in json.load(open('/root/csp-exam/data/contests.json'))}
n=0
for f in glob.glob('/root/csp-exam/data/contests/*/results.json'):
    if os.path.basename(os.path.dirname(f)) not in idx:
        continue
    for k,v in json.load(open(f)).items():
        if v.get('judging'): n+=1
print(n)")
  [ "$busy" = "0" ] && break
  sleep 5
done
expect "没有残留的判题中状态" "$busy" "0"
# 孤儿目录：判分过程中删掉比赛才会产生；正常流程下 data/contests 里每个目录都该在索引里
python3 -c "
import json,glob,os
idx = {c['id'] for c in json.load(open('/root/csp-exam/data/contests.json'))}
dirs = {os.path.basename(p) for p in glob.glob('/root/csp-exam/data/contests/*')}
orphan = sorted(dirs - idx)
print('   [%s] 没有孤儿比赛目录（%s）' % ('PASS' if not orphan else 'FAIL', '、'.join(orphan) or '无'))
raise SystemExit(1 if orphan else 0)
" && pass "data/contests 下没有孤儿目录" || fail "有孤儿比赛目录（比赛被删除后判分线程又写了回成绩）"
python3 -c "
import json
for cid, kh, name in (('c1', '$KH1', 'CSP'), ('c2', '$KH2', 'OI'), ('c3', '$KH3', 'IOI')):
    e = (json.load(open('/root/csp-exam/data/contests/%s/results.json' % cid)).get(kh) or {})
    items = ', '.join('%s=%s(%s)x%s' % (k, v.get('score'), v.get('status_text'), v.get('tries'))
                      for k, v in sorted((e.get('problems') or {}).items()))
    print('   [%s] %-4s 总分=%-4s %s' % (cid, name, e.get('total'), items))
"

echo
echo "=== 7. 还原：按快照把「公布成绩 / 提交开关」还原成运行前的原值 ==="
# 只还原本脚本动过的 c1/c2/c3，且还原成**运行前的原值**（不是写死 0/1）：老师可能本来就
# 公布了成绩、或者本来就关着提交，写死就会冲掉老师的设置。题目与名单本脚本从头到尾没改过。
restore_switches
trap - EXIT INT TERM        # 已经还原过了，撤掉 trap（再跑一次也无害：还原是幂等的）
python3 -c "
import json, io
cs = json.load(io.open('/root/csp-exam/data/contests.json', encoding='utf-8'))
for c in cs:
    try:
        e = json.load(io.open('/root/csp-exam/data/contests/%s/exam.json' % c['id'], encoding='utf-8'))
        rel = '已公布' if e.get('released') else '未公布'
    except (OSError, ValueError):
        rel = '(读不到 exam.json)'
    print('  %-3s %-18s 提交%s  公布%s' % (c['id'], c['title'],
          '开放' if c.get('open', True) else '关闭', rel))
print('  剩余比赛：', [c['title'] for c in cs])
"

echo
echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
