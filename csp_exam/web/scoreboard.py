"""紧凑 CSP 排行榜；公开成绩投影与教师提交入口分别鉴权。"""

import hashlib
import html
import json
import urllib.parse
from .ui import page


# Rank-table behavior styles live in static/scoreboard.css.


def icon(name: str) -> str:
    paths = {
        "chevron": '<path d="m5 3 4 5-4 5"/>',
        "search": '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>',
        "refresh": '<path d="M13 5a6 6 0 1 0 1 6M13 1v4H9"/>',
        "arrow": '<path d="M4 12 12 4M5 4h7v7"/>',
        "lock": '<rect x="3" y="7" width="10" height="7" rx="2"/><path d="M5 7V5a3 3 0 0 1 6 0v2"/>',
    }
    return ('<svg class="icon icon-sm" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + paths[name] + '</svg>')


def _query(cid: str, key: str = "", **extra) -> str:
    return urllib.parse.urlencode({"c": cid, **({"key": key} if key else {}), **extra})


def _json_bytes(state: dict) -> bytes:
    return json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _etag(state: dict) -> str:
    return '"' + hashlib.sha256(_json_bytes(state)).hexdigest() + '"'


def _safe_next(value: str, cid: str = "") -> str:
    fallback = "/scoreboard?" + _query(cid) if cid else "/admin"
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        return fallback
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return fallback
    path = parsed.path
    if (parsed.scheme or parsed.netloc or value.startswith("//") or "%" in path
            or not (path in ("/scoreboard", "/admin") or path.startswith("/admin/"))
            or path == "/admin/login"):
        return fallback
    # 旧链接可能携带 key；登录后使用 HttpOnly cookie，跳转不再携带密钥。
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k not in ("key", "password")]
    return urllib.parse.urlunsplit(("", "", path, urllib.parse.urlencode(query), parsed.fragment))


def render_login(next_url: str, error: str = "") -> bytes:
    next_url = _safe_next(next_url)
    body = ('<div class="card"><form method="post" action="/admin/login">'
            '<label for="admin-key">管理密钥</label><input id="admin-key" name="key" type="password" '
            'autocomplete="current-password" required autofocus>'
            '<input name="next" type="hidden" value="' + html.escape(next_url, quote=True) + '">'
            + ('<p class="alert alert-danger mt-3" role="alert">' + html.escape(error) + '</p>' if error else '') +
            '<button class="btn btn-primary w-100 mt-3" type="submit">登录</button></form>'
            '<a class="text-secondary text-center d-block mt-3" href="/">返回学生入口</a></div>')
    return page("教师登录", body, auth=True)


def login_route(handler, q, *, post=False):
    from ..core.security import ADMIN_COOKIE, make_admin_cookie
    form = handler._form() if post else q
    target = _safe_next(form.get("next", ""), q.get("c", ""))
    if handler._check_admin(form.get("key", "")):
        cookie = (f"{ADMIN_COOKIE}={make_admin_cookie()}; Path=/; Max-Age=2592000; "
                  "HttpOnly; SameSite=Lax")
        return handler._redirect(target, cookie)
    return handler._send(render_login(target, "管理密钥不正确" if post else ""), 403 if post else 200)


def route(handler, q, path):
    from ..core import scoreboard
    # 条件请求必须在鉴权与公开开关检查之后，不能复用老师的响应。
    teacher = handler._check_admin(q.get("key", ""))
    # 学生页面始终使用公开投影；同一浏览器的管理员 cookie 不能自动升级它。
    private = (path == "/admin/scoreboard"
               or (path == "/api/scoreboard" and q.get("private") == "1"))
    if private and not teacher:
        if path != "/api/scoreboard":
            return handler._admin_login()
        return handler._json({"ok": False, "error": "需要管理员登录"}, 403)
    state = scoreboard.snapshot(q.get("c", ""), private=private)
    if path == "/api/scoreboard":
        if not state.get("ok"):
            return handler._json(state, 404)
        if not private and not state.get("released"):
            return handler._json({**state, "ok": False,
                                  "error": "比赛结束且老师公布成绩后才能查看排行榜。"}, 403)
        tag = _etag(state)
        unchanged = handler.headers.get("If-None-Match") == tag
        handler.send_response(304 if unchanged else 200)
        handler.send_header("ETag", tag)
        handler.send_header("Cache-Control", "no-store")
        handler._cors_headers()
        if unchanged:
            handler.end_headers()
            return
        data = _json_bytes(state)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    return handler._send(render(q.get("c", ""), state, private=private,
                                key=q.get("key", "") if private else "", teacher=teacher,
                                student=bool(handler._student(q.get("c", "")))),
                         404 if not state.get("ok") else
                         403 if not private and not state.get("released") else 200)


