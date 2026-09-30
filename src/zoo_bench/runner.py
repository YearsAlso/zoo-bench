"""测量编排。

职责边界：

- 每个测量单元跑在**独立子进程**里（design D4），由 :mod:`zoo_bench.worker` 执行；
  本模块只负责派生、超时、收结果，以及把失败当成一等结果而不是异常。
- 子进程内完成：适配器装载 → ``setup``（不计时）→ 预热 → 正式采样 → ``teardown``。
  计时区间的两端都在子进程内，故进程派生、依赖导入、线程池创建都不计入被测耗时。
- 父进程组装结果，并把**同运行内相对比**与**绝对耗时**分成两组（design D6）：前者是选型
  证据的主体，后者必须标注不可跨运行比较。
- 自检结果随结果一并产出（spec: measurement-protocol）。
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .metrics import summarize

#: 单个子进程的上限（秒）。不是被测指标，只用于避免挂死时整轮无终止。
CHILD_TIMEOUT_SECONDS = 900.0

DEFAULT_WARMUP_ROUNDS = 3
DEFAULT_MEASURED_ROUNDS = 30

#: 等价性验证的并发度。
DEFAULT_VERIFY_CONCURRENCY = 4

#: 跨执行体档位的框架开销允许的倍数上限。超过即判自检失败——说明执行体成本被漏算进了开销
#: （design D5 的验证要求：开销应基本恒定，占端到端比例随执行体变长而下降）。
OVERHEAD_TIER_RATIO_LIMIT = 3.0

#: 执行体实测耗时与档位设定值的允许偏差。
BODY_DEVIATION_TOLERANCE = 0.30

#: 绝对耗时数字的固定标注。措辞刻意直白：CI runner 是共享虚拟机，跨运行引用这些数字
#: 一句话就能被否（design D6）。
ABSOLUTE_NOTE = "仅在同一次运行内部有效，不可跨运行比较"


@dataclass(frozen=True)
class UnitSpec:
    """一个测量单元。

    Attributes:
        adapter: 适配器标识。
        framework: 被测框架的发行版本（如 ``zoo-framework==0.6.0``）。
        concurrency: 并发度，即每轮提交的执行体个数。
        body_tier_us: 执行体目标耗时（微秒）。
        warmup_rounds: 预热轮数，其数据不进入统计。
        measured_rounds: 正式采样轮数。
        extra_modules: 需要额外装载的外部适配器模块（点分路径）。
    """

    adapter: str
    framework: str
    concurrency: int
    body_tier_us: float
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS
    measured_rounds: int = DEFAULT_MEASURED_ROUNDS
    extra_modules: tuple[str, ...] = field(default_factory=tuple)


def run_in_child(
    kind: str, spec: dict[str, Any], *, timeout: float = CHILD_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """在一个全新解释器里执行 ``kind``。

    失败**不抛异常**，而是返回 ``{"status": "failed", ...}``——"某档失败、其余档照常完成"
    是设计要求（design D4），把失败做成异常会毁掉整轮。

    Args:
        kind: ``"verify"`` 或 ``"measure"``。
        spec: 传给子进程的规格（须可 JSON 序列化）。
        timeout: 子进程上限（秒）。

    Returns:
        ``{"status": "ok", "child_elapsed_seconds": float, "payload": dict}`` 或
        ``{"status": "failed", "error": str, "stderr": str}``。
    """
    # 把父进程的 import 路径传下去，使外部适配器模块在全新解释器里也能被装载
    search_path = os.pathsep.join(entry for entry in sys.path if entry)

    with tempfile.TemporaryDirectory(prefix="zoo-bench-") as workdir:
        out_path = Path(workdir) / "result.json"
        command = [
            sys.executable,
            "-m",
            "zoo_bench.worker",
            kind,
            json.dumps(spec, ensure_ascii=False),
            str(out_path),
        ]

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, "PYTHONPATH": search_path},
            )
        except subprocess.TimeoutExpired:
            return {"status": "failed", "error": f"子进程超出 {timeout:.0f}s 上限", "stderr": ""}

        elapsed = time.perf_counter() - started
        if completed.returncode != 0 or not out_path.exists():
            return {
                "status": "failed",
                "error": f"子进程退出码 {completed.returncode}",
                "stderr": completed.stderr[-4000:],
            }

        return {
            "status": "ok",
            "child_elapsed_seconds": elapsed,
            "payload": json.loads(out_path.read_text(encoding="utf-8")),
        }


def _absolute_from_rounds(rounds: list[dict[str, Any]], spec: UnitSpec) -> dict[str, Any]:
    """由各轮原始数据算出该单元的绝对耗时组。"""
    batch = spec.concurrency
    walls = [float(record["wall_seconds"]) for record in rounds]
    bodies = [float(value) for record in rounds for value in record["body_seconds"]]
    per_task = [wall / batch for wall in walls]

    end_to_end = summarize(per_task)
    body = summarize(bodies)
    end_to_end_median = float(end_to_end["median"])
    body_median = float(body["median"])
    overhead = end_to_end_median - body_median

    return {
        "note": ABSOLUTE_NOTE,
        "concurrency": batch,
        "end_to_end_per_task_seconds": end_to_end,
        "body_seconds": body,
        "round_wall_seconds": summarize(walls),
        "throughput_per_second": batch / end_to_end_median if end_to_end_median else 0.0,
        "framework_overhead_seconds": overhead,
        "framework_overhead_ratio": overhead / end_to_end_median if end_to_end_median else 0.0,
    }


def body_deviation_check(absolute: dict[str, Any], spec: UnitSpec) -> dict[str, Any]:
    """执行体实测耗时与档位设定值的偏差（spec: measurement-protocol 的自检要求）。"""
    target = spec.body_tier_us / 1_000_000.0
    observed = float(absolute["body_seconds"]["median"])
    deviation = (observed - target) / target if target else 0.0

    return {
        "body_target_seconds": target,
        "body_observed_median_seconds": observed,
        "body_deviation": deviation,
        "body_deviation_ok": abs(deviation) <= BODY_DEVIATION_TOLERANCE,
    }


def check_overhead_across_tiers(units: list[dict[str, Any]]) -> dict[str, Any]:
    """跨执行体档位的框架开销应落在同一量级。

    这是 design D5 那个 bug 的鉴别判据：若埋点口径有误、把执行体成本漏算进了开销，开销会
    随档位单调显著上升。**只看"最短档的数字好看"发现不了这个问题**。
    """
    groups: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for unit in units:
        if unit["status"] != "ok":
            continue
        key = (unit["spec"]["adapter"], unit["spec"]["concurrency"])
        groups.setdefault(key, []).append(unit)

    checks: list[dict[str, Any]] = []
    for (adapter, concurrency), group in sorted(groups.items()):
        ordered = sorted(group, key=lambda unit: unit["spec"]["body_tier_us"])
        if len(ordered) < 2:
            continue

        overheads = [float(unit["absolute"]["framework_overhead_seconds"]) for unit in ordered]
        baseline = overheads[0]
        if baseline <= 0:
            # 最短档开销非正时比值无意义；如实记录原始值，不据此判否（避免误报）
            checks.append(
                {
                    "adapter": adapter,
                    "concurrency": concurrency,
                    "body_tiers_us": [unit["spec"]["body_tier_us"] for unit in ordered],
                    "overheads_seconds": overheads,
                    "growth": None,
                    "ok": True,
                    "note": "最短档开销非正，比值判据不适用，已记录原始值供人工判读",
                }
            )
            continue

        growth = max(overheads) / baseline
        checks.append(
            {
                "adapter": adapter,
                "concurrency": concurrency,
                "body_tiers_us": [unit["spec"]["body_tier_us"] for unit in ordered],
                "overheads_seconds": overheads,
                "growth": growth,
                "ok": growth <= OVERHEAD_TIER_RATIO_LIMIT,
                "note": "",
            }
        )

    return {"checks": checks, "ok": all(check["ok"] for check in checks)}


def check_process_isolation(units: list[dict[str, Any]]) -> dict[str, Any]:
    """每个测量单元都跑在各自独立的子进程里（design D4）。"""
    parent_pid = os.getpid()
    child_pids = [
        int(unit["process"]["pid"]) for unit in units if unit["status"] == "ok" and "process" in unit
    ]

    return {
        "parent_pid": parent_pid,
        "child_pids": child_pids,
        "all_distinct": len(set(child_pids)) == len(child_pids),
        "none_equals_parent": all(pid != parent_pid for pid in child_pids),
    }


def _relative_block(units: list[dict[str, Any]]) -> dict[str, Any]:
    """同一次运行内的相对比——选型证据的主体（design D6）。

    对每个 (并发度, 执行体档位) 组合，以被测框架为基准给出各对照方案相对它的倍数。比值由
    同一台机器、同一时刻的两两对照得出，因此对该次运行的整体漂移不敏感——这正是它可作为
    证据、而绝对耗时不可的原因。

    自标 ``comparable = False`` 的方案（design D3）不进入比值，但会被列名，以免读者以为它们
    被遗漏。
    """
    by_key: dict[tuple[int, float], dict[str, dict[str, Any]]] = {}
    for unit in units:
        if unit["status"] != "ok":
            continue
        key = (unit["spec"]["concurrency"], unit["spec"]["body_tier_us"])
        by_key.setdefault(key, {})[unit["spec"]["adapter"]] = unit

    comparisons: list[dict[str, Any]] = []
    for (concurrency, tier), group in sorted(by_key.items()):
        subject = group.get("zoo")
        if subject is None:
            continue
        subject_median = float(subject["absolute"]["end_to_end_per_task_seconds"]["median"])
        if not subject_median:
            continue

        ratios: dict[str, float] = {}
        excluded: list[str] = []
        for adapter, unit in sorted(group.items()):
            if adapter == "zoo":
                continue
            if not unit["adapter"]["comparable"]:
                excluded.append(adapter)
                continue
            other_median = float(unit["absolute"]["end_to_end_per_task_seconds"]["median"])
            ratios[adapter] = other_median / subject_median

        comparisons.append(
            {
                "concurrency": concurrency,
                "body_tier_us": tier,
                "subject": "zoo",
                "subject_median_seconds": subject_median,
                "ratios_vs_subject": ratios,
                "excluded_incomparable": excluded,
                "note": "比值 >1 表示该对照方案比被测框架慢；仅在同一次运行内成立",
            }
        )

    return {
        "note": "同一次运行内的相对比，是选型证据的主体；对本次运行的整体漂移不敏感",
        "comparisons": comparisons,
    }


def _run_self_check(
    units: list[dict[str, Any]], verification: dict[str, Any]
) -> dict[str, Any]:
    """汇总三类自检（spec: measurement-protocol 的"测量过程自身 MUST 被自检"）。"""
    body_checks = [
        {"adapter": unit["spec"]["adapter"], "concurrency": unit["spec"]["concurrency"], **unit["checks"]["body"]}
        for unit in units
        if unit["status"] == "ok" and "checks" in unit
    ]
    body_ok = all(check["body_deviation_ok"] for check in body_checks)

    overhead = check_overhead_across_tiers(units)
    equivalence_ok = all(
        result["status"] == "ok" and result["payload"]["ok"] for result in verification.values()
    )

    return {
        "body_deviation": {"checks": body_checks, "ok": body_ok},
        "overhead_across_tiers": overhead,
        "adapter_equivalence": {"results": verification, "ok": equivalence_ok},
        "ok": body_ok and overhead["ok"] and equivalence_ok,
    }


def measure_matrix(
    specs: list[UnitSpec],
    *,
    verify_adapters: bool = True,
    verify_concurrency: int = DEFAULT_VERIFY_CONCURRENCY,
) -> dict[str, Any]:
    """跑完一批测量单元，返回运行结果。

    Args:
        specs: 要跑的单元清单。
        verify_adapters: 是否先对每个用到的适配器做等价性验证。
        verify_concurrency: 等价性验证使用的并发度。

    Returns:
        含 ``run`` / ``units`` / ``verification`` / ``process_isolation`` / ``self_check`` /
        ``relative`` 的运行结果。可 ``json.dumps`` 落盘。
    """
    started_at = time.time()
    run_started = time.perf_counter()

    verification: dict[str, Any] = {}
    if verify_adapters:
        for adapter in sorted({spec.adapter for spec in specs}):
            extra: list[str] = []
            for spec in specs:
                if spec.adapter == adapter:
                    extra.extend(spec.extra_modules)
            verification[adapter] = run_in_child(
                "verify",
                {
                    "adapter": adapter,
                    "concurrency": verify_concurrency,
                    "extra_modules": sorted(set(extra)),
                },
            )

    units: list[dict[str, Any]] = []
    for spec in specs:
        result = run_in_child("measure", asdict(spec))
        record: dict[str, Any] = {"spec": asdict(spec), "status": result["status"]}

        if result["status"] == "ok":
            payload = result["payload"]
            record["process"] = {"pid": payload["pid"]}
            record["adapter"] = payload["adapter"]
            record["body"] = {
                "iterations": payload["body_iterations"],
                "checksum": payload["body_checksum"],
            }
            record["absolute"] = _absolute_from_rounds(payload["rounds"], spec)
            record["checks"] = {"body": body_deviation_check(record["absolute"], spec)}
            record["child_elapsed_seconds"] = result["child_elapsed_seconds"]
        else:
            record["error"] = result.get("error", "")
            record["stderr"] = result.get("stderr", "")

        units.append(record)

    run_wall = time.perf_counter() - run_started
    isolated = check_process_isolation(units)

    return {
        "run": {
            "started_at_epoch": started_at,
            "wall_seconds": run_wall,
            "platform": f"{platform.system()}-{platform.machine()}",
            "python": platform.python_version(),
            "warmup_rounds": sorted({spec.warmup_rounds for spec in specs}),
            "measured_rounds": sorted({spec.measured_rounds for spec in specs}),
            "unit_count": len(units),
            "failed_unit_count": sum(1 for unit in units if unit["status"] != "ok"),
            "absolute_note": ABSOLUTE_NOTE,
        },
        "units": units,
        "verification": verification,
        "process_isolation": isolated,
        "self_check": _run_self_check(units, verification),
        "relative": _relative_block(units),
    }
