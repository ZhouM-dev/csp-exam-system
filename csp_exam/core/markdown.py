"""够用的 Markdown → HTML（题面渲染）"""

from __future__ import annotations


import html
def md_to_html(text: str) -> str:
    """把题面 Markdown 渲染成 HTML（够用就好）。

    支持：标题、粗体/斜体/行内代码、围栏代码块、无序/有序列表、引用、分隔线、
    简单表格、段落。先整体转义再生成标签，避免题面里混进 HTML。
    """
    import re as _re
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_code = False
    in_list = ""          # "ul" / "ol" / ""
    in_table = False
    # 正在收集的显示公式块 `$$ … $$`（整块收完才吐出去 —— 见下面那段的说明）
    in_math: list[str] = []

    def inline(t: str) -> str:
        t = html.escape(t)
        t = _re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
        t = _re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
        t = _re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<i>\1</i>", t)
        return t

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = ""

    def close_table():
        nonlocal in_table
        if in_table:
            out.append("</table>")
            in_table = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        # 显示公式块 `$$ … $$`：**整块必须待在同一个元素里**。
        # KaTeX 是在页面上扫这对定界符的，逐行成段落会把它拆成三段
        # （`$$` / 公式 / `$$`），定界符配不上对，页面上就把 `$$` 和公式原样显示出来
        # （踩过：M06 / T00036 那道题的公式全是这样）。
        if in_math:
            in_math.append(stripped)
            if "$$" in stripped:
                out.append('<div class="math-block">'
                           + html.escape("\n".join(in_math)) + "</div>")
                in_math = []
            continue
        if stripped.startswith("$$"):
            close_list(); close_table()
            if stripped.count("$$") >= 2:          # 一行写完：`$$x$$`
                out.append('<div class="math-block">' + html.escape(stripped) + "</div>")
            else:
                in_math = [stripped]
            continue
        if stripped.startswith("```"):
            close_list(); close_table()
            if in_code:
                out.append("</pre>")
                in_code = False
            else:
                out.append('<pre class="stmt-code">')
                in_code = True
            continue
        if in_code:
            out.append(html.escape(raw))
            continue
        if not stripped:
            close_list(); close_table()
            continue
        m = _re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list(); close_table()
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            continue
        if _re.match(r"^(-{3,}|\*{3,})$", stripped):
            close_list(); close_table()
            out.append("<hr>")
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue                      # 表格的分隔行
            if not in_table:
                close_list()
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>")
            continue
        close_table()
        m = _re.match(r"^([-*+])\s+(.*)$", stripped)
        if m:
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{inline(m.group(2))}</li>")
            continue
        m = _re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{inline(m.group(2))}</li>")
            continue
        close_list()
        if stripped.startswith(">"):
            out.append(f"<blockquote>{inline(stripped.lstrip('> '))}</blockquote>")
            continue
        out.append(f"<p>{inline(stripped)}</p>")
    if in_code:
        out.append("</pre>")
    if in_math:                   # `$$` 没写收尾（老师少写了一个）：照样吐出来，别把题面吞了
        out.append('<div class="math-block">' + html.escape("\n".join(in_math)) + "</div>")
    close_list(); close_table()
    return "\n".join(out)