def _rows(cid: str, state: dict, private: bool, key: str) -> str:
    parts = []
    for row in state.get("rows", []):
        name = html.escape(str(row["name"]))
        href = "/admin/student?" + _query(cid, key, k=row["kaohao"]) if private else ""
        if href:
            name = (f'<a class="student-name" href="{html.escape(href, quote=True)}" '
                    f'title="查看提交 · {html.escape(row["kaohao"], quote=True)}">{name}{icon("arrow")}</a>')
        else:
            name = f'<span class="student-name">{name}</span>'
        rank = row["rank"] if row["rank"] is not None else "—"
        leader = " leader" if isinstance(rank, int) and rank <= 3 else ""
        label = {"queued": "排队中", "judging": "评测中", "error": "待重测", "blocked": "待补资料"}.get(row.get("state"), "待评测")
        total = label if row["pending"] else str(row["total"]) if row["submitted"] else "—"
        parts.append(f'<tr><td class="rank-col rank{leader}">{rank}</td>'
                     f'<td class="name-col">{name}</td>'
                     f'<td class="total-col total{" waiting" if row["pending"] or not row["submitted"] else ""}">{total}</td>')
        for score, problem in zip(row["scores"], state["problems"]):
            blank = row["pending"] or not row["submitted"]
            cls = "blank" if blank else "full" if score >= problem["full"] else "part" if score > 0 else "zero"
            text = "—" if blank else str(score)
            hint = f'{problem["name"]} · {score} / {problem["full"]}' if not blank else "待评测" if row["pending"] else "未提交"
            if href and row["submitted"]:
                cell = (f'<a class="score {cls}" href="{html.escape(href, quote=True)}#T{problem["no"]}" '
                        f'title="查看提交 · {html.escape(hint, quote=True)}" '
                        f'aria-label="{html.escape(str(row["name"]) + " · " + problem["name"] + " · 查看提交", quote=True)}">{text}</a>')
            else:
                cell = f'<span class="score {cls}" title="{html.escape(hint, quote=True)}">{text}</span>'
            parts.append(f'<td class="problem-col">{cell}</td>')
        parts.append('</tr>')
    return "".join(parts) or f'<tr><td class="empty" colspan="{3 + len(state.get("problems", []))}">暂无学生</td></tr>'


