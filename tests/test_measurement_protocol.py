"""测量协议的验证（spec: measurement-protocol）。

覆盖 tasks 2.7 与 3.1–3.8。三处刻意的安排：

1. **自检的鉴别力用合成数据直接验**（让开销随档位涨到 10 倍，判据必须判否），而不是只在真实
   运行上"看起来通过"——真实运行的噪声会让"通过"既可能是判据有效、也可能是判据恒真。
2. **依赖注入类的验证用外部模块实现**（准备阶段慢的适配器、丢弃任务的适配器），顺带验证了
   "外部适配器无需改动包内文件也能被装载"这条要求。
3. **耗时断言只对能证伪的量下**：容差来自实测（四档校准偏差 −4.5%~+4.1%），不是猜的。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zoo_bench.metrics import percentile, summarize
from zoo_bench.runner import (
    ABSOLUTE_NOTE,
    BODY_DEVIATION_TOLERANCE,
    OVERHEAD_TIER_RATIO_LIMIT,
    UnitSpec,
    body_deviation_check,
    check_overhead_across_tiers,
    grade_body_deviation,
    measure_matrix,
    run_in_child,
    summarize_self_check,
)
from zoo_bench.workloads.body import body_for_tier

#: 真实矩阵里的执行体档位（matrix.yaml 的 body_tiers_us）。
TIERS_US = (40, 300, 2700, 10000)
FRAMEWORK = "zoo-framework==0.6.0"


def _unit_spec(adapter: str, **overrides: Any) -> UnitSpec:
    """小规模单元：够验证协议，又不至于让用例跑得慢。"""
    defaults: dict[str, Any] = {
        "framework": FRAMEWORK,
        "concurrency": 2,
        "body_tier_us": 300,
        "warmup_rounds": 1,
        "measured_rounds": 3,
    }
    defaults.update(overrides)
    return UnitSpec(adapter=adapter, **defaults)


# ------------------------------------------------------------------ 度量原语


def test_percentile_interpolates_between_neighbours() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0) == pytest.approx(1.0)
    assert percentile([1.0, 2.0, 3.0, 4.0], 100) == pytest.approx(4.0)


def test_summarize_reports_dispersion_not_only_a_center_value() -> None:
    summary = summarize([1.0, 2.0, 3.0, 4.0, 5.0])
    assert summary["n"] == 5
    assert summary["median"] == pytest.approx(3.0)
    assert float(summary["stdev"]) > 0
    assert float(summary["iqr"]) > 0
    assert {"p50", "p95", "p99"} <= set(summary)
    assert float(summary["relative_spread"]) > 0


def test_summarize_rejects_empty_samples() -> None:
    with pytest.raises(ValueError, match="样本为空"):
        summarize([])


# ------------------------------------------------------------------ 3.1 负载生成


def _observed_us(tier_us: float, samples: int = 9) -> float:
    """某档实测耗时的**最小值**。

    **取最小值而不是中位数**——这是"成本"量，竞争只会让它变大，故最小值是无竞争条件下的估计。
    中位数会被机器当时的负载抬高：实测同一台机器上同一档在套件内曾偏到 +77%，而单独跑时
    三次都在 5% 以内。用中位数断言校准正确，等于让校准为当时有多少别的进程在跑负责。

    报告里两者都在场（`body_seconds` 同时给 median / min / max），门禁另行判定中位数——
    这里断言的是"校准是否把执行体放对了量级"。
    """
    body = body_for_tier(tier_us)
    return min(body() for _ in range(samples)) * 1e6


@pytest.mark.parametrize("tier_us", TIERS_US)
def test_body_duration_matches_its_tier(tier_us: float) -> None:
    observed_us = _observed_us(tier_us)
    deviation = (observed_us - tier_us) / tier_us
    assert abs(deviation) <= BODY_DEVIATION_TOLERANCE, (
        f"{tier_us}us 档实测 {observed_us:.1f}us，偏差 {deviation:.1%}"
    )


def test_body_duration_rises_with_tier() -> None:
    observed = [_observed_us(tier_us) for tier_us in TIERS_US]
    assert observed == sorted(observed), f"执行体耗时不随档位单调上升：{observed}"


# ------------------------------------------------------------------ 3.2–3.4 / 2.7 真实运行

SMALL_MATRIX_ADAPTERS = ("zoo", "thread_pool")
SMALL_MATRIX_TIERS = (300, 2700)


@pytest.fixture(scope="module")
def small_run() -> dict[str, Any]:
    """一次小规模真实运行：两个适配器 × 两个档位 × 3 轮采样。"""
    specs = [
        _unit_spec(adapter, body_tier_us=tier)
        for adapter in SMALL_MATRIX_ADAPTERS
        for tier in SMALL_MATRIX_TIERS
    ]
    return measure_matrix(specs)


def test_run_carries_both_durations_from_the_same_run(small_run: dict[str, Any]) -> None:
    """3.2：同一份原始数据里同时有端到端耗时与该单元实测的执行体耗时。"""
    for unit in small_run["units"]:
        assert unit["status"] == "ok", unit.get("stderr")
        absolute = unit["absolute"]
        assert float(absolute["end_to_end_per_task_seconds"]["median"]) > 0
        assert float(absolute["body_seconds"]["median"]) > 0


def test_warmup_samples_are_excluded(small_run: dict[str, Any]) -> None:
    """3.3：采样数恰好等于「正式轮数 × 并发度」，预热轮的数据不在其中。"""
    for unit in small_run["units"]:
        spec = unit["spec"]
        assert unit["absolute"]["body_seconds"]["n"] == spec["measured_rounds"] * spec["concurrency"]


def test_samples_report_percentiles_and_dispersion(small_run: dict[str, Any]) -> None:
    """3.3：只给单一中心值的度量不合格。"""
    for unit in small_run["units"]:
        body = unit["absolute"]["body_seconds"]
        assert body["n"] > 1
        assert {"p50", "p95", "p99"} <= set(body)
        assert float(body["stdev"]) >= 0
        assert "relative_spread" in body


def test_absolute_and_relative_are_separate_groups(small_run: dict[str, Any]) -> None:
    """3.4：两组字段分离，各自带说明，读取任一组无需依赖另一组。"""
    for unit in small_run["units"]:
        # 绝对耗时组自带「不可跨运行比较」的标注
        assert unit["absolute"]["note"] == ABSOLUTE_NOTE

    relative = small_run["relative"]
    assert "note" in relative
    assert relative["comparisons"], "以 zoo 为基准的相对比应存在"
    for comparison in relative["comparisons"]:
        assert comparison["subject"] == "zoo"
        assert comparison["ratios_vs_subject"], "对照方案的比值应存在"
        assert "仅在同一次运行内成立" in comparison["note"]


def test_each_unit_runs_in_its_own_process(small_run: dict[str, Any]) -> None:
    """2.7：每个测量单元跑在各自独立的子进程里。"""
    isolation = small_run["process_isolation"]
    assert isolation["all_distinct"], f"有单元共用了进程：{isolation['child_pids']}"
    assert isolation["none_equals_parent"], "有单元跑在父进程里"


def test_a_failing_unit_does_not_stop_the_others() -> None:
    """2.7：某档失败时其余档仍完成。"""
    result = measure_matrix(
        [
            _unit_spec("no_such_adapter", concurrency=1, warmup_rounds=0, measured_rounds=1),
            _unit_spec("thread_pool", concurrency=1, warmup_rounds=0, measured_rounds=1),
        ],
        verify_adapters=False,
    )
    by_adapter = {unit["spec"]["adapter"]: unit for unit in result["units"]}

    assert by_adapter["no_such_adapter"]["status"] == "failed"
    assert by_adapter["no_such_adapter"]["error"]
    assert by_adapter["thread_pool"]["status"] == "ok"


def test_overhead_is_checked_across_tiers_in_a_real_run(small_run: dict[str, Any]) -> None:
    """3.7：跨档位的开销判据真实运行下被执行，各组的原始值可供人工判读。"""
    checks = small_run["self_check"]["overhead_across_tiers"]["checks"]
    assert {check["adapter"] for check in checks} == set(SMALL_MATRIX_ADAPTERS)
    for check in checks:
        assert len(check["overheads_seconds"]) >= 2
        assert len(check["body_tiers_us"]) == len(check["overheads_seconds"])


# ------------------------------------------------------------------ 3.5 等价性验证

_DROPPING_MODULE = "zoo_bench_dropping_probe"

_DROPPING_SOURCE = '''"""刻意丢弃任务的适配器，用于证明等价性验证有鉴别力。"""

from zoo_bench.adapters import BaseAdapter, Tier, register


@register
class DroppingAdapter(BaseAdapter):
    name = "dropping_probe"
    tier = Tier.BARE

    def setup(self, *, workers):
        self._bodies = []

    def submit(self, body):
        self._bodies.append(body)

    def drain(self):
        first, self._bodies = self._bodies[0], []
        return [first()]

    def teardown(self):
        pass
'''

SLOW_SETUP_SECONDS = 0.2
_SLOW_SETUP_MODULE = "zoo_bench_slow_setup_probe"

_SLOW_SETUP_SOURCE = f'''"""准备阶段刻意很慢的适配器，用于验证准备开销不计入被测区间。"""

import time

from zoo_bench.adapters import BaseAdapter, Tier, register


@register
class SlowSetupAdapter(BaseAdapter):
    name = "slow_setup_probe"
    tier = Tier.BARE

    def setup(self, *, workers):
        time.sleep({SLOW_SETUP_SECONDS})
        self._queue = []

    def submit(self, body):
        self._queue.append(body)

    def drain(self):
        bodies, self._queue = self._queue, []
        return [body() for body in bodies]

    def teardown(self):
        pass
'''


def _write_external_module(tmp_path: Path, name: str, source: str, monkeypatch) -> str:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")
    # 子进程是全新解释器：parent 的 sys.path 由 run_in_child 经 PYTHONPATH 传递
    monkeypatch.syspath_prepend(str(tmp_path))
    return name


def test_adapter_equivalence_is_verified_for_every_adapter(small_run: dict[str, Any]) -> None:
    """3.5：正常适配器全部通过等价性验证。"""
    equivalence = small_run["self_check"]["adapter_equivalence"]
    assert equivalence["ok"]
    for adapter in SMALL_MATRIX_ADAPTERS:
        assert equivalence["results"][adapter]["payload"]["ok"]


def test_equivalence_verification_catches_a_dropping_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3.5 的鉴别力：丢弃任务的适配器必须被判否——否则这套验证等于装饰。"""
    name = _write_external_module(tmp_path, _DROPPING_MODULE, _DROPPING_SOURCE, monkeypatch)

    result = run_in_child(
        "verify",
        {"adapter": "dropping_probe", "concurrency": 2, "extra_modules": [name]},
    )
    assert result["status"] == "ok", result.get("stderr")
    assert result["payload"]["ok"] is False
    assert result["payload"]["observed_count"] == 1


