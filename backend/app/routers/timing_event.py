"""计时事件接口：消费采样时钟漂移明细，按受影响来源和采样区间生成可核对事件。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, PageResult
from app.services.timing_event import EVENT_RULES, timing_event_service

router = APIRouter(prefix="/api/timing_event", tags=["计时事件"])


@router.get("/summary")
def summary() -> dict[str, Any]:
    """计时事件总览：按类型与可用性统计。"""
    data = timing_event_service.summary()
    data["类型说明"] = {kind: rule["计时影响"] for kind, rule in EVENT_RULES.items()}
    return data


@router.get("/export")
def export_events() -> dict[str, Any]:
    """导出全部计时事件。"""
    items, total = timing_event_service.list_events(page=1, size=10000)
    return {"module": "timing_event", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_events(
    source_code: str | None = Query(default=None, description="按来源编号过滤"),
    event_type: str | None = Query(default=None, description="按时钟跳变、缓存补送、重启交界、无法断定、采样缺口过滤"),
    usability: str | None = Query(default=None, description="允许、仅参考、不允许"),
    pending_only: bool = Query(default=False, description="只看待现场核对"),
    keyword: str | None = Query(default=None, description="按事件编号、依据样本或采样区间检索"),
    page: int = 1,
    size: int = 50,
) -> PageResult[dict]:
    """按来源、类型和可用性过滤计时事件。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = timing_event_service.list_events(
        source_code=source_code,
        event_type=event_type,
        usability=usability,
        pending_only=pending_only,
        keyword=keyword,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{event_id}", response_model=dict)
def get_event(event_id: int) -> dict:
    """读取单条计时事件，包含判定依据与依据样本。"""
    event = timing_event_service.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"计时事件 {event_id} 不存在或已归档")
    return event


@router.post("/generate", response_model=ActionResult)
def generate_all() -> ActionResult:
    """从最新漂移明细重新生成全部计时事件。"""
    results = timing_event_service.generate_all()
    count = sum(int(item.get("事件数", 0)) for item in results)
    pending = sum(int(item.get("待核对", 0)) for item in results)
    return ActionResult(ok=True, message=f"已生成 {count} 条计时事件，其中 {pending} 条待现场核对")


@router.post("/sources/{source_code}/generate", response_model=ActionResult)
def generate_source(source_code: str) -> ActionResult:
    """从单个来源的漂移明细生成计时事件。"""
    result = timing_event_service.generate_source(source_code)
    return ActionResult(
        ok=True,
        message=f"{source_code} 已生成 {result['事件数']} 条计时事件，其中 {result['待核对']} 条待现场核对",
    )
