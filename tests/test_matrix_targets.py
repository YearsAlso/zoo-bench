"""被测目标解析与安装校验的验证。

新增了"git 引用也能作为被测目标"这条能力（原先只认 PyPI 规格）。它的**风险点很具体**：
分支安装的版本号是仓库里写着的声明值（可能滞后、也可能与已发布版本撞号），拿它当身份就会
出现"以为在测 dev 分支、其实装的是某个 release"的静默错配。故这一批用例的重心在**校验**，
不在解析——解析错了会立刻报错，校验漏了则会安静地测错东西。
"""

from __future__ import annotations

from typing import Any

import pytest

from zoo_bench import cli, environment, storage
from zoo_bench import matrix as matrix_module
from zoo_bench.render import blocks as blocks_module

PYPI = "zoo-framework==0.7.1b0"
GIT = "zoo-framework @ git+https://github.com/YearsAlso/zoo-framework.git@dev"


# ------------------------------------------------------------------ 解析


def test_parses_a_pypi_specifier() -> None:
    target = matrix_module.parse_target(PYPI)

    assert target.kind == "pypi"
    assert target.name == "zoo-framework"
    assert target.version == "0.7.1b0"
    assert target.label == "zoo-framework 0.7.1b0"


def test_parses_a_git_specifier() -> None:
    target = matrix_module.parse_target(GIT)

    assert target.kind == "git"
    assert target.name == "zoo-framework"
    assert target.url == "https://github.com/YearsAlso/zoo-framework.git"
    assert target.ref == "dev"
    assert target.label == "zoo-framework dev"


def test_git_specifier_may_omit_the_package_name() -> None:
    """``git+URL@ref`` 不带包名也是合法写法——名字从仓库地址最后一段取。"""
    target = matrix_module.parse_target("git+https://github.com/YearsAlso/zoo-framework.git@dev")

    assert target.kind == "git"
    assert target.name == "zoo-framework"


def test_git_specifier_without_a_ref_is_rejected() -> None:
    """没有 ``@分支`` 的 git 目标指向默认分支，**但不是本 harness 想要的写法**——report 无法
    说明测的是哪个引用。明确拒绝比让它默默取默认分支好。"""
    with pytest.raises(matrix_module.MatrixError, match="分支"):
        matrix_module.parse_target("zoo-framework @ git+https://github.com/o/r.git")


def test_unrecognised_specifier_is_rejected() -> None:
    with pytest.raises(matrix_module.MatrixError, match="无法识别"):
        matrix_module.parse_target("zoo-framework")


def test_git_targets_get_a_readable_slug() -> None:
    """slug 取分支名而不是整条 URL：URL 进目录名又长又难读，具体 commit 由环境自述记录。"""
    assert storage.framework_slug(PYPI) == "zoo-framework-0.7.1b0"
    assert storage.framework_slug(GIT) == "zoo-framework-dev"


def test_nameless_git_target_also_gets_a_readable_slug() -> None:
    """不带包名的写法也要拿到干净 slug。

    真事故的守卫：目录命名与安装校验原先各自解析一遍 spec，于是同一个引用在文件名里是
    `zoo-framework-dev`、在 slug 里变成整条 URL（`git-https-github.com-…`）。现在两处都走
    `targets.parse_target`，这条用例固定"只有一个解释器"。
    """
    nameless = "git+https://github.com/YearsAlso/zoo-framework.git@dev"

    assert storage.framework_slug(nameless) == "zoo-framework-dev"
    assert "/" not in storage.framework_slug(nameless)
    assert storage.framework_slug(nameless) == storage.framework_slug(GIT)


def test_select_framework_accepts_a_bare_ref() -> None:
    matrix = matrix_module.Matrix(
        frameworks=(PYPI, GIT), concurrency=(1,), body_tiers_us=(300.0,)
    )

    assert matrix_module.select_framework(matrix, "0.7.1b0") == PYPI
    assert matrix_module.select_framework(matrix, "dev") == GIT


# ------------------------------------------------------------------ 安装校验


def test_installed_pypi_version_must_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_installed_framework_version", lambda: "0.6.0")

    problem = cli._verify_installed(matrix_module.parse_target(PYPI))

    assert problem is not None
    assert "0.7.1b0" in problem, "要指明该装哪个版本"
    assert "pip install" in problem


def test_installed_pypi_version_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_installed_framework_version", lambda: "0.7.1b0")
    assert cli._verify_installed(matrix_module.parse_target(PYPI)) is None


