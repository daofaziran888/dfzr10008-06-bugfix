"""API接口生成业务规则：状态流转、字段校验与契约快照读取都收在这里。

接口配置本身只保存「引用封装编号 + 契约快照号」的指针，真正的字段映射/聚合规则
通过数据模型封装的冻结快照读取；草稿怎么改都不会影响已发布接口的回放口径。
"""
from __future__ import annotations

from typing import Any

from app.services.datapack import ServiceError
from app.store import store

MODULE = "apigen"
REQUIRED_FIELDS = ["接口编号", "接口名称", "请求方法"]
STATUS_ORDER = ["草稿", "测试中", "已发布", "已废弃"]
# 动作 → (允许的前置状态, 执行后的目标状态)
ACTION_RULES: dict[str, tuple[tuple[str, ...], str | None]] = {
    "发布接口": (("草稿", "测试中"), "已发布"),
    "废弃接口": (("已发布",), "已废弃"),
    "删除接口": (("已废弃",), None),
}
NEGATIVE_ACTIONS = ["废弃接口"]


class ApigenService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("接口编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [store.clone(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.clone(store.find(MODULE, entry_id))

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"接口配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于API接口生成可执行范围"
        allowed_from, target = ACTION_RULES[action]
        with store.lock:
            if entry.get("status") not in allowed_from:
                return None, f"当前状态「{entry.get('status')}」不允许执行{action}"
            if action == "删除接口":
                store.rows(MODULE).remove(entry)
                return {"id": entry_id, "deleted": True}, "接口配置已删除"
            if action == "发布接口":
                # 发布瞬间把指针指向模型封装当前最新冻结快照，之后草稿再改也不动这份契约
                self._pin_contract(entry)
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            return store.clone(entry), f"接口配置已{action}，契约已绑定模型封装冻结快照"

    def _pin_contract(self, entry: dict[str, Any]) -> None:
        """发布接口时绑定模型封装的最新快照号；没引用封装或封装未发布则跳过。"""
        from app.services.datapack import DatapackService

        code = entry.get("引用封装编号")
        if not code:
            return
        try:
            contract = DatapackService().get_contract(str(code))
        except ServiceError:
            return
        entry["契约快照号"] = contract["契约快照号"]

    def read_contract(self, entry_id: int) -> dict[str, Any]:
        """接口配置读取契约：按绑定的快照号回放模型封装冻结的字段映射与聚合规则。"""
        from app.services.datapack import DatapackService

        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise ServiceError(404, f"接口配置 {entry_id} 不存在或已归档")
        code = entry.get("引用封装编号")
        if not code:
            raise ServiceError(409, f"接口配置 {entry.get('接口编号')} 未引用任何模型封装，没有契约可读")
        snapshot_no = entry.get("契约快照号")
        contract = DatapackService().get_contract(
            str(code), int(snapshot_no) if snapshot_no is not None else None
        )
        return {
            "接口编号": entry.get("接口编号"),
            "引用封装编号": code,
            "绑定快照号": snapshot_no,
            "契约": contract,
            "只读": True,
        }