def test_setup_cost_is_outside_the_measured_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """3.8：准备阶段注入固定延迟后，测得的结果耗时不变。

    这是"计时区间不含准备开销"的可证伪判据——若 `setup` 被计入，实测端到端会 ≥ 200ms。
    """
    name = _write_external_module(tmp_path, _SLOW_SETUP_MODULE, _SLOW_SETUP_SOURCE, monkeypatch)

    result = measure_matrix(
        [
            _unit_spec(
                "slow_setup_probe",
                concurrency=1,
                warmup_rounds=0,
                measured_rounds=3,
                extra_modules=(name,),
            )
        ],
        verify_adapters=False,
    )
    unit = result["units"][0]
    assert unit["status"] == "ok", unit.get("stderr")

    median = float(unit["absolute"]["end_to_end_per_task_seconds"]["median"])
    assert median < SLOW_SETUP_SECONDS / 2, f"准备阶段的开销疑似被计入：{median:.4f}s"


# ------------------------------------------------------------------ 3.6 自检的鉴别力


def _synthetic_unit(adapter: str, tier_us: float, overhead: float) -> dict[str, Any]:
    return {
        "status": "ok",
        "spec": {"adapter": adapter, "concurrency": 1, "body_tier_us": tier_us},
        "absolute": {"framework_overhead_seconds": overhead},
        "checks": {},
    }


