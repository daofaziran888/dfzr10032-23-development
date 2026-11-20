"""采样时钟漂移 → 计时事件 → 交班记录 的回归测试。

运行：cd backend && python3 -m pytest tests -q
"""
from __future__ import annotations

import pytest

from app.seed_clock import CLOCK_SEED_ROWS
from app.store import store


@pytest.fixture(autouse=True)
def fresh_store():
    """每个用例都从示例数据重建内存仓库，避免用例之间互相污染。"""
    store._tables = {
        name: [dict(row) for row in rows]
        for name, rows in {**CLOCK_SEED_ROWS}.items()
    }
    from app.services.clock_drift import clock_drift_service

    clock_drift_service.bootstrap()
    from app.services.timing_event import timing_event_service

    timing_event_service.generate_all()
    yield store


def details_of(source: str) -> list[dict]:
    return [row for row in store.rows("clock_drift_detail") if row["来源编号"] == source]


def samples_of(source: str) -> list[dict]:
    from app.services.clock_drift import clock_drift_service

    return clock_drift_service.preview_source(source)["样本"]


def test_clock_jump_is_corrected_and_backfill_not_moved():
    details = details_of("SRC-PRESS-A")
    kinds = {row["类型"] for row in details}
    assert kinds == {"时钟跳变", "缓存补送"}

    jump = next(row for row in details if row["类型"] == "时钟跳变")
    assert jump["时钟偏移秒"] == 35
    assert jump["修正时间可用性"] == "允许"

    backfill = next(row for row in details if row["类型"] == "缓存补送")
    assert backfill["样本范围"] == "RD-A-010"
    assert "不按接入顺序改写时间" in backfill["判定依据"]

    # 补送包的修正时刻仍按设备序号落在 10:09，不按接入顺序挪到 10:11 之后。
    corrected = {row["读数编号"]: row["修正时刻"] for row in samples_of("SRC-PRESS-A")}
    assert corrected["RD-A-010"] == "2026-09-01T10:09:00"
    assert corrected["RD-A-011"] == "2026-09-01T10:10:00"


def test_restart_seam_keeps_both_readings_and_flags_minute_precision():
    details = details_of("SRC-PRESS-B")
    seam = next(row for row in details if row["类型"] == "重启交界")
    assert seam["修正时间可用性"] == "不允许"
    assert seam["同刻双读数"]
    twin = seam["同刻双读数"][0]
    assert twin["旧包"] == "RD-B-012" and twin["旧包读数"] == 3.16
    assert twin["新包"] == "RD-B-006" and twin["新包读数"] == 4.1
    assert seam["交界可信"] is True
    assert seam["交界起点"] == "2026-09-01T14:06:00"

    # 重启后整段只能按推定时间参考，不能当作已确认交付。
    usability = {row["读数编号"]: row["修正时间可用性"] for row in samples_of("SRC-PRESS-B")}
    assert usability["RD-B-006"] == "修正时间仅可作参考"
    assert usability["RD-B-011"] == "修正时间仅可作参考"
    assert usability["RD-B-005"] == "允许按修正时间继续使用"


def test_ambiguous_interval_blocks_following_samples():
    details = details_of("SRC-PRESS-C")
    ambiguous = next(row for row in details if row["类型"] == "无法断定")
    assert ambiguous["修正时间可用性"] == "不允许"
    usability = {row["读数编号"]: row["修正时间可用性"] for row in samples_of("SRC-PRESS-C")}
    assert usability["RD-C-005"] == "不允许按修正时间使用"
    assert usability["RD-C-008"] == "不允许按修正时间使用"


def test_gap_does_not_merge_neighbouring_ranges():
    details = details_of("SRC-PRESS-D")
    assert {row["类型"] for row in details} == {"缓存补送", "采样缺口"}
    gap = next(row for row in details if row["类型"] == "采样缺口")
    assert gap["修正时间可用性"] == "不允许"


