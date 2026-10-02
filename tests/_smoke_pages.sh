#!/bin/bash
# 全路由冒烟：把两个端所有页面都请求一遍，任何 500 或日志报错都算失败
set -u
cd /root/csp-exam || exit 1
BASE=http://127.0.0.1:8080
KEY=$(cat data/admin_key.txt)
PASS=0; FAIL=0
LOG=/root/csp-exam/logs/exam.log
BEFORE=$(wc -l < "$LOG")

CID=$(python3 -c "
from csp_exam.core import store
cs = store.list_contests()
print(cs[0]['id'] if cs else '')")
KH=$(python3 -c "
from csp_exam.core import store
r = store.load_roster('$CID')
print(sorted(r)[0] if r else '')")

J=/root/csp-exam/tests/tmp/smoke.jar; rm -f $J
curl -s -o /dev/null -c $J -X POST "$BASE/enter" --max-time 20 \
  --data-urlencode "c=$CID" --data-urlencode "kaohao=$KH"

check() {   # $1=说明 $2=url $3=jar(可空)
  local code
  if [ -n "${3:-}" ]; then
    code=$(curl -s -o /tmp/smoke.html -w '%{http_code}' -b "$3" --max-time 30 "$2")
  else
    code=$(curl -s -o /tmp/smoke.html -w '%{http_code}' --max-time 30 "$2")
  fi
  if [ "$code" = "200" ] || [ "$code" = "302" ] || [ "$code" = "404" ] || [ "$code" = "403" ]; then
    echo "   [PASS] $1（HTTP $code）"; PASS=$((PASS+1))
  else
    echo "   [FAIL] $1（HTTP $code）"; FAIL=$((FAIL+1))
  fi
}

echo "=== 学生端页面（考号 $KH，比赛 $CID）==="
check "考试入口"        "$BASE/enter?c=$CID"
check "比赛列表页"      "$BASE/contests" "$J"
check "比赛页"          "$BASE/hall?c=$CID" "$J"
check "题面页"          "$BASE/problem?c=$CID&p=1" "$J"
check "考生须知"        "$BASE/help?c=$CID" "$J"
check "查成绩"          "$BASE/score?c=$CID" "$J"
check "大样例页"        "$BASE/sample?c=$CID&p=1" "$J"

echo
echo "=== 本轮删掉的两个入口（口径：#1 统一交文件夹 / #6 「我的提交」页已删）==="
# 「我的提交」页整页已删：必须 404
code=$(curl -s -o /dev/null -w '%{http_code}' -b "$J" --max-time 30 "$BASE/result?c=$CID")
[ "$code" = "404" ] && { echo "   [PASS] 我的提交页已删（/result 404）"; PASS=$((PASS+1)); } \
                    || { echo "   [FAIL] /result 还在（HTTP $code）"; FAIL=$((FAIL+1)); }
# 代码提交页已删：要 302 跳回本场比赛页
loc=$(curl -s -o /dev/null -w '%{http_code}:%{redirect_url}' -b "$J" --max-time 30 "$BASE/submit?c=$CID&p=1")
case "$loc" in
  302*"/hall?c=$CID") { echo "   [PASS] 代码提交页已删（/submit 302 回比赛页）"; PASS=$((PASS+1)); } ;;
  *) { echo "   [FAIL] /submit 没跳回比赛页：$loc"; FAIL=$((FAIL+1)); } ;;
esac

echo
echo "=== 已登录学生点别的考场链接（不能无限重定向）==="
n=$(curl -s -o /root/csp-exam/tests/tmp/enter2.html -b $J -L --max-redirs 6 \
      -w '%{num_redirects}' --max-time 20 "$BASE/enter?c=c2")
if [ "$n" = "0" ]; then
  echo "   [PASS] /enter?c=别的考场 不重定向（显示该场登录页）"; PASS=$((PASS+1))
else
  echo "   [FAIL] /enter?c=别的考场 跳了 $n 次"; FAIL=$((FAIL+1))
fi
grep -q '考号' /root/csp-exam/tests/tmp/enter2.html \
  && { echo "   [PASS] 显示的是考号输入页"; PASS=$((PASS+1)); } \
  || { echo "   [FAIL] 页面不是登录页"; FAIL=$((FAIL+1)); }
check "跨场访问 hall"   "$BASE/hall?c=c2" "$J"

