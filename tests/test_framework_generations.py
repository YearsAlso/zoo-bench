"""适配器按世代分流的验证（spec: adapter-contract 的两条新增要求）。

本文件覆盖三类断言，区别在**它们在本机能不能都跑到**：

- **世代无关**：探测结果的形态、失败说明的完整性、完成信号与在飞表的一致性。任何版本下都跑。
- **当前代专属** 与 **上一代专属**：本机一次只能装一个框架版本，另一代的用例**显式跳过**并
  说明原因（design D3 的风险 2）。两代各自的验证靠"同一套用例在两种环境下各跑一遍"——
  跳过不是静默的：报告里能看到它是被跳过、以及为什么。
- **有牙齿的守卫**：故意注入实现违规，确认断言会红。只读一遍测试代码不足以判定其有鉴别力。
"""

from __future__ import annotations

import importlib.metadata as metadata
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import zoo_framework

from zoo_bench import environment, generations
from zoo_bench.adapters import Tier, registry
from zoo_bench.adapters import zoo as zoo_adapter
from zoo_bench.generations import (
    CURRENT_REQUIRED,
    GENERATION_CURRENT,
    GENERATION_LABELS,
    GENERATION_PREVIOUS,
    PREVIOUS_REQUIRED,
    GenerationNotDrivable,
    probe_drive_generation,
)
from zoo_bench.i18n import LANG_ZH
from zoo_bench.render import blocks as blocks_module
from zoo_bench.runner import UnitSpec, measure_matrix
from zoo_bench.workloads.body import body_for_tier
from zoo_bench.workloads.identity import MarkerBody, expected_markers, matches_expected_set

FRAMEWORK = "zoo-framework==9.9.9"
INSTALLED = probe_drive_generation()["generation"]


@pytest.fixture
def adapter() -> Any:
    """一个已 ``setup`` 的 zoo 适配器；用例结束后必定 ``teardown``。"""
    registry.load_builtins()
    instance = zoo_adapter.ZooAdapter()
    instance.setup(workers=4)
    try:
        yield instance
    finally:
        instance.teardown()


def _skip_unless(generation: str) -> None:
    if generation != INSTALLED:
        pytest.skip(f"本机装的是「{INSTALLED}」代驱动面，该用例只对「{generation}」代成立")


def _environment_table_text(subject: dict[str, Any]) -> str:
    """把 ``subject`` 渲染成环境章节的表格文本（只走该章节，不牵动其余章节的模型键）。"""
    model = {"environment": {"subject": subject}}
    return "\n".join(
        " ".join(row)
        for block in blocks_module._environment_blocks(model, lang=LANG_ZH)
        for row in block.rows
    )


# --------------------------------------------------------------------------- 世代探测


def test_probe_names_every_capability_it_examined() -> None:
    """世代判定的依据必须**可读**，不是一句"未知"。

    失败说明要能直接回答"探测了什么、结果如何"——否则排查只能靠猜，而猜错的代价是去读
    被测框架的源码才发现"原来它早就说过"。
    """
    probe = probe_drive_generation()
    assert probe["generation"] in (GENERATION_CURRENT, GENERATION_PREVIOUS)
    assert probe["label"].strip()

    # 两代的要求项都应出现在探测结果里，否则判定依据就是残缺的
    required = set(CURRENT_REQUIRED) | set(PREVIOUS_REQUIRED)
    reported = {item["capability"] for item in probe["capabilities"]}
    assert required <= reported, f"探测结果缺少判定所需项：{sorted(required - reported)}"

    for item in probe["capabilities"]:
        observed = item["observed"]
        assert observed.strip(), f"{item['capability']} 没有给出形态说明"
        assert observed not in {"True", "False"}, (
            f"{item['capability']} 的形态说明是布尔值——世代判定失败时它答不出「探测到的形态是什么」"
        )


