"""留档与报告模型的验证（spec: comparison-report）。

覆盖 tasks 4.1 / 4.2 / 4.8 / 4.9，以及结论与不利数据这两项**实质性主张**。用合成结果而不是
真实运行：这两个主张的判别力要靠构造特定场景（某档对照方案更快、始终更快、从不更快）才能
验出来，真实运行的档位分布不由我们控制。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zoo_bench import storage
from zoo_bench.report import LOAD_CAVEAT, OVERHEAD_THRESHOLD, build_model, subject_name
from zoo_bench.runner import ABSOLUTE_NOTE

FRAMEWORK = "zoo-framework==9.9.9"


def _summary(value: float, n: int = 3) -> dict[str, float | int]:
    return {
        "n": n,
        "median": value,
        "mean": value,
        "min": value,
        "max": value,
        "stdev": 0.0,
        "iqr": 0.0,
        "p50": value,
        "p95": value,
        "p99": value,
        "relative_spread": 0.0,
    }


def _unit(
    adapter: str,
    *,
    tier_us: float,
    e2e_per_task: float,
    body: float,
    concurrency: int = 4,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overhead = e2e_per_task - body
    adapter_meta: dict[str, Any] = {
        "name": adapter,
        "tier": "bare",
        "comparable": True,
        "notes": "",
        "drive_level": "",
    }
    adapter_meta.update(meta or {})
    return {
        "spec": {"adapter": adapter, "concurrency": concurrency, "body_tier_us": tier_us},
        "status": "ok",
        "process": {"pid": 1000},
        "adapter": adapter_meta,
        "absolute": {
            "note": ABSOLUTE_NOTE,
            "concurrency": concurrency,
            "end_to_end_per_task_seconds": _summary(e2e_per_task),
            "body_seconds": _summary(body),
            "framework_overhead_seconds": overhead,
            "framework_overhead_ratio": overhead / e2e_per_task,
            "throughput_per_second": concurrency / e2e_per_task,
        },
        "checks": {"body": {"body_deviation_ok": True}},
    }


def _subject_unit(
    *, tier_us: float, e2e_per_task: float, body: float, drive_level: str = "调度派发层"
) -> dict[str, Any]:
    """被测框架的单元：**必须声明 tier 为 ``under_test``**，否则模型识别不出被测对象。

    这个 helper 的存在本身是一条教训：最初直接用 ``_unit("zoo", ...)``，而它的默认 tier 是
    ``bare``，于是"不利数据"那几条断言因为被测对象为 None 而**被动通过**——测试是绿的，但
    验的是一个空集合。被测对象靠 tier 识别、不靠名字，fixture 必须如实声明。
    """
    return _unit(
        "zoo",
        tier_us=tier_us,
        e2e_per_task=e2e_per_task,
        body=body,
        meta={"tier": "under_test", "drive_level": drive_level},
    )


def _comparison(tier_us: float, subject_median: float, ratios: dict[str, float]) -> dict[str, Any]:
    return {
        "concurrency": 4,
        "body_tier_us": tier_us,
        "subject": "zoo",
        "subject_median_seconds": subject_median,
        "ratios_vs_subject": ratios,
        "excluded_incomparable": [],
        "note": "比值 >1 表示该对照方案比被测框架慢；仅在同一次运行内成立",
    }


def _result(
    units: list[dict[str, Any]],
    comparisons: list[dict[str, Any]] | None = None,
    *,
    verification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run": {"platform": "test", "python": "3.13", "absolute_note": ABSOLUTE_NOTE},
        "units": units,
        "relative": {"note": "同一次运行内的相对比", "comparisons": comparisons or []},
        "verification": verification or {},
        "process_isolation": {"parent_pid": 1, "child_pids": [1000], "all_distinct": True},
        "self_check": {"ok": True},
    }


# ------------------------------------------------------------------ 4.1 留档与寻址


def test_saved_run_can_be_located_by_version(tmp_path: Path) -> None:
    """4.1：给定版本可定位到对应数据，且内容原样读回。"""
    payload = {"units": [], "marker": "α"}
    path = storage.save_run(payload, framework=FRAMEWORK, root=tmp_path)

    assert path.exists()
    assert storage.load_run(path) == payload

    # 纯版本号与完整规格都应能定位
    assert storage.latest_run(FRAMEWORK, root=tmp_path) == path
    assert storage.latest_run("9.9.9", root=tmp_path) == path
    assert storage.available_frameworks(root=tmp_path) == [storage.framework_slug(FRAMEWORK)]


def test_multiple_runs_of_one_version_coexist_and_latest_wins(tmp_path: Path) -> None:
    storage.save_run({"n": 1}, framework=FRAMEWORK, root=tmp_path, timestamp="20260101T000000Z")
    latest = storage.save_run({"n": 2}, framework=FRAMEWORK, root=tmp_path, timestamp="20260102T000000Z")

    files = storage.run_files(FRAMEWORK, root=tmp_path)
    assert len(files) == 2
    assert storage.latest_run(FRAMEWORK, root=tmp_path) == latest


def test_missing_version_is_reported_as_absent(tmp_path: Path) -> None:
    assert storage.latest_run("1.2.3", root=tmp_path) is None
    assert storage.run_files("1.2.3", root=tmp_path) == []


def test_results_root_defaults_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认留档位置必须是**当前工作目录**下的 ``results/``。

    这是真事故的回归守卫：早先取"包所在目录"，非 editable 安装（CI 就是）会把留档写进
    ``site-packages/results/``——数据既不在仓库里、也进不了构建产物。而"留档"的全部意义就是
    这些数据能被找回来。
    """
    monkeypatch.chdir(tmp_path)

    assert storage.results_root() == tmp_path / "results"

    saved = storage.save_run({"marker": 1}, framework="zoo-framework==9.9.9")
    assert saved.is_relative_to(tmp_path), f"留档跑到了包所在目录：{saved}"


