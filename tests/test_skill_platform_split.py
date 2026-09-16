"""Skill 层「通用规范 / 平台适配表」拆分测试。

设计思想与代码层 PlatformAdapter 协议同构：skill 正文保持通道无关
（不含任何平台实例名/专有工具名），平台专有内容（执行命令、MCP 工具、
调度平台、下游链路）集中到 references/platform_adaptation.md 映射表——
开源后接新平台只改映射文件，skill 正文零改动。

扫描豁免：platform_adaptation.md 本身是平台专有内容的指定承载文件；
frontmatter 的 allowed-tools（mcp__dp-asset-mcp__*）是技能运行配置，
不属于正文规范。
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SKILLS = _REPO / ".claude" / "skills"
_MAPPING = _SKILLS / "data-developer" / "references" / "platform_adaptation.md"

# skill 正文不得出现的平台专有字面量（新平台接入时也不该回来）
_FORBIDDEN = [
    "bdp-cli",  # BDP 命令行工具
    "bdp-asset-mcp",  # 代码层 manifest 的 MCP 键名
    "data-map",  # 内部 MCP 工具
    "edit-flow-task",  # 内部 MCP 工具
    "Doris",  # 下游 MPP 链路
    "数据质量平台",  # BDP 专有调度模块（通用措辞应为"平台 DQC 调度"）
    "BDP",  # 平台实例名（正文只谈"平台/通道"）
]


def _scanned_files() -> list[Path]:
    return [p for p in sorted(_SKILLS.rglob("*.md")) if p != _MAPPING]


class TestSkillBodiesChannelNeutral:
    def test_no_platform_specific_literals_in_skill_bodies(self):
        """skill 正文通道无关——平台专有字面量全部收敛到映射文件。"""
        offenders: list[str] = []
        for path in _scanned_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                for term in _FORBIDDEN:
                    if term in line:
                        offenders.append(f"{path.relative_to(_REPO)}:{lineno}: {term}")
        assert not offenders, "skill 正文含平台专有字面量:\n" + "\n".join(offenders)

    def test_mapping_file_is_the_designated_carrier(self):
        """豁免文件本身确含平台专有内容（防有人把映射文件改名后扫描面失控）。"""
        text = _MAPPING.read_text(encoding="utf-8")
        assert "bdp-cli" in text and "BDP" in text


class TestMappingFileContract:
    def test_six_verbs_mirror_platformadapter(self):
        """与代码层 PlatformAdapter 六动词同构——skill 层能力映射表。"""
        text = _MAPPING.read_text(encoding="utf-8")
        for verb in (
            "table_metadata",
            "sql_execute",
            "dqc_execute",
            "lineage",
            "task_ops",
            "artifact_search",
        ):
            assert verb in text, f"映射表缺动词 {verb}"

    def test_carries_the_extracted_platform_content(self):
        """从 skill 正文抽出的内容必须先在映射文件落位，再删正文。"""
        text = _MAPPING.read_text(encoding="utf-8")
        assert "ide execute-sql" in text  # V8 编译验证命令示例
        assert "Doris" in text  # 哑替换数据不可导入生产下游的链路警告
        assert "调度" in text  # DQC 用例接入平台调度提醒
        assert "第〇节" in text  # 能力分级探测回链 verification_checklist

    def test_notes_code_layer_counterpart(self):
        """映射文件指认代码层对应物（两层同构可对照维护）。"""
        text = _MAPPING.read_text(encoding="utf-8")
        assert "bdp_manifest.json" in text


class TestSkillsReferenceMapping:
    def test_data_developer_references_mapping(self):
        for rel in (
            ".claude/skills/data-developer/SKILL.md",
            ".claude/skills/data-developer/references/workflow.md",
            ".claude/skills/data-developer/references/verification_checklist.md",
        ):
            assert "platform_adaptation.md" in (_REPO / rel).read_text(encoding="utf-8"), rel

    def test_change_management_references_mapping(self):
        for rel in (
            ".claude/skills/change-management/SKILL.md",
            ".claude/skills/change-management/references/workflow.md",
        ):
            assert "平台通道适配映射" in (_REPO / rel).read_text(encoding="utf-8"), rel