def test_self_check_summary_names_every_failing_item() -> None:
    """门禁只说"未通过"会迫使排查者去下载留档数据——失败必须自带定位信息。
    CI 上那是一次往返，而一次往返足以让人干脆把门禁关掉。"""
    self_check = {
        "ok": False,
        "body_deviation": {
            "checks": [
                {
                    "adapter": "zoo",
                    "concurrency": 4,
                    "body_target_seconds": 1e-3,
                    "body_observed_median_seconds": 2e-3,
                    "body_deviation": 1.0,
                    "body_deviation_ok": False,
                }
            ],
            "ok": False,
        },
        "overhead_across_tiers": {
            "checks": [
                {
                    "adapter": "thread_pool",
                    "concurrency": 1,
                    "growth": 9.0,
                    "overheads_seconds": [0.0001, 0.0009],
                    "ok": False,
                }
            ],
            "ok": False,
        },
        "adapter_equivalence": {
            "results": {
                "zoo": {
                    "status": "ok",
                    "payload": {"ok": False, "expected_count": 4, "observed_count": 1},
                },
                "process_pool": {"status": "failed", "error": "子进程退出码 1"},
            },
            "ok": False,
        },
    }

    problems = summarize_self_check(self_check)

    assert len(problems) == 4, f"每一项失败都该被列出：{problems}"
    assert any("档位偏差" in problem and "zoo" in problem for problem in problems)
    assert any("增长过快" in problem and "thread_pool" in problem for problem in problems)
    assert any("等价性验证未通过" in problem for problem in problems)
    assert any("未能执行" in problem and "process_pool" in problem for problem in problems)