def test_same_data_renders_identically(tmp_path: Path) -> None:
    """4.1：同一份留档数据重复建模结果一致——渲染可重跑，无需重新测量。"""
    source = _result(
        [
            _unit("zoo", tier_us=300, e2e_per_task=0.00035, body=0.0003),
            _unit("bare_thread", tier_us=300, e2e_per_task=0.00031, body=0.0003),
        ],
        [_comparison(300, 0.00035, {"bare_thread": 0.886})],
    )
    path = storage.save_run(source, framework=FRAMEWORK, root=tmp_path)

    first = build_model(storage.load_run(path))
    second = build_model(storage.load_run(path))
    assert first == second


# ------------------------------------------------------------------ 4.2 环境自述


def test_model_carries_the_environment_it_was_given() -> None:
    environment = {"hardware": {"cpu_model": "Synthetic CPU"}, "harness": {"version": "0.0.1"}}
    model = build_model(_result([]), environment=environment)
    assert model["environment"] == environment


def test_model_marks_missing_environment_as_missing() -> None:
    """自述缺失时如实标 null，渲染层据此拒绝发布——不拿空对象冒充"已自述"。"""
    model = build_model(_result([]))
    assert model["environment"] is None


def test_environment_collector_never_fabricates_a_cpu_model() -> None:
    """取不到 CPU 型号时记 null 并说明原因，**不退回 platform.processor() 那种架构串**。"""
    from zoo_bench import environment

    model, source = environment.cpu_model()
    # 无论成败都必须给出说明：成功时是来源，失败时是原因
    assert source.strip(), "来源说明不得为空"
    if model is None:
        assert any(marker in source for marker in ("取", "失败", "不支持", "没有")), source


