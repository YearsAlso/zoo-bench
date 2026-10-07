"""开销归因的验证（spec: measurement-protocol 的四段口径 + comparison-report 的归因维度）。

这一维的核心是一句恒等式：**四段的边界取自同一组时刻，故逐任务满足
`提交侧 + 手交 + 执行体 + 回程 = 端到端`**。故本文件的重点是把这条钉住——它一旦不成立，
下面所有"落在哪一段"的结论都失去意义。

另有两处刻意用**证据**而不是声明来验：跨进程档位（执行体的起止时刻观测不到）与框架接缝的
还原（包装是临时改类属性，没还原会污染同进程里后续的一切）。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from zoo_bench import attribution

# ------------------------------------------------------------------ 四段的恒等式


class _FakeBody:
    """只带打点字段的假执行体：供 :func:`segment_round` 的纯逻辑用例用。"""

    def __init__(self, started_at: float | None, finished_at: float | None) -> None:
        self.started_at = started_at
        self.finished_at = finished_at


def test_the_four_segments_sum_to_the_end_to_end() -> None:
    """恒等式：逐任务，四段之和 == 排空返回 − 该任务的提交开始。"""
    bodies = [_FakeBody(10.5, 20.5), _FakeBody(11.0, 21.0)]
    spans = [(10.0, 10.4), (10.6, 10.9)]

    rows = attribution.segment_round(bodies, spans, drained_at=30.0)

    for row, (submitted_at, _) in zip(rows, spans, strict=True):
        total = sum(row[key] for key in attribution.SEGMENT_KEYS)
        assert total == pytest.approx(row["end_to_end_seconds"])
        assert row["end_to_end_seconds"] == pytest.approx(30.0 - submitted_at)


def test_each_segment_is_the_difference_of_its_own_two_instants() -> None:
    """每段的边界必须落在**它自己那两个时刻**上，不能借用轮或别人的时刻。"""
    rows = attribution.segment_round([_FakeBody(10.5, 20.5)], [(10.0, 10.4)], drained_at=30.0)

    row = rows[0]
    assert row["submit_side_seconds"] == pytest.approx(0.4)
    assert row["handoff_seconds"] == pytest.approx(0.1)
    assert row["body_seconds"] == pytest.approx(10.0)
    assert row["return_seconds"] == pytest.approx(9.5)


def test_a_body_that_never_ran_locally_is_reported_as_unmeasurable() -> None:
    """执行体在别的进程里跑过时，它的打点在本进程读不到——报错，而不是给一个错的分段。"""
    with pytest.raises(RuntimeError, match=r"attribution\.reason\.cross_process"):
        attribution.segment_round([_FakeBody(None, None)], [(10.0, 10.1)], drained_at=11.0)


def test_consistency_check_has_teeth() -> None:
    """判据必须能发现"某段被漏算或被算两遍"——否则它只是装饰。

    注入一处漏算（把回程抹掉）后，逐任务的和就不再等于端到端。
    """
    rows = attribution.segment_round([_FakeBody(10.5, 20.5)], [(10.0, 10.4)], drained_at=30.0)
    assert attribution.max_consistency_deviation(rows) == pytest.approx(0.0)

    broken = [dict(rows[0], return_seconds=0.0)]
    assert attribution.max_consistency_deviation(broken) > attribution.CONSISTENCY_TOLERANCE


# ------------------------------------------------------------------ 细分


class _FakeInstrument(attribution.drill_down):
    """只喂累计量的细分器：用来验"减出互不重叠的桶"这一步算术，不碰框架。

    三个接缝是嵌套的（派发与策略查询都发生在调度轮内部），故它们的和不能当结果用——这条用例
    就是钉住那个减法：桶之和必须**恒等于**提交侧。
    """

    def __init__(self, *, round_total: float, dispatch: float, policy: float) -> None:
        super().__init__()
        self.totals[attribution.SEAL_SCHEDULING_ROUND] = round_total
        self.totals[attribution.SEAL_DISPATCH] = dispatch
        self.totals[attribution.SEAL_POLICY_LOOKUP] = policy


def test_drill_buckets_are_disjoint_and_sum_to_the_submit_side() -> None:
    instrument = _FakeInstrument(round_total=60.0, dispatch=14.0, policy=20.0)

    buckets = instrument.buckets(concurrency=1, submit_side_seconds=80.0)

    assert buckets["scheduling_round_seconds"] == pytest.approx(26.0), "调度轮要减掉被它含的两项"
    assert buckets["dispatch_seconds"] == pytest.approx(14.0)
    assert buckets["policy_lookup_seconds"] == pytest.approx(20.0)
    assert buckets["submit_side_other_seconds"] == pytest.approx(20.0), "提交侧减去调度轮"
    assert sum(buckets.values()) == pytest.approx(80.0)


def test_derived_buckets_never_go_negative() -> None:
    """由减法得到的两个桶取 0 而不是负数——负的"其余"没有含义。

    被**量到**的三项不夹：它们是测量值，夹一下等于把测出来的东西改掉。故这条只针对减法得到的
    那两项。
    """
    instrument = _FakeInstrument(round_total=10.0, dispatch=4.0, policy=3.0)

    buckets = instrument.buckets(concurrency=1, submit_side_seconds=5.0)

    assert buckets["scheduling_round_seconds"] == pytest.approx(3.0)
    assert buckets["submit_side_other_seconds"] == 0.0, "提交侧不够减时取 0"
    assert all(value >= 0 for value in buckets.values())


def test_drill_overlap_is_published_instead_of_being_papered_over() -> None:
    """各项之和超出提交侧时，**把超出量报出来**——不要夹成一个看起来自洽的划分。

    实测里这个超出确实存在（约 3%），而它的原因尚未解释清楚。此时正确的做法是如实发布，
    让读者知道这些数不能相加；假装它们是一个划分会让下游的每条结论都建立在一个未验证的
    等式上。
    """
    overlapping = [
        {
            "buckets": _FakeInstrument(round_total=10.0, dispatch=60.0, policy=50.0).buckets(
                concurrency=1, submit_side_seconds=100.0
            ),
            "submit_side_seconds": 100.0,
        }
    ]

    verdict = attribution.drill_overlap(overlapping)

    assert verdict["max_overlap_ratio"] > 0, "超出了就要说出来"
    assert verdict["note"] == "report.attribution.drill_note"


def test_drill_overlap_is_zero_when_the_items_fit() -> None:
    """各项装得下时超出量为 0——这条与上一条成对，证明它不是一个恒正的装饰。"""
    fitting = [
        {
            "buckets": _FakeInstrument(round_total=10.0, dispatch=3.0, policy=4.0).buckets(
                concurrency=1, submit_side_seconds=100.0
            ),
            "submit_side_seconds": 100.0,
        }
    ]

    assert attribution.drill_overlap(fitting)["max_overlap_ratio"] == pytest.approx(0.0)


class _SyntheticSeam:
    """一个只有 classmethod 的合成接缝：用来验**包装机制**本身，不依赖框架的某一代。"""

    calls = 0

    @classmethod
    def lookup(cls, path: str, default_value: object = None) -> str:
        cls.calls += 1
        return f"{path}/{default_value}"


def test_wrapping_a_classmethod_keeps_its_binding() -> None:
    """classmethod 要连着 `cls` 一起包——只包函数体的话调用处会把 cls 落到第一个形参上。

    实测踩过这个坑：包装 `ParamsFactory.get_params` 时报
    `got multiple values for argument 'default_value'`——因为 `getattr(cls, name)` 对
    classmethod 返回的是**已绑定**的方法，它的签名里没有 cls。
    """
    original = vars(_SyntheticSeam)["lookup"]
    instrument = attribution.drill_down()
    instrument._wrap(_SyntheticSeam, "lookup", attribution.SEAL_POLICY_LOOKUP)

    assert _SyntheticSeam.lookup("worker:period", default_value=None) == "worker:period/None"
    assert _SyntheticSeam().lookup("worker:phase") == "worker:phase/None", "实例上调用也要成立"
    assert instrument.totals[attribution.SEAL_POLICY_LOOKUP] > 0.0
    assert instrument.installed == [attribution.SEAL_POLICY_LOOKUP]

    instrument.__exit__()
    assert vars(_SyntheticSeam)["lookup"] is original, "退出时必须还原"


def test_wrapping_is_undone_even_when_the_body_raises() -> None:
    """包装是临时改类属性——哪怕里面抛了也要还原，否则污染同进程里后续的一切。"""
    from zoo_framework.core.waiter.base_waiter import BaseWaiter

    original = vars(BaseWaiter)["execute_service"]
    with pytest.raises(ValueError, match="夹在中间"), attribution.drill_down():
        raise ValueError("夹在中间")

    assert vars(BaseWaiter)["execute_service"] is original


# ------------------------------------------------------------------ 真实探查


def test_probe_reports_four_segments_for_an_in_process_adapter() -> None:
    result = attribution.probe_group(
        "thread_pool", tier_us=300, concurrency=1, warmup_rounds=1, measured_rounds=5
    )

    assert result["status"] == "ok"
    assert set(result["segments"]) == set(attribution.SEGMENT_KEYS)
    assert all(value >= 0 for value in result["segments"].values())
    assert result["consistency"]["ok"] is True, "四段之和与端到端必须自洽"
    assert result["end_to_end_seconds"] > 0


def test_probe_reports_a_cross_process_adapter_as_unmeasurable() -> None:
    """证据性的边界：执行体在子进程里跑时，它的起止时刻在这里读不到。

    那种方案量到的是 IPC 延迟——它是它与其他方案不可比的另一件事，不该混进"框架自身的开销"。
    """
    result = attribution.probe_group(
        "process_pool", tier_us=300, concurrency=1, warmup_rounds=1, measured_rounds=3
    )

    assert result["status"] == "not_measurable"
    assert result["reason"] == "attribution.reason.cross_process"
    assert "segments" not in result


def test_the_subject_is_drilled_and_a_comparison_is_not() -> None:
    """细分只对被测框架做——"下一步动哪里"只对它有意义。"""
    subject = attribution.probe_group(
        "zoo", tier_us=300, concurrency=1, warmup_rounds=1, measured_rounds=5, drill=True
    )

    assert subject["status"] == "ok"
    assert len(subject["seals"]) >= 2, "用到的接缝要逐条声明"
    # 策略查询那三处入口只在当前代有：两种归属都必须有名分——既不能说"用到了不存在的入口"，
    # 也不能把它说成"花了 0 微秒"（那与"没有这一层"含义相反）
    policy = attribution.SEAL_POLICY_LOOKUP
    assert policy in subject["seals"] or policy in subject["unavailable_seals"]
    assert set(subject["drill_down"]) == set(attribution.DRILL_KEYS)
    # 细分各项是**独立测量**，不冒充划分：超出提交侧的量要如实发布，而不是夹成一个自洽的样子
    assert subject["drill_overlap"]["max_overlap_ratio"] is not None
    assert subject["drill_overlap"]["note"] == "report.attribution.drill_note"
    assert subject["instrumentation"]["baseline_end_to_end_seconds"] > 0


def test_instrumentation_cost_is_measured_not_assumed() -> None:
    """插桩自身的成本要量出来随结论发布，而不是让读者自己去猜它有多大。

    它由**配对**的两轮得到（同一子进程里交替量），故机器漂移不会落进这个差里；结果是正是负
    都照实给——负的说明这一轮里漂移比插桩成本还大，那本身也是读者该知道的信息。
    """
    result = attribution.probe_group(
        "zoo", tier_us=300, concurrency=1, warmup_rounds=1, measured_rounds=6, drill=True
    )

    instrumentation: dict[str, Any] = result["instrumentation"]
    assert instrumentation["baseline_end_to_end_seconds"] > 0
    assert instrumentation["delta_seconds"] == pytest.approx(
        result["end_to_end_seconds"] - instrumentation["baseline_end_to_end_seconds"]
    )


def test_calibrated_iterations_is_reusable_across_bodies() -> None:
    """迭代次数按**组**校准一次即可：每个执行体各校一遍会让探查本身变得极慢。"""
    iterations = attribution.calibrated_iterations(300.0)

    assert iterations >= 1
    body = attribution.SegmentBody(iterations)
    elapsed = body()
    assert body.started_at is not None and body.finished_at is not None
    assert elapsed > 0
    assert time.perf_counter() > body.finished_at


# ------------------------------------------------------------------ 编排接入


def _spec(*, concurrency: int = 1, tier_us: float = 300.0) -> Any:
    from zoo_bench.runner import UnitSpec

    return UnitSpec(
        adapter="thread_pool",
        framework="zoo-framework==9.9.9",
        concurrency=concurrency,
        body_tier_us=tier_us,
        warmup_rounds=1,
        measured_rounds=2,
    )


def test_runner_carries_the_attribution_groups_into_the_result() -> None:
    """归因在独立子进程里跑、结论进结果结构——它不是"渲染层自己去找"。"""
    from zoo_bench.runner import measure_matrix

    result = measure_matrix([_spec()], verify_adapters=False, with_semantics=False)

    attribution = result["attribution"]
    assert attribution is not None
    assert attribution["cores"] and attribution["cores"] > 0, "判据要取自环境"
    assert attribution["tiers_us"] and attribution["concurrencies"]
    assert attribution["groups"], "至少要量一组"
    assert all(
        group["status"] in {"ok", "not_measurable", "probe_failed"}
        for group in attribution["groups"]
    )
    assert attribution["note"], "抽样范围要写进结论"


def test_runner_can_skip_the_attribution_probe() -> None:
    from zoo_bench.runner import measure_matrix

    result = measure_matrix(
        [_spec()], verify_adapters=False, with_semantics=False, with_attribution=False
    )

    assert result["attribution"] is None


def test_sampling_never_goes_above_the_machines_parallelism() -> None:
    """并发度的上界取机器并行能力：超过它时那几段量到的是争用，不是框架。"""
    from zoo_bench.runner import _attribution_specs

    specs = [_spec(concurrency=value) for value in (1, 4, 64)]

    planned = _attribution_specs(specs, cores=4, warmup_rounds=1, measured_rounds=1)
    chosen = sorted({item["concurrency"] for item in planned})

    assert chosen == [1, 4], "64 超出 4 核，不该出现在抽样里"


def test_sampling_keeps_the_lowest_degree_when_the_machine_is_smaller_than_the_matrix() -> None:
    """机器比矩阵里最低的并发度还小（核数 1、矩阵从 4 起）时仍要量。

    一条都不量会让整个维度凭空消失，那比"量了但在结论里标注超订"更坏。
    """
    from zoo_bench.runner import _attribution_specs

    planned = _attribution_specs(
        [_spec(concurrency=4)], cores=1, warmup_rounds=1, measured_rounds=1
    )

    assert [item["concurrency"] for item in planned] == [4]


def test_the_attribution_note_discloses_over_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    """在超订下量时要**说出来**，否则读者会把争用读成框架开销。"""
    from zoo_bench import runner as runner_module

    monkeypatch.setattr(runner_module.os, "cpu_count", lambda: 1)

    attribution = runner_module._run_attribution([_spec(concurrency=4)])

    assert attribution["cores"] == 1
    assert attribution["over_subscribed_concurrencies"] == [4]
    assert attribution["note"] == [
        "report.attribution.diagnosis_note",
        "report.attribution.oversubscribed_note",
    ]


def test_attribution_never_gates() -> None:
    """归因**不参与任何门禁**：开销高低是被测对象的事实，不是 harness 的错误。

    把它做成门禁等于因为被测框架慢而让 CI 红——那会让这条维度从"诊断"退化成"考核"。
    """
    from zoo_bench.runner import measure_matrix

    result = measure_matrix([_spec()], verify_adapters=False, with_semantics=False)

    assert "attribution" not in result["self_check"], "自检里不该出现这一维"
    assert "attribution" not in result["self_check"].get("adapter_equivalence", {})
