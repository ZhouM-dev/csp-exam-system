"""Bind existing server-rendered forms to unmodified Tabler components.

The page shell follows Tabler's official sample/sidebar layouts. This adapter
changes presentation classes only; form routes, fields, scripts and IDs survive.
"""
import html
from html.parser import HTMLParser
from pathlib import Path
import re
from string import Template

TEMPLATES = Path(__file__).with_name("templates")
ASSETS = "/static/vendor/tabler/1.6.1/"
ATTRIBUTE_RE = re.compile(r'([^\s/=>]+)(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?')
ALIASES = {
    "muted": "text-secondary", "ok": "text-success", "warn": "text-warning", "err": "text-danger",
    "flash": "alert", "flash-ok": "alert-success", "flash-err": "alert-danger", "flash-info": "alert-info",
    "tag": "badge", "tag-oi": "bg-blue-lt", "tag-ioi": "bg-green-lt", "tag-csp": "bg-yellow-lt",
    "tag-ok": "bg-green-lt", "tag-bad": "bg-red-lt", "tag-wait": "bg-secondary-lt", "tag-level": "bg-blue-lt",
    "panel": "card", "sr-only": "visually-hidden", "pcard-item": "card card-body",
    "toolbar": "card-header justify-content-between gap-2 flex-wrap", "tools": "d-flex gap-2 align-items-center",
    "refresh": "btn-icon btn-outline-secondary", "heading": "d-flex gap-2 align-items-center mb-4",
    "scroll": "table-responsive", "count": "text-secondary", "modal-close": "btn-outline-secondary",
    "kv": "flex-row flex-wrap gap-4",
    "tree": "overflow-auto",
}


class Components(HTMLParser):
    def __init__(self, admin=False):
        super().__init__(convert_charrefs=False)
        self.out = []
        self.admin = admin

    def start(self, tag, attrs, closed=False):
        raw = self.get_starttag_text()
        original = dict(attrs)
        classes = (original.get("class") or "").split()
        additions = []
        for cls in classes:
            additions.extend(ALIASES.get(cls, "").split())
        if (tag == "button" or "btn" in classes) and not any(c in classes for c in ("dropdown-item", "navbar-toggler")):
            additions += ["btn"]
            if not any(c.startswith(("btn-outline-", "btn-primary", "btn-secondary", "btn-danger", "btn-link")) for c in classes + additions):
                additions += ["btn-secondary" if "btn-gray" in classes else "btn-danger" if "btn-danger" in classes else "btn-primary"]
            if self.admin and "btn-gray" in classes:
                additions = ["btn-outline-secondary" if c == "btn-secondary" else c for c in additions]
                classes = ["btn-outline-secondary" if c == "btn-secondary" else c for c in classes]
        if tag == "input" and original.get("type", "text") not in ("hidden", "checkbox", "radio", "submit", "button"):
            additions += ["form-control"]
        if tag == "input" and original.get("type") in ("checkbox", "radio"):
            additions += ["form-check-input"]
        if tag == "textarea": additions += ["form-control", "font-monospace"]
        if tag == "select": additions += ["form-select"]
        if tag == "label": additions += ["form-label"]
        if tag == "table": additions += ["table", "table-vcenter"]
        if "card" in classes and "card-body" not in classes and "table-responsive" not in classes: additions += ["p-4", "mb-3"]
        if "row" in classes:
            classes.remove("row")
            additions += ["d-flex", "flex-wrap", "align-items-center", "gap-2"]
        if tag == "h2" and "modal-title" not in classes: additions += ["h3", "mt-4", "mb-3"]
        if tag == "h1": additions += ["page-title"]
        if tag == "pre": additions += ["bg-dark", "text-light", "rounded", "p-3"]
        if tag == "code": additions += ["font-monospace"]
        if additions:
            value = html.escape(" ".join(dict.fromkeys(classes + additions)), quote=True)
            replacement = 'class="' + value + '"'
            # Match whole attributes, so a title/value containing `class=...`
            # remains untouched (and IDs, fields and event handlers survive).
            attribute_start = re.match(r'<[^\s>]+', raw).end()
            existing = next((m for m in ATTRIBUTE_RE.finditer(raw, attribute_start)
                             if m[1].lower() == 'class'), None)
            raw = (raw[:existing.start()] + replacement + raw[existing.end():] if existing
                   else raw[:-2 if closed else -1] + " " + replacement + ("/>" if closed else ">"))
        self.out.append(raw)

    def handle_starttag(self, tag, attrs): self.start(tag, attrs)
    def handle_startendtag(self, tag, attrs): self.start(tag, attrs, True)
    def handle_endtag(self, tag): self.out.append(f"</{tag}>")
    def handle_data(self, data): self.out.append(data)
    def handle_entityref(self, name): self.out.append(f"&{name};")
    def handle_charref(self, name): self.out.append(f"&#{name};")
    def handle_comment(self, data): self.out.append(f"<!--{data}-->")
    def handle_decl(self, decl): self.out.append(f"<!{decl}>")


def components(body, admin=False):
    parser = Components(admin=admin)
    parser.feed(body)
    parser.close()
    return "".join(parser.out)


def document(title, body, *, extra_head="", extra_script="", auth=False, show_title=True):
    sidebar = ""
    found = re.search(r'<aside\b[^>]*id="csp-admin-sidebar"[^>]*>.*?</aside>', body, re.S)
    if found:
        sidebar = found.group()
        body = body[:found.start()] + body[found.end():]
        # The page header already names this screen; avoid a second identical heading.
        body = re.sub(r'<h([12])\b[^>]*>\s*' + re.escape(html.escape(title)) + r'\s*</h\1>', '', body, count=1)
        body = '<div class="csp-admin-content">' + body + '</div>'
        extra_script += '<script defer src="/static/admin-ui.js"></script>'
    source = (TEMPLATES / ("tabler-auth.html" if auth else "tabler-page.html")).read_text(encoding="utf-8")
    title_html = html.escape(title)
    heading = ('<div class="page-header d-print-none"><div class="container-xl">'
               '<div class="page-pretitle">CSP-J/S 模拟考试</div>'
               '<h1 class="page-title">' + title_html + '</h1></div></div>') if show_title else ""
    return Template(source).substitute(title=title_html, body=components(body, admin=bool(sidebar)), sidebar=sidebar,
                                       heading=heading, extra_head=extra_head, extra_script=extra_script,
                                       assets=ASSETS).encode("utf-8")