def test_harness_commit_resolves_from_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``harness commit`` 必须能从**工作目录**解析出来。

    真事故的守卫：只取包所在目录时，非 editable 安装（CI 就是）那里是 site-packages、不是
    git 仓库，报告里于是写着 `harness commit: None`——而"这份报告由哪个 commit 产出"正是这
    一项要回答的问题。
    """
    from zoo_bench import environment

    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(repo_root)

    commit, source = environment.harness_commit()
    assert commit, f"工作目录在仓库内时应能取到 commit，实际：{source}"
    assert len(commit) == 40


# ------------------------------------------------------------------ 4.8 / 4.9 声明与标注


def test_model_declares_the_load_is_a_stand_in() -> None:
    """4.8：负载是替身这一事实必须随报告出现，并给出其构成。"""
    model = build_model(_result([]))
    assert model["load"]["is_stand_in"] is True
    assert "JSON" in model["load"]["composition"]
    assert model["load"]["caveat"] == LOAD_CAVEAT
    assert "替身" in model["load"]["caveat"]


def test_model_declares_the_driven_level() -> None:
    """spec 的"被测层级被声明"：不声明层级，派发原语开销会被读成终端用户延迟。"""
    drive_level = "调度派发层：直接驱动 BaseWaiter.execute_service()"
    units = [_unit("zoo", tier_us=300, e2e_per_task=0.00035, body=0.0003, meta={"drive_level": drive_level})]
    model = build_model(_result(units))

    kinds = {caveat["kind"] for caveat in model["caveats"]}
    assert "被测层级" in kinds
    assert any(caveat["text"] == drive_level for caveat in model["caveats"])


def test_model_annotates_incomparable_and_costing_caveats() -> None:
    """4.9：不同架构的对照与含额外成本的对照，各自带口径说明。"""
    units = [
        _unit("zoo", tier_us=300, e2e_per_task=0.00035, body=0.0003, meta={"drive_level": "派发层"}),
        _unit(
            "celery",
            tier_us=300,
            e2e_per_task=0.02,
            body=0.0003,
            meta={"comparable": False, "notes": "架构不同、不可直接对标"},
        ),
        _unit(
            "process_pool",
            tier_us=300,
            e2e_per_task=0.002,
            body=0.0003,
            meta={"notes": "执行体与返回值跨进程序列化的成本计入端到端"},
        ),
    ]
    model = build_model(_result(units))
    kinds = {caveat["kind"] for caveat in model["caveats"]}
    assert {"不可直接对标", "口径偏差", "被测层级"} <= kinds


def test_model_reports_failed_units_as_a_caveat() -> None:
    units = [
        _unit("zoo", tier_us=300, e2e_per_task=0.00035, body=0.0003),
        {"spec": {"adapter": "celery", "concurrency": 4, "body_tier_us": 300}, "status": "failed"},
    ]
    model = build_model(_result(units))
    failed = [caveat for caveat in model["caveats"] if caveat["kind"] == "未完成的单元"]
    assert failed and failed[0]["count"] == 1


# ------------------------------------------------------------------ 结论：交叉点


def test_conclusion_names_the_tier_where_overhead_drops_below_threshold() -> None:
    # 40 µs 档开销占比高（40/300），300 µs 档已低于 15%
    units = [
        _subject_unit(tier_us=40, e2e_per_task=0.000080, body=0.000040),
        _subject_unit(tier_us=300, e2e_per_task=0.000340, body=0.000300),
        _subject_unit(tier_us=2700, e2e_per_task=0.002740, body=0.002700),
    ]
    model = build_model(_result(units))
    crossing = model["conclusion"]["overhead_crossings"][0]

    assert crossing["first_tier_at_or_below_threshold_us"] == 300
    assert crossing["threshold"] == OVERHEAD_THRESHOLD
    assert any("300" in line for line in model["conclusion"]["summary"])


def test_conclusion_headline_is_about_the_subject_not_the_best_baseline() -> None:
    """头条交叉点必须是被测框架那一行。

    实测踩过这个坑：取所有方案里的最小值，于是头条写着"约 40 µs 时开销降到 15% 以下"——那是
    某个对照方案的数字，而被测框架自己是 2700 µs。**头条说错了对象，整份报告的结论就被误读。**
    """
    units = [
        _subject_unit(tier_us=40, e2e_per_task=0.000080, body=0.000040),
        _subject_unit(tier_us=2700, e2e_per_task=0.002715, body=0.002700),
        # 对照方案在最短档就已远低于阈值——若实现取最小值，头条会变成 40
        _unit("bare_thread", tier_us=40, e2e_per_task=0.000080, body=0.000043),
    ]
    model = build_model(_result(units))

    headline = model["conclusion"]["summary"][0]
    assert "zoo" in headline, f"头条必须点名被测框架：{headline}"
    assert "2700" in headline, f"头条该用被测框架的交叉点：{headline}"


def test_conclusion_admits_absence_of_a_crossing() -> None:
    """所测档位内没有一档低于阈值时如实说明，**不硬造一个交叉点**。"""
    units = [
        _subject_unit(tier_us=tier, e2e_per_task=0.000060, body=0.000040) for tier in (40, 300)
    ]
    model = build_model(_result(units))

    crossing = model["conclusion"]["overhead_crossings"][0]
    assert crossing["first_tier_at_or_below_threshold_us"] is None
    assert "没有一档" in crossing["note"]
    # 摘要句只断言稳定的部分：`first_tier...` 为 None 才是机器可读的判据，摘要句是给人看的
    assert any("15%" in line for line in model["conclusion"]["summary"])


# ------------------------------------------------------------------ 4.7 不利数据


def test_unfavorable_lists_the_tiers_where_a_baseline_is_faster() -> None:
    units = [
        _subject_unit(tier_us=tier, e2e_per_task=0.0001, body=0.00008) for tier in (40, 300, 2700)
    ]
    model = build_model(
        _result(
            units,
            [
                _comparison(40, 0.0001, {"bare_thread": 0.50}),
                _comparison(300, 0.0001, {"bare_thread": 0.90}),
                _comparison(2700, 0.0001, {"bare_thread": 1.10}),
            ],
        )
    )

    unfavorable = model["unfavorable"]
    assert unfavorable["found"] is True
    assert [item["body_tier_us"] for item in unfavorable["items"]] == [40, 300]
    assert all("快" in item["gap"] for item in unfavorable["items"])

    turning = model["conclusion"]["relative_turnings"][0]
    assert turning["first_tier_baseline_not_faster_us"] == 2700


def test_unfavorable_records_when_the_baseline_never_loses() -> None:
    """对照方案在所测档位内始终更快时，明说"未观测到"，并把每个档位列为不利数据。"""
    units = [_subject_unit(tier_us=tier, e2e_per_task=0.0001, body=0.00008) for tier in (40, 300)]
    model = build_model(
        _result(
            units,
            [_comparison(40, 0.0001, {"bare_thread": 0.5}), _comparison(300, 0.0001, {"bare_thread": 0.8})],
        )
    )

    turning = model["conclusion"]["relative_turnings"][0]
    assert turning["first_tier_baseline_not_faster_us"] is None
    assert "未观测到" in turning["note"]
    assert len(model["unfavorable"]["items"]) == 2
    assert any("始终快于" in line for line in model["conclusion"]["summary"])


def test_unfavorable_is_empty_when_the_subject_always_wins() -> None:
    """被测框架全程占优时该组为空——发布流程据此拒绝发布（spec 的硬要求）。

    空**不等于**可以省略：`found` 为 False 是给发布门禁看的信号。
    """
    units = [_subject_unit(tier_us=300, e2e_per_task=0.00008, body=0.00008)]
    model = build_model(_result(units, [_comparison(300, 0.00008, {"bare_thread": 1.5})]))

    assert model["subject"] == "zoo", "fixture 必须让被测对象可被识别，否则本用例验的是空集合"
    assert model["unfavorable"]["found"] is False
    assert model["unfavorable"]["items"] == []
    assert "不合格" in model["unfavorable"]["note"]


# ------------------------------------------------------------------ 语义维度与四个维度


def test_semantics_dimension_is_present_and_declares_its_scope_when_unmeasured() -> None:
    """4.4 的口径声明：**未测量也要在场并声明口径**——省略会让读者以为这个维度不存在，
    而它恰是被测框架最主要的差异化。"""
    model = build_model(_result([]))
    semantics = model["dimensions"]["semantics"]

    assert semantics["status"] == "not_probed"
    assert "内部开关对照" in semantics["scope_note"]
    assert "不与对照方案横向比较" in semantics["scope_note"]


def test_semantics_dimension_is_used_verbatim_when_measured() -> None:
    measured = {"title": "调度语义的代价", "status": "ok", "rows": [{"item": "优先级", "delta": 1.2}]}
    model = build_model(_result([]), semantics=measured)
    assert model["dimensions"]["semantics"] == measured


def test_all_four_dimensions_are_present() -> None:
    model = build_model(_result([]))
    assert set(model["dimensions"]) == {"latency", "overhead", "throughput", "semantics"}


def test_subject_is_identified_by_tier_not_by_name() -> None:
    """被测框架靠 tier 识别，不靠写死名字——否则换名字就会静默失配。"""
    units = [
        _unit("some-other-name", tier_us=300, e2e_per_task=0.0001, body=0.00008, meta={"tier": "under_test"}),
    ]
    assert subject_name(units) == "some-other-name"


def test_model_has_no_subject_when_no_unit_claims_under_test() -> None:
    model = build_model(_result([_unit("bare_thread", tier_us=300, e2e_per_task=0.0001, body=0.00008)]))
    assert model["subject"] is None
    assert model["conclusion"]["relative_turnings"] == []


@pytest.mark.parametrize("name", ["latency", "overhead", "throughput"])
def test_metric_dimensions_report_a_unit_and_a_note(name: str) -> None:
    model = build_model(_result([_unit("zoo", tier_us=300, e2e_per_task=0.00035, body=0.0003)]))
    dimension = model["dimensions"][name]
    assert dimension["unit"]
    assert dimension["note"]
    assert dimension["rows"]
