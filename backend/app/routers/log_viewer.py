"""采集日志接口：维护日志条目，覆盖开始处理、标记已处理、忽略日志等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.log_viewer import LogViewerService
from app.services.timing_event import timing_event_service

router = APIRouter(prefix="/api/log_viewer", tags=["采集日志"])

service = LogViewerService()

LIST_FIELDS = ["日志编号", "关联任务", "日志级别", "记录时间", "日志内容", "错误堆栈", "处理状态", "处理人"]
STATUSES = ["未处理", "处理中", "已处理", "已忽略"]


@router.get("/timing-events")
def timing_events(
    source_code: str | None = Query(default=None, description="按受影响来源过滤"),
    pending_only: bool = Query(default=False, description="只看待现场核对"),
    size: int = 100,
) -> dict[str, Any]:
    """采集日志消费采样时钟漂移明细后产生的计时事件，按受影响来源与采样区间汇总。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = timing_event_service.list_events(
        source_code=source_code, pending_only=pending_only, page=1, size=size
    )
    return {
        "module": "timing_event",
        "total": total,
        "summary": timing_event_service.summary(),
        "items": items,
    }


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出采集日志清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "log_viewer", "total": total, "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按日志编号检索"),
    status: str | None = Query(default=None, description="未处理、处理中、已处理、已忽略"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按日志编号与状态过滤采集日志列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条日志条目明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日志条目 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条日志条目，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="日志条目已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条日志条目执行开始处理、标记已处理、忽略日志；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
