"""Server-authoritative contest windows. Naive dates always mean Beijing time."""
from datetime import datetime, timedelta, timezone
import math
import time

BEIJING = timezone(timedelta(hours=8))
START_FIELDS = ("start_at", "started_at", "start_time", "open_at")
END_FIELDS = ("end_at", "ends_at", "end_time", "deadline")
LABELS = {"unscheduled": "尚未设置时间", "pending": "尚未开始", "running": "考试进行中", "ended": "比赛已结束"}


def timestamp(value):
    if isinstance(value, bool) or not value:
        return 0
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = float(value)
            if not math.isfinite(number) or number <= 0:
                return 0
            datetime.fromtimestamp(number, BEIJING)  # Reject unrenderable numeric dates.
            return int(number)
        dt = datetime.fromisoformat(str(value).strip().replace("/", "-"))
        return max(0, int(dt.replace(tzinfo=BEIJING).timestamp() if dt.tzinfo is None else dt.timestamp()))
    except (ValueError, TypeError, OverflowError, OSError):
        return 0


def window(contest):
    c = contest or {}
    start = next((timestamp(c.get(k)) for k in START_FIELDS if timestamp(c.get(k))), 0)
    end = next((timestamp(c.get(k)) for k in END_FIELDS if timestamp(c.get(k))), 0)
    if start and not end:
        from .store import duration_of
        end = start + duration_of(c) * 60
    return start, end


def phase(contest, now=None):
    start, end = window(contest)
    if not start or end <= start:
        # Historical published contests remain readable; undated drafts stay locked.
        return "ended" if (contest or {}).get("released") else "unscheduled"
    now = time.time() if now is None else now
    return "pending" if now < start else "running" if now < end else "ended"


def require_running(contest, now=None):
    state = phase(contest, now)
    if state != "running":
        raise ClosedContest(LABELS[state] + "，无法交卷。")


class ClosedContest(ValueError):
    pass


def parse_schedule(form, contest=None):
    start = timestamp(form.get("start_at"))
    end = timestamp(form.get("end_at"))
    if not start or not end or end <= start:
        raise ValueError("请填写有效的开始、结束时间（北京时间），结束必须晚于开始。")
    old_start, _ = window(contest)
    if old_start and old_start <= time.time() and old_start != start:
        raise ValueError("已经开始的比赛不能修改开始时间；需要另开考试请新建比赛。")
    return {"start_at": start, "end_at": end, "duration_min": math.ceil((end - start) / 60)}


def input_value(value):
    ts = timestamp(value)
    return datetime.fromtimestamp(ts, BEIJING).strftime("%Y-%m-%dT%H:%M:%S") if ts else ""


def display(value):
    ts = timestamp(value)
    return datetime.fromtimestamp(ts, BEIJING).strftime("%Y-%m-%d %H:%M:%S") if ts else "未设置"
