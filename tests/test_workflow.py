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


def test_workflow_grants_write_only_where_needed() -> None:
    """写权限只给两处，各有理由。

    `contents: write` 是**把留档提交回本仓库**用的——"报告可追溯到原始数据"对读者必须成立，
    而 CI 产物要登录才能下载。这**不是**跨仓库写回：design D9 禁的是向框架仓库回写。
    """
    permissions = _document().get("permissions", {})
    assert permissions.get("contents") == "write", "留档要提交回本仓库"
    assert permissions.get("pages") == "write", "Pages 需要写权限"


def test_workflow_never_pushes_to_another_repository() -> None:
    """推送只针对本仓库。"""
    text = _text()
    assert "YearsAlso/zoo-framework" not in text, "不该出现框架仓库地址：没有任何回写路径"
    # 裸 `git push`（不带远端 URL）= 推本仓库的 origin
    assert "git push\n" in text or text.rstrip().endswith("git push")


def test_workflow_guards_against_a_self_triggering_loop() -> None:
    """归档提交会推到 main，而本工作流正是 push 到 main 触发的——**必须有防循环标记**。

    没有它，每一轮工作流都会触发下一轮，无限跑下去。这条是本文件里最该存在的断言。
    """
    text = _text()
    assert "git commit" in text, "归档步骤应当提交"
    assert "[skip actions]" in text, "提交信息里必须有防循环标记"


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
