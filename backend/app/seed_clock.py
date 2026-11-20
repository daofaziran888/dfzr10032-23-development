"""采样时钟漂移、计时事件与交班记录的示例数据。

这里刻意保留了现场最容易混淆的几种情况：

* 1 号压力计：设备时钟在运行中向前跳变，偏移持续存在；另有一个包被网关缓存后补送。
* 2 号压力计：设备重启后序号从 1 重新起算、设备时钟回拨，网关同时补送重启前的
  序号 6；同一个设备时刻出现两条不同压力读数，接入时点只精确到分钟。
* 3 号压力计：设备间隔与接入间隔同时多出一个采样周期，序号连续，无法断定是
  时钟跳变、采样暂停还是两者叠加。
* 4 号压力计：既有缓存补送，又有始终没有到达的序号缺口。

原始读数只保存设备序号、设备时刻、接入时点、接入顺序和读数本身，不预置
“这是跳变 / 这是补送”的结论，判定全部由 services/clock_drift.py 完成。
"""
from __future__ import annotations

from typing import Any

DAY = "2026-09-01"


def _source(
    source_id: int,
    code: str,
    name: str,
    period: int,
    tolerance: int,
    jump_threshold: int,
    backfill_threshold: int,
    precision: str,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "status": "启用",
        "pending": False,
        "abnormal": False,
        "来源编号": code,
        "来源名称": name,
        "采样周期秒": period,
        "时间容差秒": tolerance,
        "时钟跳变阈值秒": jump_threshold,
        "补送判定秒": backfill_threshold,
        "接入时间精度": precision,
        "状态": "启用",
        "负责人": "值班管理员",
    }


def _sample(
    sample_id: int,
    code: str,
    source: str,
    seq: int,
    device: str,
    ingest: str,
    arrival: int,
    value: float,
) -> dict[str, Any]:
    return {
        "id": sample_id,
        "status": "已采集",
        "pending": False,
        "abnormal": False,
        "来源编号": source,
        "读数编号": code,
        "设备序号": seq,
        "设备时刻": f"{DAY}T{device}",
        "接入时点": f"{DAY} {ingest}",
        "接入顺序": arrival,
        "读数": value,
    }


SOURCES: list[dict[str, Any]] = [
    _source(1, "SRC-PRESS-A", "1号压力计", 60, 5, 30, 120, "秒"),
    _source(2, "SRC-PRESS-B", "2号压力计", 60, 5, 30, 120, "分钟"),
    _source(3, "SRC-PRESS-C", "3号压力计", 30, 3, 15, 90, "秒"),
    _source(4, "SRC-PRESS-D", "4号压力计", 60, 5, 30, 120, "秒"),
]


SAMPLES: list[dict[str, Any]] = []


def _add(code: str, source: str, seq: int, device: str, ingest: str, arrival: int, value: float) -> None:
    SAMPLES.append(_sample(len(SAMPLES) + 1, code, source, seq, device, ingest, arrival, value))


# 1 号压力计：10:04:00 之后设备时钟向前跳 35 秒；10:09:35 的包被缓存到 10:11:30 才接入。
_add("RD-A-001", "SRC-PRESS-A", 1, "10:00:00", "10:00:00", 1, 2.10)
_add("RD-A-002", "SRC-PRESS-A", 2, "10:01:00", "10:01:00", 2, 2.11)
_add("RD-A-003", "SRC-PRESS-A", 3, "10:02:00", "10:02:00", 3, 2.12)
_add("RD-A-004", "SRC-PRESS-A", 4, "10:03:00", "10:03:00", 4, 2.13)
_add("RD-A-005", "SRC-PRESS-A", 5, "10:04:00", "10:04:00", 5, 2.14)
_add("RD-A-006", "SRC-PRESS-A", 6, "10:05:35", "10:05:00", 6, 2.15)
_add("RD-A-007", "SRC-PRESS-A", 7, "10:06:35", "10:06:00", 7, 2.16)
_add("RD-A-008", "SRC-PRESS-A", 8, "10:07:35", "10:07:00", 8, 2.17)
_add("RD-A-009", "SRC-PRESS-A", 9, "10:08:35", "10:08:00", 9, 2.18)
_add("RD-A-010", "SRC-PRESS-A", 10, "10:09:35", "10:11:30", 12, 2.19)
_add("RD-A-011", "SRC-PRESS-A", 11, "10:10:35", "10:10:00", 10, 2.20)
_add("RD-A-012", "SRC-PRESS-A", 12, "10:11:35", "10:11:00", 11, 2.21)