def test_probe_does_not_read_version_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    """世代判定**不读版本号字符串**：把版本改成任意值，判定结果都不变。

    诱饵版本刻意**扫一串**而不是只改一个：只改一个值时，若实现里的版本分支恰好把判定推向
    与真值相同的世代，用例照样绿——而它看起来正在守这条线。这一串里同时有"看着更旧""看着
    更新""正是两个已知版本"三类，任何把版本映射到世代的分支都至少被一个诱饵掀翻。

    实测发行元数据里的写法与索引不一致（索引叫 ``0.7.1b0``、轮子元数据里写着
    ``0.7.1-beta``），且未来世代的版本号无从预知——这是判定不能靠版本号的直接理由。

    注意这条只拦得住**输出上的**版本分支；若分支碰巧与真值一致，它拦不住。那种情形由
    源码级守卫 ``test_adapter_does_not_branch_on_version_strings`` 兜。
    """
    baseline = probe_drive_generation()

    for decoy in ("0.0.1", "0.6.0", "0.7.1b0", "0.7.1-beta", "99.0.0", "999.999-bogus"):
        monkeypatch.setattr(metadata, "version", lambda name, _decoy=decoy: _decoy)
        monkeypatch.setattr(zoo_framework, "__version__", decoy)
        assert probe_drive_generation() == baseline, f"版本号 {decoy!r} 改变了世代判定结果"


class _PartialDriveSurface:
    """只提供一部分驱动能力的替身：既不是上一代、也不是当前代。

    刻意拼出"两边各沾一点"的形态（有当前代的构造参数、又有上一代的可写 workers），
    因为它才是真实的误判风险所在——两边都不沾的替身一眼就能看出来。
    """

    def __init__(self, model_name: str | None = None, pool_size: int | None = None) -> None:
        del model_name, pool_size
        self.workers: list[Any] = []


def test_unmatched_drive_surface_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """驱动面无法识别时 MUST 明确失败，MUST NOT 静默退回某一代。"""
    monkeypatch.setattr(generations, "BaseWaiter", _PartialDriveSurface)

    with pytest.raises(GenerationNotDrivable) as caught:
        probe_drive_generation()

    message = str(caught.value)
    # 失败原因必须逐项列出探测了什么、结果如何
    for capability in ("构造参数接受 model_name", "workers 可赋值", "停机入口 shutdown 可调用"):
        assert capability in message, f"失败说明没提到探测过的能力：{capability}"
    assert "存在且接受赋值" in message, "失败说明没有给出形态，只给了结论"
    assert "不会退回" in message, "失败说明没有讲清为什么不退回默认路径"


def test_undrivable_surface_leaves_nothing_to_measure(monkeypatch: pytest.MonkeyPatch) -> None:
    """判定失败时**不留下可驱动的状态**——否则后续会拿一个半装的适配器去产出数字。"""
    monkeypatch.setattr(generations, "BaseWaiter", _PartialDriveSurface)

    instance = zoo_adapter.ZooAdapter()
    with pytest.raises(GenerationNotDrivable):
        instance.setup(workers=4)

    assert not hasattr(instance, "_waiter"), "失败后仍留下了调度器"
    assert not hasattr(instance, "_cond"), "失败后仍留下了完成信号状态"


