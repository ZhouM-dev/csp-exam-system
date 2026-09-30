"""CSP 模拟赛考试服务。

分层：
    core/   业务与数据（数据存储、评测站对接、判分、建题、题单导入）
    web/    HTTP 与页面（路由、学生端页面、管理端页面、共用 HTML）
    tests/  自测（不依赖网络的纯逻辑测试）
    tools/  出题与运维脚本

启动：python3 -m csp_exam         （systemd 用 run.py，效果相同）
数据：data/ 目录（与包同级），日志：logs/exam.log
"""

__version__ = "2.0.0"
