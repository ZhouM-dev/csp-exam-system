#!/bin/bash
# 验收新功能：部分分显示、管理端详细提交结果、题目可搜索下拉。
#
# 本轮改造后的口径：
#   * 考号是「前缀-级别+5 位随机数」——一律用 store.kaohao_of_name() 从本场名单里取，不写死
#   * 「我的提交」页 /result 已删（404）：学生的逐题成绩改到「查成绩」/score 看
#   * 提交详情删了「讲评报告」「在评测站打开这条记录」，改成**测试点明细**三层
#   * 没有「反馈模式」开关了：三种赛制一律公布前零反馈（连"部分正确"都不给），
#     成绩只在「查成绩」页、老师点「公布成绩」之后可见
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat /root/csp-exam/data/admin_key.txt)
T=/root/csp-exam/tests/tmp; mkdir -p $T
PASS=0; FAIL=0
pass() { echo "   [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "   [FAIL] $1"; FAIL=$((FAIL+1)); }
has() { grep -q "$2" "$1" && pass "$3" || fail "$3（页面里没有「$2」）"; }
hasnt() { grep -q "$2" "$1" && fail "$3（不该出现「$2」）" || pass "$3"; }
release() {  # $1=cid $2=0|1 —— 老师「发布/收回成绩」：学生可见性**唯一**的开关
  # 只发 released：**不再**顺手带 open=1 —— 那是「提交开关」，是另一件事（老师可能特意关着提交）。
  curl -s -o /dev/null --max-time 20 -X POST "$BASE/admin/release?key=$KEY&c=$1" \
    --data-urlencode "released=$2"
}
# —— 开关快照 / 还原（开头快照，收尾/trap 还原）----------------------------------
# 本脚本会临时动 c1 的「公布成绩」和「提交开关」，两个开关的**运行前原值**在开头记进 $SW，
# 收尾（含中途被打断）按快照逐字段还原：
#   公布成绩 —— 本场 exam.json 的 released（学生可见性看它）+ contests.json 索引里的 released（镜像）
#   提交开关 —— contests.json 索引里的 open
# 还原必须用**原值**：老师可能本来就公布了成绩、或本来就关着提交，写死 0/1 会把老师的设置冲掉。
# （真实事故：老师「关闭提交 + 公布成绩」之后，任何人跑一次验收，成绩被悄悄收回。）
SW=$T/_new_switches_before.json
snapshot_switches() {  # 把 c1 的运行前原值记到 $SW（必须在第一次改动这些开关**之前**调）
  python3 - "$SW" c1 <<'PY'
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

# c1 的夹具是三种情形：学生01（全对）、学生02（17/0/70 部分分，老数据 total 是错的）、学生03（有名册、无有效提交）
KH1=$(python3 -c "
from csp_exam.core import store
print(store.kaohao_of_name('c1', '学生01'))")
KH2=$(python3 -c "
from csp_exam.core import store
print(store.kaohao_of_name('c1', '学生02'))")
KH3=$(python3 -c "
from csp_exam.core import store
print(store.kaohao_of_name('c1', '学生03'))")
if [ -z "$KH1" ] || [ -z "$KH2" ] || [ -z "$KH3" ]; then
  fail "c1 名单里找不到学生01/学生02/学生03（夹具丢了？先跑 tests/_cleanup.sh 或检查 data/contests/c1/roster.json）"
  echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
  exit 1
fi
echo "  c1 考号：学生01=$KH1 学生02=$KH2 学生03=$KH3"
python3 -c "
import json
r = json.load(open('/root/csp-exam/data/contests/c1/results.json'))
p2 = (r.get('$KH2') or {}).get('problems') or {}
print('   [%s] 学生02的 17/0/70 分夹具还在（T1=%s T2=%s T3=%s）'
      % ('PASS' if [p2.get('T' + str(i), {}).get('score') for i in (1, 2, 3)] == [17, 0, 70] else 'FAIL',
         p2.get('T1', {}).get('score'), p2.get('T2', {}).get('score'), p2.get('T3', {}).get('score')))
" || fail "读 c1 夹具失败"

# 开头先把两个开关的运行前原值快照下来（下面第 3b/5 节要临时改 c1 的「公布成绩」；
# 本脚本不提交作业，所以不动提交开关）——收尾按快照还原，绝不写死 0/1。
snapshot_switches
trap 'restore_switches' EXIT       # 正常收尾、Ctrl-C、被 kill 都会走还原（幂等，重复跑安全）
trap 'exit 130' INT TERM           # 收到信号先退出，交给上面的 EXIT 兜住

echo
echo "=== 1. 题目清单接口（下拉搜索的数据源）==="
curl -s -o /tmp/cat.json -w '  HTTP %{http_code}\n' --max-time 30 "$BASE/api/problems?key=$KEY"
python3 - <<'PY'
import json
d = json.load(open('/tmp/cat.json', encoding='utf-8'))
items = d.get('items') or []
print('  ok=%s 题数=%s 读取时间=%s 错误=%s' % (d.get('ok'), d.get('count'), d.get('fetched_at'), d.get('error') or '无'))
print('  前 3 题:', [(i['pid'], i['title']) for i in items[:3]])
PY
python3 -c "
import json
d = json.load(open('/tmp/cat.json', encoding='utf-8'))
raise SystemExit(0 if (d.get('ok') and len(d.get('items') or []) > 10) else 1)
" && pass "题库清单可用（>10 题）" || fail "题库清单异常"
curl -s -o /tmp/cat_anon.json -w '' --max-time 10 "$BASE/api/problems"
grep -q '"ok": *false' /tmp/cat_anon.json && pass "无密钥访问题目接口被拒绝" || fail "题目接口没做鉴权"

echo
echo "=== 2. 部分分：管理端成绩总表 ==="
curl -s -o /tmp/scores_c1.html -w '  GET /admin/scores?c=c1 -> HTTP %{http_code}\n' --max-time 20 "$BASE/admin/scores?key=$KEY&c=c1"
has /tmp/scores_c1.html "部分正确" "c1 成绩表把 17/70 分显示成「部分正确」"
python3 - <<PY
import re
p = open('/tmp/scores_c1.html', encoding='utf-8').read()
t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', p))
i = t.find('名次')
print('  表格内容：', t[i:i+260])
print('  是否还有裸的「答案错误 17」写法：', '17 答案错误' in t)
PY
has /tmp/scores_c1.html 'class="part"' "部分分用了橙色 part 样式"
# 总分必须是每题得分之和（老数据里 total 字段可能是错的：出现过 17+0+70 却显示 0）
python3 -c "
import re
p = open('/tmp/scores_c1.html', encoding='utf-8').read()
t = re.sub(r'<[^>]+>', ' ', p)
row = [l for l in t.splitlines() if '$KH2' in l]
row = row[0] if row else t
print('  学生02那行末尾：', row.strip()[-60:])
raise SystemExit(0 if '87' in row else 1)
" && pass "总分按每题得分求和（学生02 = 17+0+70 = 87）" || fail "总分没按每题求和"
has /tmp/scores_c1.html '/admin/student' "成绩表里有进详情页的链接"

echo
echo "=== 3. 详细提交结果页 ==="
curl -s -o /tmp/detail.html -w '  GET /admin/student?c=c1&k=$KH1 -> HTTP %{http_code}\n' --max-time 20 "$BASE/admin/student?key=$KEY&c=c1&k=$KH1"
has /tmp/detail.html "学生01" "显示学生姓名"
has /tmp/detail.html "得分" "显示逐题得分"
has /tmp/detail.html "100 / 100 分" "得分带满分（x/100 分）"
has /tmp/detail.html "测试点明细" "有测试点明细区块"
has /tmp/detail.html "查看提交的代码" "能查看学生提交的代码"
# 本轮提交详情改了：删掉「在评测站打开这条记录」，改成页面里的逐点明细（点状态看输入/输出/答案）
hasnt /tmp/detail.html 'record/[0-9]' "不再有「在评测站打开这条记录」的外部链接"
hasnt /tmp/detail.html "讲评报告" "不再有「讲评报告」"
# 题目编号：学生用的文件夹名/freopen 名是「题目编号」（值仍是原来的 slug）
has /tmp/detail.html "candy" "标出第 1 题的题目编号（candy）"
has /tmp/detail.html "$KH1/candy/candy.cpp" "标出第 1 题判分用的文件路径"
python3 - <<'PY'
import re
p = open('/tmp/detail.html', encoding='utf-8').read()
t = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', p))
i = t.find('第 1 题')
print('  第 1 题片段：', t[i:i+260])
PY
printf '  学生02（17/70 分的学生）详情页：'
curl -s -o /tmp/detail2.html --max-time 20 "$BASE/admin/student?key=$KEY&c=c1&k=$KH2"
grep -oE "得分 ?[0-9]+ / 100 分" /tmp/detail2.html | sort -u | tr '\n' ' '; echo
has /tmp/detail2.html "部分正确" "详情页显示部分分"
# 情况 A：完全没有成绩记录（把学生03的记录删掉）→ 应该提示"还没有提交"
python3 - <<PY
import json, io, shutil
f = "/root/csp-exam/data/contests/c1/results.json"
shutil.copyfile(f, "$T/c1_results_backup.json")     # 夹具：跑完要原样还回去
r = json.load(io.open(f, encoding="utf-8"))
r.pop("$KH3", None)
json.dump(r, io.open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("   已清掉 c1 里学生03的记录（原记录已备份）")
PY
printf '  完全没记录的学生：'
curl -s -o /tmp/detail3a.html -w 'HTTP %{http_code}  ' --max-time 20 "$BASE/admin/student?key=$KEY&c=c1&k=$KH3"
grep -o "这位学生还没有提交" /tmp/detail3a.html | head -1
has /tmp/detail3a.html "这位学生还没有提交" "没记录的学生显示「还没有提交」"

# 情况 B：有记录但一道题都没交（空记录）→ 应该提示"一次有效提交都没有"+ 逐题"未提交"
python3 - <<PY
import json, io, time
f = "/root/csp-exam/data/contests/c1/results.json"
r = json.load(io.open(f, encoding="utf-8"))
r["$KH3"] = {"problems": {}, "total": 0,
             "submitted_at": time.strftime("%Y-%m-%d %H:%M:%S")}
json.dump(r, io.open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("   给学生03写了一条空记录")
PY
curl -s -o /tmp/detail3.html -w '' --max-time 20 "$BASE/admin/student?key=$KEY&c=c1&k=$KH3"
has /tmp/detail3.html "一次有效提交都没有" "空记录的学生显示「一次有效提交都没有」"
has /tmp/detail3.html "未提交" "空记录的学生逐题显示「未提交」"
# 名单里没有的考号 / 不存在的比赛
printf '  不存在的考号：'
curl -s -o /tmp/detail4.html -w 'HTTP %{http_code}  ' --max-time 20 "$BASE/admin/student?key=$KEY&c=c1&k=ZZ-9999"
grep -o "还没有提交" /tmp/detail4.html | head -1; echo

echo
echo "=== 3b. 学生视角：公布前零反馈 / 公布后才在「查成绩」看得到部分分 ==="
# 没有「反馈模式」开关了：所有比赛一律——判分照常跑（老师要看成绩），但老师「公布成绩」
# 之前学生端不给任何判定与分数（连"部分正确"都不给）；成绩只在「查成绩」页、公布后可见。
# 3b 要验「公布前零反馈」，前提是 c1 **此刻未公布**（老师可能已经公布过了）——显式复位成
# 未公布；这只是测试前提，收尾会按开头的快照还原成**运行前的原值**（不是还成 0）。
release c1 0
JS=$T/stu_part.jar; rm -f $JS
curl -s -o /dev/null -c $JS -X POST "$BASE/enter" --data-urlencode "c=c1" --data-urlencode "kaohao=$KH2" --max-time 10
# (1) 公布前：只看到「已提交 / 未提交」，没有判定、没有分数、也不提"正在判分"
curl -s -b $JS -o /tmp/hall_unrel.html --max-time 20 "$BASE/hall?c=c1"
has /tmp/hall_unrel.html "已提交" "公布前学生看得到「已提交 / 未提交」"
hasnt /tmp/hall_unrel.html "部分正确" "公布前拿不到判定（连部分正确都没有）"
hasnt /tmp/hall_unrel.html "答案正确" "公布前不给「答案正确」"
hasnt /tmp/hall_unrel.html "答案错误" "公布前不给「答案错误」"
hasnt /tmp/hall_unrel.html "17 / 100" "公布前拿不到逐题分数"
python3 - <<'PY'
# 分数单元格与"正在判分"这类字样一个都不该有（判定词上面已逐条查过）
import re
p = open('/tmp/hall_unrel.html', encoding='utf-8').read()
cells = [x for t in re.findall(r'<b>(\d+)</b>\s*分|</span>\s*(\d+)\s*分', p) for x in t if x]
judge = re.search(r'(?:正在|还在|开始)判分|判分中', p)
print('   分数单元格：%s  判分字样：%s' % (cells or '无', judge.group(0) if judge else '无'))
raise SystemExit(1 if (cells or judge) else 0)
PY
[ $? = 0 ] && pass "公布前没有分数单元格、也不提「正在判分」" || fail "公布前泄露了分数或判分状态"
# (2) 「我的提交」页已删：成绩统一到「查成绩」页看，公布前那页只说"还没公布"
code=$(curl -s -b $JS -o /tmp/res_part.html -w '%{http_code}' --max-time 20 "$BASE/result?c=c1")
[ "$code" = "404" ] && pass "「我的提交」页已删（/result 404）" || fail "「我的提交」页还在（HTTP $code）"
printf '  公布前查成绩页：'
code=$(curl -s -b $JS -o /tmp/score_unrel.html -w '%{http_code}' --max-time 20 "$BASE/score?c=c1&kaohao=$KH2")
grep -oE "成绩还没有公布|等老师公布" /tmp/score_unrel.html | head -1; echo
[ "$code" = "200" ] && pass "公布前「查成绩」页正常打开（HTTP 200，只是不给成绩）" \
  || fail "公布前「查成绩」页异常（HTTP $code）"
hasnt /tmp/score_unrel.html "总分" "公布前「查成绩」页不给总分"
hasnt /tmp/score_unrel.html "部分正确" "公布前「查成绩」页不给逐题判定"
# (3) 公布之后：「查成绩」页逐题看得到 17/100 与 70/100
release c1 1
curl -s -b $JS -o /tmp/score_part.html --max-time 20 "$BASE/score?c=c1&kaohao=$KH2"
has /tmp/score_part.html "部分正确（17/100）" "公布后「查成绩」页能看到 17/100"
has /tmp/score_part.html "部分正确（70/100）" "公布后「查成绩」页能看到 70/100"
python3 -c "
import re
p = open('/tmp/score_part.html', encoding='utf-8').read()
t = re.sub(r'<[^>]+>', ' ', p)
hits = re.findall(r'部分正确（(\d+)/100）', t)
print('  公布后查成绩页看到的：', hits)
raise SystemExit(0 if '17' in hits and '70' in hits else 1)
" && pass "公布后学生能看到 17/100 与 70/100" || fail "公布后分数没显示对"
# (5) 关闭提交之后，**已经公布的成绩不能跟着消失**
#     踩过：老师考完的自然顺序是「关闭提交 → 公布成绩」，而比赛页以前在"提交已关闭"时
#     直接 return，学生只看到一句"本场比赛已关闭提交"，公布了的分数一个字都没有。
curl -s -o /dev/null --max-time 20 -X POST "$BASE/admin/release?key=$KEY&c=c1" \
  --data-urlencode "open=0"
curl -s -b $JS -o /tmp/hall_closed.html --max-time 20 "$BASE/hall?c=c1"
has /tmp/hall_closed.html "已关闭提交" "关闭提交后学生看到「已关闭提交」提示"
has /tmp/hall_closed.html "总分" "关闭提交后**仍然**看得到总分（成绩没被一起藏掉）"
has /tmp/hall_closed.html 'href="/score' "比赛页有去「查成绩」的链接"
# 关提交关的只是"交"，不是"看"：题面照常打开、比赛页不该再摆上传表单
code=$(curl -s -o /tmp/stmt_closed.html -w '%{http_code}' -b $JS --max-time 20 "$BASE/problem?c=c1&p=1")
[ "$code" = "200" ] && pass "关闭提交后题面照常打开（HTTP 200）" \
  || fail "关闭提交后题面打不开了（HTTP $code）"
grep -q 'action="/upload' /tmp/hall_closed.html \
  && fail "关闭提交后比赛页还摆着上传表单（会让人以为能交）" \
  || pass "关闭提交后比赛页不再显示上传表单"
# (4) 还原：按快照还原成**运行前的原值**（不是写死 0 —— 老师可能本来就公布了成绩）
restore_switches

echo
echo "=== 4. 配题：可搜索下拉（保存新格式）==="
curl -s -o /tmp/contest_c2.html -w '  GET /admin?c=c2 -> HTTP %{http_code}\n' --max-time 20 "$BASE/admin?key=$KEY&c=c2"
has /tmp/contest_c2.html "csp-search" "配题区有搜索框"
has /tmp/contest_c2.html "csp-catalog" "配题区有题库候选区"
has /tmp/contest_c2.html "problems_json" "配题表单带 problems_json 字段"
has /tmp/contest_c2.html "手动填写" "保留了手动填写兜底"
# 英文名「跟着本场走」：problems_json 里的 name（老写法 slug 也认）就是学生看到的名字；
# 老师侧的**题目编号**（T00001）由题库登记表决定，不在这里传
curl -s -o /dev/null -w '  POST /admin/exam（problems_json）-> HTTP %{http_code}\n' --max-time 30 \
  -X POST "$BASE/admin/exam?key=$KEY&c=c2" \
  --data-urlencode 'problems_json=[{"pid":"P1002","slug":"road"},{"pid":"P1001","slug":"candy"}]' \
  --data-urlencode 'full=100'
python3 -c "
import json, io
e = json.load(io.open('/root/csp-exam/data/contests/c2/exam.json', encoding='utf-8'))
got = [(p['no'], p['pid'], p.get('name')) for p in e.get('problems', [])]
nums = [p.get('code') for p in e.get('problems', [])]
print('  保存后的题目顺序：', got, '题目编号：', nums)
raise SystemExit(0 if got == [(1, 'P1002', 'road'), (2, 'P1001', 'candy')] else 1)
" && pass "新格式保存生效且保序" || fail "新格式保存顺序不对"
# 旧的 pids 写法还能用
curl -s -o /dev/null -w '  POST /admin/exam（旧的 pids 写法）-> HTTP %{http_code}\n' --max-time 30 \
  -X POST "$BASE/admin/exam?key=$KEY&c=c2" \
  --data-urlencode 'pids=P1001:candy,P1002:road' --data-urlencode 'full=100'
python3 -c "
import json, io
e = json.load(io.open('/root/csp-exam/data/contests/c2/exam.json', encoding='utf-8'))
got = [(p['no'], p['pid'], p.get('name')) for p in e.get('problems', [])]
print('  旧写法保存结果：', got)
raise SystemExit(0 if got == [(1, 'P1001', 'candy'), (2, 'P1002', 'road')] else 1)
" && pass "旧的手工填写格式仍然能用" || fail "旧格式被改坏了"

echo
echo "=== 5. 还原本场数据（成绩表夹具 + 按快照还原开关）==="
python3 - <<PY
import json, io, re, shutil
from csp_exam.core import store
f = "/root/csp-exam/data/contests/c1/results.json"
shutil.copyfile("$T/c1_results_backup.json", f)
r = json.load(io.open(f, encoding='utf-8'))
# 顺手清掉旧脚本（写死 GD-0001/GD-0003 那版）留下的孤儿记录：
# 考号是旧格式、名单里已经没有这个号，会在成绩总表里显示成一个姓名为「?」的多余行
roster = store.load_roster('c1')
orphan = [k for k in r if k not in roster and re.match(r'^[A-Z]+-\d+\$', k)]
for k in orphan:
    r.pop(k, None)
json.dump(r, io.open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
e = r.get("$KH3") or {}
print("  c1 成绩表已还原：学生03 =", e.get("problems"), "总分", e.get("total"))
if orphan:
    print("  清掉旧脚本留下的孤儿记录：", orphan)
PY
# 开关按快照还原成**运行前的原值**（不是写死 0：老师可能本来就公布了成绩、或本来就关着提交）
restore_switches
trap - EXIT INT TERM        # 已经还原过了，撤掉 trap（再跑一次也无害：还原是幂等的）
python3 -c "
from csp_exam.core import store
print('  c1 成绩公布状态：', '已公布' if store.load_exam('c1').get('released') else '未公布')
print('  剩余比赛：', [c['title'] for c in store.list_contests()])
"
echo
echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