def test_timing_events_carry_basis_and_usability():
    events = store.rows("timing_event")
    assert len(events) == 7
    for event in events:
        assert event["依据样本"]
        assert event["判定依据"]
        assert event["允许继续使用"] in {"允许", "仅参考", "不允许"}
        assert bool(event["允许按修正时间继续使用"]) is (event["允许继续使用"] == "允许")

    seam = next(event for event in events if event["类型"] == "重启交界")
    assert seam["待现场核对"] is True
    assert "不得与新时段合并计入" in seam["核对提示"]
    assert "RD-B-012" in seam["依据样本"] and "RD-B-006" in seam["依据样本"]


def test_handover_splits_deliverable_and_pending_without_overlap():
    from app.services.handover import handover_service

    preview = handover_service.preview()
    intervals = preview["区间"]
    deliverable = [item for item in intervals if item["区间类型"] == "可交付"]
    pending = [item for item in intervals if item["区间类型"] == "待核对"]
    assert deliverable and pending

    # 同一来源内，待核对区间不与可交付区间时间重叠。
    for block in pending:
        for item in deliverable:
            if item["来源编号"] != block["来源编号"]:
                continue
            assert not (block["区间起点"] < item["区间终点"] and item["区间起点"] < block["区间终点"])

    # 重启交界的可交付区间止于旧时段序号 5，不把交界包算进去。
    b_deliverable = [item for item in deliverable if item["来源编号"] == "SRC-PRESS-B"]
    assert len(b_deliverable) == 1
    assert "RD-B-012" not in b_deliverable[0]["依据样本"]


def test_confirmed_ranges_are_not_counted_twice():
    from app.services.handover import handover_service

    first, message = handover_service.create_record({"班次": "白班 08:00-20:00"})
    assert first is not None and "待核对" in message
    assert first["可交付区间数"] >= 4

    second, message2 = handover_service.create_record({"班次": "白班 08:00-20:00"})
    assert second is not None
    assert second["可交付区间数"] == 0
    assert second["待核对区间数"] == first["待核对区间数"]

    # 只剩待核对区间时仍可交班，但可交付区间必须为 0，历史记录里的样本不重复出现。
    third, message3 = handover_service.create_record({"班次": "夜班 20:00-08:00"})
    assert third is not None
    assert third["可交付区间数"] == 0
    assert "待核对" in message3

    # 历史记录里的可交付区间不重复，且样本不重复出现。
    codes = []
    for row in store.rows("handover_interval"):
        if row["区间类型"] == "可交付" and row["交班编号"] == first["交班编号"]:
            codes.extend(row["依据样本"].split("、"))
    assert len(codes) == len(set(codes))


def test_field_resolution_does_not_recount_neighbours():
    from app.services.handover import handover_service

    record, _ = handover_service.create_record({"班次": "白班 08:00-20:00"})
    pending = [row for row in store.rows("handover_interval") if row["区间类型"] == "待核对"]
    seam = next(row for row in pending if row["依据事件"] == "TME-B-002")

    resolved, message = handover_service.resolve_interval(
        seam["id"], {"结论": "确认可交付", "核对人": "王工", "核对说明": "现场确认旧包先落盘"}
    )
    assert resolved is not None and "确认可交付" in message
    assert resolved["区间类型"] == "可交付"

    preview = handover_service.preview()
    resumed = [
        item for item in preview["区间"]
        if item["来源编号"] == "SRC-PRESS-B" and item["区间类型"] != "待核对"
    ]
    # 重启后的仅参考区间随第一份记录移交过，第二次预览里标成“已移交”而不重复列出；
    # 现场确认的交界区间则并入已计入范围。
    assert all(item.get("已计入") or item.get("已移交") for item in resumed)
    assert any(item.get("已移交") and item["区间类型"] == "仅参考" for item in resumed)
    assert any(item.get("已计入") and "RD-B-012" in item["依据样本"] for item in resumed)

    seen = []
    for item in resumed:
        seen.extend(item["依据样本"].split("、"))
    assert len(seen) == len(set(seen))
