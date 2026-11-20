"""采样时钟漂移业务规则。

输入：同一来源的原始读数（设备序号、设备时刻、接入时点、接入顺序、读数）。
输出：按来源和采样区间划分的漂移明细，保留判定依据，并区分三类容易混淆的情况：

1. 设备时钟跳变：设备间隔偏离采样周期，但序号连续、接入间隔仍接近采样周期，
   跳变后的偏移量在后续样本中稳定存在。按累计偏移修正设备时刻。
2. 网关缓存补送：设备间隔与序号连续，但接入顺序晚于后续序号的样本、接入滞后
   超过补送阈值。样本保留在设备序号位置，不按接入顺序改写时间。
3. 无法断定：设备间隔和接入间隔同时多出同样的采样周期，序号仍连续；既可能是
   时钟跳变，也可能是采样暂停或补送，证据不足时不修改时间。

重启单独按“设备序号回到 1 且设备时刻回拨”识别；重启交界处接入时间只精确到
分钟时，旧包与新时段包的先后无法证实，输出为待现场核对，不臆造修正时间。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.store import store

SOURCE_MODULE = "clock_drift_source"
SAMPLE_MODULE = "clock_drift_sample"
DETAIL_MODULE = "clock_drift_detail"

USABLE_ALLOW = "允许"
USABLE_REFERENCE = "仅参考"
USABLE_BLOCK = "不允许"

USABLE_TEXT = {
    USABLE_ALLOW: "允许按修正时间继续使用",
    USABLE_REFERENCE: "修正时间仅可作参考",
    USABLE_BLOCK: "不允许按修正时间使用",
}


@dataclass
class WorkSample:
    raw: dict[str, Any]
    seq: int
    device: datetime
    ingest: datetime
    ingest_precision: int
    arrival: int
    epoch: int = 0
    corrected: datetime | None = None
    offset: int = 0
    usability: str = USABLE_ALLOW
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def code(self) -> str:
        return str(self.raw.get("读数编号") or self.raw.get("id"))


def parse_device(value: Any) -> datetime:
    return datetime.fromisoformat(str(value))


def parse_ingest(value: Any) -> tuple[datetime, int]:
    """接入时点转时间，并返回精度秒数：只到分钟的记录按 60 秒不确定度处理。"""
    text = str(value).strip().replace(" ", "T")
    moment = datetime.fromisoformat(text)
    precision = 60 if len(text) <= 16 else 1
    return moment, precision


def fmt(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if moment else ""


def span_text(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return ""
    if start == end:
        return fmt(start)
    return f"{fmt(start)} ~ {fmt(end)}"


class ClockDriftService:
    # ---------- 查询 ----------

    def list_sources(
        self,
        *,
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(SOURCE_MODULE)
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("来源编号", "")) or keyword in str(row.get("来源名称", ""))
            ]
        if extra:
            for field_name, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field_name, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_source(self, source_code: str) -> dict[str, Any] | None:
        for row in store.rows(SOURCE_MODULE):
            if row.get("来源编号") == source_code:
                return row
        return None

    def list_samples(
        self,
        *,
        source_code: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(SAMPLE_MODULE))
        if source_code:
            rows = [row for row in rows if row.get("来源编号") == source_code]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("读数编号", "")) or keyword in str(row.get("来源编号", ""))
            ]
        rows.sort(key=lambda row: (str(row.get("来源编号", "")), int(row.get("设备序号", 0)), int(row.get("接入顺序", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def list_details(
        self,
        *,
        source_code: str | None = None,
        keyword: str | None = None,
        anomaly: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = list(store.rows(DETAIL_MODULE))
        if source_code:
            rows = [row for row in rows if row.get("来源编号") == source_code]
        if anomaly:
            rows = [row for row in rows if row.get("类型") == anomaly]
        if keyword:
            rows = [
                row for row in rows
                if keyword in str(row.get("明细编号", ""))
                or keyword in str(row.get("依据样本", ""))
                or keyword in str(row.get("判定依据", ""))
            ]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    # ---------- 计算入口 ----------

    def bootstrap(self) -> None:
        """服务启动时把示例来源算一遍，前端打开就能看到漂移明细。"""
        if store.rows(DETAIL_MODULE):
            return
        for source in store.rows(SOURCE_MODULE):
            self.analyze_source(str(source["来源编号"]))

    def analyze_all(self) -> list[dict[str, Any]]:
        results = []
        for source in store.rows(SOURCE_MODULE):
            results.append(self.analyze_source(str(source["来源编号"])))
        return results

    def analyze_source(self, source_code: str) -> dict[str, Any]:
        result = self._analyze(source_code)
        if "error" not in result:
            self._replace_details(source_code, result["明细"])
        return result

    def preview_source(self, source_code: str) -> dict[str, Any]:
        """只计算不落库，供交班记录重新核对修正后的时间轴。"""
        return self._analyze(source_code)
    # ---------- 计算核心 ----------

    def _analyze(self, source_code: str) -> dict[str, Any]:
        source = self.get_source(source_code)
        if source is None:
            return {"来源编号": source_code, "error": f"来源 {source_code} 不存在", "details": [], "samples": []}

        period = int(source.get("采样周期秒", 60))
        tolerance = int(source.get("时间容差秒", 5))
        jump_threshold = int(source.get("时钟跳变阈值秒", 30))
        backfill_threshold = int(source.get("补送判定秒", 120))

        raw_samples = [row for row in store.rows(SAMPLE_MODULE) if row.get("来源编号") == source_code]
        samples = [
            WorkSample(
                raw=row,
                seq=int(row.get("设备序号", 0)),
                device=parse_device(row["设备时刻"]),
                ingest=parse_ingest(row["接入时点"])[0],
                ingest_precision=parse_ingest(row["接入时点"])[1],
                arrival=int(row.get("接入顺序", 0)),
            )
            for row in raw_samples
        ]
        if not samples:
            return {"来源编号": source_code, "来源名称": source.get("来源名称"), "details": [], "samples": []}

        epochs = self._assign_epochs(samples)
        details: list[dict[str, Any]] = []
        for epoch_index, epoch in enumerate(epochs):
            epoch.sort(key=lambda item: (item.seq, item.device, item.arrival))
            for item in epoch:
                item.epoch = epoch_index
            self._analyze_epoch(source, epoch, epoch_index, epochs, period, tolerance, jump_threshold, backfill_threshold, details)

        details.sort(key=lambda row: (str(row.get("起始时刻") or ""), str(row.get("类型") or "")))
        return {
            "来源编号": source_code,
            "来源名称": source.get("来源名称"),
            "采样周期秒": period,
            "样本数": len(samples),
            "明细数": len(details),
            "明细": details,
            "样本": [self._sample_view(item) for item in sorted(samples, key=self._sample_sort_key)],
            "统计": self._statistics(details),
            "epoch数": len(epochs),
        }

    def list_intervals(
        self,
        *,
        source_code: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """交班模块消费用：没有来源筛选时，把所有来源的明细汇总成一个可核对列表。"""
        rows = list(store.rows(DETAIL_MODULE))
        if source_code:
            rows = [row for row in rows if row.get("来源编号") == source_code]
        rows.sort(key=lambda row: (str(row.get("来源编号", "")), str(row.get("起始时刻", ""))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def _assign_epochs(self, samples: list[WorkSample]) -> list[list[WorkSample]]:
        """按设备序号与设备时刻还原时段，不按接入顺序改写真实先后。

        依次放入样本，优先补进“序号正好接得上、设备时刻不倒退”且序号位还空着的
        时段；其次补进设备时刻落在这个时段范围内、序号位空着的时段（网关缓存补送的
        旧包）。只有序号回到 1 且设备时刻倒退时，才开一个新时段（设备重启）。
        接入顺序只用于同一设备时刻的并列判断，绝不直接改写设备时刻。
        """
        ordered = sorted(samples, key=lambda item: (item.arrival, item.device, item.seq))
        epochs: list[list[WorkSample]] = []
        for sample in ordered:
            tail_fits: list[WorkSample] = []
            middle_fits: list[WorkSample] = []
            for epoch in epochs:
                max_seq = max(item.seq for item in epoch)
                max_device = max(item.device for item in epoch)
                min_device = min(item.device for item in epoch)
                free = all(item.seq != sample.seq for item in epoch)
                if not free:
                    continue
                if sample.seq == max_seq + 1 and sample.device >= max_device:
                    tail_fits.append(epoch)
                elif sample.seq <= max_seq and min_device <= sample.device <= max_device:
                    middle_fits.append(epoch)
            if tail_fits:
                # 多个时段的序号都接得上时，选最近仍在接入的那个（实时包留在当前时段）。
                chosen = max(tail_fits, key=lambda e: max(item.arrival for item in e))
                chosen.append(sample)
                continue
            if middle_fits:
                # 补送的旧包：放回设备时刻所在、序号位空着的时段。
                chosen = min(
                    middle_fits,
                    key=lambda e: min(
                        abs((sample.device - item.device).total_seconds()) for item in e
                    ),
                )
                chosen.append(sample)
                continue
            if sample.seq == 1:
                epochs.append([sample])
                continue
            # 序号对不上任何时段：归入设备时刻最近的、序号更早的时段，交给缺口规则处理。
            fallback: list[WorkSample] | None = None
            fallback_gap: float | None = None
            for epoch in epochs:
                if max(item.seq for item in epoch) >= sample.seq:
                    continue
                gap = abs((sample.device - max(item.device for item in epoch)).total_seconds())
                if fallback_gap is None or gap < fallback_gap:
                    fallback = epoch
                    fallback_gap = gap
            if fallback is not None:
                fallback.append(sample)
            else:
                epochs.append([sample])
        return epochs

    def _analyze_epoch(
        self,
        source: dict[str, Any],
        epoch: list[WorkSample],
        epoch_index: int,
        epochs: list[list[WorkSample]],
        period: int,
        tolerance: int,
        jump_threshold: int,
        backfill_threshold: int,
        details: list[dict[str, Any]],
    ) -> None:
        if not epoch:
            return
        epoch.sort(key=lambda item: (item.seq, item.device, item.arrival))

        first = epoch[0]
        if epoch_index == 0:
            first.corrected = first.device
            first.offset = 0
        else:
            previous_epoch = epochs[epoch_index - 1]
            previous_last = max(previous_epoch, key=lambda item: (item.seq, item.device))
            anchor = (previous_last.corrected or previous_last.device) + timedelta(seconds=period)
            first.offset = int((first.device - anchor).total_seconds())
            first.corrected = anchor
            first.usability = USABLE_REFERENCE
            first.note = "重启时段起点按上一时段最后一个样本加采样周期推定，接入时间精度受限"
            details.append(
                self._restart_detail(source, epochs, epoch_index, first, previous_last, period)
            )

        jump_start: WorkSample | None = None
        jump_deviation = 0
        jump_previous: WorkSample | None = None
        # 无法断定的跳变之后，后续样本的绝对时间失去锚点：不能只把可疑的一条
        # 标成待核对，却把其后的样本当作已确认时间继续交付。
        anchor_lost = False
        previous_lag = 0
        # 滞后基线：重启后的整段接入可能整体偏晚，基线取时段首条的滞后，
        # 后面的样本只有“比基线再多滞后一个补送阈值”才算缓存补送。
        if epoch_index > 0:
            previous_lag = int((first.ingest - (first.corrected or first.device)).total_seconds())

        def flush_jump() -> None:
            nonlocal jump_start, jump_previous, jump_deviation
            if jump_start is None:
                return
            affected = [
                item for item in epoch
                if item.seq >= jump_start.seq
                and item.usability == USABLE_ALLOW
                and item.offset == jump_start.offset
                and item.device >= jump_start.device
            ]
            end = affected[-1] if affected else jump_start
            details.append(
                self._jump_detail(
                    source, jump_previous or jump_start, jump_start, end,
                    period, jump_deviation, jump_start.offset,
                )
            )
            jump_start = None
            jump_previous = None
            jump_deviation = 0

        for index in range(1, len(epoch)):
            previous = epoch[index - 1]
            current = epoch[index]
            d_seq = current.seq - previous.seq
            expected = d_seq * period
            observed = int((current.device - previous.device).total_seconds())
            deviation = observed - expected
            ingest_delta = int((current.ingest - previous.ingest).total_seconds())
            corrected_expected_arrival = (previous.corrected or previous.device) + timedelta(seconds=expected)
            lag = int((current.ingest - corrected_expected_arrival).total_seconds())

            # 先按上一样本的累计偏移给出临时修正值，本样本若自身判定为跳变会在下面覆盖。
            current.corrected = current.device - timedelta(seconds=previous.offset)
            current.offset = previous.offset

            if d_seq > 1:
                flush_jump()
                previous_lag = 0
                # 缺口样本本身的时间可用；缺口只阻断“跨缺口合并”，不把两端样本标成不可用。
                current.corrected = current.device - timedelta(seconds=current.offset)
                details.append(
                    self._gap_detail(source, previous, current, d_seq, period, observed, deviation, lag)
                )
                continue

            if abs(deviation) > jump_threshold:
                ambiguous = (
                    abs(deviation - period) <= tolerance
                    and abs(ingest_delta - (expected + deviation)) <= tolerance
                )
                if ambiguous:
                    flush_jump()
                    details.append(
                        self._ambiguous_detail(source, previous, current, period, observed, deviation, ingest_delta, lag)
                    )
                    current.usability = USABLE_BLOCK
                    current.note = "设备间隔与接入间隔同时多出一个采样周期，无法断定是时钟跳变还是采样暂停"
                    jump_start = None
                    anchor_lost = True
                    continue
                # 设备时钟跳变：累计偏移，后续样本按新偏移修正。
                # 跳变样本自身的修正时刻取“上一样本修正时刻 + 采样周期”，
                # 不能沿用新偏移（否则跳变点本身会被多扣一次）。
                jump_deviation = deviation
                jump_previous = previous
                current.corrected = (previous.corrected or previous.device) + timedelta(seconds=expected)
                current.offset = previous.offset + deviation
                current.usability = USABLE_ALLOW
                current.note = f"按累计时钟偏移 {current.offset} 秒修正"
                if jump_start is None:
                    jump_start = current
                continue

            if jump_start is not None and abs(deviation) <= tolerance:
                current.offset = previous.offset
                current.corrected = current.device - timedelta(seconds=current.offset)
                current.usability = USABLE_ALLOW
                current.note = f"按累计时钟偏移 {current.offset} 秒修正"
            else:
                jump_start = None

            if anchor_lost and current.usability == USABLE_ALLOW:
                current.usability = USABLE_BLOCK
                current.note = "前段存在无法断定的间隔，后续绝对时间无法锚定"

            out_of_order = self._arrival_out_of_order(epoch, current)
            # 补送判据看“滞后量是否突然增大”，而不是绝对滞后：重启后整段接入都偏晚，
            # 那是时钟锚点不确定造成的，不应逐条记成缓存补送。
            late = lag - previous_lag > backfill_threshold
            displaced = out_of_order and lag > period
            previous_lag = lag
            if late or displaced:
                flush_jump()
                details.append(self._backfill_detail(source, previous, current, period, observed, lag, out_of_order))
                current.usability = USABLE_ALLOW
                current.note = "网关缓存补送，按设备序号保留原位"

        # 跳变明细在时段内合并成一条，覆盖所有共享同一偏移的连续样本。
        flush_jump()

        # 重启后的绝对时间锚定依赖“上一时段最后一条 + 采样周期”的推定，且接入时间
        # 只精确到分钟；整个新时段最多只能作参考，不能当作已确认的可交付时间。
        if epoch_index > 0:
            first.corrected = first.device - timedelta(seconds=first.offset)
            for item in epoch:
                if item.usability == USABLE_ALLOW:
                    item.usability = USABLE_REFERENCE
                    item.note = (item.note + "；" if item.note else "") + "重启后绝对时间锚定不确定，仅可作参考"

    def _arrival_out_of_order(self, epoch: list[WorkSample], sample: WorkSample) -> bool:
        later_arrivals = [
            item.arrival for item in epoch
            if item.seq > sample.seq and item.arrival < sample.arrival
        ]
        return bool(later_arrivals)

    def _sample_sort_key(self, item: WorkSample) -> tuple[int, int, int]:
        return (item.epoch, item.seq, item.arrival)

    def _sample_view(self, item: WorkSample) -> dict[str, Any]:
        return {
            "读数编号": item.code,
            "来源编号": item.raw.get("来源编号"),
            "设备序号": item.seq,
            "设备时刻": fmt(item.device),
            "接入时点": fmt(item.ingest),
            "接入顺序": item.arrival,
            "接入精度秒": item.ingest_precision,
            "读数": item.raw.get("读数"),
            "时段序号": item.epoch + 1,
            "时钟偏移秒": item.offset,
            "修正时刻": fmt(item.corrected),
            "修正时间可用性": USABLE_TEXT[item.usability],
            "说明": item.note,
        }

    # ---------- 明细构造 ----------

    def _base_detail(
        self,
        source: dict[str, Any],
        dtype: str,
        *,
        sample_range: str,
        device_range: str,
        ingest_range: str,
        basis: str,
        evidence: list[str],
        usability: str,
        action: str,
        start: datetime,
        end: datetime,
        expected: int = 0,
        observed: int = 0,
        deviation: int = 0,
        offset: int = 0,
        corrected_range: str = "",
        affected: int = 1,
    ) -> dict[str, Any]:
        return {
            "id": 0,
            "status": "待核对" if usability == USABLE_BLOCK else "已生成",
            "pending": usability == USABLE_BLOCK,
            "abnormal": True,
            "明细编号": "",
            "来源编号": source.get("来源编号"),
            "来源名称": source.get("来源名称"),
            "类型": dtype,
            "样本范围": sample_range,
            "受影响样本数": affected,
            "设备区间": device_range,
            "接入区间": ingest_range,
            "期望间隔秒": expected,
            "观测间隔秒": observed,
            "偏差秒": deviation,
            "时钟偏移秒": offset,
            "修正后区间": corrected_range,
            "判定依据": basis,
            "依据样本": "、".join(evidence),
            "依据样本列表": evidence,
            "修正时间可用性": usability,
            "可用性说明": USABLE_TEXT[usability],
            "处置": action,
            "起始时刻": fmt(start),
            "结束时刻": fmt(end),
            "区间样本": list(evidence),
        }

    def _jump_detail(
        self,
        source: dict[str, Any],
        previous: WorkSample,
        start: WorkSample,
        end: WorkSample,
        period: int,
        deviation: int,
        offset: int,
    ) -> dict[str, Any]:
        evidence = [previous.code, start.code, end.code]
        if end.code != start.code:
            evidence = [previous.code, start.code, end.code]
        basis = (
            f"设备序号 {previous.seq}→{start.seq} 连续，设备间隔 {int((start.device - previous.device).total_seconds())} 秒、"
            f"比采样周期 {period} 秒多出 {deviation} 秒，而接入间隔仍为约 {period} 秒；"
            f"此后 {end.seq - start.seq + 1} 条样本的偏移稳定保持 {offset} 秒，判定为设备时钟跳变。"
        )
        return self._base_detail(
            source,
            "时钟跳变",
            sample_range=f"{start.code} ~ {end.code}" if end.code != start.code else start.code,
            device_range=span_text(previous.device, end.device),
            ingest_range=span_text(previous.ingest, end.ingest),
            basis=basis,
            evidence=evidence,
            usability=USABLE_ALLOW,
            action=f"保留设备序号先后，按 {offset} 秒累计偏移修正设备时刻",
            start=start.corrected or start.device,
            end=end.corrected or end.device,
            expected=(end.seq - previous.seq) * period,
            observed=int((end.device - previous.device).total_seconds()),
            deviation=deviation,
            offset=offset,
            corrected_range=span_text(start.corrected, end.corrected),
            affected=end.seq - start.seq + 1,
        )

    def _backfill_detail(
        self,
        source: dict[str, Any],
        previous: WorkSample,
        current: WorkSample,
        period: int,
        observed: int,
        lag: int,
        out_of_order: bool,
    ) -> dict[str, Any]:
        order_note = "接入顺序晚于后续序号的样本" if out_of_order else "接入顺序正常但接入滞后"
        basis = (
            f"设备序号 {previous.seq}→{current.seq} 连续、设备间隔 {observed} 秒与采样周期 {period} 秒一致，"
            f"说明包内设备时间没有跳变；但{order_note}，按修正时刻计算接入滞后约 {lag} 秒，"
            "超过补送阈值，判定为网关缓存补送。样本保留在设备序号位置，不按接入顺序改写时间。"
        )
        return self._base_detail(
            source,
            "缓存补送",
            sample_range=current.code,
            device_range=fmt(current.device),
            ingest_range=fmt(current.ingest),
            basis=basis,
            evidence=[previous.code, current.code],
            usability=USABLE_ALLOW,
            action="按设备序号保留原位，修正时刻取设备时刻减当前时钟偏移",
            start=current.corrected or current.device,
            end=current.corrected or current.device,
            expected=period,
            observed=observed,
            deviation=0,
            offset=current.offset,
            corrected_range=fmt(current.corrected),
        )

    def _ambiguous_detail(
        self,
        source: dict[str, Any],
        previous: WorkSample,
        current: WorkSample,
        period: int,
        observed: int,
        deviation: int,
        ingest_delta: int,
        lag: int,
    ) -> dict[str, Any]:
        basis = (
            f"设备序号 {previous.seq}→{current.seq} 连续，但设备间隔 {observed} 秒比采样周期 {period} 秒多出"
            f" {deviation} 秒，接入间隔也同步多出 {deviation} 秒。序号没有缺口、读数连续，"
            "既可能是设备时钟跳变，也可能是采样恰好暂停一个周期；现有证据无法断定，不修改时间。"
        )
        return self._base_detail(
            source,
            "无法断定",
            sample_range=f"{previous.code} ~ {current.code}",
            device_range=span_text(previous.device, current.device),
            ingest_range=span_text(previous.ingest, current.ingest),
            basis=basis,
            evidence=[previous.code, current.code],
            usability=USABLE_BLOCK,
            action="保留原始时间，标记待现场核对，不按修正时间拼接",
            start=(previous.corrected or previous.device) + timedelta(seconds=period),
            end=current.corrected or current.device,
            expected=period,
            observed=observed,
            deviation=deviation,
            offset=current.offset,
            corrected_range="",
        )

    def _gap_detail(
        self,
        source: dict[str, Any],
        previous: WorkSample,
        current: WorkSample,
        d_seq: int,
        period: int,
        observed: int,
        deviation: int,
        lag: int,
    ) -> dict[str, Any]:
        missing = d_seq - 1
        basis = (
            f"设备序号从 {previous.seq} 跳到 {current.seq}，缺少 {missing} 个序号；"
            f"设备间隔 {observed} 秒、期望 {d_seq * period} 秒，偏移 {deviation} 秒。"
            "缺口期间没有可核对的读数，不能与前后样本拼成连续观测区间。"
        )
        return self._base_detail(
            source,
            "采样缺口",
            sample_range=f"{previous.code} ~ {current.code}",
            device_range=span_text(previous.device, current.device),
            ingest_range=span_text(previous.ingest, current.ingest),
            basis=basis,
            evidence=[previous.code, current.code],
            usability=USABLE_BLOCK,
            action="缺口单独标记，前后区间分别交付，不跨缺口合并",
            start=(previous.corrected or previous.device) + timedelta(seconds=period),
            end=(current.corrected or current.device) - timedelta(seconds=period),
            expected=d_seq * period,
            observed=observed,
            deviation=deviation,
            offset=current.offset,
            corrected_range="",
            affected=missing,
        )

    def _restart_detail(
        self,
        source: dict[str, Any],
        epochs: list[list[WorkSample]],
        epoch_index: int,
        first: WorkSample,
        previous_last: WorkSample,
        period: int,
    ) -> dict[str, Any]:
        previous_epoch = epochs[epoch_index - 1]
        current_epoch = epochs[epoch_index]
        old_last = max(previous_epoch, key=lambda item: (item.seq, item.device))
        first = min(current_epoch, key=lambda item: (item.seq, item.device))
        # 交界处同一个设备时刻的两条不同读数：旧时段最后一条与新时段同设备时刻的那条。
        seam_twin = next((item for item in current_epoch if item.device == old_last.device), None)
        # 复位后首条与重启前最后一个包接入落在同一分钟时，先后无法证实。
        same_minute = (
            old_last.ingest.replace(second=0, microsecond=0)
            == first.ingest.replace(second=0, microsecond=0)
        )
        evidence: list[str] = [old_last.code]
        for item in (seam_twin, first):
            if item is not None and item.code not in evidence:
                evidence.append(item.code)

        collisions: list[dict[str, Any]] = []
        for old_item in sorted(previous_epoch, key=lambda item: (item.device, item.seq)):
            twins = [
                new_item for new_item in current_epoch
                if new_item.device == old_item.device and new_item.raw.get("读数") != old_item.raw.get("读数")
            ]
            if not twins:
                continue
            twin = twins[0]
            collisions.append({
                "设备时刻": fmt(old_item.device),
                "旧包": old_item.code,
                "旧包序号": old_item.seq,
                "旧包读数": old_item.raw.get("读数"),
                "新包": twin.code,
                "新包序号": twin.seq,
                "新包读数": twin.raw.get("读数"),
            })
            if len(collisions) == 1:
                for code in (old_item.code, twin.code):
                    if code not in evidence:
                        evidence.append(code)

        pieces = [
            f"设备序号由 {old_last.seq} 回到 {first.seq}，设备时刻由 {fmt(old_last.device)} 回拨到 {fmt(first.device)}，判定为设备重启，新时段序号重新起算。"
        ]
        if collisions:
            head = collisions[0]
            pieces.append(
                f"{head['新包']}（新时段序号{head['新包序号']}，读数{head['新包读数']}）与"
                f"{head['旧包']}（重启前序号{head['旧包序号']}，读数{head['旧包读数']}）设备时刻同为 {head['设备时刻']}，"
                "同一个设备时刻出现两条不同压力读数"
            )
            if len(collisions) > 1:
                pieces.append(
                    f"，新旧时段设备时刻完全重叠，共 {len(collisions)} 个设备时刻各出现两条不同读数（明细见“同刻双读数”）"
                )
            pieces.append("。")
        boundary_start = old_last.corrected or old_last.device
        boundary_end = first.corrected or first.device
        if same_minute:
            pieces.append(
                "重启前最后一个包与"
                f"新时段首个包（{first.code}）接入同为 {first.ingest.strftime('%H:%M')}，接入时间只精确到分钟，"
                "两个包在交界处的先后无法证实。"
            )
            # 接入时间只到分钟时，交界区间的起点最晚可能落在该分钟末尾，
            # 终点最早可能落在该分钟开头；不假设旧包在前，按最保守的边界扣除。
            boundary_start = old_last.ingest.replace(second=59)
            boundary_end = first.ingest.replace(second=0)
            if boundary_end < boundary_start:
                boundary_start, boundary_end = boundary_end, boundary_start
        pieces.append(
            f"旧时段序号 1~{old_last.seq - 1} 可按原时间交付；交界处序号 {old_last.seq} 与"
            f"新时段序号 {first.seq} 待现场核对；新时段自序号 2 起只能按推定修正时间使用。"
        )
        basis = "".join(pieces)
        detail = self._base_detail(
            source,
            "重启交界",
            sample_range=f"{old_last.code} / {first.code}",
            device_range=f"{fmt(old_last.device)} / {fmt(first.device)}",
            ingest_range=f"{old_last.ingest.strftime('%H:%M')} / {first.ingest.strftime('%H:%M')}",
            basis=basis,
            evidence=evidence,
            usability=USABLE_BLOCK,
            action="旧时段与新时段分开交付；交界区间交下一班现场核对，不合并、不重复计入",
            start=old_last.corrected or old_last.device,
            end=first.corrected or first.device,
            expected=period,
            observed=int((first.ingest - old_last.ingest).total_seconds()),
            deviation=0,
            offset=first.offset,
            corrected_range="",
            affected=2,
        )
        deliverable_last = next(
            (item for item in previous_epoch if item.seq == old_last.seq - 1),
            None,
        )
        detail["旧时段可交付至样本"] = deliverable_last.code if deliverable_last else ""
        detail["旧时段可交付至序号"] = deliverable_last.seq if deliverable_last else 0
        detail["交界样本列表"] = [old_last.code, first.code]
        detail["同刻双读数"] = collisions
        detail["交界起点"] = fmt(boundary_start)
        detail["交界终点"] = fmt(boundary_end)
        detail["交界可信"] = same_minute
        return detail

    def _statistics(self, details: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for detail in details:
            key = str(detail.get("类型"))
            result[key] = result.get(key, 0) + 1
        result["待核对"] = sum(1 for detail in details if detail.get("pending"))
        return result

    def _replace_details(self, source_code: str, details: list[dict[str, Any]]) -> None:
        """整体替换某个来源的漂移明细，避免重复分析时把同一条明细计数两次。"""
        rows = store.rows(DETAIL_MODULE)
        kept = [row for row in rows if row.get("来源编号") != source_code]
        next_id = max((int(row.get("id", 0)) for row in kept), default=0) + 1
        numbered: list[dict[str, Any]] = []
        for index, detail in enumerate(details, start=1):
            detail["id"] = next_id
            detail["明细编号"] = f"DRF-{source_code.split('-')[-1]}-{index:03d}"
            numbered.append(detail)
            next_id += 1
        rows[:] = kept + numbered

    def summary(self) -> dict[str, Any]:
        sources = store.rows(SOURCE_MODULE)
        details = store.rows(DETAIL_MODULE)
        per_source = []
        for source in sources:
            code = source.get("来源编号")
            rows = [row for row in details if row.get("来源编号") == code]
            per_source.append({
                "来源编号": code,
                "来源名称": source.get("来源名称"),
                "采样周期秒": source.get("采样周期秒"),
                "明细数": len(rows),
                "待核对": sum(1 for row in rows if row.get("pending")),
                "类型分布": self._statistics(rows),
            })
        return {
            "来源数": len(sources),
            "样本数": len(store.rows(SAMPLE_MODULE)),
            "明细数": len(details),
            "待核对": sum(1 for row in details if row.get("pending")),
            "按来源": per_source,
        }


clock_drift_service = ClockDriftService()
