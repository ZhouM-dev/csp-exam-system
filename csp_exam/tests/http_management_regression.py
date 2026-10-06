"""管理按钮和页面合同验收。全部数据放在 TemporaryDirectory。--live 启用实际标程评测。"""
import http.client
from http.server import ThreadingHTTPServer
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from unittest.mock import patch
import zipfile

from csp_exam.core import importer, judgelocal, problems, scoreboard, security, store
from csp_exam.web import admin_pages
from csp_exam.web.server import Handler

SOURCE = '#include <cstdio>\nint main(){freopen("sum.in","r",stdin);freopen("sum.out","w",stdout);int a,b;scanf("%d%d",&a,&b);printf("%d\\n",a+b);return 0;}'

def bundle_files(pid="G01", title="加法", expected=b"5\n"):
    base = f"题目库/{pid}-{title}/"
    return {base+"题目.md": f"# {title}\n计算两数之和。".encode(), base+"标程.cpp": SOURCE.encode(),
            base+"data/01.in": b"2 3\n", base+"data/01.out": expected}

class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.forms=[]; self.buttons=[]; self.scripts=[]; self.current=None
    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag == "a" and attrs.get("href"): self.links.append(attrs["href"])
        if tag == "form": self.forms.append((attrs.get("method","get").lower(),attrs.get("action","")))
        if tag == "button": self.buttons.append(attrs)
        if tag == "script" and not attrs.get("src") and attrs.get("type","text/javascript") in ("text/javascript","module"):
            self.current=[]
    def handle_data(self, text):
        if self.current is not None: self.current.append(text)
    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current)); self.current=None


