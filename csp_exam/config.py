"""集中配置：路径、端口、各类上限、语言表、密码文件位置。

以前这些散在各个模块里（`exam_server` 顶部一堆常量、`hydro_client` 里的 compose 路径、
`import_problemset` 的导入目录……）。现在统一放这里，改一处就能生效，也方便部署到别的机器。

环境变量可覆盖的项：CSP_EXAM_PORT。
"""

from __future__ import annotations

import os
import threading

#: 包所在目录（.../csp_exam），以及它的上一级（部署根，data/ 与 logs/ 都在这里）
PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PKG_DIR)
#: 兼容旧写法：以前模块里到处用 HERE 表示"部署根目录"
HERE = ROOT_DIR

DATA_DIR = os.path.join(ROOT_DIR, "data")
LOG_DIR = os.path.join(ROOT_DIR, "logs")
SAMPLES_DIR = os.path.join(DATA_DIR, "samples")          # 大样例（学生可下载）
STATEMENTS_DIR = os.path.join(DATA_DIR, "statements")    # 题面正本

#: 题单导入接口文档（/docs/problemset 会把它渲染出来）。以包内那份为准，
#: 兼容早期放在部署根目录的副本。
_DOC_NAMES = "题目单导入接口.md"
_DOC_CANDIDATES = [os.path.join(PKG_DIR, _DOC_NAMES), os.path.join(ROOT_DIR, _DOC_NAMES)]
PS_DOC = next((p for p in _DOC_CANDIDATES if os.path.isfile(p)), _DOC_CANDIDATES[0])

#: 考生须知：考区官方通告的原文（`/help` 会把这份 Markdown 渲染成整页）。
#: 每年考区通告不一样，换考区/换年份就换这一份文件，代码不用动。
#: 文件缺失时 `/help` 只给「以考点发的纸质通告为准」加原文链接，不会 500。
NOTICE_DOC = os.path.join(PKG_DIR, "notice_guangdong.md")

#: 服务端口
PORT = int(os.environ.get("CSP_EXAM_PORT", "8080"))
#: 学生会话 cookie 名（管理端 cookie 名在 core/security.py）
STUDENT_COOKIE = "csp"

#: 上传上限
MAX_UPLOAD = 32 * 1024 * 1024         # 学生提交：32MB
MAX_PS_UPLOAD = 512 * 1024 * 1024     # 题单 zip：512MB
MAX_UPLOAD_FILES = 1024
MAX_SOURCE_BYTES = 100 * 1024        # CSP 官方源文件上限
MAX_STUDENT_ARCHIVE = 256 * 1024 * 1024
MAX_JUDGE_QUEUE = 64

#: 判分并发上限（每个判分任务会去评测站提交并等结果，别开太多）
JUDGE_SLOTS = threading.Semaphore(1) # 2 vCPU / 3.4 GiB 评测机：避免多个 2 GiB 任务同时运行

#: 题单导入任务的状态（jid -> 进度/报告），由 web/admin_pages 使用
PS_JOBS: dict = {}
PS_JOBS_LOCK = threading.Lock()

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