def test_version_comparison_survives_a_non_normalised_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**真事故的守卫**：框架发布的 0.7.1b0 在 PyPI 索引里叫 ``0.7.1b0``，而它写进轮子元数据的
    是 ``0.7.1-beta``（未经 PEP 440 归一）——``importlib.metadata`` 报的是后者。

    字符串相等会把一个**装对了的环境**判成装错。校验要按归一后的版本比，否则门禁天天误报，
    而误报的门禁会被人绕过。
    """
    monkeypatch.setattr(cli, "_installed_framework_version", lambda: "0.7.1-beta")

    assert cli._verify_installed(matrix_module.parse_target("zoo-framework==0.7.1b0")) is None
    assert cli._same_version("0.7.1-beta", "0.7.1b0") is True
    assert cli._same_version("0.6.0", "0.7.1b0") is False
    assert cli._same_version(None, "0.7.1b0") is False


def test_git_target_requires_a_git_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """装的是某个 release、而矩阵要的是分支——**必须拦下**，否则会安静地测错东西。"""
    monkeypatch.setattr(environment, "install_source", lambda *_: {"url": "https://files.pythonhosted.org/x"})

    problem = cli._verify_installed(matrix_module.parse_target(GIT))

    assert problem is not None
    assert "不是 git 安装" in problem


def test_git_target_requires_the_declared_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """装的是另一个分支/标签 → 拦下。**版本号帮不上忙**：分支的版本号只是声明值。"""
    monkeypatch.setattr(
        environment,
        "install_source",
        lambda *_: {
            "url": "https://github.com/YearsAlso/zoo-framework.git",
            "vcs_info": {"vcs": "git", "requested_revision": "main", "commit_id": "a" * 40},
        },
    )

    problem = cli._verify_installed(matrix_module.parse_target(GIT))

    assert problem is not None
    assert "dev" in problem and "main" in problem


def test_git_target_requires_the_declared_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """引用对、但仓库不是那个仓库（比如同名 fork）→ 拦下。"""
    monkeypatch.setattr(
        environment,
        "install_source",
        lambda *_: {
            "url": "https://github.com/someone-else/zoo-framework.git",
            "vcs_info": {"vcs": "git", "requested_revision": "dev", "commit_id": "a" * 40},
        },
    )

    problem = cli._verify_installed(matrix_module.parse_target(GIT))

    assert problem is not None
    assert "来源与矩阵不符" in problem


def test_git_target_passes_when_the_source_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        environment,
        "install_source",
        lambda *_: {
            "url": "https://github.com/YearsAlso/zoo-framework.git",
            "vcs_info": {"vcs": "git", "requested_revision": "dev", "commit_id": "b" * 40},
        },
    )

    assert cli._verify_installed(matrix_module.parse_target(GIT)) is None


# ------------------------------------------------------------------ 报告里要写明来源


def _model_with_source(install_source: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "environment": {
            "hardware": {"cpu_model": "CPU", "logical_cores": 4, "platform": "Linux-x86_64"},
            "os": {"system": "Linux", "release": "1", "version": "1"},
            "python": {"version": "3.13.0", "implementation": "CPython", "executable": "/py"},
            "subject": {
                "dist_version": "0.7.1-beta",
                "module_version": "0.7.1-beta",
                "notes": "",
                "install_source": install_source,
            },
            "harness": {"version": "0.0.1", "commit": "deadbeef"},
            "command": ["zoo-bench", "run"],
        },
        "run": {},
        "load": {"composition": "x", "caveat": "y"},
        "conclusion": {"summary": [], "note": "", "overhead_crossings": [], "relative_turnings": []},
        "dimensions": {
            "latency": {"title": "延迟", "note": "n", "rows": []},
            "overhead": {"title": "开销", "note": "n", "rows": []},
            "throughput": {"title": "吞吐", "note": "n", "rows": []},
            "semantics": {"title": "语义", "status": "x", "scope_note": "s", "items": [],
                          "reason": "r"},
        },
        "unfavorable": {"items": [], "found": True, "note": "n"},
        "caveats": [],
        "absolute": {"note": "n", "process_isolation": {}},
        "self_check": {"ok": True},
    }


def test_report_shows_the_git_commit_for_branch_installs() -> None:
    """分支安装的报告必须写明**来自哪个提交**——版本号对分支只是声明值，回答不了这件事。"""
    source = {
        "url": "https://github.com/YearsAlso/zoo-framework.git",
        "vcs_info": {
            "vcs": "git",
            "requested_revision": "dev",
            "commit_id": "6a9af4a1234567890abcdef1234567890abcdef",
        },
    }
    text = "\n".join(
        part
        for block in blocks_module.build_blocks(_model_with_source(source), [])
        for part in (block.text, *block.items, *block.headers, *(c for row in block.rows for c in row))
    )

    assert "安装来源" in text
    assert "dev" in text
    assert "6a9af4a12345" in text, "要给出提交（截短显示即可）"


def test_report_has_no_source_rows_for_a_plain_pypi_install() -> None:
    text = "\n".join(
        part
        for block in blocks_module.build_blocks(_model_with_source(None), [])
        for part in (block.text, *block.items, *block.headers, *(c for row in block.rows for c in row))
    )

    assert "安装来源" not in text


def test_archive_paths_stay_distinct_between_a_release_and_a_branch() -> None:
    """同一版本号可能既是已发布版、又在分支上出现——两者的留档目录不能撞。"""
    assert storage.framework_slug(PYPI) != storage.framework_slug(GIT)


def test_matrix_yaml_entries_are_all_parseable() -> None:
    """本仓库真实的 matrix.yaml 里每条都必须能被解析。

    **刻意不要求它"同时含两种来源"**：0.7.x 目前装不上（派发面变了、适配器要适配，见
    matrix.yaml 里的说明），清单因此只有 PyPI 条目。能力由解析与校验的用例覆盖，清单内容
    由本用例保证"没有一条是坏掉的"。
    """
    matrix = matrix_module.load(matrix_module.DEFAULT_MATRIX_PATH)

    assert matrix.frameworks, "清单不能为空"
    for item in matrix.frameworks:
        target = matrix_module.parse_target(item)
        assert target.name
        assert storage.framework_slug(item), f"{item} 无法生成目录名"
