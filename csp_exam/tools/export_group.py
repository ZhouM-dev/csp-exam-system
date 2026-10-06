#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一个「名单分组」的学生在**每场考试**里的提交代码 + 成绩导出成一个目录树（可重复跑）。

    sudo python3 csp_exam/tools/export_group.py "XX集训S"          # 导到 /root/csp-exam/tests/tmp/export-group
    sudo python3 csp_exam/tools/export_group.py "XX集训S" /tmp/out --tgz

产出（都在输出目录下）：

    导出说明.txt / meta.json
    总览-每人每场总分.csv          每个学生 × 每场的总分矩阵
    总览-每场情况.csv              每场人数 / 交卷人数 / 组内平均 / 最高
    逐点明细-全部场次.csv          每个测试点的状态、用时、内存（不含输入输出原文）
    <场次号-比赛名>/成绩.csv        该场全场成绩（含"是否本组"一列）
    <场次号-比赛名>/题目/           该场的题面：`<第几题>-<英文名>.md` 原文 + 题目汇总.html
    <场次号-比赛名>/提交代码/<姓名-考号>/…
                                   学生交上来的文件夹**原样**（代码 + 个人信息文件 + 他们顺手带的
                                   .exe/.in/.out 都在），照着学生自己的目录结构摆

CSV 一律带 UTF-8 BOM（Excel 双击不乱码）。**只读**，不动考试服务的任何数据。
学生名单按**姓名**匹配分组（分组里存的就是姓名；一个人在多组里，就会出现在每个组的导出里）。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import sys
import tarfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import csp_exam.compat  # noqa: F401
from html import escape as html_escape  # noqa: E402
from csp_exam.core import localoj, problems as mp, store  # noqa: E402
from csp_exam.core.markdown import md_to_html  # noqa: E402
from csp_exam.web.ui import CSS as SITE_CSS  # noqa: E402

