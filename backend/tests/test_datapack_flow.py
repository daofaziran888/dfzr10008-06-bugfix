"""数据模型封装链路回归：字段映射编辑、乐观并发、发布快照与接口契约回放。

直接用标准库运行，不引入新依赖：
    .venv/bin/python tests/test_datapack_flow.py

覆盖 DATA-0002（order_id→order_no、amount→gmv 改为 amount→net_gmv）场景：
1. 列表按封装编号 + 字段映射过滤，只保留命中的模型封装行；
2. 详情读取草稿工作副本，编辑保存后展示 net_gmv 且版本号 +1；
3. 两个同基线并发编辑只允许一个成功（乐观锁 409）；
4. 发布冻结快照，已发布接口始终按旧快照回放，历史快照只读；
5. 废弃封装只读，删除封装后历史快照仍可回放。
"""
from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.apigen import ApigenService  # noqa: E402
from app.services.datapack import DatapackService, ServiceError  # noqa: E402
from app.store import store  # noqa: E402


class DatapackFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        store.reset()
        self.dp = DatapackService()
        self.api = ApigenService()

    def test_list_filter_by_code_and_field_mapping(self) -> None:
        rows, total = self.dp.list_entries(keyword="DATA-0002", field_mapping="gmv")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["封装编号"], "DATA-0002")
        self.assertIn("amount→gmv", rows[0]["字段映射"])
        # 换成 net_gmv 初始没有命中
        self.assertEqual(self.dp.list_entries(field_mapping="net_gmv")[1], 0)

    def test_detail_distinguishes_draft_and_snapshot(self) -> None:
        detail = self.dp.get_entry(2)
        self.assertEqual(detail["字段映射"], "order_id→order_no, amount→gmv")
        self.assertEqual(detail["版本号"], 1)
        self.assertEqual(detail["最新快照号"], 1)
        self.assertEqual(len(detail["发布历史"]), 1)
        self.assertTrue(detail["发布历史"][0]["只读"])

    def test_edit_persists_net_gmv_and_bumps_version(self) -> None:
        entry = self.dp.update_entry(
            2, {"字段映射": "order_id→order_no, amount→net_gmv"}, expected_version=1
        )
        self.assertEqual(entry["version"], 2)
        # 已封装的工作副本被改动后回到草稿，等待重新发布
        self.assertEqual(entry["status"], "草稿")
        detail = self.dp.get_entry(2)
        self.assertIn("net_gmv", detail["字段映射"])
        self.assertEqual(detail["版本号"], 2)

    def test_concurrent_edits_only_one_succeeds(self) -> None:
        outcomes: list[bool] = []

        def edit() -> None:
            try:
                self.dp.update_entry(
                    2, {"字段映射": "order_id→order_no, amount→net_gmv"}, expected_version=1
                )
                outcomes.append(True)
            except ServiceError as error:
                self.assertEqual(error.status_code, 409)
                outcomes.append(False)

        threads = [threading.Thread(target=edit) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(self.dp.get_entry(2)["版本号"], 2)

    def test_stale_baseline_rejected_with_409(self) -> None:
        self.dp.update_entry(2, {"字段映射": "a→b"}, expected_version=1)
        with self.assertRaises(ServiceError) as ctx:
            self.dp.update_entry(2, {"字段映射": "c→d"}, expected_version=1)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_published_contract_keeps_old_snapshot_after_draft_edit(self) -> None:
        self.dp.update_entry(
            2, {"字段映射": "order_id→order_no, amount→net_gmv"}, expected_version=1
        )
        # 接口配置 APIG-0001 绑定快照 1，继续按旧 gmv 口径回放
        contract = self.api.read_contract(1)
        snapshot = contract["契约"]["契约快照"]
        self.assertEqual(contract["绑定快照号"], 1)
        self.assertIn("amount→gmv", snapshot["字段映射"])
        self.assertNotIn("net_gmv", snapshot["字段映射"])
        # 契约端点指定旧快照号同样回放旧口径
        old = self.dp.get_contract("DATA-0002", snapshot_no=1)
        self.assertIn("amount→gmv", old["契约快照"]["字段映射"])
        self.assertTrue(old["只读"])

    def test_republish_freezes_new_snapshot_but_keeps_history(self) -> None:
        self.dp.update_entry(
            2, {"字段映射": "order_id→order_no, amount→net_gmv"}, expected_version=1
        )
        entry, _ = self.dp.run_action(2, "发布封装")
        self.assertEqual(entry["status"], "已发布")
        snapshots = self.dp.list_snapshots(2)
        self.assertEqual(len(snapshots), 2)
        self.assertIn("amount→gmv", snapshots[0]["字段映射"])
        self.assertIn("net_gmv", snapshots[1]["字段映射"])
        self.assertTrue(all(snap["只读"] for snap in snapshots))

    def test_deprecated_and_deleted_are_readonly_but_history_replays(self) -> None:
        self.dp.run_action(2, "废弃封装")
        with self.assertRaises(ServiceError) as ctx:
            self.dp.update_entry(2, {"字段映射": "x→y"}, expected_version=1)
        self.assertEqual(ctx.exception.status_code, 409)
        self.dp.run_action(2, "删除封装")
        self.assertIsNone(self.dp.get_entry(2))
        # 记录删除后，历史快照仍可回放
        replay = self.dp.get_contract("DATA-0002", snapshot_no=1)
        self.assertIn("amount→gmv", replay["契约快照"]["字段映射"])

    def test_returned_rows_are_detached_from_store(self) -> None:
        rows, _ = self.dp.list_entries(keyword="DATA-0001")
        rows[0]["字段映射"] = "HACKED"
        self.assertNotEqual(self.dp.get_by_code("DATA-0001")["字段映射"], "HACKED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
