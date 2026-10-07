"""调度语义维度探查的验证（spec: measurement-protocol 的世代限定 + design D7）。

这一维度有两处特殊之处，本文件的重点也随之：

1. **它的结论是按世代得出的**——同一项在两代上可以结论相反（实测超时语义在上一代"判定存在
   但执行动作未实现"，在当前代真的会摘表熔断）。故断言分两类：世代无关的自洽性，与**按代各
   绑一条**的结论（另一代显式跳过，而不是静默通过）。
2. **"没测"与"测了发现不可测"必须区分得开**，且判据本身可证伪。
"""

from __future__ import annotations

from typing import Any

import pytest

from zoo_bench import semantics
from zoo_bench.generations import (
    GENERATION_CURRENT,
    GENERATION_PREVIOUS,
    probe_drive_generation,
)
from zoo_bench.i18n import LANG_ZH, t
from zoo_bench.report import build_model
from zoo_bench.runner import UnitSpec, measure_matrix

FRAMEWORK = "zoo-framework==9.9.9"
INSTALLED = probe_drive_generation()["generation"]


def _skip_unless(generation: str) -> None:
    if generation != INSTALLED:
        pytest.skip(f"本机装的是「{INSTALLED}」代驱动面，该结论只对「{generation}」代成立")


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
    """真实探查的结论与它自己的实测值自洽，且留下"按哪一代探查"的记录。

    **刻意不硬编码"超时未生效"**——那会在版本升级时变成一条假断言。这里断言的是自洽性：
    判否时必须有理由，且观测值确实没有显著早于执行体结束。
    """
    item = semantics.probe_dispatch_timeout()

    assert item["observed_seconds"] > 0
    assert item["evidence"]
    assert item["derived_on"] == INSTALLED, "结论必须记下它是在哪一代上得出的"
    if item["enforced"]:
        assert item["reason"] == ""
        assert item["observed_seconds"] < item["body_seconds"] * semantics.ENFORCEMENT_RATIO
    else:
        assert item["reason"], "判否必须给出理由，否则读者无法区分“未测”与“不可测”"
        assert item["observed_seconds"] >= item["body_seconds"] * semantics.ENFORCEMENT_RATIO


def test_the_driving_method_is_recorded_with_the_conclusion() -> None:
    """驱动方式是**结论的一部分**，必须随结论留在报告里。

    超时判定是调度轮的副产物：只派发一轮的话它没有机会执行，量到的成了"执行体自己跑了多久"。
    换代或改探查时，方法不进报告的话，"未观测到摘除"会被读成"这一代不支持超时"——两个结论
    完全相反，而报告上看不出区别。
    """
    probe = semantics.probe_dispatch_timeout()["probe"]
    assert probe == "semantics.probe.behavioral"
    assert "按调度轮" in t(probe, LANG_ZH)


def test_previous_generation_timeout_is_not_enforced() -> None:
    """上一代的结论：判定存在，但执行动作未实现（取消/摘除那段是注释）。

    这条**把世代绑进断言**，在另一代上 skip 而非静默通过：结论随版本改变，跳过即是提醒。
    第二段断言的关键在"观测到的确实是**离开在飞表**，而不是探查上限"——实测踩过这个坑：
    循环里先重新调度再检查时，执行体一完成就被再次派发，在飞表永远非空，读到的 0.6012s
    其实是探查上限，而报告上看起来像一个摘除时刻。
    """
    _skip_unless(GENERATION_PREVIOUS)

    item = semantics.probe_dispatch_timeout()
    assert item["enforced"] is False
    assert item["reason"]
    assert item["reason"] == "semantics.reason.previous_no_action"
    assert item["usable"] is False
    assert item["settled_observed"] is True, "没观测到离开在飞表时，读数不是摘除时刻"
    assert item["observed_seconds"] < item["body_seconds"] * 2, (
        f"读数 {item['observed_seconds']:.4f}s 应落在执行体自然结束附近"
        f"（{item['body_seconds']}s），而不是探查上限"
    )


def test_current_generation_timeout_is_enforced_and_says_what_that_means() -> None:
    """当前代的结论与上一代**相反**：超时到了真的会在 ``run_timeout`` 附近摘表熔断。

    证据里必须写明"生效的是观测与熔断、不是终止"：读者很容易把"摘除了"读成"被杀掉了"，
    而 CPython 无法安全中断一个正在执行的线程——那是两个不同的承诺。
    """
    _skip_unless(GENERATION_CURRENT)

    item = semantics.probe_dispatch_timeout()
    assert item["enforced"] is True
    assert item["usable"] is True
    assert item["settled_observed"] is True
    assert item["observed_seconds"] < item["limit_seconds"] * 3, (
        f"摘除时刻 {item['observed_seconds']:.4f}s 应落在 run_timeout 附近"
        f"（{item['limit_seconds']}s），而不是等执行体自然结束（{item['body_seconds']}s）"
    )
    assert item["evidence"] == "semantics.evidence.reaped", "生效的含义必须写进证据"
    assert item["evidence_params"]["note"] == "semantics.reap_note", (
        "生效的是观测与熔断、不是终止——这半句必须还在证据里"
    )
    assert item["evidence_params"]["location"], "证据要给出判定动作的坐标，便于按版本核对"


