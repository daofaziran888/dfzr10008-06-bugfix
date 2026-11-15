"""数据模型封装接口：维护模型封装，覆盖草稿编辑、发布快照、契约读取与状态动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.datapack import MODULE, ServiceError, DatapackService

router = APIRouter(prefix="/api/datapack", tags=["数据模型封装"])

service = DatapackService()

LIST_FIELDS = ["封装编号", "封装名称", "原始数据源", "输出结构", "字段映射", "聚合规则", "缓存策略", "封装状态"]
STATUSES = ["草稿", "已封装", "已发布", "已废弃"]


def _raise(error: ServiceError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按封装编号检索"),
    field_mapping: str | None = Query(default=None, alias="字段映射", description="按字段映射内容检索，如 net_gmv"),
    status: str | None = Query(default=None, description="草稿、已封装、已发布、已废弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按封装编号与字段映射等条件过滤；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        field_mapping=field_mapping,
        status=status,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据模型封装清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": MODULE, "total": total, "items": items}


@router.get("/contract")
def get_contract(
    code: str = Query(..., description="封装编号，如 DATA-0002"),
    snapshot_no: int | None = Query(default=None, description="指定快照号回放；不传取最新冻结快照"),
) -> dict[str, Any]:
    """接口契约读取：只读冻结快照，草稿改字段映射不影响已发布接口的回放口径。"""
    try:
        return service.get_contract(code, snapshot_no)
    except ServiceError as error:
        _raise(error)


@router.get("/{entry_id}/snapshots")
def list_snapshots(entry_id: int) -> dict[str, Any]:
    """列出一条模型封装的全部历史发布快照；快照只读，不提供修改入口。"""
    try:
        items = service.list_snapshots(entry_id)
    except ServiceError as error:
        _raise(error)
    return {"module": MODULE, "entry_id": entry_id, "total": len(items), "items": items}


@router.get("/{entry_id}/snapshots/{snapshot_no}")
def get_snapshot(entry_id: int, snapshot_no: int) -> dict[str, Any]:
    """读取指定历史快照内容，用于核对已发布接口当时冻结的口径。"""
    try:
        return service.get_snapshot(entry_id, snapshot_no)
    except ServiceError as error:
        _raise(error)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条模型封装明细（草稿工作副本 + 发布快照历史）；不存在时给出可读说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"模型封装 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条模型封装，缺字段时说明原因而不是静默丢弃。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except ServiceError as error:
        return ActionResult(ok=False, message=error.message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="模型封装已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """保存草稿编辑：必须携带 expected_version 做乐观锁，过期版本会被 409 拦下。"""
    expected_version = payload.values.pop("expected_version", None)
    try:
        expected = int(expected_version) if expected_version is not None else None
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="expected_version 必须是整数版本号")
    try:
        entry = service.update_entry(entry_id, payload.values, expected_version=expected)
    except ServiceError as error:
        return ActionResult(ok=False, message=error.message)
    return ActionResult(ok=True, message=f"草稿已保存，当前版本 v{entry['version']}", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条模型封装执行发布封装、废弃封装、删除封装；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