#: 汇总页的外壳。KaTeX 走 CDN（联网时公式排版正常；断网时退化成 `$…$` 原文，不影响阅读）
_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>%s · 题目</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<style>%s
body{background:#f7f8fa}</style>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
        onload="if(window.renderMathInElement)renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'\\\\[',right:'\\\\]',display:true},{left:'$',right:'$',display:false}],throwOnError:false,ignoredTags:['script','noscript','style','textarea','pre','code','option']});"></script>
</head><body><div class="wrap">
<h1>%s</h1>
<p class="muted">本场共 %d 道题 · 导出于 %s</p>
%s
<p class="muted" style="margin-top:30px">这份 HTML 是给他看/打印用的；同目录下的 .md 是题面原文（放回出题工具也能用）。
题面里的公式联网时由 KaTeX 排版。</p>
</div></body></html>"""


def safe(s: str, n: int = 40) -> str:
    return (re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(s or '')).strip()[:n] or 'x')


def write_csv(path: str, rows) -> None:
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        csv.writer(f).writerows(rows)


def export(group_name: str, out: str) -> dict:
    g = next((x for x in store.load_groups() if x.get('name') == group_name), None)
    members = [str(n) for n in (g or {}).get('students') or []]
    if not members:
        raise SystemExit('分组「%s」不存在或没有成员' % group_name)
    mset = set(members)
    os.makedirs(out, exist_ok=True)

    scope = []
    for c in store.list_contests():
        roster = store.load_roster(c['id']) or {}
        mine = {k: v for k, v in roster.items() if str((v or {}).get('name') or '') in mset}
        if mine:
            scope.append((c, roster, mine))

    overview = [['场次', '比赛名称', '日期(创建)', '本组人数', '有成绩人数', '组内平均分', '组内最高分']]
    per_student: dict[str, dict] = {}
    detail = [['场次', '考号', '姓名', '题号', '满分', '得分', '判定', '提交次数',
               '测试点', '点状态', '用时(ms)', '内存(KB)', '说明']]
    meta = {'group': group_name, 'members': members,
            'exported_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'contests': []}

    for c, roster, mine in scope:
        cid, title = c['id'], str(c.get('title') or c['id'])
        cdir = os.path.join(out, '%s-%s' % (cid, safe(title, 30)))
        os.makedirs(cdir, exist_ok=True)

        n_files = n_bytes = 0
        for kh in sorted(mine):
            src = store.upload_dir(cid, kh)
            if not os.path.isdir(src):
                continue
            name = store.student_name(cid, kh)
            dst = os.path.join(cdir, '提交代码', '%s-%s' % (safe(name, 20), kh))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
            for root, _dd, fs in os.walk(dst):
                for f in fs:
                    n_files += 1
                    n_bytes += os.path.getsize(os.path.join(root, f))

        rows = store.ranking(cid, include_all=True)
        probs = store.load_exam(cid).get('problems', [])
        # 列 = **现配的题 ∪ 成绩记录里出现过的题号**：考完之后改过这场的题目配置时，
        # 只按现配出列会让"每题得分加起来 ≠ 总分"（踩过：c2/10月4 那场改过配置）。
        keys: list[str] = []
        colname: dict[str, str] = {}
        for p in probs:
            k = store.problem_dir_name(int(p['no']))
            keys.append(k)
            colname[k] = store.code_of(p)
        cfg_keys = set(keys)
        for r in rows:
            for k in (r['problems'] or {}):
                if k not in keys:
                    keys.append(k)
                    colname[k] = '%s(已不在配置里)' % k
        extra_keys = [k for k in keys if k not in cfg_keys]
        score_rows = [['名次', '考号', '姓名', '是否本组'] + [colname[k] for k in keys]
                      + ['总分', '提交时间']]
        for r in rows:
            cells = []
            for k in keys:
                got = (r['problems'] or {}).get(k) or {}
                cells.append(got.get('score') if got else '未交')
            score_rows.append([r['rank'], r['kaohao'], r['name'],
                               '是' if str(r['name']) in mset else '',
                               *cells, r['total'], r.get('at') or ''])
            per_student.setdefault(str(r['name']), {})[cid] = (
                r['total'] if str(r['name']) in mset else '')
        write_csv(os.path.join(cdir, '成绩.csv'), score_rows)
        if extra_keys:
            io.open(os.path.join(cdir, '注意-这场改过题目配置.txt'), 'w',
                    encoding='utf-8').write(
                '这场的题目配置在考完之后被改过：\n'
                '  现在配置里的题：%s\n'
                '  成绩记录里还有（现在配置里没有）：%s\n'
                '所以「成绩.csv」里给这些题留了列（名字后面标了"已不在配置里"），'
                '分数是当时那场留下的、加起来正好等于总分。\n'
                '「题目」目录里只有**现在配置**的题面 —— 那几道被移除的题，题面要到各自的题目详情页看。\n'
                % ('、'.join('%s(%s)' % (k, colname[k]) for k in keys if k in cfg_keys) or '—',
                   '、'.join(extra_keys)))
            print('   ⚠ %s 这场改过题目配置（成绩里有 %s，配置里没有）'
                  % (cid, '、'.join(extra_keys)))

        n_sub, tot = 0, []
        for kh in sorted(mine):
            s = (store.load_results(cid) or {}).get(kh) or {}
            if s:
                n_sub += 1
                if isinstance(s.get('total'), int):
                    tot.append(s['total'])
            for pid, p in (s.get('problems') or {}).items():
                full = next((int(x.get('full', 100)) for x in probs
                             if store.code_of(x) == pid), 100)
                pts = p.get('testcases') or []
                if not pts:
                    detail.append([cid, kh, store.student_name(cid, kh), pid, full,
                                   p.get('score'), p.get('status_text'), p.get('tries'),
                                   '', '', '', '', ''])
                    continue
                for t in pts:
                    detail.append([cid, kh, store.student_name(cid, kh), pid, full,
                                   p.get('score'), p.get('status_text'), p.get('tries'),
                                   t.get('no'), t.get('status_text'), t.get('time'),
                                   t.get('memory'), str(t.get('message') or '')[:120]])
        avg = (sum(tot) / len(tot)) if tot else 0
        overview.append([cid, title, str(c.get('created_at') or ''), len(mine), n_sub,
                         '%.1f' % avg, max(tot) if tot else 0])

        # ---- 4) 这一场的题目（**只要题面**：.md 原文 + 一个可双击看的汇总 HTML）----
        pdir = os.path.join(cdir, '题目')
        os.makedirs(pdir, exist_ok=True)
        blocks = []
        for p in probs:
            pid = str(p.get('pid') or '')
            slug = store.code_of(p)
            stmt_src = localoj.problem_statement(pid) or ''
            info = mp.load_problem_info().get(pid) or {}
            ptitle = str(info.get('title') or '')
            fname = '%s-%s.md' % (p.get('no'), safe(slug, 24))
            io.open(os.path.join(pdir, fname), 'w', encoding='utf-8', newline='\n').write(
                stmt_src if stmt_src.strip() else '（这道题还没有题面）\n')
            blocks.append(
                '<h2>第 %s 题 · <code>%s</code> · %s</h2>\n'
                '<p class="muted">满分 %s · %s 毫秒 / %s MB · 题面原文见同目录 <code>%s</code></p>\n'
                '<div class="card stmt">%s</div>\n'
                % (p.get('no'), html_escape(slug), html_escape(ptitle),
                   p.get('full', 100), info.get('time_ms') or 1000,
                   info.get('memory_mb') or 256, html_escape(fname),
                   md_to_html(stmt_src) or '<p class="muted">（还没有题面）</p>'))
        io.open(os.path.join(pdir, '题目汇总.html'), 'w', encoding='utf-8', newline='\n').write(
            _HTML % (html_escape(title), SITE_CSS, html_escape(title), len(probs),
                     meta['exported_at'],
                     (('<p class="warn">注意：这场的题目配置在考完之后改过 —— 成绩里还有 %s，'
                       '但现在的配置里没有它们，所以这里只有现配的题面。</p>' % '、'.join(extra_keys))
                      if extra_keys else '') + '\n'.join(blocks)))
        meta['contests'].append({'cid': cid, 'title': title, 'group_students': len(mine),
                                 'submitted': n_sub, 'files': n_files, 'bytes': n_bytes,
                                 'problems': [{'no': p.get('no'), 'slug': store.code_of(p),
                                               'pid': p.get('pid')} for p in probs]})
        print('   %-5s %-30s 组内 %2d 人 / 交卷 %2d 人 · 代码 %3d 个 %6.1f MB'
              % (cid, title[:30], len(mine), n_sub, n_files, n_bytes / 1048576))

    cids = [c['id'] for c, _r, _m in scope]
    titles = {c['id']: str(c.get('title') or c['id']) for c, _r, _m in scope}
    mat = [['姓名'] + ['%s(%s)' % (titles[x][:22], x) for x in cids] + ['合计']]
    for nm in members:
        vals = [(per_student.get(nm) or {}).get(x, '') for x in cids]
        nums = [v for v in vals if isinstance(v, int)]
        mat.append([nm] + [v if v != '' else '未参加' for v in vals] + [sum(nums) if nums else 0])
    write_csv(os.path.join(out, '总览-每人每场总分.csv'), mat)
    write_csv(os.path.join(out, '总览-每场情况.csv'), overview)
    write_csv(os.path.join(out, '逐点明细-全部场次.csv'), detail)
    io.open(os.path.join(out, '导出说明.txt'), 'w', encoding='utf-8').write(
        '分组：%s（%d 人）\n导出时间：%s\n\n目录结构：\n'
        '  总览-每人每场总分.csv   每个学生 × 每场考试的总分矩阵\n'
        '  总览-每场情况.csv       每场的人数 / 交卷人数 / 组内平均分 / 最高分\n'
        '  逐点明细-全部场次.csv   每个测试点的状态、用时、内存（不含输入输出原文）\n'
        '  <场次号-比赛名>/成绩.csv          该场全场成绩（含"是否本组"一列）\n'
        '  <场次号-比赛名>/题目/            该场的题面（<第几题>-<英文名>.md 原文 + 题目汇总.html）\n'
        '  <场次号-比赛名>/提交代码/<姓名-考号>/…  学生交的文件夹原样（含个人信息文件）\n'
        % (group_name, len(members), meta['exported_at']))
    io.open(os.path.join(out, 'meta.json'), 'w', encoding='utf-8').write(
        json.dumps(meta, ensure_ascii=False, indent=1))
    print()
    print('   场次 %d 个／学生 %d 人／逐点明细 %d 行' % (len(scope), len(members), len(detail) - 1))
    return meta


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    group = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith('--') \
        else '/root/csp-exam/tests/tmp/export-group'
    want_tgz = '--tgz' in sys.argv
    if os.path.isdir(out):
        shutil.rmtree(out)
    print('导出分组「%s」→ %s' % (group, out))
    export(group, out)
    if want_tgz:
        tgz = out.rstrip('/') + '.tgz'
        with tarfile.open(tgz, 'w:gz') as t:
            t.add(out, arcname=os.path.basename(out))
        print('   打包：%s（%.1f MB）' % (tgz, os.path.getsize(tgz) / 1048576))
    return 0


if __name__ == '__main__':
    sys.exit(main())
