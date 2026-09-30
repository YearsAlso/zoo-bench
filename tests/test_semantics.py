"""调度语义维度探查的验证（spec: comparison-report 的语义口径 + design D7）。

这一维度的特殊之处：它的**结论是"没有一项可测"**。所以本文件的重点不是"探到了什么"，而是
——"没测"与"测了发现不可测"必须区分得开，且判据本身可证伪。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

import pytest

from zoo_bench import semantics
from zoo_bench.report import build_model
from zoo_bench.runner import UnitSpec, measure_matrix

FRAMEWORK = "zoo-framework==9.9.9"


def _installed_framework_version() -> str | None:
    try:
        return version("zoo-framework")
    except PackageNotFoundError:
        return None


# ------------------------------------------------------------------ 判据的鉴别力


def test_enforcement_judgement_detects_a_real_cancellation() -> None:
    """判据在"超时生效"时判真——只验真实探查返回 False 无法区分判据有效与判据恒假。"""
    assert semantics.timeout_is_enforced(0.02, body_seconds=0.15, limit_seconds=0.01) is True


def test_enforcement_judgement_detects_an_ineffective_timeout() -> None:
    """执行体跑到自然结束（未被打断）时判否。"""
    assert semantics.timeout_is_enforced(0.152, body_seconds=0.15, limit_seconds=0.01) is False


def test_enforcement_judgement_rejects_invalid_probe_parameters() -> None:
    """``run_timeout`` 不小于执行体耗时时，探查本身就无意义——拒绝而不是给个默认答案。"""
    with pytest.raises(ValueError, match="run_timeout"):
        semantics.timeout_is_enforced(0.1, body_seconds=0.15, limit_seconds=0.0)
    with pytest.raises(ValueError, match="run_timeout"):
        semantics.timeout_is_enforced(0.1, body_seconds=0.15, limit_seconds=0.2)


# ------------------------------------------------------------------ 真实探查


def test_dispatch_timeout_probe_is_mechanically_consistent() -> None:
    """真实探查的结论与它自己的实测值自洽。

    **刻意不硬编码"超时未生效"**——那会在版本升级时变成一条假断言。这里断言的是自洽性：
    判否时必须有理由，且观测值确实没有显著早于执行体结束。
    """
    item = semantics.probe_dispatch_timeout()

    assert item["observed_seconds"] > 0
    assert item["evidence"]
    if item["enforced"]:
        assert item["reason"] == ""
        assert item["observed_seconds"] < item["body_seconds"] * semantics.ENFORCEMENT_RATIO
    else:
        assert item["reason"], "判否必须给出理由，否则读者无法区分“未测”与“不可测”"
        assert item["observed_seconds"] >= item["body_seconds"] * semantics.ENFORCEMENT_RATIO


def test_dispatch_timeout_finding_is_recorded_per_version() -> None:
    """0.6.0 上的实测结论：超时判定存在，但执行动作未实现。

    这条**把版本绑进断言**，并在其他版本上 skip 而非静默通过：升级 matrix.yaml 的固定版本
    后它会被跳过从而提醒重跑核对——与 design 的版本偏斜风险里"追版本时把实现坐标重新核对
    一遍"是同一条要求。
    """
    installed = _installed_framework_version()
    if installed != "0.6.0":
        pytest.skip(f"本结论绑定 0.6.0；当前装的是 {installed}，需对新版本重跑核对")

    item = semantics.probe_dispatch_timeout()
    assert item["enforced"] is False
    assert "注释" in item["evidence"]


def test_event_layer_probe_matches_the_symbol_it_checks() -> None:
    """事件层的可用性取决于"执行反应器所需的符号是否存在"——探查结论必须与之一致。"""
    from zoo_framework.reactor.event_reactor import EventReactor

    layer = semantics.probe_event_layer()
    expected = hasattr(EventReactor, "perform")

    assert layer["items"], "事件层应枚举出优先级与重试"
    for item in layer["items"]:
        assert item["usable"] is expected
        assert item["evidence"]
        if not expected:
            assert item["reason"], "不可用时必须说明原因"


def test_event_layer_enumerates_priority_and_retry() -> None:
    names = {item["item"] for item in semantics.probe_event_layer()["items"]}
    assert names == {"优先级", "重试"}


# ------------------------------------------------------------------ 汇总与接入


def test_probe_summary_distinguishes_not_measured_from_not_probed() -> None:
    """``not_measured`` 必须带逐项证据；这正是它与"没跑过探查"的分界。"""
    outcome = semantics.probe_semantics()

    assert outcome["scope_note"], "口径声明在任何状态下都必须在场"
    assert outcome["items"], "枚举结果不能为空"
    assert outcome["recheck_on_version_bump"], "结论按版本得出，必须声明升级后要重跑"

    usable = [item for item in outcome["items"] if item["usable"]]
    assert outcome["usable_items"] == [item["item"] for item in usable]
    assert outcome["status"] == ("ok" if usable else "not_measured")
    if not usable:
        assert outcome["reason"], "没有可测项时必须说明原因，不能只说'未测量'"


def test_runner_exposes_the_semantics_probe(tmp_path) -> None:
    """探查作为一次独立子进程运行，结论进结果结构——语义维度不是"渲染层自己去找"。"""
    result = measure_matrix(
        [
            UnitSpec(
                adapter="thread_pool",
                framework=FRAMEWORK,
                concurrency=1,
                body_tier_us=300,
                warmup_rounds=0,
                measured_rounds=1,
            )
        ],
        verify_adapters=False,
    )
    assert result["semantics"]["status"] in {"ok", "not_measured"}


def test_runner_can_omit_the_semantics_probe() -> None:
    result = measure_matrix(
        [
            UnitSpec(
                adapter="thread_pool",
                framework=FRAMEWORK,
                concurrency=1,
                body_tier_us=300,
                warmup_rounds=0,
                measured_rounds=1,
            )
        ],
        verify_adapters=False,
        with_semantics=False,
    )
    assert result["semantics"] is None


def test_model_uses_the_probed_semantics_verbatim() -> None:
    probed: dict[str, Any] = {"title": "调度语义的代价", "status": "not_measured", "items": []}
    result = {"units": [], "run": {}, "relative": {"comparisons": []}, "semantics": probed}
    assert build_model(result)["dimensions"]["semantics"] == probed


def test_model_marks_semantics_as_not_probed_when_the_probe_never_ran() -> None:
    """没跑探查与跑了但不可测是两件事——前者是遗漏，后者是结论。"""
    model = build_model({"units": [], "run": {}, "relative": {"comparisons": []}})
    block = model["dimensions"]["semantics"]

    assert block["status"] == "not_probed"
    assert block["items"] == []
    assert block["scope_note"], "口径声明即使在未探查时也要在场"
