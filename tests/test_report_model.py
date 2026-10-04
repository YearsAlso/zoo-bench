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
    *, tier_us: float, e2e_per_task: float, body: float, concurrency: int = 4,
    drive_level: str = "调度派发层",
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
        concurrency=concurrency,
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
    self_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "run": {"platform": "test", "python": "3.13", "absolute_note": ABSOLUTE_NOTE},
        "units": units,
        "relative": {"note": "同一次运行内的相对比", "comparisons": comparisons or []},
        "verification": verification or {},
        "process_isolation": {"parent_pid": 1, "child_pids": [1000], "all_distinct": True},
        "self_check": self_check or {"ok": True},
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
    """4.9：不同架构的对照与含额外成本的对照，各自带口径说明。

    每个方案**自己声明的口径**都要进报告——按关键字替读者挑哪条重要，代价是漏掉没被命中的。
    """
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
    assert {"不可直接对标", "口径说明", "被测层级"} <= kinds

    annotated = {caveat["adapter"] for caveat in model["caveats"] if caveat.get("adapter")}
    assert {"celery", "process_pool"} <= annotated


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
    # 40 微秒 档开销占比高（40/300），300 微秒 档已低于 15%
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


def test_queueing_contaminated_groups_are_marked_and_kept_out_of_the_headline() -> None:
    """实测：并发度 64 时同一适配器的四档开销差异达 35.5 倍——那是**排队**不是成本错算。

    受污染的组不给交叉点，其开销数字在报告里也不该被当作框架开销来读；原始值仍在场。
    """
    units = [
        _subject_unit(tier_us=40, e2e_per_task=0.000080, body=0.000040, concurrency=1),
        _subject_unit(tier_us=2700, e2e_per_task=0.002715, body=0.002700, concurrency=1),
        _subject_unit(tier_us=40, e2e_per_task=0.000080, body=0.000040, concurrency=64),
        _subject_unit(tier_us=2700, e2e_per_task=0.004400, body=0.002700, concurrency=64),
    ]
    contaminated = {
        "ok": True,
        "overhead_across_tiers": {
            "gated_concurrency": 1,
            "ok": True,
            "checks": [
                {
                    "adapter": "zoo",
                    "concurrency": 64,
                    "gated": False,
                    "ok": False,
                    "growth": 35.5,
                    "body_tiers_us": [40.0, 2700.0],
                    "overheads_seconds": [1e-5, 3.5e-4],
                }
            ],
        },
    }
    model = build_model(_result(units, self_check=contaminated))

    by_concurrency = {c["concurrency"]: c for c in model["conclusion"]["overhead_crossings"]}
    assert by_concurrency[1]["queueing_contaminated"] is False
    assert by_concurrency[1]["first_tier_at_or_below_threshold_us"] == 2700

    polluted = by_concurrency[64]
    assert polluted["queueing_contaminated"] is True
    assert polluted["first_tier_at_or_below_threshold_us"] is None, "受污染的组不该给交叉点"
    assert "排队" in polluted["note"]

    kinds = {caveat["kind"] for caveat in model["caveats"]}
    assert "受排队污染的开销数字" in kinds, "受污染的组必须在报告里点名，而不是默默留在表里"
    assert any(
        caveat.get("groups") == ["zoo/64"] for caveat in model["caveats"]
    ), "要指名具体是哪一组"


def test_headline_still_uses_the_trustworthy_rows_only() -> None:
    """头条只该用未被污染的行——否则一个排队数字会把结论左右。"""
    units = [
        _subject_unit(tier_us=40, e2e_per_task=0.000080, body=0.000040, concurrency=1),
        _subject_unit(tier_us=2700, e2e_per_task=0.002715, body=0.002700, concurrency=1),
    ]
    model = build_model(_result(units))

    headline = model["conclusion"]["summary"][0]
    assert "2700" in headline


