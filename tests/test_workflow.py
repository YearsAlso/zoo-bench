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


def _steps() -> list[dict]:
    return _document()["jobs"]["bench"]["steps"]


def test_workflow_publishes_comparisons_between_adjacent_versions() -> None:
    """「每个版本完成后给出一个性能提升报告」要有落点：站点上能读到相邻两版的对比。

    **比哪几对由矩阵里的书写顺序决定**，不在这里另立一份清单——两处真源必然漂移。故这一步
    只允许有一处，且必须经 `zoo-bench frameworks` 拿顺序。
    """
    compare_steps = [step for step in _steps() if "zoo-bench compare" in (step.get("run") or "")]
    assert len(compare_steps) == 1, "对比生成应恰好是一步，不要在两处各生成一遍"

    run = compare_steps[0]["run"]
    assert "--out" in run and "site/" in run, "对比页要落进站点目录"
    assert "zoo-bench frameworks" in run, "比哪几对由矩阵顺序决定，不是抄在流程里"


def test_workflow_lists_comparisons_after_generating_them() -> None:
    """首页会**发现**站点里的对比页，故生成必须排在写首页之前——顺序反了就列不出来。"""
    names = [step.get("name", "") for step in _steps()]
    assert names.index("生成跨版本对比") < names.index("写站点首页")


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


#: 会让 GitHub 静默跳过工作流的提交信息关键词。
SKIP_KEYWORDS = ("[skip ci]", "[ci skip]", "[no ci]", "[skip actions]", "[actions skip]")


def _triggers() -> dict:
    """取 ``on:`` 段。

    YAML 会把裸 ``on`` 解析成布尔 ``True``（不是字符串 ``"on"``），故两种键都试。
    """
    document = _document()
    return document.get(True) or document.get("on") or {}


def test_workflow_guards_against_a_self_triggering_loop() -> None:
    """归档提交会推到 main，而本工作流正是 push 到 main 触发的——**必须有防循环**。

    用**声明式的路径过滤**，不用提交信息里的跳过关键词。后者实测踩过：归档提交原先在信息里写
    那个关键词，而提交正文正好**在论证这个标记**，于是关键词被读走、整次推送被静默跳过——
    随后 push 的六个提交一个都没触发工作流，直到查 run 列表才发现。关键词的根本毛病是文本
    匹配：任何人把"跳过构建"这件事写进提交信息就会误伤。
    """
    push = _triggers().get("push", {})
    assert "results/**" in (push.get("paths-ignore") or []), "防循环要靠路径过滤"
    assert "git commit" in _text(), "归档步骤应当提交"


def test_workflow_commit_message_carries_no_skip_keyword() -> None:
    """归档提交的信息里不得出现跳过关键词。

    它与上一条是同一个坑的两面：关键词一旦出现在提交信息里（哪怕是论证它的文字），GitHub
    就会静默跳过整次构建——而"构建没跑"看起来和"构建通过了"完全不一样，很容易被忽略。
    """
    for line in _text().splitlines():
        if "git commit -m" not in line:
            continue
        for keyword in SKIP_KEYWORDS:
            assert keyword not in line, f"提交信息里出现了 {keyword}：会把整次构建静默跳过"


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