def render(cid: str, state: dict, *, private: bool = False, key: str = "", teacher: bool = False,
           student: bool = False) -> bytes:
    title = str(state.get("title") or "排行榜")
    query = _query(cid, key)
    back = "/admin?" + query if private else "/hall?" + _query(cid)
    if private:
        teacher_link = f'<a class="teacher" href="/scoreboard?{html.escape(_query(cid, view="public"))}">公开视图</a>'
    elif teacher:
        teacher_link = f'<a class="teacher" href="/admin/scoreboard?{html.escape(query)}">教师视图</a>'
    else:
        login_query = urllib.parse.urlencode({"c": cid, "next": "/scoreboard?" + _query(cid)})
        teacher_link = f'<a class="teacher" href="/admin/login?{html.escape(login_query)}">教师登录</a>'
    if student and not private:
        teacher_link = f'<a class="btn btn-sm btn-outline-secondary" href="/logout?{html.escape(_query(cid))}">退出登录</a>'
    badge = '<span id="published" class="badge bg-yellow-lt">未公布</span>' if private and not state.get("published") else '<span id="published" class="badge bg-yellow-lt" hidden>未公布</span>'
    body = (f'<section class="board"><div class="topline d-flex justify-content-between align-items-center mb-3"><nav class="crumb d-flex align-items-center gap-2 text-secondary" aria-label="导航">'
            f'<a href="{html.escape(back)}">{"比赛管理" if private else "比赛"}</a>{icon("chevron")}<span>排行榜</span></nav>{teacher_link}</div>'
            f'<header class="heading"><h1 id="contest-title">{html.escape(title)}</h1>{badge}</header>')
    if not state.get("ok"):
        body += '<section class="panel locked">' + html.escape(state.get("error", "读取失败")) + '</section>'
    else:
        locked = not state.get("released")
        heads = ''.join(f'<th scope="col" class="problem-col" title="满分 {p["full"]}">{html.escape(p["name"])}</th>' for p in state.get("problems", []))
        body += ('<section class="panel" aria-label="排行榜"><div id="board-content"' + (' hidden' if locked else '') + '>'
                 '<div class="toolbar"><span id="count" class="count"><strong>' + str(len(state.get("rows", []))) + '</strong> 位学生</span>'
                 '<div class="tools"><span id="status" class="board-status" role="status"></span><label class="search">'
                 '<span class="sr-only">搜索学生</span>' + icon("search") +
                 '<input id="search" type="search" placeholder="搜索学生" autocomplete="off"></label>'
                 '<button id="refresh" class="refresh" type="button" aria-label="刷新排行榜" title="刷新">' + icon("refresh") + '</button></div></div>'
                 '<div class="scroll" tabindex="0" role="region" aria-label="成绩表"><table aria-label="学生分数">'
                 '<thead id="board-head"><tr><th scope="col" class="rank-col">名次</th><th scope="col" class="name-col">学生</th>'
                 '<th scope="col" class="total-col">总分</th>' + heads + '</tr></thead>'
                 '<tbody id="board-rows">' + ("" if locked else _rows(cid, state, private, key)) + '</tbody></table></div></div>'
                 '<div id="locked" class="locked"' + ('' if locked else ' hidden') + '>' + icon("lock") + '比赛结束且老师公布成绩后才能查看排行榜。</div></section>')
        config = {"cid": cid, "key": key if private else "", "private": private,
                  "endpoint": "/api/scoreboard?" + _query(cid, key if private else "", **({"private": "1"} if private else {})),
                  "etag": _etag(state)}
        body += ('<script type="application/json" id="board-data">' + _json_bytes(state).decode().replace("<", "\\u003c") + '</script>'
                 '<script type="application/json" id="board-config">' + json.dumps(config, ensure_ascii=False).replace("<", "\\u003c") + '</script>'
                 '<script>' + SCRIPT + '</script>')
    return page(title + ' · 排行榜', body + '</section>', show_title=False,
                extra_head='<link rel="stylesheet" href="/static/scoreboard.css">')


