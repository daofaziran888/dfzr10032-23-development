"""交班记录业务规则：用计时事件划定可交付的连续观测区间。

交班记录的输入只有计时事件（不重新解释原始读数），输出三样东西：

* 可交付区间：同一来源内，相邻样本连续、修正时间可用性为“允许”、且中间
  没有待核对事件的时段。
* 待核对区间：计时事件覆盖、但证据不足以按修正时间交付的时段，连同判定依据
  和依据样本一起交给下一班。
* 说明：每个区间为什么可用 / 为什么不能用，依据是哪几条样本。

区间之间按固定顺序切分、互不重叠，避免同一段观测被两个交班批次重复计入。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.services.clock_drift import (
    SOURCE_MODULE,
    USABLE_BLOCK,
    USABLE_REFERENCE,
    USABLE_TEXT,
    clock_drift_service,
)
from app.services.timing_event import timing_event_service
from app.store import store

HANDOVER_MODULE = "handover"
INTERVAL_MODULE = "handover_interval"

SHIFTS = ["白班 08:00-20:00", "夜班 20:00-08:00"]


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S")


class HandoverService:
    # ---------- 查询 ----------

    def list_records(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        source_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(HANDOVER_MODULE))
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("交班编号", "")) or keyword in str(row.get("交班摘要", ""))
            ]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if source_code:
            rows = [row for row in rows if source_code in (row.get("涉及来源") or [])]
        rows.sort(key=lambda row: int(row.get("id", 0)), reverse=True)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_record(self, record_id: int) -> dict[str, Any] | None:
        return store.find(HANDOVER_MODULE, record_id)

    def list_intervals(
        self,
        *,
        handover_code: str | None = None,
        source_code: str | None = None,
        kind: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(INTERVAL_MODULE))
        if handover_code:
            rows = [row for row in rows if row.get("交班编号") == handover_code]
        if source_code:
            rows = [row for row in rows if row.get("来源编号") == source_code]
        if kind:
            rows = [row for row in rows if row.get("区间类型") == kind]
        rows.sort(key=lambda row: (str(row.get("来源编号", "")), str(row.get("区间起点", ""))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def interval_detail(self, interval_id: int) -> dict[str, Any] | None:
        return store.find(INTERVAL_MODULE, interval_id)

    # ---------- 预览 ----------

    def preview(self, source_code: str | None = None) -> dict[str, Any]:
        """只计算不落库：展示当前所有来源可交付 / 待核对的区间。

        已经进入交班记录的可交付样本会标成“已计入”，再次生成交班记录时不会重复
        计入；待核对区间不受影响，会继续交给下一班。
        """
        codes = [source_code] if source_code else [str(row["来源编号"]) for row in store.rows(SOURCE_MODULE)]
        committed = self._committed_codes()
        resolved = self._resolved_events()
        intervals: list[dict[str, Any]] = []
        for code in codes:
            intervals.extend(self._build_intervals(code, committed, resolved))
        counts = {}
        for item in intervals:
            counts[item["区间类型"]] = counts.get(item["区间类型"], 0) + 1
        return {
            "来源数": len(codes),
            "区间数": len(intervals),
            "可交付": counts.get("可交付", 0),
            "待核对": counts.get("待核对", 0),
            "仅参考": counts.get("仅参考", 0),
            "已计入": sum(1 for item in intervals if item.get("已计入")),
            "已移交": sum(1 for item in intervals if item.get("已移交")),
            "区间": intervals,
        }

    def _resolved_events(self) -> dict[str, dict[str, Any]]:
        """已现场回填过结论的待核对事件：结论 + 依据样本。

        回填过的事件不再重复列入下一班清单；确认可交付的样本并入已计入范围，
        确认不可用的样本保持不可交付。
        """
        result: dict[str, dict[str, Any]] = {}
        for row in store.rows(INTERVAL_MODULE):
            event = str(row.get("依据事件") or "")
            conclusion = str(row.get("核对结论") or "")
            if not event or not conclusion:
                continue
            result[event] = {
                "结论": conclusion,
                "依据样本": [code for code in str(row.get("依据样本") or "").split("、") if code],
                "交班编号": row.get("交班编号"),
                "区间类型": "可交付" if conclusion == "确认可交付" else "待核对",
            }
        return result

    def _committed_codes(self) -> dict[str, tuple[str, str]]:
        """已经随交班记录移交过的样本 → (交班编号, 区间类型)。

        可交付样本确认计入后不再重复计入；仅参考样本随记录移交后也不在下一次
        记录里重复列出，但仍不是可交付结果。
        """
        result: dict[str, tuple[str, str]] = {}
        for row in store.rows(INTERVAL_MODULE):
            kind = str(row.get("区间类型"))
            if kind not in ("可交付", "仅参考"):
                continue
            if row.get("status") == "待核对":
                continue
            for code in str(row.get("依据样本") or "").split("、"):
                if code:
                    result.setdefault(code, (str(row.get("交班编号") or ""), kind))
        return result

    # ---------- 生成交班记录 ----------

    def create_record(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        source_code = str(values.get("来源编号") or "").strip() or None
        shift = str(values.get("班次") or "").strip() or SHIFTS[0]
        if shift not in SHIFTS:
            return None, f"班次「{shift}」不在可登记范围内，请选择：{'、'.join(SHIFTS)}"
        if source_code and not any(row.get("来源编号") == source_code for row in store.rows(SOURCE_MODULE)):
            return None, f"来源 {source_code} 不存在，无法生成交班记录"
        next_shift = SHIFTS[(SHIFTS.index(shift) + 1) % len(SHIFTS)]

        preview = self.preview(source_code)
        # 已计入过历史交班记录的可交付样本不再重复写入新记录，也不重复计入时长。
        intervals = [
            item for item in preview["区间"]
            if not item.get("已计入") and not item.get("已移交")
        ]
        fresh_pending = [item for item in intervals if item["区间类型"] == "待核对"]
        fresh_deliverable = [item for item in intervals if item["区间类型"] == "可交付"]
        if not fresh_pending and not fresh_deliverable:
            return None, "没有新的可交付区间或待核对区间；已确认时段不重复计入，请核对已生成的交班记录"

        rows = store.rows(HANDOVER_MODULE)
        record_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        used = {str(row.get("交班编号")) for row in rows}
        serial = record_id
        while f"HND-{serial:04d}" in used:
            serial += 1
        code = f"HND-{serial:04d}"
        sources = sorted({item["来源编号"] for item in intervals})
        deliverable = fresh_deliverable
        pending = fresh_pending
        references = [item for item in intervals if item["区间类型"] == "仅参考"]

        for index, item in enumerate(intervals, start=1):
            item["id"] = 0
            item["status"] = "待核对" if item["区间类型"] == "待核对" else "已移交"
            item["pending"] = item["区间类型"] == "待核对"
            item["abnormal"] = item["区间类型"] == "待核对"
            item["交班编号"] = code
            item["区间编号"] = f"{code}-{item['来源编号'].split('-')[-1]}-{index:03d}"
            item["接班班次"] = next_shift
        store.rows(INTERVAL_MODULE).extend(intervals)
        self._renumber_intervals()

        record = {
            "id": record_id,
            "status": "待交接" if pending else "已完成",
            "含仅参考": bool(references),
            "pending": bool(pending),
            "abnormal": False,
            "交班编号": code,
            "班次": shift,
            "接班班次": next_shift,
            "涉及来源": sources,
            "已计入区间数": preview.get("已计入", 0),
            "可交付区间数": len(deliverable),
            "待核对区间数": len(pending),
            "仅参考区间数": len(references),
            "可交付时长秒": sum(int(item["覆盖时长秒"]) for item in deliverable),
            "待核对时长秒": sum(int(item["覆盖时长秒"]) for item in pending),
            "交班摘要": (
                f"{len(sources)} 个来源共 {len(intervals)} 段观测："
                f"{len(deliverable)} 段可交付、{len(references)} 段仅参考、{len(pending)} 段待现场核对"
            ),
            "交接说明": self._summary_text(deliverable, references, pending),
            "待核对清单": [self._brief(item) for item in pending],
            "可交付清单": [self._brief(item) for item in deliverable],
            "仅参考清单": [self._brief(item) for item in references],
        }
        rows.append(record)
        message = "交班记录已生成"
        if pending:
            message += "，待核对区间已列入下一班核对清单"
        if references:
            message += "，仅参考区间已随记录移交"
        return record, message

    def _renumber_intervals(self) -> None:
        rows = store.rows(INTERVAL_MODULE)
        next_id = 1
        for row in rows:
            row["id"] = next_id
            next_id += 1

    def _brief(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "区间编号": item.get("区间编号"),
            "来源编号": item.get("来源编号"),
            "采样区间": item.get("采样区间"),
            "依据样本": item.get("依据样本"),
            "边界样本": item.get("边界样本"),
            "依据事件": item.get("依据事件"),
            "说明": item.get("区间说明"),
        }

    def _summary_text(
        self,
        deliverable: list[dict[str, Any]],
        references: list[dict[str, Any]],
        pending: list[dict[str, Any]],
    ) -> str:
        parts = []
        if deliverable:
            parts.append(
                "可交付：" + "；".join(
                    f"{item['来源编号']} {item['采样区间']}（{item['依据样本']}）" for item in deliverable
                )
            )
        if references:
            parts.append(
                "仅参考：" + "；".join(
                    f"{item['来源编号']} {item['采样区间']}（{item['依据样本']}）" for item in references
                )
            )
        if pending:
            parts.append(
                "待现场核对：" + "；".join(
                    f"{item['来源编号']} {item['采样区间']}（{item['原因']}，依据 {item['依据样本']}）"
                    for item in pending
                )
            )
        return "。".join(parts) + "。" if parts else "无可交班内容。"

    # ---------- 区间划分核心 ----------

    def _build_intervals(
        self,
        source_code: str,
        committed: dict[str, tuple[str, str]] | None = None,
        resolved: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        source = next((row for row in store.rows(SOURCE_MODULE) if row.get("来源编号") == source_code), None)
        if source is None:
            return []
        samples = self._samples(source_code)
        if not samples:
            return []
        events = timing_event_service.events_for_source(source_code)
        intervals = self._split(source, samples, events, committed or {}, resolved or {})
        return intervals

    def _samples(self, source_code: str) -> list[dict[str, Any]]:
        """按设备时间轴排好样本：重启后的样本顺延到上一时段末尾之后，不按接入顺序排。"""
        preview = clock_drift_service.preview_source(source_code)
        rows = preview.get("样本") or []
        rows.sort(key=lambda row: (int(row["时段序号"]), int(row["设备序号"]), int(row["接入顺序"])))
        ordered: list[dict[str, Any]] = []
        last_end: datetime | None = None
        period = int(preview.get("采样周期秒") or 60)
        for row in rows:
            start = _moment(row["修正时刻"] or row["设备时刻"])
            if last_end is not None and start <= last_end:
                # 防御性兜底：修正后仍与前一条重叠时，顺延一个采样周期，保持时间轴单调。
                start = last_end + timedelta(seconds=period)
            row["时间轴起点"] = start
            row["时间轴终点"] = start
            ordered.append(row)
            last_end = max(last_end or start, start)
        return ordered

    def _split(
        self,
        source: dict[str, Any],
        samples: list[dict[str, Any]],
        events: list[dict[str, Any]],
        committed: dict[str, tuple[str, str]],
        resolved: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        period = int(source.get("采样周期秒", 60))
        tolerance = int(source.get("时间容差秒", 5))
        blocked = self._blocked_codes(samples, events)
        for event_code, resolution in resolved.items():
            codes = [code for code in resolution["依据样本"] if code]
            if resolution["结论"] == "确认可交付":
                for code in codes:
                    committed.setdefault(code, (str(resolution.get("交班编号") or ""), "可交付"))
                    blocked.discard(code)
            else:
                blocked.update(codes)

        # 逐个样本决定交付状态：可交付 → 可交付；仅参考 → 仅参考；
        # 已计入过交班记录的 → 已计入（不重复计入）；其余不进入交付区间。
        marks: list[tuple[str, dict[str, Any]]] = []
        for sample in samples:
            code = sample["读数编号"]
            if code in blocked or sample["修正时间可用性"] == USABLE_TEXT[USABLE_BLOCK]:
                continue
            if sample["修正时间可用性"] == USABLE_TEXT[USABLE_REFERENCE]:
                marks.append(("reference_done" if code in committed else "reference", sample))
            elif code in committed:
                marks.append(("committed", sample))
            else:
                marks.append(("allow", sample))

        segments: list[tuple[str, list[dict[str, Any]]]] = []
        for mark, sample in marks:
            if segments and segments[-1][0] == mark:
                previous = segments[-1][1][-1]
                gap = int((sample["时间轴起点"] - previous["时间轴起点"]).total_seconds())
                sequential = int(sample["设备序号"]) - int(previous["设备序号"]) == 1
                same_epoch = int(sample["时段序号"]) == int(previous["时段序号"])
                if same_epoch and sequential and abs(gap - period) <= tolerance:
                    segments[-1][1].append(sample)
                    continue
            segments.append((mark, [sample]))

        intervals: list[dict[str, Any]] = []
        for mark, group in segments:
            if mark == "allow":
                intervals.append(self._deliverable(source, group, period))
            elif mark == "reference":
                intervals.append(self._reference(source, group, period))
            elif mark == "reference_done":
                item = self._reference(source, group, period)
                item["已移交"] = True
                item["计入交班编号"] = committed.get(group[0]["读数编号"], ("", ""))[0]
                item["status"] = "已移交"
                item["区间说明"] = (
                    f"{len(group)} 条样本已在交班记录 {item['计入交班编号']} 中作为仅参考区间移交，"
                    "本次不再重复列出。"
                )
                intervals.append(item)
            else:
                item = self._deliverable(source, group, period)
                item["已计入"] = True
                item["计入交班编号"] = committed.get(group[0]["读数编号"], ("", ""))[0]
                item["status"] = "已计入"
                item["区间说明"] = (
                    f"{len(group)} 条样本已在交班记录 {item['计入交班编号']} 中确认交付，"
                    "本次不再重复计入。"
                )
                intervals.append(item)

        # 待核对区间按来源顺序切开，同一来源内互不重叠，交给下一班时只出现一次。
        last_end: dict[str, str] = {}
        for event in sorted(events, key=lambda item: str(item.get("区间起点", ""))):
            if not event.get("待现场核对"):
                continue
            if str(event.get("事件编号")) in resolved:
                continue
            pending = self._pending_interval(source, samples, event, blocked)
            previous_end = last_end.get(str(pending["来源编号"]))
            if previous_end and pending["区间起点"] and pending["区间起点"] < previous_end:
                pending["区间起点"] = previous_end
                pending["采样区间"] = (
                    f"{pending['区间起点']} ~ {pending['区间终点']}"
                    if pending["区间终点"] and pending["区间终点"] != pending["区间起点"]
                    else pending["区间起点"]
                )
            if pending["区间起点"]:
                last_end[str(pending["来源编号"])] = pending["区间终点"] or pending["区间起点"]
            intervals.append(pending)

        intervals.sort(key=lambda item: (item["区间起点"], item["区间类型"]))
        return self._dedupe(intervals)

    def _blocked_codes(self, samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> set[str]:
        """待核对事件里真正不能交付的样本。

        * 重启交界：交界两侧的包（旧时段最后一个包与新时段首包）都不能算已确认，
          因为它们的先后无法证实。
        * 无法断定：可疑间隔之后的样本本身已被标成“不允许”，这里不再重复处理。
        * 采样缺口：缺口两端样本仍各自可用，缺口只阻断跨缺口的合并，不阻断样本。
        """
        blocked: set[str] = set()
        for event in events:
            if not event.get("待现场核对") or event.get("类型") != "重启交界":
                continue
            for code in event.get("依据样本列表") or []:
                blocked.add(str(code))
        return blocked

    def _deliverable(self, source: dict[str, Any], group: list[dict[str, Any]], period: int) -> dict[str, Any]:
        start = group[0]["时间轴起点"]
        end = group[-1]["时间轴起点"]
        codes = [item["读数编号"] for item in group]
        return {
            "id": 0,
            "status": "可交付",
            "pending": False,
            "abnormal": False,
            "区间类型": "可交付",
            "来源编号": source.get("来源编号"),
            "来源名称": source.get("来源名称"),
            "采样区间": f"{_stamp(start)} ~ {_stamp(end)}" if start != end else _stamp(start),
            "区间起点": _stamp(start),
            "区间终点": _stamp(end),
            "覆盖时长秒": max(int((end - start).total_seconds()), 0),
            "样本数": len(group),
            "依据样本": "、".join(codes),
            "依据事件": "",
            "区间说明": (
                f"{len(group)} 条样本设备序号连续、修正时间可用性均为“允许”，"
                f"相邻间隔与采样周期 {period} 秒一致，中间没有待核对事件。"
            ),
            "原因": "",
            "是否已确认": True,
        }

    def _pending_interval(
        self,
        source: dict[str, Any],
        samples: list[dict[str, Any]],
        event: dict[str, Any],
        blocked: set[str],
    ) -> dict[str, Any]:
        codes = [str(code) for code in (event.get("依据样本列表") or []) if code]
        # 重启交界事件同时引用了旧时段边界样本；交界本身只覆盖旧时段最后一条与新时段首条。
        if event.get("类型") == "重启交界" and event.get("交界样本列表"):
            codes = [str(code) for code in event["交界样本列表"] if code] or codes
        start = _moment(event["区间起点"]) if event.get("区间起点") else None
        end = _moment(event["区间终点"]) if event.get("区间终点") else start
        if start is None or end is None:
            affected = [sample for sample in samples if sample["读数编号"] in codes]
            start = end = affected[0]["时间轴起点"] if affected else None
        # 用修正后的时间轴判断样本是否落在事件区间内：重启后设备时刻会与新时段
        # 重叠，按设备时刻判断会把新时段的样本误算进交界区间。
        inside = [
            sample for sample in samples
            if start is not None and end is not None
            and start <= sample["时间轴起点"] <= end
        ]
        # 待核对区间只统计时段内部的样本；缺口本身没有读数时样本数为 0，
        # 事件判定用到的两侧边界样本单独列在“边界样本”，避免与相邻可交付区间混淆。
        inside_codes = [sample["读数编号"] for sample in inside]
        boundary_codes = [code for code in codes if code not in inside_codes]
        return {
            "id": 0,
            "status": "待核对",
            "pending": True,
            "abnormal": True,
            "区间类型": "待核对",
            "来源编号": source.get("来源编号"),
            "来源名称": source.get("来源名称"),
            "采样区间": f"{_stamp(start)} ~ {_stamp(end)}" if start != end else _stamp(start),
            "区间起点": _stamp(start) if start else "",
            "区间终点": _stamp(end) if end else "",
            "覆盖时长秒": max(int((end - start).total_seconds()), 0) if start and end else 0,
            "样本数": len(set(inside_codes)),
            "依据样本": "、".join(inside_codes) or "、".join(boundary_codes),
            "区间内样本": "、".join(inside_codes),
            "边界样本": "、".join(boundary_codes),
            "依据事件": event.get("事件编号"),
            "区间说明": event.get("判定依据", ""),
            "原因": event.get("核对提示") or event.get("计时影响", ""),
            "是否已确认": False,
            "允许继续使用": event.get("允许继续使用"),
        }

    def _reference(self, source: dict[str, Any], group: list[dict[str, Any]], period: int) -> dict[str, Any]:
        start = group[0]["时间轴起点"]
        end = group[-1]["时间轴起点"]
        codes = [item["读数编号"] for item in group]
        return {
            "id": 0,
            "status": "仅参考",
            "pending": True,
            "abnormal": False,
            "区间类型": "仅参考",
            "来源编号": source.get("来源编号"),
            "来源名称": source.get("来源名称"),
            "采样区间": f"{_stamp(start)} ~ {_stamp(end)}" if start != end else _stamp(start),
            "区间起点": _stamp(start),
            "区间终点": _stamp(end),
            "覆盖时长秒": max(int((end - start).total_seconds()), 0),
            "样本数": len(group),
            "依据样本": "、".join(codes),
            "依据事件": "",
            "区间说明": (
                f"{len(group)} 条样本设备序号连续，但绝对时间锚定依赖重启后的推定值，"
                f"修正时间只能作参考，需现场核对设备时钟后确认。"
            ),
            "原因": "重启后绝对时间锚定不确定，修正时间仅可作参考",
            "是否已确认": False,
            "允许继续使用": USABLE_REFERENCE,
        }

    def _dedupe(self, intervals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """同一来源、同一区间类型、起止与依据都相同的区间只保留一条。"""
        seen: set[tuple[str, ...]] = set()
        result = []
        for item in intervals:
            key = (
                str(item["来源编号"]),
                str(item["区间类型"]),
                str(item["区间起点"]),
                str(item["区间终点"]),
                str(item.get("依据事件") or item.get("依据样本") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    # ---------- 现场核对回填 ----------

    def resolve_interval(self, interval_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """现场核对完成后回填结论：确认可交付，或维持不可用。

        回填只改这一段区间的结论，不会把已确认的相邻时段重新计入，
        也不会自动并入新交班记录。
        """
        interval = store.find(INTERVAL_MODULE, interval_id)
        if interval is None:
            return None, f"区间 {interval_id} 不存在或已移除"
        if interval.get("区间类型") != "待核对":
            return None, "只有待核对区间需要现场核对回填"
        conclusion = str(values.get("结论") or "").strip()
        if conclusion not in ("确认可交付", "确认不可用"):
            return None, "请选择结论：确认可交付 / 确认不可用"
        verifier = str(values.get("核对人") or "").strip() or "值班管理员"
        note = str(values.get("核对说明") or "").strip()
        interval["核对结论"] = conclusion
        interval["核对人"] = verifier
        interval["核对说明"] = note
        interval["是否已确认"] = conclusion == "确认可交付"
        interval["status"] = "已核对" if interval["是否已确认"] else "维持待核对"
        interval["pending"] = not interval["是否已确认"]
        interval["abnormal"] = not interval["是否已确认"]
        if conclusion == "确认可交付":
            interval["区间类型"] = "可交付"
            interval["原因"] = ""
            interval["区间说明"] = (
                f"现场核对（{verifier}）确认可交付。" + (f"核对说明：{note}" if note else "")
            )
        else:
            interval["区间说明"] = (
                f"现场核对（{verifier}）判定不可按修正时间交付。" + (f"核对说明：{note}" if note else "")
            )
        self._propagate_resolution(interval, conclusion, verifier, note)
        return interval, f"待核对区间已按现场结论更新为「{conclusion}」"

    def _propagate_resolution(
        self,
        interval: dict[str, Any],
        conclusion: str,
        verifier: str,
        note: str,
    ) -> None:
        """同一条计时事件在多个交班记录里都出现过：把结论同步到其余仍未核对的区间，
        避免上一班的清单一直显示待核对。"""
        event = str(interval.get("依据事件") or "")
        if not event:
            return
        for row in store.rows(INTERVAL_MODULE):
            if row is interval or row.get("依据事件") != event:
                continue
            if row.get("区间类型") != "待核对" or row.get("核对结论"):
                continue
            row["核对结论"] = conclusion
            row["核对人"] = verifier
            row["核对说明"] = note
            row["是否已确认"] = conclusion == "确认可交付"
            row["status"] = "已核对" if row["是否已确认"] else "维持待核对"
            row["pending"] = not row["是否已确认"]
            row["abnormal"] = not row["是否已确认"]
            if conclusion == "确认可交付":
                row["区间类型"] = "可交付"
                row["原因"] = ""
            row["区间说明"] = (
                f"现场核对（{verifier}）{'确认可交付' if conclusion == '确认可交付' else '判定不可按修正时间交付'}。"
                + (f"核对说明：{note}" if note else "")
            )

    # ---------- 汇总 ----------

    def summary(self) -> dict[str, Any]:
        rows = store.rows(HANDOVER_MODULE)
        intervals = store.rows(INTERVAL_MODULE)
        return {
            "交班记录数": len(rows),
            "待交接": sum(1 for row in rows if row.get("pending")),
            "已登记区间": len(intervals),
            "可交付区间": sum(1 for row in intervals if row.get("区间类型") == "可交付"),
            "待核对区间": sum(1 for row in intervals if row.get("区间类型") == "待核对"),
        }


handover_service = HandoverService()
