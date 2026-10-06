"""生产只读冒烟：真实页面、角色隔离与统一成绩投影，不创建或修改比赛。"""
import http.client
import json
import os
from pathlib import Path
import sys
import urllib.parse
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from csp_exam.core import security,store


def main():
    base=urllib.parse.urlsplit(os.environ.get("CSP_EXAM_URL","http://127.0.0.1:8080"));passed=0
    admin=security.ADMIN_COOKIE+"="+security.make_admin_cookie()
    def get(path,cookie=""):
        cls=http.client.HTTPSConnection if base.scheme=="https" else http.client.HTTPConnection
        conn=cls(base.hostname,base.port,timeout=30)
        conn.request("GET",path,headers={"Cookie":cookie});res=conn.getresponse();data=res.read();status=res.status;conn.close();return status,data
    def check(label,condition):
        nonlocal passed
        if not condition:raise AssertionError(label)
        passed+=1
    for path in ["/health","/","/help","/docs/problemset"]:
        check("公共入口 "+path,get(path)[0]==200)
    for path in ["/admin","/admin/new","/admin/groups","/admin/problems","/admin/problem","/admin/judge"]:
        check("教师页面 "+path,get(path,admin)[0]==200)
    check("评测设置鉴权",get("/admin/judge")[0]==403)
    for contest in store.list_contests():
        cid=contest["id"];exam=store.load_exam(cid);roster=store.load_roster(cid)
        for route in ["/admin?c=","/admin/scores?c=","/admin/scoreboard?c=","/admin/print?c="]:
            check("比赛页面 "+route+cid,get(route+cid,admin)[0] in (200,302))
        status,body=get("/api/scoreboard?private=1&c="+cid,admin);private=json.loads(body)
        check("教师榜可读 "+cid,status==200 and private.get("private") and len(private.get("rows",[]))==len(roster))
        expected=store.ranking(cid,include_all=True)
        check("成绩投影一致 "+cid,[(r["kaohao"],r["total"],r["rank"]) for r in private["rows"]]==[(r["kaohao"],r["total"],r["rank"]) for r in expected])
        status,body=get("/api/scoreboard?c="+cid);public=json.loads(body)
        check("公开开关一致 "+cid,status==200 and bool(public.get("released"))==bool(exam.get("released")))
        check("公开榜不泄漏考号 "+cid,all("kaohao" not in r and "state" not in r for r in public.get("rows",[])))
        if not exam.get("released"):check("未公布不泄漏分数 "+cid,"rows" not in public and "problems" not in public)
        check("私榜不能匿名读取 "+cid,get("/api/scoreboard?private=1&c="+cid)[0]==403)
        if roster:
            kh=next(iter(roster));student="csp="+security.make_cookie(cid,kh)
            check("学生本场页面 "+cid,get("/hall?c="+cid,student)[0]==200)
            check("学生成绩页 "+cid,get("/score?c="+cid,student)[0]==200)
            check("提交详情 "+cid,get("/admin/student?c="+cid+"&k="+kh,admin)[0]==200)
            check("学生不能看管理榜 "+cid,get("/admin/scoreboard?c="+cid,student)[0]==403)
    print(json.dumps({"passed":passed,"contests":len(store.list_contests()),"read_only":True},ensure_ascii=False))

if __name__=="__main__":main()