SCRIPT = r"""
(()=>{
const cfg=JSON.parse(document.getElementById('board-config').textContent);
let state=JSON.parse(document.getElementById('board-data').textContent),etag=cfg.etag,busy=false;
const body=document.getElementById('board-rows'),search=document.getElementById('search'),status=document.getElementById('status'),refresh=document.getElementById('refresh');
const arrow='<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M4 12 12 4M5 4h7v7"/></svg>';
function node(tag,text,cls){const n=document.createElement(tag);n.textContent=text;if(cls)n.className=cls;return n}
function studentHref(r){const q=new URLSearchParams({c:cfg.cid,k:r.kaohao});if(cfg.key)q.set('key',cfg.key);return '/admin/student?'+q}
function drawRows(){
 const filter=search.value.trim().toLocaleLowerCase();
 const rows=(state.rows||[]).filter(r=>(r.name+' '+(cfg.private?r.kaohao:'')).toLocaleLowerCase().includes(filter));
 const fragment=document.createDocumentFragment();
 rows.forEach(r=>{
  const tr=document.createElement('tr'),rank=r.rank===null?'—':r.rank;
  tr.append(node('td',rank,'rank-col rank'+(r.rank!==null&&r.rank<=3?' leader':'')));
  const name=node('td','','name-col'),who=node(cfg.private?'a':'span',r.name,'student-name');
  if(cfg.private){who.href=studentHref(r);who.title='查看提交 · '+r.kaohao;who.insertAdjacentHTML('beforeend',arrow)}
  name.append(who);tr.append(name);
  const label=({queued:'排队中',judging:'评测中',error:'待重测',stale:'待重测',blocked:'待补资料'})[r.state]||'待评测';
  tr.append(node('td',r.pending?label:r.submitted?r.total:'—','total-col total'+(r.pending||!r.submitted?' waiting':'')));
  r.scores.forEach((v,i)=>{
   const p=state.problems[i],blank=r.pending||!r.submitted,cls=blank?'blank':v>=p.full?'full':v>0?'part':'zero';
   const td=node('td','','problem-col'),score=node(cfg.private&&r.submitted?'a':'span',blank?'—':v,'score '+cls);
   score.title=blank?(r.pending?'待评测':'未提交'):p.name+' · '+v+' / '+p.full;
   if(cfg.private&&r.submitted){score.href=studentHref(r)+'#T'+p.no;score.title='查看提交 · '+score.title;score.setAttribute('aria-label',r.name+' · '+p.name+' · 查看提交')}
   td.append(score);tr.append(td);
  });fragment.append(tr);
 });
 if(!rows.length){const tr=document.createElement('tr'),td=node('td',filter?'未找到学生':'暂无学生','empty');td.colSpan=3+(state.problems||[]).length;tr.append(td);fragment.append(tr)}
 body.replaceChildren(fragment);
 document.getElementById('count').replaceChildren(node('strong',rows.length),' 位学生'+(filter?' / '+state.rows.length:''));
}
function draw(){
 document.getElementById('contest-title').textContent=state.title;
 document.title=state.title+' · 排行榜';
 document.getElementById('published').hidden=!cfg.private||state.published;
 document.getElementById('board-content').hidden=!state.released;
 document.getElementById('locked').hidden=!!state.released;
 if(!state.released){body.replaceChildren();return}
 const tr=document.createElement('tr');
 ['名次','学生','总分'].forEach((v,i)=>{const th=node('th',v,['rank-col','name-col','total-col'][i]);th.scope='col';tr.append(th)});
 state.problems.forEach(p=>{const th=node('th',p.name,'problem-col');th.scope='col';th.title='满分 '+p.full;tr.append(th)});
 document.getElementById('board-head').replaceChildren(tr);drawRows();
}
async function update(manual=false){
 if(busy||(!manual&&document.hidden))return;
 busy=true;refresh.disabled=true;const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),10000);
 try{
  const response=await fetch(cfg.endpoint,{cache:'no-store',credentials:'same-origin',headers:{'If-None-Match':etag},signal:abort.signal});
  if(response.status===304){status.textContent='';return}
  if(response.status===403){state={ok:true,released:false,title:state.title};etag='';draw();document.getElementById('locked').textContent=cfg.private?'请重新登录':'比赛结束且老师公布成绩后才能查看排行榜。';return}
  if(!response.ok)throw new Error('update');
  const next=await response.json();if(!next.ok)throw new Error('state');
  etag=response.headers.get('ETag')||'';
  state=next;draw();status.textContent='';
 }catch(e){status.textContent='连接中断';}
 finally{clearTimeout(timeout);busy=false;refresh.disabled=false}
}
search.addEventListener('input',drawRows);refresh.addEventListener('click',()=>update(true));
document.addEventListener('visibilitychange',()=>{if(!document.hidden)update()});
setInterval(update,15000);
})();
"""
