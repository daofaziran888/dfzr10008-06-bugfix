"""数据模型封装业务规则：状态流转、字段校验、发布快照与乐观并发控制都收在这里。

核心口径：
- 模型封装上的字段映射、聚合规则是「草稿工作副本」，编辑保存只改工作副本并累加版本号；
- 发布封装时把当时的工作副本深拷贝成一条不可变快照，接口契约只读快照、不随草稿变化；
- 编辑接口必须携带 expected_version 做乐观锁，两个并发编辑只有基于最新版本的那个能成功；
- 历史发布快照只追加、不改写，已发布接口因此始终能按旧快照回放。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

MODULE = "datapack"
CODE_FIELD = "封装编号"
REQUIRED_FIELDS = ["封装编号", "封装名称", "原始数据源"]
# 允许通过编辑接口修改的字段；封装编号是快照归属键，一经创建不可修改
EDITABLE_FIELDS = ["封装名称", "原始数据源", "输出结构", "字段映射", "聚合规则", "缓存策略"]
SNAPSHOT_FIELDS = ["原始数据源", "输出结构", "字段映射", "聚合规则", "缓存策略"]
STATUS_ORDER = ["草稿", "已封装", "已发布", "已废弃"]
# 动作 → (允许的前置状态, 执行后的目标状态)
ACTION_RULES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "发布封装": (("草稿", "已封装"), "已发布"),
    "废弃封装": (("已封装", "已发布"), "已废弃"),
    "删除封装": (("已废弃",), None),
}
NEGATIVE_ACTIONS = ["废弃封装"]


class ServiceError(Exception):
    """业务校验失败：路由层统一翻译成 HTTP 错误，业务判断不泄漏到路由里。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DatapackService:
    # ---------------------------------------------------------------- 列表/详情
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        field_mapping: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """按封装编号与字段映射等条件过滤；多条件之间是「与」，只返回命中的那几行。"""
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get(CODE_FIELD, ""))]
        if field_mapping:
            rows = [row for row in rows if field_mapping in str(row.get("字段映射", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        # 深拷贝后再出仓：列表页拿到的行被组件改写也不会污染仓库里的工作副本
        return [store.clone(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = store.clone(entry)
        history = store.snapshots(MODULE, str(entry.get(CODE_FIELD, "")))
        detail["版本号"] = entry.get("version", 1)
        detail["最新快照号"] = history[-1]["snapshot_no"] if history else None
        detail["发布历史"] = history
        return detail

    def get_by_code(self, code: str) -> dict[str, Any] | None:
        entry = store.find_by_code(MODULE, CODE_FIELD, code)
        return store.clone(entry) if entry is not None else None

    # ---------------------------------------------------------------- 新建/编辑
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        code = str(values[CODE_FIELD]).strip()
        if any(str(row.get(CODE_FIELD, "")) == code for row in rows):
            raise ServiceError(409, f"封装编号 {code} 已存在，不能重复登记")
        with store.lock:
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            for field in EDITABLE_FIELDS:
                if field not in REQUIRED_FIELDS and values.get(field) is not None:
                    entry[field] = values.get(field)
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            entry["version"] = 1
            rows.append(entry)
            return store.clone(entry), []

    def update_entry(
        self,
        entry_id: int,
        values: dict[str, Any],
        *,
        expected_version: int | None,
    ) -> dict[str, Any]:
        """保存草稿编辑：乐观锁校验版本，成功后工作副本更新、版本号 +1。

        已发布/已封装的封装被再次编辑时回到「草稿」，必须重新发布才会产生新快照；
        已废弃的封装只读，拒绝编辑。
        """
        if expected_version is None:
            raise ServiceError(400, "缺少 expected_version，无法确认编辑基线，请刷新详情后重试")
        with store.lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                raise ServiceError(404, f"模型封装 {entry_id} 不存在或已归档")
            if entry.get("status") == "已废弃":
                raise ServiceError(409, "模型封装已废弃，内容只读，不能继续编辑")
            current_version = int(entry.get("version", 1))
            if int(expected_version) != current_version:
                raise ServiceError(
                    409,
                    f"版本已过期：当前最新版本为 v{current_version}，"
                    f"本次编辑基于 v{expected_version}，请刷新后基于最新版本重新提交",
                )
            # 只接受白名单字段；封装编号等不可变字段即便被传上来也不会落库
            changed = {
                field: values[field]
                for field in EDITABLE_FIELDS
                if field in values and str(values[field] or "").strip() != ""
            }
            if not changed:
                raise ServiceError(400, "没有可保存的字段变更")
            entry.update(changed)
            entry["version"] = current_version + 1
            # 已封装/已发布的工作副本一旦被改动就回到草稿，必须重新发布才会产生新快照；
            # 旧快照不受影响，接口契约继续读旧快照
            if entry.get("status") in ("已封装", "已发布"):
                entry["status"] = "草稿"
            entry["pending"] = entry["status"] != "已废弃"
            return store.clone(entry)

    # ---------------------------------------------------------------- 动作/快照
    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"模型封装 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据模型封装可执行范围"
        allowed_from, target = ACTION_RULES[action]
        with store.lock:
            if entry.get("status") not in allowed_from:
                return None, f"当前状态「{entry.get('status')}」不允许执行{action}"
            if action == "删除封装":
                store.rows(MODULE).remove(entry)
                return {"id": entry_id, "deleted": True}, "模型封装已删除，历史发布快照仍保留备查"
            if action == "发布封装":
                self._freeze_snapshot(entry)
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return store.clone(entry), f"模型封装已{action}，字段映射与聚合规则已冻结为新快照"

    def _freeze_snapshot(self, entry: dict[str, Any]) -> dict[str, Any]:
        """把发布瞬间的工作副本冻结成不可变快照；只追加，不改写历史快照。"""
        code = str(entry.get(CODE_FIELD, ""))
        history = store.snapshots(MODULE, code)
        snapshot = {
            "snapshot_no": len(history) + 1,
            "封装版本": int(entry.get("version", 1)),
            CODE_FIELD: code,
            "published_at": _now(),
            "只读": True,
        }
        for field in SNAPSHOT_FIELDS:
            snapshot[field] = store.clone(entry.get(field))
        store.add_snapshot(MODULE, code, snapshot)
        return store.clone(snapshot)

    def list_snapshots(self, entry_id: int) -> list[dict[str, Any]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise ServiceError(404, f"模型封装 {entry_id} 不存在或已归档")
        return store.snapshots(MODULE, str(entry.get(CODE_FIELD, "")))

    def get_snapshot(self, entry_id: int, snapshot_no: int) -> dict[str, Any]:
        for snapshot in self.list_snapshots(entry_id):
            if int(snapshot["snapshot_no"]) == snapshot_no:
                return snapshot
        raise ServiceError(404, f"快照号 {snapshot_no} 不存在，历史发布内容只读且不会被改写")

    # ---------------------------------------------------------------- 接口契约
    def get_contract(self, code: str, snapshot_no: int | None = None) -> dict[str, Any]:
        """接口配置读取契约：永远读冻结快照，不直接读草稿工作副本。

        snapshot_no 为空时取最新快照；指定快照号时按历史快照回放（已发布接口的用法）。
        """
        entry = store.find_by_code(MODULE, CODE_FIELD, code)
        history = store.snapshots(MODULE, code)
        if not history:
            if entry is None:
                raise ServiceError(404, f"模型封装 {code} 不存在或已归档，也没有历史快照")
            raise ServiceError(409, f"模型封装 {code} 尚未发布，没有冻结快照可供接口契约读取")
        if snapshot_no is None:
            snapshot = history[-1]
        else:
            snapshot = next(
                (item for item in history if int(item["snapshot_no"]) == int(snapshot_no)),
                None,
            )
            if snapshot is None:
                raise ServiceError(404, f"模型封装 {code} 不存在快照号 {snapshot_no}")
        return {
            CODE_FIELD: code,
            "草稿版本号": entry.get("version", 1) if entry is not None else None,
            "封装状态": entry.get("status") if entry is not None else "已删除（仅存历史快照）",
            "契约快照号": snapshot["snapshot_no"],
            "契约快照": snapshot,
            "只读": True,
        }
