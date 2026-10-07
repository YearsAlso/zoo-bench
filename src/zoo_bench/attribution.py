"""开销归因：把每任务端到端拆成四段，指出超出对照的部分落在哪一段。

**为什么要有四段**：报告里的"框架开销占比"只回答"贵多少"，不回答"贵在哪"。想优化的人拿到那个
数字之后只能回源码里猜——实测过一次，猜想与事实差得很远（热路径上那两次日志调用只占 1-2%，
而真正的开销在每轮的策略查询里）。

四段的边界**必须落在仪器能独立观测到的时刻**，否则四段之和与端到端对不上（见
:func:`segment_round`）：

===================  ==========================================================
段                     边界
===================  ==========================================================
提交侧                 调用适配器的提交入口 → 它返回
手交                   提交返回 → 执行体真正开始（**由执行体自己打点**）
执行体                 执行体自报的耗时（它自己的起止时刻之差）
回程                   执行体结束 → 排空返回
===================  ==========================================================

被测框架额外把提交侧拆成四份（见 :class:`drill_down`），因为"下一步动哪里"只对它有意义：

- **调度轮**（`execute_service`）——它自己还含下面两项，故要减出来才不重复计数
- **派发**（模型把 worker 交出去，含池的入队）
- **每轮策略查询**（周期/相位/超时各查一次配置）
- **提交侧其余**（适配器自己的记账、完成信号状态等）

**计时区间内不做任何 I/O**（design D5）：逐任务的原始样本攒在内存里，本模块只交出聚合结果。

**哪些组测不了**：执行体如果跑在**另一个进程**里（如 `process_pool`），它的起止时刻在本进程
观测不到——这时如实报"不可测"并说明原因，而不是给一个把 IPC 延迟算进"手交"的分段。那种方案的
跨进程开销是它与其他方案不可比的另一件事，不属于"框架自身的开销"。
"""

from __future__ import annotations

import statistics
import time
from typing import Any

#: 四段之和与端到端允许的相对偏差（**逐任务**比较，不是拿中位数去加——中位数不可加）。
#:
#: 理论上应恒为 0：四段的边界取自同一组时刻。留一点余量只为浮点与时钟读取本身，故给得很紧；
#: 放宽到能容下"某段被漏算或被算两遍"就失去了鉴别力。
CONSISTENCY_TOLERANCE = 0.02

#: 细分那一层**不设"和必须等于提交侧"的判据**。
#:
#: 理由是实测：三个接缝在区间上确实嵌套（父的起止必然包住子的起止），但各项**独立测量**之和
#: 会超过调度轮的总量（实测约 3%——例如各项 81.0 微秒对调度轮 71.7 微秒）。这一点尚未解释
#: 清楚，故**不冒充划分**：细分项按"独立测量"呈现，并给出超出量，由读者自己判断。
#: 待解释的开口记在变更的 design 里，不在报告里假装它成立。

#: 参与细分的框架接缝，逐条对应结论里的一个细分项。
#:
#: **刻意只取两代都存在、且形态相同的入口**，这样细分本身不随世代改变（世代差异体现在数值上，
#: 而不是"有没有这一项"）：上一代每轮的策略查询次数接近 0，当前代每个 worker 每轮 3 次
#: ——那正是两代之间最值得看的一处差异。
SEAL_SCHEDULING_ROUND = "attribution.seal.scheduling_round"
SEAL_DISPATCH = "attribution.seal.dispatch"
#: 策略查询：框架每轮**逐个 worker**问配置的三处入口。
#:
#: 包"这三处"而不是包底层的 `ParamsFactory.get_params`：实测 `get_params` 本身只占其中约
#: 3-4 微秒，其余开销在解析函数自身（逐次属性查找、worker.name 的拼装等）。只包底层入口会让
#: 这一项看着很小，而把大头落进"调度轮其余"——那正是这个维度要避免的模糊。
SEAL_POLICY_LOOKUP = "attribution.seal.policy_lookup"