# 2 号压力计：14:00 重启，序号重新从 1 起算、设备时钟回拨到 14:00；
# 重启前最后一个包（序号 6，3.16）被网关缓存到 14:06 才补送，与新时段的序号 1
# （4.10）同为 14:00、同一分钟接入——同一个设备时刻两条不同读数，先后无法证实。
_add("RD-B-001", "SRC-PRESS-B", 1, "13:55:00", "13:55", 1, 3.11)
_add("RD-B-002", "SRC-PRESS-B", 2, "13:56:00", "13:56", 2, 3.12)
_add("RD-B-003", "SRC-PRESS-B", 3, "13:57:00", "13:57", 3, 3.13)
_add("RD-B-004", "SRC-PRESS-B", 4, "13:58:00", "13:58", 4, 3.14)
_add("RD-B-005", "SRC-PRESS-B", 5, "13:59:00", "13:59", 5, 3.15)
_add("RD-B-006", "SRC-PRESS-B", 1, "14:00:00", "14:06", 6, 4.10)
_add("RD-B-007", "SRC-PRESS-B", 2, "14:01:00", "14:07", 7, 4.11)
_add("RD-B-008", "SRC-PRESS-B", 3, "14:02:00", "14:07", 8, 4.12)
_add("RD-B-009", "SRC-PRESS-B", 4, "14:03:00", "14:08", 9, 4.13)
_add("RD-B-010", "SRC-PRESS-B", 5, "14:04:00", "14:08", 10, 4.14)
_add("RD-B-011", "SRC-PRESS-B", 6, "14:05:00", "14:09", 11, 4.15)
_add("RD-B-012", "SRC-PRESS-B", 6, "14:00:00", "14:06", 12, 3.16)

# 3 号压力计：15:01:30 之后设备与接入同时多出 30 秒，序号仍连续。
_add("RD-C-001", "SRC-PRESS-C", 1, "15:00:00", "15:00:00", 1, 1.50)
_add("RD-C-002", "SRC-PRESS-C", 2, "15:00:30", "15:00:31", 2, 1.51)
_add("RD-C-003", "SRC-PRESS-C", 3, "15:01:00", "15:01:00", 3, 1.52)
_add("RD-C-004", "SRC-PRESS-C", 4, "15:01:30", "15:01:31", 4, 1.53)
_add("RD-C-005", "SRC-PRESS-C", 5, "15:02:30", "15:02:31", 5, 1.54)
_add("RD-C-006", "SRC-PRESS-C", 6, "15:03:00", "15:03:01", 6, 1.55)
_add("RD-C-007", "SRC-PRESS-C", 7, "15:03:30", "15:03:30", 7, 1.56)
_add("RD-C-008", "SRC-PRESS-C", 8, "15:04:00", "15:04:01", 8, 1.57)

# 4 号压力计：序号 6 的包延迟 4 分半接入；序号 7 始终没有到达。
_add("RD-D-001", "SRC-PRESS-D", 1, "17:00:00", "17:00:00", 1, 5.00)
_add("RD-D-002", "SRC-PRESS-D", 2, "17:01:00", "17:01:00", 2, 5.01)
_add("RD-D-003", "SRC-PRESS-D", 3, "17:02:00", "17:02:00", 3, 5.02)
_add("RD-D-004", "SRC-PRESS-D", 4, "17:03:00", "17:03:00", 4, 5.03)
_add("RD-D-005", "SRC-PRESS-D", 5, "17:04:00", "17:04:00", 5, 5.04)
_add("RD-D-006", "SRC-PRESS-D", 6, "17:05:00", "17:08:30", 8, 5.05)
_add("RD-D-007", "SRC-PRESS-D", 8, "17:07:00", "17:07:01", 6, 5.07)
_add("RD-D-008", "SRC-PRESS-D", 9, "17:08:00", "17:08:01", 7, 5.08)


CLOCK_SEED_ROWS: dict[str, list[dict[str, Any]]] = {
    "clock_drift_source": SOURCES,
    "clock_drift_sample": SAMPLES,
    "clock_drift_detail": [],
    "timing_event": [],
    "handover": [],
}