def main():
    checks=[]; live="--live" in sys.argv
    def check(label, value):
        checks.append({"name":label,"ok":bool(value)})
        if not value: raise AssertionError(label)
    with tempfile.TemporaryDirectory(prefix="csp-management-") as tmp, \
        patch.object(store,"DATA_DIR",str(Path(tmp)/"data")), \
        patch.object(judgelocal,"PROBLEMS_DIR",str(Path(tmp)/"data/problems")), \
        patch.object(admin_pages,"HERE",tmp), \
        patch.object(Handler,"log_message",lambda *args: None):
        Path(store.DATA_DIR).mkdir()
        server=ThreadingHTTPServer(("127.0.0.1",0),Handler); server.daemon_threads=True
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        admin=security.ADMIN_COOKIE+"="+security.make_admin_cookie()
        def request(method,path,form=None,files=None,cookie=admin):
            headers={"Cookie":cookie};body=b""
            if files is not None:
                boundary="CSPManagementBoundary"
                for key,value in (form or {}).items():
                    body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                for field,name,content in files:
                    body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{name}"\r\n\r\n').encode()+content+b"\r\n"
                body+=f"--{boundary}--\r\n".encode();headers["Content-Type"]="multipart/form-data; boundary="+boundary
            elif form is not None:
                body=urllib.parse.urlencode(form).encode();headers["Content-Type"]="application/x-www-form-urlencoded"
            conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=60)
            conn.request(method,path,body,headers);response=conn.getresponse()
            result=(response.status,dict(response.getheaders()),response.read());conn.close();return result
        def post(path,form): return request("POST",path,form)[0] in (302,303)
        try:
            for path in ["/admin/new","/admin/groups","/admin/exam?c=x","/admin/delete?c=x","/admin/release?c=x","/admin/problem","/admin/problem-reupload","/admin/scan","/admin/selftest","/api/problemset/import"]:
                status,_,_=request("POST",path,{},cookie="")
                check("教师操作需要鉴权 "+path,status==403)
            check("未登录考生须知可读",request("GET","/help",cookie="")[0]==200)
            check("错误比赛须知拒绝",request("GET","/help?c=missing",cookie="")[0]==404)
            check("新建比赛",post("/admin/new",{"title":"按钮验收","rule":"CSP","level":"S","duration":"240"}))
            cid=store.list_contests()[0]["id"]
            check("分组创建",post("/admin/groups",{"action":"create","group_name":"验收班","names":"甲\n乙\n甲"}))
            group=store.load_groups()[0];check("分组自动合并重名",len(group["students"])==2)
            check("分组修改",post("/admin/groups",{"action":"update","gid":group["gid"],"group_name":"验收班二","names":"甲\n乙\n丙"}))
            check("分组不存在如实报错","分组不存在" in urllib.parse.unquote(request("POST","/admin/groups",{"action":"update","gid":"missing"})[1].get("Location","")))
            check("导入名单",post("/admin/roster?c="+cid,{"gid":group["gid"],"mode":"append","prefix":"GD"}))
            roster=store.load_roster(cid);check("本场名单独立生成",len(roster)==3)
            check("重排考号",post("/admin/reshuffle?c="+cid,{}));check("考号确实重排",set(roster)!=set(store.load_roster(cid)))
            kh=next(iter(store.load_roster(cid)));student="csp="+security.make_cookie(cid,kh)
            files=bundle_files()
            status,_,body=request("POST","/admin/problem",{"name":"sum","time_ms":"1000","memory_mb":"256"},[("folder",n,v) for n,v in files.items()])
            check("新建题目接通",status in (302,303) and "G01" in judgelocal.known_pids())
            check("标程与限额同入口保存",problems.load_problem_info()["G01"].get("std")==SOURCE and judgelocal.limits_of("G01")== (1000,256))
            got=problems.create_bundle({**bundle_files("G02","甲题",b"6\n"),**bundle_files("G03","乙题",b"7\n")})
            check("多题文件夹独立建题",got["ok"] and judgelocal.cases_of("G02")[0]["out"]==b"6\n" and judgelocal.cases_of("G03")[0]["out"]==b"7\n")
            picked=json.dumps([{"pid":"G01","name":"sum","full":100}])
            check("配置比赛题目",post("/admin/exam?c="+cid,{"problems_json":picked}))
            check("保存本场设置",post("/admin/release?c="+cid,{"action":"settings","duration":"200","prefix":"GD"}))
            check("设置确实生效",store.get_contest(cid)["duration_min"]==200)
            status,_,body=request("POST","/admin/scan",{"render":"1","statement":"# 预览\n内容"})
            check("题面预览",status==200 and "预览" in json.loads(body)["html"])
            check("保存题面",post("/admin/statement?pid=G01",{"pid":"G01","statement":"# 修改题面\n描述"}))
            check("题目详情保存",post("/admin/problem-detail?pid=G01",{"title":"修改加法","name":"sum","time_ms":"1200","memory_mb":"256","statement":"# 修改题面\n描述"}))
            check("题面与元信息均更新",judgelocal.limits_of("G01")== (1200,256) and problems.load_problem_info()["G01"]["title"]=="修改加法")
            source=Path(store.upload_dir(cid,kh))/"sum/sum.cpp";source.parent.mkdir(parents=True,exist_ok=True);source.write_text(SOURCE)
            result={"submitted_at":"2026-10-07","submission_state":"done","problems":{"T1":{"score":100,"status":1,"file":"sum/sum.cpp","testcases":[{"no":1,"status":1,"score":100,"input":"2 3\n","expected":"5\n","output":"5\n"}]}},"total":100}
            store.put_result(cid,kh,result)
            before_roster=store.load_roster(cid)
            check("有提交不能替换名单",post("/admin/roster?c="+cid,{"names":"其他学生","mode":"replace"}) and store.load_roster(cid)==before_roster)
            check("公布成绩",post("/admin/release?c="+cid,{"released":"1"}) and store.load_exam(cid)["released"])
            status,_,body=request("GET","/api/scoreboard?c="+cid,cookie="")
            check("公开榜接通",status==200 and json.loads(body)["rows"][0]["total"]==100)
            for path in ["/admin","/admin/new","/admin/groups","/admin?c="+cid,"/admin/print?c="+cid,"/admin/scores?c="+cid,"/admin/scoreboard?c="+cid,"/admin/student?c="+cid+"&k="+kh,"/admin/problem","/admin/problems","/admin/problem-detail?pid=G01","/admin/statement?pid=G01","/admin/judge","/docs/problemset","/help?c="+cid]:
                status,_,body=request("GET",path);check("教师页面 "+path,status==200)
                page=Page();page.feed(body.decode())
                # Follow every ordinary local navigation link in the page.
                for link in set(page.links):
                    if not link.startswith("/") or link.startswith("//"):continue
                    target=urllib.parse.urlsplit(link)
                    if target.path in ("/hall","/enter","/logout","/help"):continue
                    check("页面链接 "+target.path,request("GET",link)[0] in (200,302,303))
                for i,js in enumerate(page.scripts):
                    script=Path(tmp)/("script-"+str(len(checks))+"-"+str(i)+".js");script.write_text(js)
                    node=os.environ.get("CSP_NODE_PATH") or shutil.which("node") or ""
                    if node:check("内联脚本语法 "+path,subprocess.run([node,"--check"],input=js,text=True,capture_output=True).returncode==0)
            for path in ["/hall?c="+cid,"/score?c="+cid,"/problem?c="+cid+"&p=1","/contests"]:
                check("学生页面 "+path,request("GET",path,cookie=student)[0]==200)
            status,_,body=request("GET",f"/admin/testcase?c={cid}&k={kh}&n=1&t=1")
            check("逐点详情 API",status==200 and json.loads(body).get("ok"))
            check("查看代码",b"freopen" in request("GET",f"/admin/file?c={cid}&k={kh}&f=sum/sum.cpp")[2])
            check("下载代码",request("GET",f"/admin/file?c={cid}&k={kh}&f=sum/sum.cpp&dl=1")[0]==200)
            if live:
                status,_,body=request("POST","/admin/selftest",{"pid":"G01","source":SOURCE,"io_mode":"file"})
                check("自己测试真实编译运行",status==200 and json.loads(body).get("passed")==1)
            status,_,body=request("POST","/admin/selftest",{"pid":"G01","source":SOURCE,"io_mode":"unknown"})
            check("无效评测模式拒绝",status==400)
            # All views use one ranking projection, including stale keys and orphan results.
            results=store.load_results(cid);results[kh]["problems"]["T9"]={"score":900};results["orphan"]={"problems":{"T1":{"score":999}}};store.save_results(cid,results)
            check("统一计分仅含本场名单和题目",store.ranking(cid)[0]["total"]==100 and scoreboard.snapshot(cid,private=True)["rows"][0]["total"]==100)
            problems.remember_problem("G01",time_ms=1500)
            check("改限额撤回并标记需重测",not store.load_exam(cid)["released"] and bool(store.load_results(cid)[kh].get("rejudge_error")))
            check("未完成成绩不排名",store.ranking(cid)[0]["rank"] is None and scoreboard.snapshot(cid,private=True)["rows"][-1]["rank"] is None)
            check("阻止公布过期成绩",post("/admin/release?c="+cid,{"released":"1"}) and not store.load_exam(cid)["released"])
            # Copy report/data validation leaves the active production data untouched.
            zip_path=Path(tmp)/"bundle.zip"
            with zipfile.ZipFile(zip_path,"w") as z:
                for n,v in bundle_files("G04","导入题").items():z.writestr(n,v)
            before=problems.load_codes();report=importer.import_problemset(str(zip_path),dry_run=True,log=lambda *a:None)
            check("dry-run 不分配编号或写题库",report["ok"] and problems.load_codes()==before and "G04" not in judgelocal.known_pids())
            status,_,body=request("POST","/api/problemset/import",{"code":"sum4","time":"1000","memory":"256"},[("file","bundle.zip",zip_path.read_bytes())])
            check("题单 API 接收",status==200 and json.loads(body).get("ok"));job=json.loads(body)["job"]
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                status,_,body=request("GET","/api/problemset/report?job="+job);report=json.loads(body)
                if report.get("state")!="running":break
                time.sleep(.05)
            check("题单 API 确实导入本地库",report.get("state")=="done" and "G04" in judgelocal.known_pids() and problems.load_problem_info()["G04"].get("std")==SOURCE)
            check("软删除题目",json.loads(request("POST","/admin/problem?json=1",{"action":"delete","pid":"G04"})[2])["ok"] and problems.is_deleted("G04"))
            check("恢复题目",json.loads(request("POST","/admin/problem?json=1",{"action":"restore","pid":"G04"})[2])["ok"] and not problems.is_deleted("G04"))
            check("在用题目拒绝彻底删除",not json.loads(request("POST","/admin/problem?json=1",{"action":"purge","pid":"G01"})[2])["ok"])
            check("彻底删除闲置题",json.loads(request("POST","/admin/problem?json=1",{"action":"purge","pid":"G04"})[2])["ok"] and "G04" not in judgelocal.known_pids())
            check("删除路径越界拒绝",not json.loads(request("POST","/admin/problem?json=1",{"action":"purge","pid":".."})[2])["ok"] and source.exists())
            for bad in ["..","../outside","/tmp/x"]:
                try:judgelocal.cases_dir(bad);rejected=False
                except ValueError:rejected=True
                check("题目路径拒绝 "+bad,rejected)
            check("重传题目接通",request("POST","/admin/problem-reupload?pid=G01",{},[("folder",n,v) for n,v in bundle_files().items()])[0] in (302,303))
            check("重传后数据仍成对",judgelocal.cases_of("G01")[0]["out"]==b"5\n")
            check("删除分组",post("/admin/groups",{"action":"delete","gid":group["gid"]}) and not store.get_group(group["gid"]))
            check("收回成绩",post("/admin/release?c="+cid,{"released":"0"}) and not store.load_exam(cid)["released"])
            check("关闭提交",post("/admin/release?c="+cid,{"open":"0"}) and not store.get_contest(cid)["open"])
            check("开放提交",post("/admin/release?c="+cid,{"open":"1"}) and store.get_contest(cid)["open"])
            check("退出登录",request("GET","/logout",cookie=student)[0] in (302,303))
            check("删除临时比赛",post("/admin/delete?c="+cid,{}) and not store.get_contest(cid))
        finally:
            server.shutdown();server.server_close();thread.join()
    print(json.dumps({"passed":len(checks),"live_judge":live,"checks":checks},ensure_ascii=False))

if __name__=="__main__":main()
