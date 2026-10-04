"""子进程侧入口：在一个**全新解释器**里跑完一个测量单元或一次等价性验证。

为什么用独立解释器而不是 ``multiprocessing``：

- **隔离更彻底**。被测框架的 ``@cage`` 单例、``reactor_map``、gevent 的 monkey patch、
  asyncio 事件循环都不会与本进程或其他单元共享（design D4）。
- **规格只用 JSON 传递**，不必给"spec 必须可 pickle"这类隐性约束。
- **失败可诊断**。子进程的 stderr 能被完整捕获，失败时把真实堆栈收进结果，而不是只留一句
  "子进程异常退出"。

用法（由 :mod:`zoo_bench.runner` 调用，不面向人）::

    python -m zoo_bench.worker <verify|measure> <spec-json> <out-path>

退出码 0 表示结果已写入 ``out-path``；非 0 表示失败，原因在 stderr。
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from .adapters import Tier, registry
from .attribution import probe_group
from .semantics import probe_semantics
from .workloads.body import body_for_tier
from .workloads.identity import MarkerBody, expected_markers, matches_expected_set

#: 等价性验证的批量：够看出"丢任务"与"重复执行"，又不至于把每个适配器都拖慢。
VERIFY_BATCH = 4


def _load_adapter(spec: dict[str, Any]) -> type:
    """装载内置档位与此单元声明的外部模块，再取出适配器类。"""
    registry.load_builtins()
    extra = list(spec.get("extra_modules") or [])
    if extra:
        registry.load_external(extra)
    return registry.get(spec["adapter"])


def verify_equivalence(spec: dict[str, Any]) -> dict[str, Any]:
    """断言"提交 N 个执行体、全部执行且各执行一次"（spec: adapter-contract）。

    身份取自执行体的**返回值**（见 :mod:`zoo_bench.workloads.identity`），不取自耗时：
    墙钟在共享机器上有一层绝对量级的停顿尾部，按耗时判定会让这道门禁约 15% 的跑随机变红。
    """
    adapter_cls = _load_adapter(spec)
    concurrency = int(spec["concurrency"])

    bodies = [MarkerBody(index) for index in range(VERIFY_BATCH)]
    expected = expected_markers(VERIFY_BATCH)

    adapter = adapter_cls()
    adapter.setup(workers=concurrency)
    try:
        for body in bodies:
            adapter.submit(body)
        observed = adapter.drain()
    finally:
        adapter.teardown()

    return {
        "adapter": adapter_cls.name,
        "ok": matches_expected_set(observed, expected),
        "expected_count": VERIFY_BATCH,
        "observed_count": len(observed),
        "expected_markers": expected,
        "observed_markers": observed,
    }


def _measure_round(adapter: Any, body: Any, batch: int) -> dict[str, Any]:
    """一轮：提交 ``batch`` 个执行体、等到全部完成，记录墙钟与各执行体自报耗时。"""
    start = time.perf_counter()
    for _ in range(batch):
        adapter.submit(body)
    body_seconds = adapter.drain()
    return {
        "batch": batch,
        "wall_seconds": time.perf_counter() - start,
        "body_seconds": list(body_seconds),
    }


def measure_unit(spec: dict[str, Any]) -> dict[str, Any]:
    """完成一个测量单元的预热与正式采样。"""
    adapter_cls = _load_adapter(spec)
    concurrency = int(spec["concurrency"])

    body = body_for_tier(float(spec["body_tier_us"]))
    adapter = adapter_cls()
    rounds: list[dict[str, Any]] = []

    # 计时区间之外：子进程派生、依赖导入、线程池创建都发生在 setup 里
    adapter.setup(workers=concurrency)
    try:
        for _ in range(int(spec["warmup_rounds"])):
            _measure_round(adapter, body, concurrency)
        for index in range(int(spec["measured_rounds"])):
            record = _measure_round(adapter, body, concurrency)
            record["round"] = index
            rounds.append(record)
    finally:
        adapter.teardown()

    return {
        "pid": os.getpid(),
        "body_iterations": body.iterations,
        "body_checksum": body.checksum,
        "rounds": rounds,
        "adapter": {
            "name": adapter_cls.name,
            "tier": str(adapter_cls.tier),
            "comparable": adapter_cls.comparable,
            "notes": adapter_cls.notes,
            "drive_level": adapter_cls.drive_level,
        },
    }


def probe_attribution_task(spec: dict[str, Any]) -> dict[str, Any]:
    """把一个 (适配器, 档位, 并发度) 组的四段量出来。

    **是否细分由被测与否决定，不由调用方指定**：细分是给"下一步动哪里"用的，只对被测框架有意义；
    让调用方传这个标志会多一处可传错的东西，而适配器自己知道自己是哪一档（``tier``）。
    """
    adapter_cls = _load_adapter(spec)
    drill = adapter_cls.tier is Tier.UNDER_TEST
    return probe_group(
        adapter_cls.name,
        tier_us=float(spec["body_tier_us"]),
        concurrency=int(spec["concurrency"]),
        warmup_rounds=int(spec["warmup_rounds"]),
        measured_rounds=int(spec["measured_rounds"]),
        drill=drill,
        extra_modules=tuple(spec.get("extra_modules") or ()),
    )


def probe_semantics_task(spec: dict[str, Any]) -> dict[str, Any]:
    """调度语义维度的探查。

    参数不被使用，但与其他 kind 统一为"接收 spec"——这样 ``_KINDS`` 的分发表不必有特例。
    """
    del spec
    return probe_semantics()


_KINDS = {
    "verify": verify_equivalence,
    "measure": measure_unit,
    "semantics": probe_semantics_task,
    "attribution": probe_attribution_task,
}

_USAGE = "用法: python -m zoo_bench.worker <verify|measure|semantics|attribution> <spec-json> <out-path>"


def main(argv: list[str] | None = None) -> int:
    """子进程主入口。

    Args:
        argv: 命令行参数；None 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码：0 表示结果已落盘，2 表示用法错误，1 表示执行失败。
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        print(_USAGE, file=sys.stderr)
        return 2

    kind, spec_json, out_path = arguments
    task = _KINDS.get(kind)
    if task is None:
        print(f"未知的 kind {kind!r}；可用：{sorted(_KINDS)}", file=sys.stderr)
        return 2

    try:
        spec = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        print(f"spec 不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    try:
        payload = task(spec)
    except BaseException:
        # 真实堆栈进 stderr，供父进程收进失败原因
        traceback.print_exc()
        return 1

    Path(out_path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
