"""增量管道影响分析器 — 检测需求变化，跳过未变更的 Phase。

核心思路：
1. 计算需求文档的哈希值
2. 与上次运行的 manifest 对比
3. 若需求未变 → 跳过 Phase 1（恢复缓存输出）
4. 若需求变化 → 正常执行全部 Phase

manifest 存储格式（JSON）：
    {
        "requirement_hash": "sha256hex...",
        "updated_at": "2026-07-14T12:00:00",
        "phase1_outputs": {
            "requirement_summary": "...",
            "design_scheme": "...",
            "ddl_content": "..."
        }
    }

用法:
    analyzer = ChangeAnalyzer(output_dir)
    if analyzer.should_skip_phase1(requirement):
        analyzer.restore_phase1_outputs(state)
    # ... 执行 Phase 1 ...
    analyzer.save_manifest(requirement, state)
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from ..engine.state import WorkflowState

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = ".pipeline_manifest.json"

# Phase 1 可缓存的输出字段
_PHASE1_CACHED_FIELDS = (
    "requirement_summary",
    "design_scheme",
    "ddl_content",
)


class ChangeAnalyzer:
    """增量管道影响分析器。

    通过需求哈希对比，判断是否需要重跑 Phase 1。
    若需求未变，从 manifest 恢复 Phase 1 输出，跳过整个 Phase 1。
    """

    def __init__(self, output_dir: Path | None = None) -> None:
        self._output_dir = output_dir

    def _manifest_path(self) -> Path | None:
        if self._output_dir is None:
            return None
        return self._output_dir / MANIFEST_FILENAME

    @staticmethod
    def compute_requirement_hash(requirement: str) -> str:
        """计算需求文本的 SHA-256 哈希。

        对空白字符做规范化（strip + 压缩连续空行），避免仅格式变更导致的误判。
        """
        import re

        normalized = requirement.strip()
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _load_manifest(self) -> dict[str, Any] | None:
        path = self._manifest_path()
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("加载 manifest 失败: %s", e)
            return None

    def should_skip_phase1(self, requirement: str) -> bool:
        """判断是否可以跳过 Phase 1。

        条件：
        1. manifest 存在且可读
        2. 需求哈希匹配（需求未变）
        3. manifest 包含完整的 Phase 1 输出
        """
        manifest = self._load_manifest()
        if manifest is None:
            return False

        current_hash = self.compute_requirement_hash(requirement)
        stored_hash = manifest.get("requirement_hash", "")

        if current_hash != stored_hash:
            logger.info("需求已变更，需重跑 Phase 1")
            return False

        # 检查 Phase 1 输出是否完整
        phase1_outputs = manifest.get("phase1_outputs", {})
        if not phase1_outputs.get("requirement_summary"):
            logger.info("manifest 中无完整 Phase 1 输出，需重跑")
            return False

        logger.info("需求未变更，可跳过 Phase 1")
        return True

    def restore_phase1_outputs(self, state: WorkflowState) -> bool:
        """从 manifest 恢复 Phase 1 输出到 state。

        Returns:
            True 表示恢复成功，False 表示无可用缓存。
        """
        manifest = self._load_manifest()
        if manifest is None:
            return False

        phase1_outputs = manifest.get("phase1_outputs", {})
        restored_count = 0

        for field in _PHASE1_CACHED_FIELDS:
            value = phase1_outputs.get(field)
            if value:
                state[field] = value  # type: ignore[literal-required]
                restored_count += 1

        # 标记 Phase 1/2/3 已完成
        metadata = state.get("metadata", {})
        metadata["requirement_parsed"] = "true"
        metadata["design_done"] = "true"
        metadata["ddl_done"] = "true" if phase1_outputs.get("ddl_content") else "false"
        metadata["incremental_skip"] = "true"
        state["metadata"] = metadata

        logger.info("从 manifest 恢复 Phase 1 输出: %d 个字段", restored_count)
        return restored_count > 0

    def save_manifest(self, requirement: str, state: WorkflowState) -> None:
        """保存当前运行的 manifest 到输出目录。

        在 Phase 1 完成后调用，保存需求哈希和 Phase 1 输出。
        """
        path = self._manifest_path()
        if path is None:
            return

        phase1_outputs = {}
        for field in _PHASE1_CACHED_FIELDS:
            value = state.get(field, "")
            if value:
                phase1_outputs[field] = value

        manifest = {
            "requirement_hash": self.compute_requirement_hash(requirement),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "phase1_outputs": phase1_outputs,
        }

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("manifest 已保存: %s", path)
        except Exception as e:
            logger.warning("保存 manifest 失败: %s", e)

    @staticmethod
    def analyze_diff(old_requirement: str, new_requirement: str) -> dict[str, Any]:
        """分析需求变更的类型，用于未来细粒度影响分析。

        当前返回粗粒度分析（哪些部分变化），
        未来可用于判断只需重跑 Phase 4 而不用重跑 Phase 1。

        Returns:
            变更分析结果字典。
        """
        old_lines = set(old_requirement.strip().splitlines())
        new_lines = set(new_requirement.strip().splitlines())

        added = new_lines - old_lines
        removed = old_lines - new_lines

        return {
            "added_lines": len(added),
            "removed_lines": len(removed),
            "total_changed": len(added) + len(removed),
            "old_hash": hashlib.sha256(old_requirement.encode("utf-8")).hexdigest()[:16],
            "new_hash": hashlib.sha256(new_requirement.encode("utf-8")).hexdigest()[:16],
        }
