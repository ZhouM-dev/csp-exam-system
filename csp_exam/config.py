"""集中配置：路径、端口、各类上限、语言表、密码文件位置。

以前这些散在各个模块里（`exam_server` 顶部一堆常量、`hydro_client` 里的 compose 路径、
`import_problemset` 的导入目录……）。现在统一放这里，改一处就能生效，也方便部署到别的机器。

环境变量可覆盖的项：CSP_EXAM_PORT、CSP_HYDRO_DIR、CSP_ADMIN_PW_FILE。
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
STATEMENTS_DIR = os.path.join(DATA_DIR, "statements")    # 题面缓存

#: 题单导入接口文档（/docs/problemset 会把它渲染出来）。以包内那份为准，
#: 兼容早期放在部署根目录的副本。
_DOC_NAMES = "题目单导入接口.md"
_DOC_CANDIDATES = [os.path.join(PKG_DIR, _DOC_NAMES), os.path.join(ROOT_DIR, _DOC_NAMES)]
PS_DOC = next((p for p in _DOC_CANDIDATES if os.path.isfile(p)), _DOC_CANDIDATES[0])

#: 服务端口
PORT = int(os.environ.get("CSP_EXAM_PORT", "8080"))
#: 学生会话 cookie 名（管理端 cookie 名在 core/security.py）
STUDENT_COOKIE = "csp"

#: 上传上限
MAX_UPLOAD = 32 * 1024 * 1024         # 学生提交：32MB
MAX_PS_UPLOAD = 512 * 1024 * 1024     # 题单 zip：512MB

#: 判分并发上限（每个判分任务会去评测站提交并等结果，别开太多）
JUDGE_SLOTS = threading.Semaphore(3)

#: 题单导入任务的状态（jid -> 进度/报告），由 web/admin_pages 使用
PS_JOBS: dict = {}
PS_JOBS_LOCK = threading.Lock()

#: 学生可以选的语言（键是评测站的语言标识）
LANG_CHOICES = [
    ("cc.cc14o2", "C++14 (O2，与 CSP 一致)"),
    ("cc.cc17o2", "C++17 (O2)"),
    ("cc.cc11o2", "C++11 (O2)"),
    ("c.c11o2", "C (C11)"),
]

# —— 评测站（HydroOJ）——
HYDRO_DIR = os.environ.get("CSP_HYDRO_DIR", "/root/hydro")
HYDRO_BASE = os.environ.get("CSP_HYDRO_BASE", "http://127.0.0.1")
HYDRO_COMPOSE_FILE = os.path.join(HYDRO_DIR, "docker-compose.yml")
#: 评测站管理员密码文件（建题时的"标程自测"要用它提交）
HYDRO_ADMIN_PW_FILE = os.environ.get("CSP_ADMIN_PW_FILE", "/root/hydro-admin-password.txt")

#: 题面缓存有效期（秒）：超过就重新去评测站取
STATEMENT_TTL = 6 * 3600

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
