#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自测：题面 Markdown 渲染（`core/markdown.py`）。

盯的主要是**显示公式块 `$$ … $$`**：KaTeX 是在页面上扫这对定界符的，
逐行成段落会把它拆成三段（`$$` / 公式 / `$$`），定界符配不上对，
页面上就把 `$$` 和公式原样显示出来（踩过：M06 / T00036 那道题的公式全是这样）。
所以这里的判据是"**整块待在一个元素里**"。

用法：python3 selftest_markdown.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))   # -> src/
import csp_exam.compat  # noqa: F401  （登记平铺模块名，兼容老写法）
from csp_exam.core.markdown import md_to_html  # noqa: E402

PASS = FAIL = 0


def ok(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s %s" % (label, extra))
    else:
        FAIL += 1
        print("  [FAIL] %s %s" % (label, extra))


def main() -> int:
    print("=== 1. 显示公式块：整块一个元素 ===")
    h = md_to_html("前一句。\n\n$$\np_1<p_2<\\cdots<p_m.\n$$\n\n后一句。\n")
    ok('class="math-block">$$\np_1&lt;p_2&lt;\\cdots&lt;p_m.\n$$</div>' in h,
       "多行 $$ … $$ 整块在一个 .math-block 里")
    ok("<p>$$</p>" not in h, "没有孤零零的 <p>$$</p>")
    ok("<p>后一句。</p>" in h, "公式前后的正文照旧成段")
    h2 = md_to_html("前。\n\n$$x^2+y^2=z^2$$\n\n后。\n")
    ok('class="math-block">$$x^2+y^2=z^2$$</div>' in h2, "一行写完的 $$…$$ 也在一个元素里")

    print()
    print("=== 2. 公式里的字符别被别的规则吃掉 ===")
    h3 = md_to_html("$$\n|S|\\le k,\\qquad -1\\le a_i\n$$\n")
    ok("<table>" not in h3 and "<ul>" not in h3 and 'class="math-block"' in h3,
       "公式里的 | 和 - 不会被当成表格/列表")
    h4 = md_to_html("$$\na_i \\cdot b_j = *\n$$\n")
    ok('class="math-block"' in h4 and "<i>" not in h4 and "<b>" not in h4,
       "公式里的 * 不会被当成斜体/粗体")
    h5 = md_to_html("$$\nD_i(S)=\\min_{j\\in S} d_{i,j}.\n$$\n\n- 第一项\n- 第二项\n")
    ok('class="math-block"' in h5 and "<ul>" in h5 and "<li>第一项</li>" in h5,
       "公式后面的列表照旧是列表")

    print()
    print("=== 3. 写坏的情况别吞题面 ===")
    h6 = md_to_html("$$\n1\\le n\\le 10^5,\n\n后面的正文。\n")
    ok("后面的正文" in h6, "$$ 没写收尾时后面的题面还在（不吞）")

    print()
    print("=== 4. 老功能没被带坏 ===")
    h7 = md_to_html("# 标题\n\n- a\n- b\n\n| x | y |\n| - | - |\n| 1 | 2 |\n\n"
                    "行内 $a_i$ 与 `code`。\n\n```\ncode block\n```\n\n---\n\n> 引用\n")
    ok("<h2>标题</h2>" in h7, "标题")
    ok("<ul>" in h7 and "<li>a</li>" in h7, "无序列表")
    ok("<table>" in h7 and "<td>1</td>" in h7, "表格")
    ok("$a_i$" in h7, "行内公式原样留着（交给 KaTeX 渲染）")
    ok("<code>code</code>" in h7, "行内代码")
    ok('<pre class="stmt-code">' in h7 and "code block" in h7, "围栏代码块")
    ok("<hr>" in h7, "分隔线")
    ok("<blockquote>引用</blockquote>" in h7, "引用")
    ok("<script" not in md_to_html("<script>alert(1)</script>"), "题面里的 HTML 被转义（不注入）")

    print()
    print("================== 结果：%d 项通过，%d 项失败 ==================" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
