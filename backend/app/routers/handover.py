"""交班记录接口：用计时事件划定可交付的连续观测区间，并交接待现场核对区间。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.handover import SHIFTS, handover_service

router = APIRouter(prefix="/api/handover", tags=["交班记录"])


@router.get("/summary")
def summary() -> dict[str, Any]:
    """交班总览：交班记录数、可交付与待核对区间数。"""
    data = handover_service.summary()
    data["可选班次"] = SHIFTS
    return data


@router.get("/preview", response_model=dict)
def preview(source_code: str | None = Query(default=None, description="只预览某个来源")) -> dict[str, Any]:
    """按当前计时事件预览可交付 / 仅参考 / 待核对区间，不写入交班记录。"""
    return handover_service.preview(source_code)


@router.get("/records", response_model=PageResult[dict])
def list_records(
    keyword: str | None = Query(default=None, description="按交班编号或摘要检索"),
    status: str | None = Query(default=None, description="待交接、已完成"),
    source_code: str | None = Query(default=None, description="按涉及来源过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """交班记录列表。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = handover_service.list_records(
        keyword=keyword, status=status, source_code=source_code, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/records/{record_id}", response_model=dict)
def get_record(record_id: int) -> dict:
    """读取单条交班记录，含可交付清单与待核对清单。"""
    record = handover_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"交班记录 {record_id} 不存在或已归档")
    # 清单从区间表实时汇总：现场核对回填后，记录详情里的待核对清单会同步减少，
    # 不会停留在生成时的快照上。
    record = dict(record)
    rows = handover_service.list_intervals(handover_code=str(record.get("交班编号")), size=10000)[0]
    record["待核对清单"] = [handover_service._brief(row) for row in rows if row.get("区间类型") == "待核对"]
    record["可交付清单"] = [
        handover_service._brief(row)
        for row in rows
        if row.get("区间类型") == "可交付" and row.get("status") != "待核对"
    ]
    record["仅参考清单"] = [handover_service._brief(row) for row in rows if row.get("区间类型") == "仅参考"]
    record["待核对区间数"] = len(record["待核对清单"])
    record["可交付区间数"] = len(record["可交付清单"])
    record["仅参考区间数"] = len(record["仅参考清单"])
    record["pending"] = bool(record["待核对清单"])
    record["status"] = "待交接" if record["待核对清单"] else "已完成"
    return record


@router.post("/records", response_model=ActionResult)
def create_record(payload: EntryPayload) -> ActionResult:
    """生成交班记录：已确认的时段不会重复计入，待核对区间交给下一班。"""
    record, message = handover_service.create_record(payload.values)
    return ActionResult(ok=record is not None, message=message, entry=record)


@router.get("/intervals", response_model=PageResult[dict])
def list_intervals(
    handover_code: str | None = Query(default=None, description="按交班编号过滤"),
    source_code: str | None = Query(default=None, description="按来源编号过滤"),
    kind: str | None = Query(default=None, description="可交付、仅参考、待核对"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """交班记录里的观测区间明细。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = handover_service.list_intervals(
        handover_code=handover_code, source_code=source_code, kind=kind, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/intervals/{interval_id}", response_model=dict)
def get_interval(interval_id: int) -> dict:
    """读取单段观测区间。"""
    interval = handover_service.interval_detail(interval_id)
    if interval is None:
        raise HTTPException(status_code=404, detail=f"观测区间 {interval_id} 不存在或已归档")
    return interval


@router.post("/intervals/{interval_id}/resolve", response_model=ActionResult)
def resolve_interval(interval_id: int, payload: EntryPayload) -> ActionResult:
    """现场核对后回填结论；回填不会把已确认的相邻时段重复计入。"""
    interval, message = handover_service.resolve_interval(interval_id, payload.values)
    return ActionResult(ok=interval is not None, message=message, entry=interval)