def test_self_check_summary_is_empty_when_everything_passed() -> None:
    passed = {
        "ok": True,
        "body_deviation": {"checks": [{"body_deviation_ok": True}], "ok": True},
        "overhead_across_tiers": {"checks": [{"ok": True}], "ok": True},
        "adapter_equivalence": {"results": {"zoo": {"status": "ok", "payload": {"ok": True}}}},
    }
    assert summarize_self_check(passed) == []


def test_overhead_check_passes_when_overhead_is_flat() -> None:
    units = [_synthetic_unit("zoo", tier_us, 0.0001) for tier_us in TIERS_US]
    result = check_overhead_across_tiers(units)
    assert result["ok"], result["checks"]


def test_overhead_check_catches_cost_leaking_into_overhead() -> None:
    """design D5 那个 bug 的鉴别力：执行体成本被漏算进开销时，开销会随档位上升。

    合成数据让开销逐档放大到 16 倍——判据必须判否。**倍数刻意远大于上限（5）**：上限是按
    实测噪声（共享 runner 上见过 3.22 倍）定的，鉴别力用例必须证明它能抓住真正的泄漏。
    """
    units = [
        _synthetic_unit("zoo", tier_us, 0.0001 * (index + 1) ** 2)
        for index, tier_us in enumerate(TIERS_US)
    ]
    result = check_overhead_across_tiers(units)
    assert result["ok"] is False
    assert result["checks"][0]["growth"] > OVERHEAD_TIER_RATIO_LIMIT


def test_overhead_check_tolerates_measured_shared_runner_noise() -> None:
    """共享 runner 上实测见过同一适配器四档开销差异 3.22 倍——那是噪声，不该判否。

    把上限的**下界**固定住，避免以后有人凭感觉调紧而让门禁因环境噪声变红。
    """
    units = [_synthetic_unit("zoo", tier_us, 0.000020 * factor) for tier_us, factor in
             zip(TIERS_US, (1.0, 1.17, 1.5, 3.22), strict=True)]
    assert check_overhead_across_tiers(units)["ok"] is True


# ------------------------------------------------------------------ 偏差判定的分档


