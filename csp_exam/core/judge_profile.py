"""官方时限与机器性能倍率分开保存；未标定时为 1.0。"""

import math
import os
import time

from . import store


def load() -> dict:
    value = store._load(os.path.join(store.DATA_DIR, "judge_profile.json"), dict) or {}
    scale = float(value.get("time_scale", 1.0))
    if not math.isfinite(scale) or not 0.125 <= scale <= 8:
        raise ValueError("评测时间倍率配置无效")
    return {**value, "time_scale": scale, "calibrated": bool(value.get("calibrated", False))}


def save(scale: float, *, calibrated=False, evidence=None) -> None:
    if not math.isfinite(scale) or not 0.125 <= scale <= 8:
        raise ValueError("时间倍率应在 0.125～8 之间")
    with store._LOCK:
        store._save(os.path.join(store.DATA_DIR, "judge_profile.json"),
                    {"time_scale": scale, "calibrated": bool(calibrated),
                     "evidence": evidence or {}, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})


def effective_limit(time_ms: int, *, profile=None) -> tuple[int, dict]:
    if int(time_ms) <= 0:
        raise ValueError("题目时限必须大于零")
    profile = dict(load() if profile is None else profile)
    scale = float(profile["time_scale"])
    if not math.isfinite(scale) or not .125 <= scale <= 8:
        raise ValueError("评测时间倍率配置无效")
    profile.update(time_scale=scale, calibrated=bool(profile.get("calibrated", False)))
    return max(1, math.ceil(int(time_ms) * profile["time_scale"])), profile
