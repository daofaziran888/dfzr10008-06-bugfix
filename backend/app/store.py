"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

除业务表外，仓库还维护一张「发布快照表」(_snapshots)：模型封装发布时把当时的
字段映射、聚合规则等口径冻结下来，接口契约只读引用快照，不随草稿编辑而变化。
所有写操作都走同一把锁，配合记录上的 version 字段实现乐观并发控制。
"""
from __future__ import annotations

import copy
import threading
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [copy.deepcopy(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 快照按 (模块, 业务编号) 归组，列表按发布时间先后排列
        self._snapshots: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._bootstrap_snapshots()

    def _bootstrap_snapshots(self) -> None:
        """给种子里「已发布/已封装且曾发布过」的模型封装补一条历史冻结快照。

        这样服务一启动就能演示完整链路：草稿可改成 net_gmv，而已发布接口仍按
        快照里冻结的旧 gmv 口径回放。
        """
        snapshot_fields = ["原始数据源", "输出结构", "字段映射", "聚合规则", "缓存策略"]
        for row in self._tables.get("datapack", []):
            code = str(row.get("封装编号", ""))
            if code not in {"DATA-0002", "DATA-0003"}:
                continue
            snapshot = {
                "snapshot_no": 1,
                "封装版本": int(row.get("version", 1)),
                "封装编号": code,
                "published_at": "2026-11-01T09:00:00+00:00",
                "只读": True,
            }
            for field in snapshot_fields:
                snapshot[field] = copy.deepcopy(row.get(field))
            self._snapshots.setdefault(("datapack", code), []).append(snapshot)

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    @staticmethod
    def clone(value: Any) -> Any:
        """对外返回数据前先深拷贝，避免调用方改到仓库内部对象（快照被串改的常见根因）。"""
        return copy.deepcopy(value)

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_code(self, module: str, code_field: str, code: str) -> dict[str, Any] | None:
        """按业务编号（如封装编号 DATA-0002）定位记录，编号与主键 id 不是一回事。"""
        for row in self.rows(module):
            if str(row.get(code_field, "")) == code:
                return row
        return None

    def add_snapshot(self, module: str, code: str, snapshot: dict[str, Any]) -> None:
        """追加一条不可变发布快照；历史快照只追加、不改写，保证已发布接口可回放。"""
        key = (module, code)
        self._snapshots.setdefault(key, []).append(copy.deepcopy(snapshot))

    def snapshots(self, module: str, code: str) -> list[dict[str, Any]]:
        """返回某条记录的全部发布快照（深拷贝），最新发布的在列表末尾。"""
        return copy.deepcopy(self._snapshots.get((module, code), []))

    def latest_snapshot(self, module: str, code: str) -> dict[str, Any] | None:
        """返回最新一条冻结快照；没有发布过时返回 None。"""
        group = self._snapshots.get((module, code), [])
        return copy.deepcopy(group[-1]) if group else None

    def reset(self) -> None:
        """恢复初始示例数据并重建启动快照，主要给测试用。"""
        with self._lock:
            self._tables = {
                name: [copy.deepcopy(row) for row in rows] for name, rows in SEED_ROWS.items()
            }
            self._snapshots = {}
            self._bootstrap_snapshots()

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