def _deviation_unit(
    adapter: str, concurrency: int, *, ok: bool, tier_us: float = 10000.0
) -> dict[str, Any]:
    return {
        "status": "ok",
        "spec": {"adapter": adapter, "concurrency": concurrency, "body_tier_us": tier_us},
        "checks": {
            "body": {
                "body_deviation_ok": ok,
                "body_target_seconds": tier_us / 1e6,
                "body_observed_median_seconds": tier_us / 1e6 * (1.0 if ok else 2.0),
                "body_deviation": 0.0 if ok else 1.0,
            }
        },
        "absolute": {"framework_overhead_seconds": 0.0001},
    }


def test_body_deviation_only_gates_the_lowest_concurrency() -> None:
    """CI 首跑的诊断：偏差随并发度单调放大，而并发度 1 全部通过。

    执行体测的是墙钟，并发超过环境实际容量时它被调度推迟——那是排队，不是测量错误。真正的
    校准错误会在**所有**并发度上同样表现，故最低档足以判定。
    """
    graded = grade_body_deviation(
        [_deviation_unit("zoo", 1, ok=True), _deviation_unit("zoo", 64, ok=False)]
    )

    assert graded["gated_concurrency"] == 1
    assert graded["ok"] is True, "最高档因超订超容差，不该让判据失败"

    high = next(check for check in graded["checks"] if check["concurrency"] == 64)
    assert high["gated"] is False
    assert "环境超订" in high["note"], "不参与判定必须给出原因，且原始值仍要在场"
    assert high["body_deviation"] == 1.0


def test_body_deviation_does_not_gate_the_shortest_tier() -> None:
    """40 µs 档在并发度 1 下仍实测 +63.1%——几十微秒的窗口在共享 runner 上受调度颗粒度支配。

    排除排队因素后剩下的这条原因与并发度那条不同，故单独有用例。
    """
    graded = grade_body_deviation([_deviation_unit("zoo", 1, ok=False, tier_us=40.0)])

    assert graded["ok"] is True, "档位过短时不该判否"
    assert graded["checks"][0]["gated"] is False
    assert "档位过短" in graded["checks"][0]["note"]


def test_body_deviation_gates_failure_at_the_lowest_concurrency() -> None:
    """最低档的长档位自己超容差 → 判否。否则"只在最低档判定"会退化成"永不判定"。"""
    assert grade_body_deviation([_deviation_unit("zoo", 1, ok=False)])["ok"] is False


def test_body_deviation_has_no_gated_concurrency_without_units() -> None:
    graded = grade_body_deviation([])
    assert graded["gated_concurrency"] is None
    assert graded["checks"] == []


def test_body_deviation_check_catches_a_doubled_workload() -> None:
    """3.6：某一档执行体的实际工作量是设定值的两倍时，自检必须失败。

    这条同时是容差取值的鉴别力检查——容差若宽到抓不住翻倍，门禁就没用了。
    """
    spec = _unit_spec("zoo", concurrency=1, body_tier_us=1000)

    on_target = {"body_seconds": {"median": 0.001}}
    assert body_deviation_check(on_target, spec)["body_deviation_ok"] is True

    doubled = {"body_seconds": {"median": 0.002}}
    failed = body_deviation_check(doubled, spec)
    assert failed["body_deviation_ok"] is False
    assert failed["body_deviation"] == pytest.approx(1.0, abs=0.01)


def test_body_deviation_tolerance_tolerates_measured_contention() -> None:
    """容差必须容得下实测到的竞争：同一台机器上 10 ms 档曾偏到 +32%。

    容差若卡在 30%，门禁就会因为"机器忙"而红——而那不是测量错误，是环境噪声。按这条把容差
    的**下界**也固定住，避免以后有人凭感觉把它调紧。
    """
    spec = _unit_spec("zoo", concurrency=1, body_tier_us=10000)
    contended = {"body_seconds": {"median": 0.0132}}  # 目标 10 ms，实测 13.2 ms

    assert body_deviation_check(contended, spec)["body_deviation_ok"] is True
