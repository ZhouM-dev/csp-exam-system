"""紧凑 CSP 排行榜；公开成绩投影与教师提交入口分别鉴权。"""

import hashlib
import html
import json
import urllib.parse


CSS = """
*{box-sizing:border-box}body{margin:0;background:#f5f7fa;color:#233044;
 font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
a{color:inherit;text-decoration:none}button,input{font:inherit}button,a,input{-webkit-tap-highlight-color:transparent}
:focus-visible{outline:3px solid #92baff;outline-offset:3px}[hidden]{display:none!important}
.board{max-width:1320px;margin:0 auto;padding:42px 32px 60px}
.topline{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px}
.crumb{display:flex;align-items:center;gap:12px;color:#7d899b;font-size:13px}.crumb a:hover{color:#245cd3}
.crumb svg{width:12px;height:12px}.teacher{font-size:13px;color:#53657f}.teacher:hover{color:#245cd3}
.heading{display:flex;align-items:center;gap:12px;margin-bottom:24px}
h1{font-size:26px;font-weight:650;letter-spacing:-.5px;line-height:1.4;margin:0;overflow-wrap:anywhere}
.badge{flex-shrink:0;border:1px solid #e5d9b7;border-radius:6px;padding:2px 8px;background:#fff8e8;color:#846321;font-size:12px}
.panel{background:white;border:1px solid #e4e9f0;border-radius:12px;overflow:hidden;box-shadow:0 3px 14px #20304c05}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:18px 22px;border-bottom:1px solid #e9edf3}
.count{color:#8792a2;font-size:13px;white-space:nowrap}.count strong{color:#3b4b63;font-weight:600}
.tools{display:flex;align-items:center;gap:10px}.search{position:relative;display:flex;align-items:center}
.search svg{position:absolute;left:12px;width:16px;height:16px;color:#91a0b4;pointer-events:none}
.search input{width:220px;height:36px;border:1px solid #e1e7ef;border-radius:7px;padding:0 12px 0 36px;background:#fafbfd;color:#233044}
.search input::placeholder{color:#98a2b1}.search input:focus{border-color:#96b5e8;background:white}
.refresh{display:grid;place-items:center;width:36px;height:36px;border:1px solid #e1e7ef;border-radius:7px;background:white;color:#6d7d94;cursor:pointer}
.refresh:hover{background:#f1f5fc;color:#245cd3}.refresh svg{width:17px;height:17px}.refresh:disabled{opacity:.5;cursor:wait}
.status{font-size:12px;color:#aa563c}.scroll{overflow:auto;max-height:calc(100vh - 255px);min-height:100px}
table{border-collapse:separate;border-spacing:0;width:100%;font-variant-numeric:tabular-nums;text-align:center}
thead th{position:sticky;top:0;z-index:2;background:#fafbfd;font-weight:500;font-size:12px;color:#8490a2;
 height:48px;padding:12px 18px;border-bottom:1px solid #e8edf3;white-space:nowrap}
tbody td{height:64px;padding:12px 18px;border-bottom:1px solid #eef1f6;white-space:nowrap;background:white}
tbody tr:last-child td{border-bottom:0}tbody tr:hover td{background:#f8faff}
.rank-col{width:84px;min-width:70px}.name-col{text-align:left;min-width:155px;max-width:240px}
.total-col{width:110px;min-width:90px}.problem-col{min-width:100px}
.rank{color:#94a0b1;font-size:15px}.rank.leader{color:#365f9d;font-weight:650}
.student-name{font-weight:550;white-space:normal;overflow-wrap:anywhere;display:inline-flex;align-items:center;gap:7px}
a.student-name:hover{color:#245cd3}.student-name svg{color:#a8b5c6;width:13px;height:13px;flex-shrink:0}
.total{font-size:19px;letter-spacing:-.3px;font-weight:700;color:#253c60}.total.waiting{font-size:12px;font-weight:500;color:#97a2b1}
.score{display:inline-flex;align-items:center;justify-content:center;min-width:48px;height:30px;padding:0 10px;border-radius:6px;font-weight:550}
.score.full{background:#eaf6ef;color:#2d845b}.score.part{background:#fff5e2;color:#a87920}.score.zero{color:#8895a8;background:#f3f5f8}
.score.blank{color:#b0bac8;font-weight:400}a.score:hover{box-shadow:inset 0 0 0 1px currentColor}
.empty{text-align:center;padding:54px 20px!important;color:#8f9bad;font-size:14px}
.locked{padding:60px 20px;text-align:center;color:#8895a7}.locked svg{display:block;margin:0 auto 14px;width:28px;height:28px;color:#a5b1c1}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.login{max-width:420px;margin:12vh auto;padding:24px}.login h1{font-size:23px;margin-bottom:24px}
.login label{display:block;color:#53657f;margin-bottom:8px}.login input{width:100%;height:42px;border:1px solid #dce3ed;border-radius:7px;padding:0 12px}
.login button{width:100%;margin-top:18px;height:42px;background:#245cd3;color:white;border:0;border-radius:7px;cursor:pointer}
.login .error{color:#aa563c;font-size:13px}.login .back{display:block;text-align:center;margin-top:18px;color:#7d899b;font-size:13px}
@media(max-width:640px){.board{padding:24px 14px 36px}.topline{margin-bottom:18px}.heading{margin-bottom:20px;align-items:flex-start}
 h1{font-size:21px}.toolbar{padding:14px;gap:8px}.tools{gap:8px}.search input{width:166px}.scroll{max-height:calc(100vh - 225px)}
 thead th,tbody td{padding:12px}.name-col{min-width:128px}.rank-col{width:54px;min-width:54px}.total-col{min-width:74px}.problem-col{min-width:96px}
 .rank-col{position:sticky;left:0;z-index:1}.name-col{position:sticky;left:54px;z-index:1;border-right:1px solid #edf1f6}
 thead .rank-col,thead .name-col{z-index:3}.count{font-size:12px}.status{position:absolute;top:-19px;right:0}.toolbar{position:relative}.total{font-size:17px}}
@media(prefers-reduced-motion:no-preference){a,button{transition:color .12s,background .12s}}
"""


