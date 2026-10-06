"""本项目的评测结果状态码；数值沿用已存储的历史成绩。"""

STATUS = {
    0: "等待评测", 1: "答案正确", 2: "答案错误", 3: "运行超时", 4: "内存超限",
    5: "输出超限", 6: "运行错误", 7: "编译错误", 8: "系统错误", 9: "已取消",
    10: "未知错误", 11: "被 hack", 20: "正在评测", 21: "正在编译", 22: "已取数据",
    30: "已忽略", 31: "格式错误", 32: "Hack 成功", 33: "Hack 失败",
}
STATUS_AC = 1
#: 这些状态表示还没评完，要继续等（0=排队 20=评测中 21=编译中 22=取数据）
STATUS_PENDING = (0, 20, 21, 22)

def status_text(code) -> str:
    try:
        return STATUS.get(int(code), f"未知状态({code})")
    except (TypeError, ValueError):
        return str(code)