echo
echo "=== 「不在名单里」不能把人卡死（学生反馈：点「重新输入考号」没反应）==="
# 造一个**签名合法、但不在本场名单里**的会话（模拟老师重排考号之后学生手上的旧号）。
# 死循环是这样来的：/hall 判定不在名单 → 给那一页；那一页的按钮指向 /enter?c=…，
# 而 /enter 一看到会话还在就又 redirect 回 /hall → 自己跳自己，点多少次都是这一页。
NIN=/tmp/notin.jar
NINCOOKIE=$(python3 -c "
from csp_exam.core.security import make_cookie
print(make_cookie('$CID', 'GD-S99999'))")
printf '# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t/\tFALSE\t0\tcsp\t%s\n' "$NINCOOKIE" > $NIN
code=$(curl -s -o /tmp/nin.html -w '%{http_code}' -b $NIN -c $NIN --max-time 20 "$BASE/hall?c=$CID")
[ "$code" = "403" ] && { echo "   [PASS] 旧考号进比赛页：403 + 说明页"; PASS=$((PASS+1)); } \
  || { echo "   [FAIL] 旧考号进比赛页 HTTP $code（应当 403）"; FAIL=$((FAIL+1)); }
grep -q 'again=1' /tmp/nin.html \
  && { echo "   [PASS] 那一页的「重新输入考号」带 again=1（能破循环）"; PASS=$((PASS+1)); } \
  || { echo "   [FAIL] 按钮还是老地址，点了会被会话弹回去（卡死）"; FAIL=$((FAIL+1)); }
code=$(curl -s -o /tmp/nin2.html -w '%{http_code}' -b $NIN -c $NIN --max-time 20 "$BASE/enter?c=$CID&again=1")
[ "$code" = "200" ] && grep -q 'name="kaohao"' /tmp/nin2.html \
  && { echo "   [PASS] 点「重新输入考号」→ 考号输入页（HTTP 200）"; PASS=$((PASS+1)); } \
  || { echo "   [FAIL] 点它拿到 HTTP $code（应当是 200 的输入页）"; FAIL=$((FAIL+1)); }
loc=$(curl -s -o /dev/null -b $NIN -c $NIN -w '%{redirect_url}' --max-time 20 "$BASE/hall?c=$CID")
case "$loc" in *"/enter?c=$CID"*)
  echo "   [PASS] 会话已清掉：再去比赛页 → 跳登录页（不再是死循环）"; PASS=$((PASS+1)) ;;
  *) echo "   [FAIL] 会话没清掉，再去比赛页拿到：$loc"; FAIL=$((FAIL+1)) ;;
esac
rm -f $NIN /tmp/nin.html /tmp/nin2.html