def icon(name: str) -> str:
    paths = {
        "chevron": '<path d="m5 3 4 5-4 5"/>',
        "search": '<circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/>',
        "refresh": '<path d="M13 5a6 6 0 1 0 1 6M13 1v4H9"/>',
        "arrow": '<path d="M4 12 12 4M5 4h7v7"/>',
        "lock": '<rect x="3" y="7" width="10" height="7" rx="2"/><path d="M5 7V5a3 3 0 0 1 6 0v2"/>',
    }
    return ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" '
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
    body = ('<main class="login panel"><h1>教师登录</h1><form method="post" action="/admin/login">'
            '<label for="admin-key">管理密钥</label><input id="admin-key" name="key" type="password" '
            'autocomplete="current-password" required autofocus>'
            '<input name="next" type="hidden" value="' + html.escape(next_url, quote=True) + '">'
            + ('<p class="error" role="alert">' + html.escape(error) + '</p>' if error else '') +
            '<button type="submit">登录</button></form>'
            '<a class="back" href="/">返回首页</a></main>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>教师登录</title><style>' + CSS + '</style></head><body>' + body + '</body></html>').encode("utf-8")


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
    private = (path == "/admin/scoreboard" or q.get("private") == "1"
               or (path == "/scoreboard" and teacher and q.get("view") != "public"))
    if private and not teacher:
        if path != "/api/scoreboard":
            return handler._admin_login()
        return handler._json({"ok": False, "error": "需要管理员登录"}, 403)
    state = scoreboard.snapshot(q.get("c", ""), private=private)
    if path == "/api/scoreboard":
        if not state.get("ok"):
            return handler._json(state, 404)
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
                                key=q.get("key", ""), teacher=teacher),
                         200 if state.get("ok") else 404)


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


def render(cid: str, state: dict, *, private: bool = False, key: str = "", teacher: bool = False) -> bytes:
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
    badge = '<span id="published" class="badge">未公布</span>' if private and not state.get("published") else '<span id="published" class="badge" hidden>未公布</span>'
    body = (f'<main class="board"><div class="topline"><nav class="crumb" aria-label="导航">'
            f'<a href="{html.escape(back)}">{"比赛管理" if private else "比赛"}</a>{icon("chevron")}<span>排行榜</span></nav>{teacher_link}</div>'
            f'<header class="heading"><h1 id="contest-title">{html.escape(title)}</h1>{badge}</header>')
    if not state.get("ok"):
        body += '<section class="panel locked">' + html.escape(state.get("error", "读取失败")) + '</section>'
    else:
        locked = not state.get("released")
        heads = ''.join(f'<th scope="col" class="problem-col" title="满分 {p["full"]}">{html.escape(p["name"])}</th>' for p in state.get("problems", []))
        body += ('<section class="panel" aria-label="排行榜"><div id="board-content"' + (' hidden' if locked else '') + '>'
                 '<div class="toolbar"><span id="count" class="count"><strong>' + str(len(state.get("rows", []))) + '</strong> 位学生</span>'
                 '<div class="tools"><span id="status" class="status" role="status"></span><label class="search">'
                 '<span class="sr-only">搜索学生</span>' + icon("search") +
                 '<input id="search" type="search" placeholder="搜索学生" autocomplete="off"></label>'
                 '<button id="refresh" class="refresh" type="button" aria-label="刷新排行榜" title="刷新">' + icon("refresh") + '</button></div></div>'
                 '<div class="scroll" tabindex="0" role="region" aria-label="成绩表"><table aria-label="学生分数">'
                 '<thead id="board-head"><tr><th scope="col" class="rank-col">名次</th><th scope="col" class="name-col">学生</th>'
                 '<th scope="col" class="total-col">总分</th>' + heads + '</tr></thead>'
                 '<tbody id="board-rows">' + ("" if locked else _rows(cid, state, private, key)) + '</tbody></table></div></div>'
                 '<div id="locked" class="locked"' + ('' if locked else ' hidden') + '>' + icon("lock") + '成绩尚未公布</div></section>')
        config = {"cid": cid, "key": key if private else "", "private": private,
                  "endpoint": "/api/scoreboard?" + _query(cid, key if private else "", **({"private": "1"} if private else {})),
                  "etag": _etag(state)}
        body += ('<script type="application/json" id="board-data">' + _json_bytes(state).decode().replace("<", "\\u003c") + '</script>'
                 '<script type="application/json" id="board-config">' + json.dumps(config, ensure_ascii=False).replace("<", "\\u003c") + '</script>'
                 '<script>' + SCRIPT + '</script>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + html.escape(title) + ' · 排行榜</title><style>' + CSS + '</style></head><body>' + body + '</main></body></html>').encode("utf-8")


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
  if(response.status===403){state={ok:true,released:false,title:state.title};draw();document.getElementById('locked').textContent='请重新登录';return}
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
