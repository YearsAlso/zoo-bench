"""开销数字适用口径的验证（spec: measurement-protocol 的"只在可比的并发度范围内给出"）。

本文件的重点是把**两个触发条件各自的鉴别力**钉住：只测"超并行能力被拦下"说明不了证据性那条
有用，只测"负值被拦下"也说明不了结构性那条在干活。两条各有一组正反例。
"""

from __future__ import annotations

from typing import Any

from zoo_bench import caliber
from zoo_bench.i18n import LANG_ZH

# ------------------------------------------------------------------ 判据


def test_concurrency_above_the_machine_is_not_interpretable() -> None:
    """结构性触发：并发度超过核数即判不可读。"""
    assert not caliber.overhead_is_interpretable(
        concurrency=64, logical_cores=4, overhead_seconds=0.0001
    )


def test_concurrency_at_or_below_the_machine_stays_interpretable() -> None:
    """**等于核数仍算可读**：那条线是"超过"，不是"达到"——达到容量并没有超订。"""
    assert caliber.overhead_is_interpretable(
        concurrency=4, logical_cores=4, overhead_seconds=0.0001
    )
    assert caliber.overhead_is_interpretable(
        concurrency=1, logical_cores=4, overhead_seconds=0.0001
    )


def test_a_negative_overhead_is_never_interpretable() -> None:
    """证据性触发：开销为负不可能是合法结果——框架只会加时间不会减时间。

    这条同时兜住结构性那条漏掉的情形：容器 CPU 配额可能低于 runner 报的核数，那时核数会高估
    真实并行能力，只靠 `concurrency > cores` 会放行仍然超订的组。
    """
    assert not caliber.overhead_is_interpretable(
        concurrency=1, logical_cores=256, overhead_seconds=-0.0001
    )


def test_zero_overhead_is_interpretable() -> None:
    """恰为 0 仍可读：判否的门槛是"负"，不是"非正"。"""
    assert caliber.overhead_is_interpretable(concurrency=1, logical_cores=4, overhead_seconds=0.0)


def test_missing_core_count_does_not_withhold_everything() -> None:
    """核数取不到时**不据此判否**：环境自述缺一项就把整列抹掉，比给一个可能无效的数字更坏。

    此时由负值那条兜底——故这个正例必须是正的开销，负的仍会被拦下。
    """
    assert caliber.overhead_is_interpretable(
        concurrency=64, logical_cores=None, overhead_seconds=0.0001
    )
    assert not caliber.overhead_is_interpretable(
        concurrency=64, logical_cores=None, overhead_seconds=-0.0001
    )


def test_core_count_comes_from_the_environment_not_from_a_constant() -> None:
    """判据取自环境自述，不是硬编码——同一组数据换个核数就该得到不同判定。"""
    environment_four: dict[str, Any] = {"hardware": {"logical_cores": 4}}
    environment_sixty_four: dict[str, Any] = {"hardware": {"logical_cores": 64}}

    assert caliber.logical_cores_from_environment(environment_four) == 4
    assert caliber.logical_cores_from_environment(environment_sixty_four) == 64
    assert not caliber.overhead_is_interpretable(
        concurrency=64,
        logical_cores=caliber.logical_cores_from_environment(environment_four),
        overhead_seconds=0.0,
    )
    assert caliber.overhead_is_interpretable(
        concurrency=64,
        logical_cores=caliber.logical_cores_from_environment(environment_sixty_four),
        overhead_seconds=0.0,
    )


def test_environment_without_a_core_count_yields_none() -> None:
    """自述里没有硬件段、或核数不是整数时，如实给 None 而不是编一个默认值。"""
    assert caliber.logical_cores_from_environment(None) is None
    assert caliber.logical_cores_from_environment({}) is None
    assert caliber.logical_cores_from_environment({"hardware": {}}) is None
    assert caliber.logical_cores_from_environment({"hardware": {"logical_cores": "4"}}) is None
    assert caliber.logical_cores_from_environment({"hardware": "cpu"}) is None


def test_the_reason_says_what_is_withheld_and_what_is_not() -> None:
    """原因句要同时讲清两件事：什么被撤下、什么照旧有效。

    只说"该组不可读"会让读者以为整行数据都没了，从而放弃这一档位——而端到端与吞吐其实还在。
    """
    reason = caliber.uninterpretable_reason(LANG_ZH)
    assert "并行能力" in reason
    assert "端到端" in reason and "吞吐" in reason, "要说清哪些量仍然有效"
    assert "负" in reason, "要给出判否的证据形态，否则读者以为是阈值卡出来的"