def test_event_layer_probe_matches_the_claims_provenance() -> None:
    """事件层的可用性只在"结论与当前代同源"时由符号存在性决定；跨代时如实标未复核。

    两代的事件层接线不同（``EventWorker`` 调的入口从 ``perform`` 改成 ``execute``），把在
    某代上得出的结论与坐标当成本代的事实，等于断言一个当前代并不具备的接线。
    """
    from zoo_framework.reactor.event_reactor import EventReactor

    layer = semantics.probe_event_layer()
    assert layer["items"], "事件层应枚举出优先级与重试"

    if layer["derived_on"] == INSTALLED:
        expected = hasattr(EventReactor, "perform")
        for item in layer["items"]:
            assert item["usable"] is expected
            assert item["evidence"]
            if not expected:
                assert item["reason"], "不可用时必须说明原因"
        return

    for item in layer["items"]:
        assert item["usable"] is False
        assert item["unchecked_on"] == INSTALLED
        assert item["derived_on"] == layer["derived_on"]
        assert item["evidence"] == "semantics.evidence.unchecked"
        assert item["reason"], "标为未复核必须说明原因"


def test_event_layer_does_not_reuse_another_generations_claims() -> None:
    """跨代时 MUST NOT 把另一代的结论或坐标照抄过来——这是本层最容易犯的错。"""
    layer = semantics.probe_event_layer()
    if layer["derived_on"] == INSTALLED:
        pytest.skip(f"结论与当前代同源（{INSTALLED}），跨代挪用不适用")

    for item in layer["items"]:
        # 上一代的理由里点着 perform 这个符号；当前代根本不走它，照抄就是错的
        assert "EventReacto" not in item["reason"], "照抄了另一代的坐标"
        assert "EventReacto" not in item["evidence"], "照抄了另一代的坐标"


def test_event_layer_enumerates_priority_and_retry() -> None:
    names = {item["item"] for item in semantics.probe_event_layer()["items"]}
    assert names == {"semantics.item.priority", "semantics.item.retry"}


# ------------------------------------------------------------------ 汇总与接入


def test_probe_summary_distinguishes_not_measured_from_not_probed() -> None:
    """``not_measured`` 必须带逐项证据；这正是它与"没跑过探查"的分界。"""
    outcome = semantics.probe_semantics()

    assert outcome["scope_note"], "口径声明在任何状态下都必须在场"
    assert outcome["items"], "枚举结果不能为空"
    assert outcome["recheck_on_version_bump"], "结论按版本得出，必须声明升级后要重跑"
    assert outcome["derived_on"] == INSTALLED, "整块结论要说明得自哪一代驱动面"

    usable = [item for item in outcome["items"] if item["usable"]]
    assert outcome["usable_items"] == [item["item"] for item in usable]
    assert outcome["status"] == ("ok" if usable else "not_measured")
    if not usable:
        assert outcome["reason"], "没有可测项时必须说明原因，不能只说'未测量'"


def test_semantics_section_declares_the_generation_its_conclusions_hold_for() -> None:
    """报告里必须看得到"这一维的结论是在哪一代上得出的"。

    这一维的结论与坐标按版本成立：不写出来，读者会拿一个针对 0.6.x 的结论去解释 0.7.x。
    """
    from zoo_bench.render import blocks as blocks_module

    model = {
        "dimensions": {
            "semantics": {
                "title": "调度语义的代价",
                "status": "ok",
                "derived_on": GENERATION_CURRENT,
                "scope_note": "口径",
                "items": [],
                "usable_items": ["超时"],
            }
        }
    }
    text = "\n".join(block.text for block in blocks_module._dimension_blocks(model, lang=LANG_ZH))
    assert GENERATION_CURRENT in text
    assert "代驱动面上得出" in text


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
    assert build_model(result, lang=LANG_ZH)["dimensions"]["semantics"] == probed


def test_model_marks_semantics_as_not_probed_when_the_probe_never_ran() -> None:
    """没跑探查与跑了但不可测是两件事——前者是遗漏，后者是结论。"""
    model = build_model({"units": [], "run": {}, "relative": {"comparisons": []}}, lang=LANG_ZH)
    block = model["dimensions"]["semantics"]

    assert block["status"] == "not_probed"
    assert block["items"] == []
    assert block["scope_note"], "口径声明即使在未探查时也要在场"