echo
echo "=== 管理端页面 ==="
check "比赛列表"        "$BASE/admin?key=$KEY"
check "本场管理"        "$BASE/admin?key=$KEY&c=$CID"
check "成绩总表"        "$BASE/admin/scores?key=$KEY&c=$CID"
check "提交详情"        "$BASE/admin/student?key=$KEY&c=$CID&k=$KH"
# 找一个真实存在的提交文件来测预览（相对 uploads/<考号>/ 的路径）
RF=$(cd /root/csp-exam && python3 -c "
import os
from csp_exam.core import store
base = store.upload_dir('$CID', '$KH')
for root, dirs, fns in os.walk(base):
    for fn in sorted(fns):
        if fn.lower().endswith(('.cpp', '.c')):
            print(os.path.relpath(os.path.join(root, fn), base).replace(os.sep, '/'))
            raise SystemExit
")
if [ -n "$RF" ]; then
  echo "   （用文件 $RF 测预览）"
  check "管理端文件预览"  "$BASE/admin/file?key=$KEY&c=$CID&k=$KH&f=$(printf %s "$RF" | sed 's|/|%2F|g')"
  check "学生端文件预览"  "$BASE/file?c=$CID&f=$(printf %s "$RF" | sed 's|/|%2F|g')" "$J"
  curl -s -b "$J" "$BASE/file?c=$CID&f=$(printf %s "$RF" | sed 's|/|%2F|g')" -o /tmp/_fv.html --max-time 20
  if grep -q 'code-numbered' /tmp/_fv.html; then
    echo "   [PASS] 代码预览带行号"; PASS=$((PASS+1))
  else
    echo "   [FAIL] 预览页没有代码块"; FAIL=$((FAIL+1))
  fi
else
  echo "   [跳过] 这场比赛没有 .cpp 提交，无法测预览"
fi
check "名单分组"        "$BASE/admin/groups?key=$KEY"
check "新建题目"        "$BASE/admin/problem?key=$KEY"
check "题目列表（新页）" "$BASE/admin/problems?key=$KEY"
# 页面 200 不代表那两个按钮能用（都是点开才发的 POST，且 JS 会因为缺元素整段死掉）
# 这个脚本自己打印 [PASS]/[FAIL] 行，这里按行数计入总数
VS_OUT=$(python3 /root/csp-exam/tests/_check_viewstmt.py 2>&1)
printf '%s\n' "$VS_OUT" | grep -E '\[(PASS|FAIL)\]' | sed 's/^/  /'
PASS=$((PASS + $(printf '%s\n' "$VS_OUT" | grep -cE '\[PASS\]' || true)))
FAIL=$((FAIL + $(printf '%s\n' "$VS_OUT" | grep -cE '\[FAIL\]' || true)))
if ! printf '%s\n' "$VS_OUT" | grep -qE '\[(PASS|FAIL)\]'; then
  echo "   [FAIL] 查看题面/自己测试的检查脚本没有输出结果"
  printf '%s\n' "$VS_OUT" | tail -5 | sed 's/^/      /'
  FAIL=$((FAIL+1))
fi
check "考号表"          "$BASE/admin/print?key=$KEY&c=$CID"
check "题目清单接口"    "$BASE/api/problems?key=$KEY"
# 文档页要**真的渲染成网页**：踩过一次「把 Markdown 原文整个倒进 <pre>」——
# 页面照样 200，但老师看到的是一整屏黑底等宽的 `#`、`**` 和表格竖线。
# 判据：渲染出来必有标题和表格（原文里一个都不会有）。
curl -s --max-time 20 "$BASE/docs/problemset" -o /tmp/_doc_smoke.html
NT=$(grep -c '<table' /tmp/_doc_smoke.html || true)
NH=$(grep -c '<h2' /tmp/_doc_smoke.html || true)
if [ "$NT" -ge 1 ] && [ "$NH" -ge 1 ]; then
  echo "   [PASS] 题单导入文档渲染成网页（标题 $NH 个、表格 $NT 个）"; PASS=$((PASS+1))
else
  echo "   [FAIL] 题单导入文档没渲染（表格 $NT 个、标题 $NH 个 —— 疑似成了 Markdown 原文）"
  FAIL=$((FAIL+1))
fi

echo
echo "=== 题面公式渲染（本地 KaTeX）==="
for f in katex.min.css katex.min.js auto-render.min.js fonts/KaTeX_Main-Regular.woff2; do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/static/katex/$f")
  if [ "$code" = "200" ]; then
    echo "   [PASS] 静态资源 $f"; PASS=$((PASS+1))
  else
    echo "   [FAIL] 静态资源 $f（HTTP $code）"; FAIL=$((FAIL+1))
  fi
done
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/static/katex/../__init__.py")
[ "$code" = "404" ] && { echo "   [PASS] 静态资源拒绝目录穿越"; PASS=$((PASS+1)); } \
                    || { echo "   [FAIL] 目录穿越没挡住（$code）"; FAIL=$((FAIL+1)); }
curl -s -b "$J" "$BASE/problem?c=$CID&p=1" -o /tmp/_stmt_smoke.html --max-time 20
grep -q 'katex.min.js' /tmp/_stmt_smoke.html && grep -q 'renderMathInElement' /tmp/_stmt_smoke.html \
  && { echo "   [PASS] 题面页引入并调用 KaTeX"; PASS=$((PASS+1)); } \
  || { echo "   [FAIL] 题面页没引入 KaTeX"; FAIL=$((FAIL+1)); }
grep -q katex /tmp/hall_c1_smoke.html 2>/dev/null || curl -s -b "$J" "$BASE/hall?c=$CID" -o /tmp/hall_c1_smoke.html --max-time 20
# 只看真实的引入（<link>/<script>），别被共用脚本注释里的 "katex" 字样误伤
grep -qE '<link[^>]*katex|<script[^>]*katex' /tmp/hall_c1_smoke.html \
  && { echo "   [FAIL] 其它页面也带上了 KaTeX（浪费流量）"; FAIL=$((FAIL+1)); } \
                                      || { echo "   [PASS] 只有题面页加载 KaTeX"; PASS=$((PASS+1)); }

echo
echo "=== 日志里有没有新的报错 ==="
NEW=$(tail -n +$((BEFORE + 1)) "$LOG" | grep -cE "出错|Traceback" || true)
if [ "${NEW:-0}" = "0" ]; then
  echo "   [PASS] 没有新的异常"; PASS=$((PASS+1))
else
  echo "   [FAIL] 日志里出现 $NEW 条异常："
  tail -n +$((BEFORE + 1)) "$LOG" | grep -A6 -E "出错|Traceback" | head -20 | sed 's/^/      /'
  FAIL=$((FAIL+1))
fi

echo
echo "================== 结果：$PASS 项通过，$FAIL 项失败 =================="
# 纯 ASCII 再报一遍：中文那行经过终端/ssh 的编码转换会把数字吞掉
# （踩过：「：1 项失败」在控制台上显示成「锛? 项失败」，被当成 0 项失败，白高兴一场）。
echo "SMOKE_RESULT pass=$PASS fail=$FAIL"
# 有失败就以非 0 退出：以前只看汇总那行字，退出去永远是 0，谁也拦不住。
[ "$FAIL" = "0" ] || exit 1
