"""采样时钟漂移接口：按来源关联原始读数时间、接入时点与采样间隔，计算时钟跳变并生成漂移明细。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult
from app.services.clock_drift import clock_drift_service

router = APIRouter(prefix="/api/clock_drift", tags=["采样时钟漂移"])


@router.get("/summary")
def summary() -> dict[str, Any]:
    """漂移总览：按来源统计明细数、待核对数和类型分布。"""
    return clock_drift_service.summary()


@router.get("/sources")
def list_sources(
    request: Request,
    keyword: str | None = Query(default=None, description="按来源编号或名称检索"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """采样来源与采样参数列表。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {
        k: v for k, v in request.query_params.items()
        if k not in ("keyword", "page", "size") and v.strip()
    }
    items, total = clock_drift_service.list_sources(keyword=keyword, page=page, size=size, extra=extra)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/samples")
def list_samples(
    source_code: str | None = Query(default=None, description="按来源编号过滤"),
    keyword: str | None = Query(default=None, description="按读数编号检索"),
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """原始读数：设备序号、设备时刻、接入时点、接入顺序与读数。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = clock_drift_service.list_samples(
        source_code=source_code, keyword=keyword, page=page, size=size
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/details")
def list_details(
    source_code: str | None = Query(default=None, description="按来源编号过滤"),
    anomaly: str | None = Query(default=None, description="按类型过滤：时钟跳变、缓存补送、重启交界、无法断定、采样缺口"),
    keyword: str | None = Query(default=None, description="按明细编号或依据样本检索"),
    page: int = 1,
    size: int = 50,
) -> dict[str, Any]:
    """采样时钟漂移明细：保留判定依据、依据样本和修正时间可用性。"""
    if size > 500:
        raise HTTPException(status_code=400, detail="每页最多 500 条，请缩小分页范围")
    items, total = clock_drift_service.list_details(
        source_code=source_code, keyword=keyword, anomaly=anomaly, page=page, size=size
    )
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/details/{detail_id}", response_model=dict)
def get_detail(detail_id: int) -> dict:
    """读取单条漂移明细。"""
    entry = next(
        (row for row in clock_drift_service.list_details(size=10000)[0] if int(row.get("id", 0)) == detail_id),
        None,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail=f"漂移明细 {detail_id} 不存在，请先重新计算")
    return entry


@router.get("/preview", response_model=dict)
def preview(source_code: str = Query(description="来源编号")) -> dict[str, Any]:
    """只计算不落库：查看某个来源修正后的时间轴与明细。"""
    result = clock_drift_service.preview_source(source_code)
    if "error" in result:
        raise HTTPException(status_code=404, detail=str(result["error"]))
    return result


@router.post("/analyze", response_model=ActionResult)
def analyze_all() -> ActionResult:
    """重新计算全部来源的采样时钟漂移明细。"""
    results = clock_drift_service.analyze_all()
    count = sum(int(item.get("明细数", 0)) for item in results)
    return ActionResult(ok=True, message=f"已重新计算 {len(results)} 个来源，共 {count} 条漂移明细")


@router.post("/sources/{source_code}/analyze", response_model=ActionResult)
def analyze_source(source_code: str) -> ActionResult:
    """重新计算单个来源的采样时钟漂移明细。"""
    if clock_drift_service.get_source(source_code) is None:
        raise HTTPException(status_code=404, detail=f"来源 {source_code} 不存在")
    result = clock_drift_service.analyze_source(source_code)
    return ActionResult(
        ok=True,
        message=f"{source_code} 已计算 {result.get('明细数', 0)} 条漂移明细，其中 {result.get('统计', {}).get('待核对', 0)} 条待核对",
    )