def test_conclusion_headline_is_about_the_subject_not_the_best_baseline() -> None:
    """头条交叉点必须是被测框架那一行。

    实测踩过这个坑：取所有方案里的最小值，于是头条写着"约 40 微秒 时开销降到 15% 以下"——那是
    某个对照方案的数字，而被测框架自己是 2700 微秒。**头条说错了对象，整份报告的结论就被误读。**
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


def test_every_dimension_is_present_even_when_its_probe_did_not_run() -> None:
    """维度缺失会被读成"这一维不存在"——故未探查时给**占位**而不是省略。

    占位与"跑了但不可测"必须能区分（前者是遗漏、后者是结论），故两者的 ``status`` 不同。
    """
    model = build_model(_result([]))

    assert set(model["dimensions"]) == {
        "latency",
        "overhead",
        "throughput",
        "semantics",
        "attribution",
    }
    assert model["dimensions"]["attribution"]["status"] == "not_probed"
    assert model["dimensions"]["attribution"]["reason"], "未探查要说明为什么"


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


# ------------------------------------------------------------------ 开销数字的适用口径


def _environment(cores: int) -> dict[str, Any]:
    return {"hardware": {"logical_cores": cores}, "harness": {"version": "0.0.1"}}


def _wide_and_narrow() -> list[dict[str, Any]]:
    """一组"超并行能力"的单元与一组正常的单元，形态照实测抄。

    实测（CI runner，4 逻辑核，并发 64，10000 微秒档）：每任务端到端 10.5 毫秒，执行体**自报**
    耗时中位 106.5 毫秒（超订把它抬高了约 10.6 倍），相减得 −913.8%。
    """
    return [
        _subject_unit(tier_us=10000, e2e_per_task=0.0105, body=0.1065, concurrency=64),
        _subject_unit(tier_us=300, e2e_per_task=0.0004, body=0.0003, concurrency=4),
    ]


def test_overhead_is_withheld_above_the_machines_parallelism() -> None:
    """并发度超过机器并行能力时，开销与交叉点都不给——那个减式的结果没有意义。"""
    model = build_model(_result(_wide_and_narrow()), environment=_environment(4))

    rows = {row["concurrency"]: row for row in model["dimensions"]["overhead"]["rows"]}
    assert rows[64]["interpretable"] is False
    assert rows[64]["framework_overhead_seconds"] is None, "不可读时连数字都不给，别指望渲染层去判断"
    assert rows[64]["framework_overhead_ratio"] is None
    assert rows[64]["body_seconds"] == 0.1065, "执行体自报值仍然有效，必须照旧给出"
    assert rows[4]["interpretable"] is True
    assert rows[4]["framework_overhead_ratio"] is not None

    crossings = {c["concurrency"]: c for c in model["conclusion"]["overhead_crossings"]}
    assert crossings[64]["uninterpretable"] is True
    assert crossings[64]["overhead_ratios"] is None
    assert crossings[64]["first_tier_at_or_below_threshold_us"] is None
    assert crossings[4]["uninterpretable"] is False


def test_the_withheld_groups_are_named_in_the_caliber_caveats() -> None:
    """撤下数字必须点名到组——读者要能一眼看出该跳过哪几行。"""
    model = build_model(_result(_wide_and_narrow()), environment=_environment(4))

    caveat = next(c for c in model["caveats"] if c["kind"] == "不给出开销数字的组")
    assert caveat["groups"] == ["zoo/64"]
    assert "负值" in caveat["text"], "要给出判否的证据形态，读者才知道这不是阈值卡出来的"


def test_a_negative_overhead_is_withheld_even_with_plenty_of_cores() -> None:
    """证据性那条在结构性那条失效时仍要生效。

    容器 CPU 配额可能低于 runner 报的核数——那时 `concurrency > cores` 会放行仍然超订的组，
    而"开销为负"照样能把它认出来（框架只会加时间不会减时间）。
    """
    units = [_subject_unit(tier_us=10000, e2e_per_task=0.0105, body=0.1065, concurrency=64)]

    withheld = build_model(_result(units), environment=_environment(4))
    survived = build_model(_result(units), environment=_environment(256))

    assert withheld["dimensions"]["overhead"]["rows"][0]["interpretable"] is False
    assert survived["dimensions"]["overhead"]["rows"][0]["interpretable"] is False, (
        "核数给得再大，负开销仍然判不可读——这条不依赖核数估计"
    )


def test_missing_environment_does_not_withhold_a_positive_overhead() -> None:
    """环境自述缺失时**不据此判否**：缺一项就把整列抹掉，比给一个可能无效的数字更坏。"""
    units = [_subject_unit(tier_us=300, e2e_per_task=0.0004, body=0.0003, concurrency=64)]

    model = build_model(_result(units))

    assert model["dimensions"]["overhead"]["rows"][0]["interpretable"] is True
    assert not any(c["kind"] == "不给出开销数字的组" for c in model["caveats"])


def test_summary_says_why_no_crossing_is_given_when_everything_is_withheld() -> None:
    """全部组都不可读时，结论必须说清"是没给"而不是"没有一档达标"——两者含义相反。"""
    units = [_subject_unit(tier_us=10000, e2e_per_task=0.0105, body=0.1065, concurrency=64)]

    summary = " ".join(build_model(_result(units), environment=_environment(4))["conclusion"]["summary"])

    assert "超出该机器并行能力" in summary
    assert "没有任何一档" not in summary, "不可读不能读成「测了但不达标」"


# ------------------------------------------------------------------ 开销归因维度


def _attribution_result(groups: list[dict[str, Any]], *, cores: int = 4) -> dict[str, Any]:
    return {
        "cores": cores,
        "tiers_us": sorted({float(group["tier_us"]) for group in groups}),
        "concurrencies": sorted({int(group["concurrency"]) for group in groups}),
        "groups": groups,
        "note": "本维度是诊断不是主证据",
    }


def _group(
    adapter: str,
    *,
    tier_us: float = 300.0,
    concurrency: int = 1,
    submit_side: float = 10e-6,
    handoff: float = 20e-6,
    body: float = 300e-6,
    back: float = 30e-6,
    tier: str = "bare",
    drill: dict[str, float] | None = None,
) -> dict[str, Any]:
    segments = {
        "submit_side_seconds": submit_side,
        "handoff_seconds": handoff,
        "body_seconds": body,
        "return_seconds": back,
    }
    return {
        "adapter": adapter,
        "adapter_tier": tier,
        "comparable": True,
        "tier_us": tier_us,
        "concurrency": concurrency,
        "status": "ok",
        "reason": "",
        "segments": segments,
        "end_to_end_seconds": sum(segments.values()),
        "consistency": {"max_deviation": 0.0, "tolerance": 0.02, "ok": True},
        "seals": ["seal-a"] if drill else [],
        "drill_down": drill or {},
        "instrumentation": {},
    }


def test_attribution_names_the_segment_that_holds_the_excess() -> None:
    """结论必须指到**段**——只说"多花 57 微秒"读者不知道下一步动哪里。

    这里让被测框架的提交侧远大于对照方案、其余三段相同：超出部分应落在「提交侧」。
    """
    result = _result([])
    result["attribution"] = _attribution_result(
        [
            _group("zoo", submit_side=70e-6, tier="under_test"),
            _group("thread_pool", submit_side=10e-6),
        ]
    )

    model = build_model(result)
    dimension = model["dimensions"]["attribution"]

    assert dimension["status"] == "ok"
    finding = dimension["findings"][0]
    row = finding["per_adapter"][0]
    assert row["adapter"] == "thread_pool"
    assert row["dominant_segment"] == "submit_side_seconds"
    assert row["excess_seconds"] == pytest.approx(60e-6)
    assert row["segment_excess_seconds"]["submit_side_seconds"] == pytest.approx(60e-6)
    assert "提交侧" in dimension["summary"][0], "结论句要点到段名"


def test_attribution_lists_every_comparable_side_by_side() -> None:
    """每个对照方案都要逐段给出——没有对照的分解回答不了"超出落在哪"。"""
    result = _result([])
    result["attribution"] = _attribution_result(
        [
            _group("zoo", tier="under_test"),
            _group("thread_pool", back=5e-6),
            _group("bare_thread", back=50e-6),
        ]
    )

    model = build_model(result)

    rows = model["dimensions"]["attribution"]["findings"][0]["per_adapter"]
    assert {row["adapter"] for row in rows} == {"thread_pool", "bare_thread"}


def test_attribution_skips_adapters_that_are_not_comparable() -> None:
    """与进程内派发不同架构的方案不参与逐段对照——它的段与段之间不可比。"""
    result = _result([])
    incomparable = _group("process_pool")
    incomparable["comparable"] = False
    result["attribution"] = _attribution_result([_group("zoo", tier="under_test"), incomparable])

    model = build_model(result)

    assert model["dimensions"]["attribution"]["findings"] == [], "没有可比对象时不给结论"


def test_attribution_keeps_groups_it_could_not_measure() -> None:
    """量不成的组**留在场**并带上原因——抹掉它会让读者以为那一档不存在。"""
    result = _result([])
    unmeasurable = {
        "adapter": "process_pool",
        "adapter_tier": "stdlib",
        "comparable": True,
        "tier_us": 300.0,
        "concurrency": 1,
        "status": "not_measurable",
        "reason": "执行体的起止时刻读不到——它没有在本进程里运行",
        "seals": [],
    }
    result["attribution"] = _attribution_result([_group("zoo", tier="under_test"), unmeasurable])

    dimension = build_model(result)["dimensions"]["attribution"]

    kept = next(group for group in dimension["groups"] if group["adapter"] == "process_pool")
    assert kept["status"] == "not_measurable"
    assert "本进程" in kept["reason"], "原因要留在场，读者才知道为什么这一组没有分段"


def test_attribution_reports_the_subject_drill_down_separately() -> None:
    """被测框架的提交侧细分要与对照方案分开呈现——对照方案没有被拆不是因为它没有结构。"""
    drill = {
        "scheduling_round_seconds": 40e-6,
        "dispatch_seconds": 15e-6,
        "policy_lookup_seconds": 5e-6,
        "submit_side_other_seconds": 5e-6,
    }
    result = _result([])
    result["attribution"] = _attribution_result(
        [_group("zoo", tier="under_test", drill=drill), _group("thread_pool")]
    )

    dimension = build_model(result)["dimensions"]["attribution"]

    assert dimension["findings"][0]["subject_drill_down"] == drill
    assert any("策略查询" in line for line in dimension["summary"]), "细分要进结论句"


def test_attribution_is_marked_unprobed_when_the_probe_never_ran() -> None:
    """未探查与"跑了但不可测"必须分得开：前者是遗漏，后者是结论。"""
    result = _result([])
    result["attribution"] = None

    dimension = build_model(result)["dimensions"]["attribution"]

    assert dimension["status"] == "not_probed"
    assert dimension["reason"]
    assert dimension["segment_labels"], "标签即使在未探查时也要在场"