#: 四段的键（顺序即报告里的呈现顺序）。
SEGMENT_KEYS: tuple[str, ...] = (
    "submit_side_seconds",
    "handoff_seconds",
    "body_seconds",
    "return_seconds",
)

#: 细分项（**独立测量**，不是划分）：三个包装框架接缝量到的，外加一个"提交侧里未被它们覆盖"
#: 的余额。
DRILL_KEYS: tuple[str, ...] = (
    "scheduling_round_seconds",
    "dispatch_seconds",
    "policy_lookup_seconds",
    "submit_side_other_seconds",
)


def calibrated_iterations(tier_us: float) -> int:
    """把档位目标耗时换算成迭代次数（每个组只算一次，别让每个执行体各算一遍）。"""
    from .workloads.body import calibrate_iterations

    return calibrate_iterations(tier_us / 1_000_000.0)


class SegmentBody:
    """带打点的执行体：跑与测量路径**同一份**负载，额外记录自己的起止时刻。

    模块级、只存普通字段，故可 pickle——多进程档位要把它送到子进程。那种档位里它的打点发生在
    另一个进程，本进程读不到（:func:`probe_group` 据此判"不可测"，而不是给一个错的分段）。
    """

    def __init__(self, iterations: int) -> None:
        self.iterations = iterations
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def __call__(self) -> float:
        from .workloads.body import run_iterations

        self.started_at = time.perf_counter()
        elapsed, _ = run_iterations(self.iterations)
        self.finished_at = time.perf_counter()
        return elapsed


def _median(values: list[float]) -> float:
    return float(statistics.median(values))


def segment_round(
    bodies: list[SegmentBody], submit_spans: list[tuple[float, float]], drained_at: float
) -> list[dict[str, float]]:
    """把一轮里每个任务的四段算出来。

    四段的边界取自同一组时刻，故**逐任务**满足 ``提交侧 + 手交 + 执行体 + 回程 = 端到端``
    （端到端 = 排空返回时刻 − 该任务的提交开始时刻）。这条恒等式是自洽判据的落点：把它算错
    一处（例如用轮开始时刻代替任务的提交时刻）就不等了。

    Args:
        bodies: 本轮提交的执行体，顺序与 ``submit_spans`` 一致。
        submit_spans: 每个任务的 ``(调用提交入口的时刻, 提交返回的时刻)``。
        drained_at: 排空返回的时刻。

    Returns:
        每个任务一项，含四段与端到端（秒）。

    Raises:
        RuntimeError: 执行体没在本进程里跑（起止时刻读不到）。
    """
    rows: list[dict[str, float]] = []
    for body, (submitted_at, submitted_back_at) in zip(bodies, submit_spans, strict=True):
        if body.started_at is None or body.finished_at is None:
            raise RuntimeError("attribution.reason.cross_process")
        rows.append(
            {
                "submit_side_seconds": submitted_back_at - submitted_at,
                "handoff_seconds": body.started_at - submitted_back_at,
                "body_seconds": body.finished_at - body.started_at,
                "return_seconds": drained_at - body.finished_at,
                "end_to_end_seconds": drained_at - submitted_at,
            }
        )
    return rows


def max_consistency_deviation(rows: list[dict[str, float]]) -> float:
    """四段之和与端到端的最大相对偏差（逐任务）。"""
    worst = 0.0
    for row in rows:
        total = (
            row["submit_side_seconds"]
            + row["handoff_seconds"]
            + row["body_seconds"]
            + row["return_seconds"]
        )
        end_to_end = row["end_to_end_seconds"]
        if end_to_end <= 0:
            continue
        worst = max(worst, abs(total - end_to_end) / end_to_end)
    return worst