def test_a_failed_unit_carries_no_measurement_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """世代无法驱动时，该被测对象命名下 MUST 没有任何数据被留档。

    失败若只体现为"数字为零"或"该对象缺席"，读者分不清"没测"与"测出来是零"。
    """
    from zoo_bench import runner

    def failing_child(kind: str, spec: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del kwargs
        if kind == "measure":
            return {"status": "failed", "error": "驱动面无法识别", "stderr": "探测到的形态：..."}
        return {
            "status": "ok",
            "child_elapsed_seconds": 0.0,
            "payload": {"ok": True, "results": {}},
        }

    monkeypatch.setattr(runner, "run_in_child", failing_child)

    result = measure_matrix(
        [
            UnitSpec(
                adapter="zoo",
                framework=FRAMEWORK,
                concurrency=1,
                body_tier_us=300.0,
                warmup_rounds=0,
                measured_rounds=1,
            )
        ],
        with_semantics=False,
    )

    unit = result["units"][0]
    assert unit["status"] == "failed"
    assert "rounds" not in unit, "失败的单元不该有原始样本"
    assert "absolute" not in unit, "失败的单元不该有聚合数据"

    from zoo_bench.report import build_model

    assert build_model(result, lang=LANG_ZH)["subject"] is None, "被测对象不该出现在报告的数据里"


# --------------------------------------------------------------------------- 自述


def test_environment_self_description_names_the_drive_generation() -> None:
    """报告要能回答"这次测量用的是哪个世代的驱动面"（spec 的场景"驱动的假设被记录"）。"""
    recorded = environment.drive_generation()
    assert recorded["generation"] == INSTALLED
    assert recorded["label"].strip()


def test_environment_section_renders_the_drive_generation() -> None:
    """自述里记下的世代必须**出现在报告里**——记了不渲染等于没记。"""
    rendered = _environment_table_text(
        {
            "dist_version": "9.9.9",
            "module_version": "0.0.0",
            "drive_generation": {
                "generation": GENERATION_CURRENT,
                "label": GENERATION_LABELS[GENERATION_CURRENT],
            },
        }
    )
    assert "驱动面世代" in rendered
    assert "结果响应器" in rendered, "渲染出来的是世代名，而不是那一代到底怎么驱动"


def test_unprobed_drive_generation_is_reported_as_such() -> None:
    """探测失败如实记为不可用，**不猜一代**——自述的可信度全在这一点上。"""
    rendered = _environment_table_text(
        {
            "drive_generation": {
                "generation": None,
                "label": None,
                "error": "ImportError: 没有这个包",
            }
        }
    )
    assert "无法判定" in rendered
    assert "ImportError" in rendered, "只说无法判定、不说为什么，等于没说"


# --------------------------------------------------------------------------- 完成信号与在飞表


def test_completion_signal_never_precedes_the_body(adapter: Any) -> None:
    """完成信号不能在执行体结束**之前**触发（spec: 端到端终点的定义）。

    判据取"墙钟不得小于执行体自报耗时"：若信号提前触发，``drain`` 会在执行体还睡着时返回，
    于是墙钟小于它自报的耗时。差为负即说明这条链路的终点取错了。

    **两条断言缺一不可**。只比墙钟与自报值是空的：信号若在派发那一刻就触发，自报值还是
    初始的 0，``墙钟 >= 0`` 恒真——判据看起来在测、其实永远通过。所以还要证明自报值是
    执行体真的量出来的（信号若是提前触发的，它拿不到这个数）。

    这里**刻意不用** ``MarkerBody``：它的返回值是标记而不是耗时，喂给这条判据只会得到
    "1000 秒 > 墙钟"这种无意义的通过。要的是真会自报耗时的执行体。
    """
    body = body_for_tier(20000)
    started = time.perf_counter()
    adapter.submit(body)
    reported = adapter.drain()
    wall = time.perf_counter() - started

    assert len(reported) == 1
    assert reported[0] >= body.target_seconds * 0.5, (
        f"自报耗时是 {reported[0]:.4f}s，而该档位目标是 {body.target_seconds}s——"
        "这个数不是执行体量出来的，完成信号取的时机不对"
    )
    assert wall >= reported[0], (
        f"完成信号在 {wall:.4f}s 就触发了，而执行体自报 {reported[0]:.4f}s——"
        "端到端终点取在了执行体结束之前"
    )


def test_completion_signal_agrees_with_the_inflight_table(adapter: Any) -> None:
    """当前代：收齐完成信号时，在飞表必须已经空了。

    ``settle`` 是先摘在飞表、再投递结果，两者不一致意味着完成判定不可信（信号来自别处），
    此时不给数字比给错数字好。这条守卫证明那道检查**会红**——注入一个"在飞表非空"的读数。
    """
    _skip_unless(GENERATION_CURRENT)

    body = MarkerBody(0, overlap_seconds=0.001)
    adapter.submit(body)
    adapter.drain()

    core = adapter._waiter.core
    assert core.metrics()["inflight"] == 0, "正常路径下在飞表应为空"

    core.metrics = lambda: {"inflight": 1}  # 注入违规读数
    adapter.submit(body)
    with pytest.raises(RuntimeError, match="完成判定不自洽"):
        adapter.drain()


def test_two_bodies_at_concurrency_one_do_not_shadow_each_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两个执行体在并发度 1 下也必须各执行一次。

    在飞表以 ``worker.name`` 为键——重名会让后来的 worker 因"已在飞"被静默跳过，
    表现为"并发跑起来了、其实只有一个在跑"。上一代实测踩过这个坑。

    这里把 ``drain`` 的上限压到几秒：**违约的病症是"永远等不到"**，用生产的十分钟上限
    就成了挂起而不是失败——挂起的用例没人看得懂它测什么。上限只是失败期限，"各执行一次"
    这条判据与它无关。
    """
    monkeypatch.setattr(zoo_adapter, "DRAIN_TIMEOUT_SECONDS", 10.0)

    instance = zoo_adapter.ZooAdapter()
    instance.setup(workers=1)
    try:
        for index in range(2):
            instance.submit(MarkerBody(index))
        observed = instance.drain()
    finally:
        instance.teardown()

    assert matches_expected_set(observed, expected_markers(2))


# --------------------------------------------------------------------------- 装配与停机


def test_current_generation_takes_the_pool_size_from_the_constructor(adapter: Any) -> None:
    """2.1：当前代的模式与池尺寸经构造参数交给调度器。"""
    _skip_unless(GENERATION_CURRENT)

    assert adapter.generation == GENERATION_CURRENT
    assert adapter._waiter.worker_mode == "thread_pool"
    assert adapter._waiter.model.pool_size == 4, "池尺寸不等于声明要的并发度"


def test_previous_generation_writes_the_pool_after_construction(adapter: Any) -> None:
    """2.1：上一代维持构造后赋值。"""
    _skip_unless(GENERATION_PREVIOUS)

    assert adapter.generation == GENERATION_PREVIOUS
    assert adapter._waiter.worker_mode == "thread_pool"
    assert adapter._waiter.resource_pool._max_workers == 4


def test_teardown_is_idempotent(adapter: Any) -> None:
    """契约要求 ``teardown`` 幂等；它会被放在 ``finally`` 里，重复调用不该炸。"""
    adapter.teardown()
    adapter.teardown()


def test_teardown_leaves_no_worker_threads() -> None:
    """停机后不留残余工作线程——池没关会让下一次测量在别人的线程上跑。

    两代的停机方式不同（显式 ``shutdown`` 与显式关池），故这条对两代都要成立：
    它是"停机真的发生了"的判据，而不是某一代的实现细节。
    """
    before = set(threading.enumerate())

    instance = zoo_adapter.ZooAdapter()
    instance.setup(workers=4)
    try:
        for index in range(4):
            instance.submit(MarkerBody(index))
        instance.drain()
    finally:
        instance.teardown()

    leaked = [thread for thread in threading.enumerate() if thread not in before]
    assert not leaked, f"停机后仍有残余线程：{[thread.name for thread in leaked]}"


def test_driven_generation_is_recorded_on_the_unit() -> None:
    """驱动的假设被记录：留档里要能看出这个单元是按哪一代驱动的。"""
    instance = zoo_adapter.ZooAdapter()
    instance.setup(workers=1)
    try:
        assert instance.drive_surface["generation"] == instance.generation
        assert instance.drive_surface["capabilities"], "没有留下探测过程的记录"
    finally:
        instance.teardown()


# --------------------------------------------------------------------------- 包结构守卫


def test_generation_judgment_does_not_branch_on_version_strings() -> None:
    """源码级守卫：世代判定模块里不该出现版本号读取。

    行为用例（``test_probe_does_not_read_version_strings``）只拦得住读 ``metadata.version``
    或 ``__version__`` 的分支；这条从源码上把"读版本"这件事整体挡掉，包括间接写法。
    判据的落点是**判定模块**（``generations.py``），适配器本身不参与判定。
    """
    source = (Path(generations.__file__) or Path()).read_text(encoding="utf-8")
    for forbidden in ("importlib.metadata", "__version__", "packaging"):
        assert forbidden not in source, (
            f"世代判定源码里出现了 {forbidden!r}——判定只允许依据能力探测"
        )


def test_both_generation_paths_are_declared() -> None:
    """两条分流路径都要在源码里存在。

    这条守的是**本机装着一个版本的人**：他跑不到另一代的用例，于是"那一代没用了、
    顺手删掉"是最自然的误判——而被测框架的两代在读者那里都还在用。
    """
    source = (Path(zoo_adapter.__file__) or Path()).read_text(encoding="utf-8")
    for required in ("call_workers", "resource_pool", "on_result", "worker_running_callback"):
        assert required in source, f"分流路径缺少 {required}"


def test_registry_still_exposes_one_subject_adapter() -> None:
    """分流 MUST NOT 把被测对象拆成两个适配器——那会让"被测主体"这个概念分裂（design D1）。"""
    registry.load_builtins()
    subject_adapters = [
        name for name, cls in registry.registered().items() if cls.tier is Tier.UNDER_TEST
    ]
    assert subject_adapters == ["zoo"]
