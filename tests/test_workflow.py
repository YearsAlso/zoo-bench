"""CI 工作流的验证（tasks 5.1–5.3 与 5.7）。

**把工作流当数据来验**：它的约束——跑在原生 Linux、版本清单不硬编码、不要跨仓库写权限——都是
可以机械检查的。写在用例里，下次编辑工作流时就不会悄悄失效；而"我改的时候记得"从来不是机制。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "bench.yml"

#: 框架的版本号字面量。工作流里出现它就说明版本清单被抄了一份。
VERSION_LITERAL = re.compile(r"zoo-framework==\d")

OTHER_RUNNERS = ("windows-latest", "macos-latest", "windows-", "macos-")


def _text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _document() -> dict:
    return yaml.safe_load(_text())


def test_workflow_exists() -> None:
    assert WORKFLOW_PATH.is_file(), "CI 工作流必须存在，否则没有任何东西会跑它"


def test_workflow_does_not_build_a_platform_matrix() -> None:
    """WSL2 把跨线程唤醒放大约 3 倍，平台之间的数字不可混排——故只跑原生 Linux。"""
    text = _text().lower()
    for runner in OTHER_RUNNERS:
        assert runner not in text, f"不该出现 {runner}：平台之间的数字不可比"


def test_workflow_pins_python_313() -> None:
    text = _text()
    assert 'python-version: "3.13"' in text, "Python 版本必须显式固定，不能用默认"


def test_workflow_has_no_framework_version_literal() -> None:
    """版本清单的唯一真源是 matrix.yaml（design D8）——工作流只经 CLI 读它。"""
    found = VERSION_LITERAL.findall(_text())
    assert not found, f"工作流里出现了框架版本字面量：{found}"


def test_workflow_reads_versions_through_the_cli() -> None:
    assert "zoo-bench frameworks" in _text(), "版本清单必须经 zoo-bench frameworks 读取"


def test_workflow_grants_no_write_access_beyond_pages() -> None:
    """报告发布不回写框架仓库（design D9）：不需要 PAT，也不该有跨仓写权限。"""
    permissions = _document().get("permissions", {})
    assert permissions.get("contents") == "read", "仓库内容只应可读"
    assert permissions.get("pages") == "write", "仅 Pages 需要写权限"


def test_workflow_never_pushes_to_another_repository() -> None:
    text = _text()
    assert "git push" not in text
    assert "YearsAlso/zoo-framework" not in text, "不该出现框架仓库地址：没有任何回写路径"


def test_workflow_installs_a_cjk_font() -> None:
    """runner 默认没有中文字体，不装就会产出满是方框的报告（design D11）。"""
    assert "fonts-noto-cjk" in _text()


def test_workflow_fails_when_a_version_did_not_finish() -> None:
    """单个版本失败不中断其余（已测出的数据更有用），但末尾必须以失败结束。"""
    text = _text()
    assert "failed" in text
    assert "exit 1" in text


def test_workflow_publishes_only_after_the_bench_job(tmp_path: Path) -> None:
    document = _document()
    deploy = document["jobs"]["deploy"]
    assert deploy["needs"] == "bench", "发布必须先等测量完成"
    artifact = document["jobs"]["bench"]["steps"][-1]
    assert "upload-pages-artifact" in str(artifact["uses"])