class drill_down:
    """包装框架的几个接缝，把提交侧再分四份。

    **不改框架代码**：只在本进程里替换掉几个类属性，退出时还原。用到的接缝逐条声明并随结论
    发布，读者据此判断这份分解是按什么口径得到的。

    **三个接缝是嵌套的**（派发与策略查询都发生在调度轮内部），故不能用它们的和当"细分结果"：
    那样会把调度轮算三遍。这里按"谁包含谁"减出四个**互不重叠**的桶（见 :meth:`buckets`），
    合起来正好等于提交侧——细分只重排，不新增也不吞掉时间。
    """

    def __init__(self) -> None:
        self.totals: dict[str, float] = {
            SEAL_SCHEDULING_ROUND: 0.0,
            SEAL_DISPATCH: 0.0,
            SEAL_POLICY_LOOKUP: 0.0,
        }
        self._originals: list[tuple[Any, str, Any]] = []
        #: **实际装上**的接缝（用来声明口径）与**缺席**的接缝（用来解释少了哪几项）。
        #: 两者必须由插桩器如实回报：拿一张静态清单当"用到了"会在上一代上把没装成的也列进去，
        #: 同一项同时出现在"用到"和"缺席"两处——实测踩过。
        self.installed: list[str] = []
        self.unavailable: list[str] = []

    def _wrap(self, owner: Any, attribute: str, label: str) -> None:
        # **取类字典里的描述符本身，不取 getattr 的结果**：classmethod 经 getattr 拿出来是
        # 一个已绑定到 cls 的*方法*，`isinstance(..., classmethod)` 为假——那会让下面走普通
        # 方法分支，把 `(cls, path, ...)` 原样转给不带 cls 的函数（实测报
        # "got multiple values for argument 'default_value'"）。
        raw = vars(owner)[attribute]
        self._originals.append((owner, attribute, raw))

        if isinstance(raw, classmethod):
            inner = raw.__func__

            def timed_classmethod(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                try:
                    return inner(*args, **kwargs)
                finally:
                    self.totals[label] += time.perf_counter() - started

            setattr(owner, attribute, classmethod(timed_classmethod))
            self.installed.append(label)
            return

        def timed(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return raw(*args, **kwargs)
            finally:
                self.totals[label] += time.perf_counter() - started

        setattr(owner, attribute, timed)
        self.installed.append(label)

    def __enter__(self) -> drill_down:
        from zoo_framework.core.waiter.base_waiter import BaseWaiter

        self._wrap(BaseWaiter, "execute_service", SEAL_SCHEDULING_ROUND)
        self._wrap(BaseWaiter, "_dispatch_worker", SEAL_DISPATCH)
        # 策略查询那三处入口在当前代有、在上一代没有——**缺席要如实记下来**，不能说成"查了、
        # 花了 0 微秒"：两者含义相反（一个是不需要查，一个是没有这一层）。
        try:
            from zoo_framework.core.waiter.dispatch_core import WorkerDispatchCore
        except ImportError:
            self.unavailable.append(SEAL_POLICY_LOOKUP)
        else:
            for name in ("resolve_period", "resolve_phase", "resolve_run_timeout"):
                self._wrap(WorkerDispatchCore, name, SEAL_POLICY_LOOKUP)
        return self

    def __exit__(self, *exc: object) -> None:
        for owner, attribute, original in reversed(self._originals):
            setattr(owner, attribute, original)
        self._originals.clear()

    def buckets(self, concurrency: int, submit_side_seconds: float) -> dict[str, float]:
        """把一轮的累计量折成四项（每任务尺度，秒）。

        **它们是独立测量，不保证可加**（见上）。故这里不强行凑成一个划分：被量到的两项照实给，
        "调度轮其余"与"提交侧其他"按余额给（余额为负时取 0），超出量由
        :func:`drill_overlap` 单独报出来。

        Args:
            concurrency: 该轮提交的执行体个数（累计量是整轮的，要折算到每任务）。
            submit_side_seconds: 该轮的提交侧累计（每任务尺度）。
        """
        dispatch = self.totals[SEAL_DISPATCH] / concurrency
        policy = self.totals[SEAL_POLICY_LOOKUP] / concurrency
        round_total = self.totals[SEAL_SCHEDULING_ROUND] / concurrency
        round_remainder = max(0.0, round_total - dispatch - policy)
        return {
            "scheduling_round_seconds": round_remainder,
            "dispatch_seconds": dispatch,
            "policy_lookup_seconds": policy,
            "submit_side_other_seconds": max(
                0.0, submit_side_seconds - dispatch - policy - round_remainder
            ),
        }


def _run_round(
    adapter: Any, iterations: int, concurrency: int, *, drill: bool
) -> tuple[list[dict[str, float]], dict[str, Any] | None]:
    """跑一轮：提交 ``concurrency`` 个带打点的执行体，收集四段与（可选）细分桶。

    细分那一层同时带回**该轮自己的提交侧**（每任务尺度），使自洽判据能**逐轮**比较——
    拿一列中位数去加是错的：中位数不可加，那样比出来的偏差与分解对不对无关。
    """
    bodies = [SegmentBody(iterations) for _ in range(concurrency)]
    spans: list[tuple[float, float]] = []
    buckets: dict[str, float] | None = None

    instrument = drill_down() if drill else None
    if instrument is not None:
        instrument.__enter__()
    try:
        for body in bodies:
            started = time.perf_counter()
            adapter.submit(body)
            spans.append((started, time.perf_counter()))
        submitted_side_total = sum(back - start for start, back in spans)
        adapter.drain()
        drained_at = time.perf_counter()
        if instrument is not None:
            round_submit_side = submitted_side_total / concurrency
            buckets = {
                "buckets": instrument.buckets(concurrency, round_submit_side),
                "submit_side_seconds": round_submit_side,
                "installed": list(instrument.installed),
                "unavailable": list(instrument.unavailable),
            }
    finally:
        if instrument is not None:
            instrument.__exit__()

    return segment_round(bodies, spans, drained_at), buckets


def probe_group(
    adapter_name: str,
    *,
    tier_us: float,
    concurrency: int,
    warmup_rounds: int,
    measured_rounds: int,
    drill: bool = False,
    extra_modules: tuple[str, ...] = (),
) -> dict[str, Any]:
    """量一个 (适配器, 档位, 并发度) 组的四段；``drill`` 时另把提交侧拆成四份。

    Args:
        adapter_name: 适配器标识。
        tier_us: 执行体档位（微秒）。
        concurrency: 并发度，即每轮提交的执行体个数。
        warmup_rounds: 预热轮数，其数据不进入统计。
        measured_rounds: 正式采样轮数。
        drill: 是否包装框架接缝以细分提交侧（**只对被测框架做**——"下一步动哪里"只对它有意义，
            且对照方案的接缝不同、细分项不可比）。开启时每个采样轮**交替**跑一轮不插桩的，
            使插桩自身的成本能与插桩后的读数配对比较，而不是被机器漂移淹没。
        extra_modules: 需要额外装载的外部适配器模块。

    Returns:
        含四段中位数、端到端、自洽判据、细分桶与插桩成本的结论。跨进程档位返回
        ``status: "not_measurable"`` 与原因。
    """
    from .adapters import registry

    registry.load_builtins()
    if extra_modules:
        registry.load_external(extra_modules)
    adapter_cls = registry.get(adapter_name)

    iterations = calibrated_iterations(tier_us)
    adapter = adapter_cls()
    rows: list[dict[str, float]] = []
    drilled: list[dict[str, Any]] = []
    baseline: list[float] = []
    adapter.setup(workers=concurrency)
    try:
        for _ in range(warmup_rounds):
            _run_round(adapter, iterations, concurrency, drill=False)

        for _ in range(measured_rounds):
            if drill:
                # 交替：先量一轮不插桩的（作为插桩成本的参照），再量一轮插桩的。两轮紧挨着，
                # 机器状态的差异就落不进"插桩成本"这个差里。
                plain_rows, _ = _run_round(adapter, iterations, concurrency, drill=False)
                baseline.extend(row["end_to_end_seconds"] for row in plain_rows)
            collected, buckets = _run_round(adapter, iterations, concurrency, drill=drill)
            rows.extend(collected)
            if buckets is not None:
                drilled.append(buckets)
    except RuntimeError as exc:
        adapter.teardown()
        return {
            "adapter": adapter_name,
            "adapter_tier": str(adapter_cls.tier),
            "comparable": bool(adapter_cls.comparable),
            "tier_us": tier_us,
            "concurrency": concurrency,
            "status": "not_measurable",
            "reason": str(exc),
            "seals": [],
            "unavailable_seals": [],
        }
    else:
        adapter.teardown()

    deviation = max_consistency_deviation(rows)
    end_to_end = _median([row["end_to_end_seconds"] for row in rows])
    submit_side = _median([row["submit_side_seconds"] for row in rows])
    buckets = (
        {key: _median([item["buckets"][key] for item in drilled]) for key in DRILL_KEYS}
        if drilled
        else {}
    )
    # **逐轮**比较细分之和与该轮自己的提交侧。拿一列中位数去加是错的：中位数不可加，
    # 那样得到的偏差反映的是"中位数不满足可加性"，与分解对不对无关（实测差 17%）。
    return {
        "adapter": adapter_name,
        "adapter_tier": str(adapter_cls.tier),
        "comparable": bool(adapter_cls.comparable),
        "tier_us": tier_us,
        "concurrency": concurrency,
        "status": "ok",
        "reason": "",
        "rounds": measured_rounds,
        "sample_count": len(rows),
        "segments": {
            "submit_side_seconds": submit_side,
            "handoff_seconds": _median([row["handoff_seconds"] for row in rows]),
            "body_seconds": _median([row["body_seconds"] for row in rows]),
            "return_seconds": _median([row["return_seconds"] for row in rows]),
        },
        "end_to_end_seconds": end_to_end,
        "consistency": {
            "max_deviation": deviation,
            "tolerance": CONSISTENCY_TOLERANCE,
            "ok": deviation <= CONSISTENCY_TOLERANCE,
        },
        "seals": _used_seals(drilled),
        "drill_down": buckets,
        "drill_overlap": drill_overlap(drilled),
        "unavailable_seals": sorted(
            {name for item in drilled for name in item.get("unavailable", [])}
        ),
        "instrumentation": (
            {
                "baseline_end_to_end_seconds": _median(baseline),
                "delta_seconds": end_to_end - _median(baseline),
            }
            if baseline
            else {}
        ),
    }


def _used_seals(drilled: list[dict[str, Any]]) -> list[str]:
    """**实际装上**的接缝标签（由插桩器回报，不是照静态清单抄）。"""
    return sorted({label for item in drilled for label in item.get("installed", [])})


def drill_overlap(drilled: list[dict[str, Any]]) -> dict[str, Any]:
    """细分各项之和**超出**提交侧的最大相对量（逐轮）。

    **这不是判否用的容差，而是一条要如实发布的量**：各项独立测量之和会超过它们共同所在的
    提交侧（实测约 3%），超出多少读者应当知道——否则他会把这些数当成一个可以相加的划分。
    """
    if not drilled:
        return {"max_overlap_ratio": None, "note": ""}

    worst = 0.0
    for item in drilled:
        submit_side = item["submit_side_seconds"]
        if submit_side <= 0:
            continue
        worst = max(worst, (sum(item["buckets"].values()) - submit_side) / submit_side)
    return {
        "max_overlap_ratio": worst,
        "note": "report.attribution.drill_note",
    }
