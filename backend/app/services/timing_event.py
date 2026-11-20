"""计时事件业务规则：把采样时钟漂移明细转成可核对的计时事件。

每个事件对应一个受影响的来源和一段采样区间，保留判定依据、依据样本和
“这段结果是否允许按修正时间继续使用”的结论。交班记录只消费这里的事件，
不重新解释原始读数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.clock_drift import (
    DETAIL_MODULE,
    USABLE_ALLOW,
    USABLE_BLOCK,
    USABLE_REFERENCE,
    USABLE_TEXT,
    clock_drift_service,
)
from app.store import store

EVENT_MODULE = "timing_event"

EVENT_RULES = {
    "时钟跳变": {
        "计时影响": "设备时刻整体偏移，按累计偏移修正后与采样周期对齐",
        "现场核对": False,
    },
    "缓存补送": {
        "计时影响": "设备时间不受影响，仅接入顺序滞后，按设备序号保留原位",
        "现场核对": False,
    },
    "重启交界": {
        "计时影响": "重启前后两段时间轴不可直接拼接，交界区间的先后无法证实",
        "现场核对": True,
    },
    "无法断定": {
        "计时影响": "设备间隔与接入间隔同步多出采样周期，无法区分时钟跳变与采样暂停",
        "现场核对": True,
    },
    "采样缺口": {
        "计时影响": "缺口期间没有读数，前后两段不能合并为连续观测",
        "现场核对": True,
    },
}


def _moment(text: str) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _seconds(start: str, end: str) -> int:
    begin, finish = _moment(start), _moment(end)
    if begin is None or finish is None:
        return 0
    return max(int((finish - begin).total_seconds()), 0)


class TimingEventService:
    # ---------- 查询 ----------

    def list_events(
        self,
        *,
        source_code: str | None = None,
        event_type: str | None = None,
        usability: str | None = None,
        pending_only: bool = False,
        keyword: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(EVENT_MODULE))
        if source_code:
            rows = [row for row in rows if row.get("来源编号") == source_code]
        if event_type:
            rows = [row for row in rows if row.get("类型") == event_type]
        if usability:
            rows = [row for row in rows if row.get("允许继续使用") == usability]
        if pending_only:
            rows = [row for row in rows if row.get("待现场核对")]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("事件编号", ""))
                or keyword in str(row.get("依据样本", ""))
                or keyword in str(row.get("采样区间", ""))
            ]
        rows.sort(key=lambda row: (str(row.get("来源编号", "")), str(row.get("区间起点", ""))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        return store.find(EVENT_MODULE, event_id)

    def events_for_source(self, source_code: str) -> list[dict[str, Any]]:
        rows = [row for row in store.rows(EVENT_MODULE) if row.get("来源编号") == source_code]
        rows.sort(key=lambda row: str(row.get("区间起点", "")))
        return rows

    # ---------- 生成 ----------

    def generate_all(self) -> list[dict[str, Any]]:
        summary = []
        sources = {row.get("来源编号") for row in store.rows(DETAIL_MODULE)}
        for source_code in sorted(sources):
            summary.append(self.generate_source(str(source_code)))
        return summary

    def generate_source(self, source_code: str) -> dict[str, Any]:
        details = [row for row in store.rows(DETAIL_MODULE) if row.get("来源编号") == source_code]
        details.sort(key=lambda row: (str(row.get("起始时刻", "")), str(row.get("明细编号", ""))))
        events = [self._event_from_detail(source_code, index, detail) for index, detail in enumerate(details, start=1)]
        self._replace(source_code, events)
        return {
            "来源编号": source_code,
            "事件数": len(events),
            "待核对": sum(1 for event in events if event["待现场核对"]),
            "事件": events,
        }

    def _event_from_detail(self, source_code: str, index: int, detail: dict[str, Any]) -> dict[str, Any]:
        kind = str(detail.get("类型"))
        rule = EVENT_RULES.get(kind, {"计时影响": "", "现场核对": True})
        usability = str(detail.get("修正时间可用性"))
        allowed = usability == USABLE_ALLOW
        start = str(detail.get("起始时刻") or "")
        end = str(detail.get("结束时刻") or "")
        samples = list(detail.get("依据样本列表") or [])
        # 事件依据样本补上区间内的全部样本，交班时可直接核对是哪几条。
        interval_codes = [
            item.get("读数编号")
            for item in clock_drift_service.preview_source(source_code).get("样本", [])
            if start <= item.get("修正时刻", item.get("设备时刻", "")) <= end
        ]
        evidence = [str(code) for code in (detail.get("依据样本列表") or []) if code]
        preview_samples = clock_drift_service.preview_source(source_code).get("样本", [])
        by_time = {item.get("读数编号"): str(item.get("修正时刻", "")) for item in preview_samples}
        # 先列区间内的样本（按修正时间排序），再补判定时用到的边界样本。
        span = sorted(
            {code for code in interval_codes if code},
            key=lambda code: by_time.get(code, ""),
        )
        samples = list(dict.fromkeys(span + evidence))
        suffix = source_code.split("-")[-1]
        note = ""
        if kind == "重启交界":
            note = self._restart_note(detail)
        elif kind == "无法断定":
            note = "这段结果不允许按修正时间继续使用，需现场比对设备日志后再决定。"
        elif kind == "采样缺口":
            note = "缺口两端的可交付区间分别计算，不跨缺口合并。"
        elif kind == "缓存补送":
            note = "事件只记录接入顺序异常，设备时间与序号位置保持不变。"
        elif kind == "时钟跳变":
            note = f"跳变后按累计偏移 {detail.get('时钟偏移秒', 0)} 秒修正，修正时间可继续使用。"
        return {
            "id": 0,
            "status": "待核对" if detail.get("pending") else "已生成",
            "pending": bool(detail.get("pending")),
            "abnormal": usability != USABLE_ALLOW,
            "事件编号": f"TME-{suffix}-{index:03d}",
            "来源编号": source_code,
            "来源名称": detail.get("来源名称"),
            "类型": kind,
            "采样区间": f"{start} ~ {end}" if start != end else start,
            "区间起点": start,
            "区间终点": end,
            "覆盖时长秒": _seconds(start, end),
            "受影响样本数": int(detail.get("受影响样本数") or 0),
            "依据明细编号": detail.get("明细编号"),
            "依据样本": detail.get("依据样本", ""),
            "依据样本列表": samples,
            "判定依据": detail.get("判定依据", ""),
            "计时影响": rule["计时影响"],
            "偏差秒": detail.get("偏差秒", 0),
            "时钟偏移秒": detail.get("时钟偏移秒", 0),
            "允许继续使用": usability,
            "可用性说明": USABLE_TEXT.get(usability, usability),
            "允许按修正时间继续使用": allowed,
            "待现场核对": bool(rule["现场核对"] or detail.get("pending")),
            "核对提示": note,
            "处置": detail.get("处置", ""),
        }

    def _restart_note(self, detail: dict[str, Any]) -> str:
        old_last = str(detail.get("旧时段可交付至样本") or "")
        old_seq = int(detail.get("旧时段可交付至序号") or 0)
        usable = ""
        if old_last and old_seq:
            usable = f"旧时段序号 1~{old_seq}（截至 {old_last}）可按原时间交付；"
        return usable + "交界区间待现场核对，不得与新时段合并计入。"

    def _replace(self, source_code: str, events: list[dict[str, Any]]) -> None:
        rows = store.rows(EVENT_MODULE)
        kept = [row for row in rows if row.get("来源编号") != source_code]
        next_id = max((int(row.get("id", 0)) for row in kept), default=0) + 1
        for event in events:
            event["id"] = next_id
            next_id += 1
        rows[:] = kept + events

    # ---------- 汇总 ----------

    def summary(self) -> dict[str, Any]:
        rows = store.rows(EVENT_MODULE)
        by_type: dict[str, int] = {}
        by_usability: dict[str, int] = {}
        for row in rows:
            by_type[str(row.get("类型"))] = by_type.get(str(row.get("类型")), 0) + 1
            key = str(row.get("允许继续使用"))
            by_usability[key] = by_usability.get(key, 0) + 1
        return {
            "事件数": len(rows),
            "待现场核对": sum(1 for row in rows if row.get("待现场核对")),
            "允许按修正时间继续使用": by_usability.get(USABLE_ALLOW, 0),
            "仅参考": by_usability.get(USABLE_REFERENCE, 0),
            "不允许": by_usability.get(USABLE_BLOCK, 0),
            "按类型": by_type,
            "按可用性": by_usability,
        }


timing_event_service = TimingEventService()
