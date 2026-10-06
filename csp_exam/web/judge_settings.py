"""教师查看评测环境，设置近似性能倍率和发起旧提交重测。"""

import html
import math
import urllib.parse

from ..core import gojudge, grading, judge_profile, problems, store
from .ui import page


def route(handler, q, *, post=False):
    key = q.get("key", "")
    if not handler._check_admin(key):
        return handler._json({"ok": False, "error": "需要管理员登录"}, 403)
    query = urllib.parse.urlencode({"key": key}) if key else ""
    action_url = "/admin/judge" + ("?" + query if query else "")
    message = ""
    error = False
    queued = 0
    if post:
        form = handler._form()
        try:
            if form.get("action") == "rejudge":
                queued = grading.rejudge_contest(form.get("c", ""))
                blocked = sum(bool(r.get("rejudge_error")) for r in store.load_results(form.get("c", "")).values())
                message = (f"已安排 {queued} 份提交重测。" +
                           (f" {blocked} 份历史记录待补资料，原分已备份，不能公布为新成绩。" if blocked else "完成后请检查并重新公布。"))
            elif form.get("action") == "limits":
                pid = form.get("pid", "")
                t, m = int(form.get("time_ms", "0")), int(form.get("memory_mb", "0"))
                if t <= 0 or m <= 0 or t > 60000 or m > 2048:
                    raise ValueError("请填题面规定的时限和内存：当前主机支持 1～60000 ms / 1～2048 MiB")
                with store._LOCK:
                    info = problems.load_problem_info()
                    if pid not in info:
                        raise ValueError("题目不存在")
                    info[pid].update(time_ms=t, memory_mb=m, limit_source=form.get("note", "")[:2000])
                    problems.save_problem_info(info)
                message = "题目限额已保存，请安排受影响比赛重测。"
            else:
                judge_profile.save(float(form.get("time_scale", "")),
                                   evidence={"method": "manual", "note": form.get("note", "")[:2000]})
                message = "时间倍率已保存；从之后开始的评测生效。历史成绩需要重测。手动倍率标为未标定。"
        except (ValueError, OSError) as e:
            message = str(e)
            error = True
        if q.get("format") == "json":
            return handler._json({"ok": not error, "message": message, "queued": queued}, 400 if error else 200)
    try:
        environment = gojudge.ensure_ready()
        environment_text = environment["compiler"] + " · " + environment["sandbox"] + " · " + environment["cgroup"]
    except gojudge.GoJudgeError as e:
        environment_text = "评测已停止：" + str(e)
    profile = judge_profile.load()
    scale = profile["time_scale"]
    body = handler._admin_nav(key) + '<h1>评测设置</h1>'
    if message:
        body += handler._flash("info", message)
    body += (f'<div class="card"><p>{html.escape(environment_text)}</p>'
             '<p>NOI Linux 2.0 镜像工具链；C++ 参数：<code>-O2 -std=c++14 -static</code>。'
             'CSP 使用文件输入输出。服务器内核和 CPU 与官方评测机不同。</p>'
             f'<p>时间倍率 <b>{scale:g}</b> · {"已有基准对比记录" if profile["calibrated"] else "未标定"}</p>'
             f'<p>有效时限 = 官方时限 × 倍率。官方 1000 ms 当前为 {math.ceil(1000 * scale)} ms。'
             '使用 CPU 时间；内存和栈限额按题目设置，均不乘倍率。每份结果记录当时的倍率。</p>'
             '<p>倍率大于 1 适用于较慢机器，小于 1 适用于较快机器。建议用同一组基准程序在参考机和评测机运行，'
             '比较中位数。不同算法的加速比例可能不同，倍率不能保证边界代码与官方结果完全一致。</p></div>'
             f'<form class="card" method="post" action="{html.escape(action_url)}">'
             '<input type="hidden" name="action" value="scale">'
             f'<label>时间倍率 <input type="number" name="time_scale" min="0.125" max="8" step="any" value="{scale}"></label>'
             '<label>依据 <input name="note" placeholder="例如：参考机与本机基准耗时对比" maxlength="2000"></label>'
             '<button type="submit">保存倍率</button></form>')
    options = ''.join(f'<option value="{html.escape(c["id"])}">{html.escape(c["title"])}</option>'
                      for c in store.list_contests())
    body += (f'<form class="card" method="post" action="{html.escape(action_url)}" '
             'onsubmit="return confirm(\'重测会收回本场已公布的成绩，保留旧结果备份。确定？\')">'
             '<input type="hidden" name="action" value="rejudge"><h2>按当前规则重测</h2>'
             '<p>对 CSP / OI 的最后一次交卷重测。旧版评测成绩不能自动视为已经修正。'
             'IOI 核验留存的计分源码：全部题目重测满分可证明最高分，否则缺完整历史时标记待补资料。'
             '缺源码或原题配置不一致也保留旧分备份；其它可用提交继续重测。</p>'
             f'<select name="c">{options}</select> <button type="submit">重测本场提交</button></form>')
    missing = [pid for pid, rec in problems.load_problem_info().items()
               if not rec.get("deleted") and (not rec.get("time_ms") or not rec.get("memory_mb"))]
    if missing:
        choices = ''.join(f'<option>{html.escape(pid)}</option>' for pid in missing)
        body += (f'<form class="card" method="post" action="{html.escape(action_url)}">'
                 '<h2>缺少限额的旧题</h2><p>这些题会停止评测，请查原题面后补齐；系统不猜测官方限额。</p>'
                 '<input type="hidden" name="action" value="limits">'
                 f'<label>题目 <select name="pid">{choices}</select></label>'
                 '<label>时限（ms）<input name="time_ms" type="number" min="1" max="60000" required></label>'
                 '<label>内存（MiB）<input name="memory_mb" type="number" min="1" max="2048" required></label>'
                 '<label>依据 <input name="note" placeholder="原题面或出题人的配置" required></label>'
                 '<button type="submit">补齐限额</button></form>')
    return handler._send(page("评测设置", body))
