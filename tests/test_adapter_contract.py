"""适配器契约的验证（spec: adapter-contract）。

核心断言：任一适配器提交 N 个执行体后，**全部执行且各执行一次**。这条不成立时，数字再好看
也是错的。

本文件刻意包含一个**丢弃任务的坏适配器**。只验证"好适配器通过"不足以说明检查有效——一个
恒真的检查看起来同样通过。没有那一条，"等价性验证"就只是装饰。
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest
from _bodies import BoomBody

from zoo_bench.adapters import registry
from zoo_bench.adapters.base import BaseAdapter, OptionalDependencyMissing, Tier
from zoo_bench.workloads.identity import SlotBody, expected_durations, match_one_to_one

CONCURRENCY = 4
BATCH = 12

#: 需要外部基础设施（broker 与独立 worker）、不进默认矩阵的档位（design D3）。其等价性无法
#: 在无 broker 的环境下验证，故从参数化中排除——排除是显式的，不是静默跳过。
NEEDS_INFRASTRUCTURE = frozenset({"celery"})

registry.load_builtins()

_INTERNAL: tuple[str, ...] = tuple(
    sorted(name for name in registry.registered() if name not in NEEDS_INFRASTRUCTURE)
)

_PACKAGE_ADAPTERS = Path(__file__).resolve().parents[1] / "src" / "zoo_bench" / "adapters"


def _has_celery() -> bool:
    return importlib.util.find_spec("celery") is not None


# --------------------------------------------------------------------------- 结构断言


def test_each_tier_is_covered() -> None:
    tiers = {cls.tier for cls in registry.registered().values()}
    for tier in (Tier.UNDER_TEST, Tier.BARE, Tier.STDLIB, Tier.ECOSYSTEM):
        assert tier in tiers, f"缺少档位 {tier}"


def test_subject_goes_through_the_same_contract() -> None:
    """被测框架不是特例：同一契约、同一编排，且必须声明被驱动到哪一层。"""
    subject = registry.get("zoo")
    assert issubclass(subject, BaseAdapter)
    assert subject.tier is Tier.UNDER_TEST
    # 未声明层级时，"派发原语开销"会被读成终端用户延迟
    assert subject.drive_level.strip()


def test_register_rejects_non_adapter() -> None:
    with pytest.raises(TypeError):
        registry.register(int)  # type: ignore[arg-type]


def test_register_rejects_a_duplicate_name() -> None:
    class Clashing(BaseAdapter):
        name = "thread_pool"
        tier = Tier.BARE

        def setup(self, *, workers: int) -> None:
            pass

        def submit(self, body: Callable[[], float]) -> None:
            pass

        def drain(self) -> list[float]:
            return []

        def teardown(self) -> None:
            pass

    with pytest.raises(ValueError, match="thread_pool"):
        registry.register(Clashing)


# --------------------------------------------------------------------------- 等价性


@pytest.mark.parametrize("name", _INTERNAL)
def test_submitted_tasks_run_exactly_once(name: str) -> None:
    adapter = registry.get(name)()
    adapter.setup(workers=CONCURRENCY)
    try:
        for index in range(BATCH):
            adapter.submit(SlotBody(index))
        durations = adapter.drain()
    finally:
        adapter.teardown()

    assert match_one_to_one(durations, expected_durations(BATCH)), (
        f"{name} 未能让每个执行体各执行一次（观测 {len(durations)} 条，期望 {BATCH} 条）"
    )


@pytest.mark.parametrize("name", _INTERNAL)
def test_instance_survives_more_than_one_round(name: str) -> None:
    """``drain`` 之后同一实例应可再跑一轮——预热轮与正式采样轮就靠这个。"""
    adapter = registry.get(name)()
    adapter.setup(workers=CONCURRENCY)
    try:
        for round_index in (0, 1):
            for index in range(BATCH):
                adapter.submit(SlotBody(index))
            durations = adapter.drain()
            assert match_one_to_one(durations, expected_durations(BATCH)), (
                f"{name} 第 {round_index} 轮未能让每个执行体各执行一次"
            )
    finally:
        adapter.teardown()


@pytest.mark.parametrize("name", _INTERNAL)
def test_body_exception_propagates(name: str) -> None:
    """执行体抛异常必须传出来。被吞掉的话，失败会伪装成一条"正常"数据。"""
    adapter = registry.get(name)()
    adapter.setup(workers=CONCURRENCY)
    try:
        adapter.submit(BoomBody(f"{name} 的执行体异常必须传出来"))
        with pytest.raises(RuntimeError, match="必须传出来"):
            adapter.drain()
    finally:
        adapter.teardown()


class _DroppingAdapter(BaseAdapter):
    """故意丢弃任务的坏适配器：只执行第一个，其余静默丢弃。

    它存在的唯一目的是证明上面的等价性检查**有鉴别力**。注意它的返回值"看起来很正常"
    ——一条合理的耗时数字。若只看返回值，它与好适配器无法区分。
    """

    name = "dropping_for_discrimination_test"
    tier = Tier.BARE

    def setup(self, *, workers: int) -> None:
        self._bodies: list[Callable[[], float]] = []

    def submit(self, body: Callable[[], float]) -> None:
        self._bodies.append(body)

    def drain(self) -> list[float]:
        first, self._bodies = self._bodies[0], []
        return [first()]

    def teardown(self) -> None:
        pass


def test_equivalence_check_can_see_a_dropped_task() -> None:
    adapter = _DroppingAdapter()
    adapter.setup(workers=CONCURRENCY)
    for index in range(BATCH):
        adapter.submit(SlotBody(index))
    durations = adapter.drain()
    adapter.teardown()

    # 它的返回值看起来很正常——一条合理的耗时数字……
    assert len(durations) == 1
    # ……但"一一对应"的判据立刻暴露它丢弃了任务
    assert not match_one_to_one(durations, expected_durations(BATCH))


# --------------------------------------------------------------------------- 可选依赖与标注

#: 测试用的外部适配器：定义在 ``src/zoo_bench/`` **之外**，验证"新增对照无需修改 harness"
#: （spec: adapter-contract 的"新增对照 MUST 无需修改既有代码"）。
EXTERNAL_MODULE = "zoo_bench_external_probe"

_EXTERNAL_SOURCE = '''"""测试用的外部适配器，定义在 src/zoo_bench/ 之外。"""

from zoo_bench.adapters import BaseAdapter, Tier, register


@register
class ExternalProbeAdapter(BaseAdapter):
    name = "external_probe"
    tier = Tier.BARE

    def setup(self, *, workers):
        self._queue = []

    def submit(self, body):
        self._queue.append(body)

    def drain(self):
        bodies, self._queue = self._queue, []
        return [body() for body in bodies]

    def teardown(self):
        pass
'''


def test_external_adapter_needs_no_change_inside_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / f"{EXTERNAL_MODULE}.py").write_text(_EXTERNAL_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    loaded, skipped = registry.load_external([EXTERNAL_MODULE])
    assert loaded == [EXTERNAL_MODULE]
    assert skipped == []

    # 它与内置档位走同一条路径，且能通过同一套等价性检查
    adapter = registry.get("external_probe")()
    adapter.setup(workers=CONCURRENCY)
    try:
        for index in range(BATCH):
            adapter.submit(SlotBody(index))
        durations = adapter.drain()
    finally:
        adapter.teardown()

    assert match_one_to_one(durations, expected_durations(BATCH))


def test_missing_optional_dependency_raises_an_actionable_error() -> None:
    """没装 extras 时给可照抄的安装命令，而不是裸 ``ImportError``。"""
    if _has_celery():
        pytest.skip("本环境已安装 celery，无法验证缺失时的行为")

    with pytest.raises(OptionalDependencyMissing, match=r"zoo-bench\[celery\]"):
        importlib.import_module("zoo_bench.adapters.celery_pool")


def test_missing_optional_dependency_is_skipped_not_fatal() -> None:
    """可选依赖缺失是可预期情形（design D3），MUST NOT 让整轮装载失败。"""
    if _has_celery():
        pytest.skip("本环境已安装 celery")

    loaded, skipped = registry.load_builtins()
    assert any(path.endswith("celery_pool") for path in skipped)
    assert not any(path.endswith("celery_pool") for path in loaded)


def test_incomparable_adapter_declares_itself_as_such() -> None:
    """design D3：与进程内派发不同架构的方案必须自标不可对标，否则会污染交叉点结论。

    无 celery 时该模块不可导入（见上一条），故走源码结构检查。这是**刻意留的降级路径**：
    装了 celery 的 CI 上应改回实例检查（tasks 2.6）。
    """
    tree = ast.parse((_PACKAGE_ADAPTERS / "celery_pool.py").read_text(encoding="utf-8"))

    adapter_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name.endswith("Adapter")
    ]
    assert len(adapter_classes) == 1, "celery 适配器模块应恰好定义一个适配器类"

    declared = {
        target.id: ast.unparse(node.value)
        for node in adapter_classes[0].body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert declared["comparable"] == "False"
    assert declared["tier"] == "Tier.ECOSYSTEM"
    assert "不可直接对标" in declared["notes"]
