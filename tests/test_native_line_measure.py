"""原生任务线的测量驱动验证（spec: native-task-line「该线 MUST 在同一轮运行内对照三种形态」等）。

本文件按 task 3.x 的验收点组织，重点仍是**鉴别力**：每条断言都要能答"若实现把这件事做错，它会红吗"。
故凡关键判据都配一条"它分辨的是什么"的说明，并在 `tasks.md` 3.x 的记录里附上注入实验的收据。

**真跑子进程、不桩**：该线的数字只能由 :mod:`zoo_bench.native_line.child` 在自己的进程里产出
（配置目录 MUST 先于框架 import 就位，design D13），故这些用例走真实的 :func:`run_line`，只是把
轮数压到 3+6、并发度压到 1——判据与全量跑的是同一段代码，代价约 1 秒/次。

**扩展不在任何包索引上**（它由 CI 从框架仓同 tag 的源码构建），故"这台机器上装的是不是那份扩展"
由被测线自己前置核验；核验结论是**环境不适用**（能力缺失 / 扩展缺席 / 来源不同源）时跳过用例——
那不是本仓的实现问题，也不该让套件常年报红。除那几条之外的任何失败都照常判红。
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest

from zoo_bench.metrics import summarize
from zoo_bench.native_line.config import SWITCHES
from zoo_bench.native_line.forms import FORM_NATIVE, FORM_PURE, FORM_ROUTED
from zoo_bench.native_line.line import CHILD_MODULE, NativeLinePlan, run_line
from zoo_bench.native_line.measure import (
    FORM_PAIRS,
    ORDER_RULE,
    PAIR_PERCENTILES,
    comparison_key,
)

#: 被测框架规格（扩展的来源核验拿它与扩展的安装来源比对，design D9）.
FRAMEWORK = "zoo-framework==0.11.0"

#: 该线所在的目录（源码判据要扫它）.
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "src" / "zoo_bench" / "native_line"

#: 属于"这台机器的环境不适用"的结论——这些理由下的失败不该让用例报红（见模块 docstring）.
_ENVIRONMENT_REASONS = frozenset(
    {
        "native_line.reason.capability_missing",
        "native_line.reason.extension_unavailable",
        "native_line.reason.task_missing",
        "native_line.reason.source_untraceable",
        "native_line.reason.source_mismatch",
    }
)

#: 压到最小的测量轮数：判据与全量跑同段代码，只是样本少（用例只验结构，不用数值下结论）.
WARMUP_ROUNDS = 3
MEASURED_ROUNDS = 6

#: 该线的模块（按文件扫的那几条判据用它；``__init__`` 不是被测单位故不在内）.
_MODULES = tuple(sorted(path.stem for path in PACKAGE_DIR.glob("*.py") if path.stem != "__init__"))


def _measured(
    registers: tuple[int, ...] = (1, 8),
    *,
    config: dict[str, Any] | None = None,
    concurrencies: tuple[int, ...] = (1,),
    timeout: float = 300.0,
) -> dict[str, Any]:
    """真跑一轮该线；环境不适用时跳过用例，其余失败原样返回（由用例去断言）."""
    plan = NativeLinePlan(
        register_counts=registers,
        concurrencies=concurrencies,
        warmup_rounds=WARMUP_ROUNDS,
        measured_rounds=MEASURED_ROUNDS,
    )
    payload = run_line(
        FRAMEWORK, plan, config=SWITCHES if config is None else config, timeout=timeout
    )
    if payload["reason"] in _ENVIRONMENT_REASONS:
        pytest.skip(f"本机环境不适用（{payload['reason']}）：{payload.get('evidence', '')[:300]}")
    return payload


def _grid(
    payload: dict[str, Any], form: str, registers: int, concurrency: int = 1
) -> dict[str, Any]:
    """取某一格（形态, 规模, 并发度）的留档条目."""
    matches = [
        item
        for item in payload["groups"]
        if item["form"] == form
        and int(item["registers"]) == registers
        and int(item["concurrency"]) == concurrency
    ]
    assert len(matches) == 1, f"({form}, R{registers}, c={concurrency}) 的留档条目不是恰好一条"
    return matches[0]


def _ok_measurement(
    payload: dict[str, Any], form: str, registers: int, concurrency: int = 1
) -> dict[str, Any]:
    """取某一格的测量数据；该格不是 ok 时用例立即红（"缺数"不该被当成"无事发生"）."""
    grid = _grid(payload, form, registers, concurrency)
    assert grid["status"] == "ok", f"({form}, R{registers}) 不是 ok：{grid['reason']}"
    return grid["measurement"]


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    """一次默认配置下的真实测量（模块内共用，省掉重复的真跑）.

    整条线为 `failed` 时**在这里就红**：那样下面每个用例报的都是同一条根因，而不是各自撞出一个
    令人费解的键缺失。
    """
    result = _measured()
    assert result["status"] == "ok", f"默认配置下该线应为 ok，实得 {result['reason']}"
    assert result["closed_groups"] == [], "默认配置下不该有关闭态的格"
    return result


def _pair(payload: dict[str, Any], tier: str) -> dict[str, Any]:
    """某个档位的配对记录."""
    matches = [item for item in payload["pairwise"] if item["tier"] == tier]
    assert len(matches) == 1, f"档位 {tier} 的配对记录不是恰好一条"
    return matches[0]


# ------------------------------------------------------------------ 该线跑得通


def test_the_line_produces_a_complete_grid(payload: dict[str, Any]) -> None:
    """三形态 × 两档位的每一格都在场且各自出数——缺格与"某格没跑"必须区分得开."""
    assert payload["caliber"], "没有口径声明：数字的来源就无从声明"
    combos = {(item["form"], item["registers"], item["concurrency"]) for item in payload["groups"]}
    assert combos == {
        (form, registers, 1)
        for form in (FORM_PURE, FORM_NATIVE, FORM_ROUTED)
        for registers in (1, 8)
    }
    assert all(item["status"] == "ok" for item in payload["groups"])


# ------------------------------------------------------------------ 3.1 每规模一个类


def test_each_tier_gets_its_own_worker_class(payload: dict[str, Any]) -> None:
    """一个形态下一个规模一个类，且每格查到的统计条目就是它自己那一个类.

    分辨的是：若同一形态的两个规模共用了一个类名，框架统计会把两档混成一条（design D4 的实测
    依据）——那时该形态的类名只剩一个、或某一格的 ``arms`` 里冒出别的类。
    """
    class_map = payload["class_map"]
    assert len(class_map) == len(payload["plan"]["forms"]) * 2, "类数应为 形态数 × 档位数"
    for form in (FORM_PURE, FORM_NATIVE, FORM_ROUTED):
        names = {
            name: entry["registers"] for name, entry in class_map.items() if entry["form"] == form
        }
        assert len(names) == 2, f"{form} 形态的两个规模共用了一个类名：规模会混进同一条统计"
        assert sorted(names.values()) == [1, 8], f"{form} 形态的类↔规模映射不覆盖两档"
    assert len({entry["tier"] for entry in class_map.values()}) == 2

    for grid in payload["groups"]:
        measurement = grid["measurement"]
        entry = class_map[measurement["class_name"]]
        assert entry["registers"] == grid["registers"], (
            f"类名 {measurement['class_name']} 对应的规模 {entry['registers']} "
            f"与这一格的 {grid['registers']} 不一致"
        )
        snapshot = measurement["policy"]["snapshot"]
        if measurement["policy"]["recording_expected"]:
            # 记录统计的格：它自己那个类目必须在快照里，且逐臂块就是快照里那一条（不许另算一份）
            assert measurement["class_name"] in snapshot, "记录统计的格自己的类目不在快照里"
            assert all(
                measurement["policy"]["arms"][arm] == stats
                for arm, stats in snapshot[measurement["class_name"]].items()
            ), "该格的逐臂条目与快照里它自己那一条对不上"
        else:
            # 两臂恒定在场（缺样本时零填充），故"有没有走过"读 arms_present，不读 arms 的键集
            assert measurement["class_name"] not in snapshot, "不记账的形态不该在快照里有类目"
            assert measurement["policy"]["arms_present"] == []
            assert measurement["policy"]["n_sum"] == 0


# ------------------------------------------------------------------ 3.2 同一驱动接缝与零记账


def test_the_quiet_forms_record_nothing_and_the_closure_check_covers_them(
    payload: dict[str, Any],
) -> None:
    """纯解释 / 原生两形态跑完快照为空，且这条核验发生在路由形态第一次提交**之前**.

    分辨的是两件事：① 不记账的形态若记了账，它的耗时就不再是"零开销"的对照组——判据是这两格的
    逐臂条目为空、``n_sum`` 为 0、``recording_expected`` 为假；② 核验的时点若被挪到跑完之后，
    三形态共处一进程时快照不可能整个为空，那条核验就成了空结论——故断言 ``checked_at`` 与覆盖面
    （轮数 × 并发度 × 不记账形态数）。
    """
    for form in (FORM_PURE, FORM_NATIVE):
        for registers in (1, 8):
            policy = _ok_measurement(payload, form, registers)["policy"]
            assert policy["arms_present"] == [], f"{form} 形态在快照里留了类目"
            assert policy["n_sum"] == 0
            assert policy["recording_expected"] is False
            # 该判据对这几格**不适用**（它们本就不该记账），故是三态里的 None 而不是 False
            assert policy["n_sum_matches_submissions"] is None

    routed = _ok_measurement(payload, FORM_ROUTED, 1)
    assert routed["policy"]["n_sum"] == routed["submissions_total"]
    assert routed["policy"]["n_sum_matches_submissions"] is True, "逐臂次数之和必须等于提交数"

    closure = payload["closure"][0]
    assert closure["available"] is True and closure["snapshot_empty"] is True
    assert closure["checked_at"] == "before_first_routing_submission"
    assert set(closure["forms"]) == {FORM_PURE, FORM_NATIVE}
    quiet_keys = 2 * 2  # 不记账的形态数 × 档位数
    assert closure["submissions"] == WARMUP_ROUNDS * 1 * quiet_keys, (
        "覆盖面是 轮数 × 并发度 × 不记账形态数"
    )
    assert closure["child"]["pid"] == routed["pid"], "核验必须属于产出这些数字的那一次运行"


# ------------------------------------------------------------------ 配对采样（不跨运行拼接）


def test_the_three_forms_of_one_tier_come_from_one_sampling(payload: dict[str, Any]) -> None:
    """同档位三形态的数字出自**同一个子进程的同一次配对采样**（spec「对照不跨运行拼接」）.

    分辨的是：若把形态拆到各自的进程里跑（本仓实测过那一版：进程间漂移约 100 µs，比小规模上两
    形态的真实差距还大），各格的 pid 会不同、配对样本无从产生；那时这条断言必红。
    """
    pids = {int(grid["measurement"]["pid"]) for grid in payload["groups"]}
    assert len(pids) == 1, "同一个并发度下的各格必须出自同一个子进程"
    assert os.getpid() not in pids, "数字必须来自测量子进程，不能是跑用例的这个进程"

    assert len(payload["pairwise"]) == 2, "逐档位一条配对记录"
    for pair in payload["pairwise"]:
        assert pair["child"]["pid"] in pids
        assert pair["cycle_count"] == MEASURED_ROUNDS
        assert set(pair["forms"]) == {FORM_PURE, FORM_NATIVE, FORM_ROUTED}
        expected = {
            comparison_key(first, second)
            for first, second in FORM_PAIRS
            if first in pair["forms"] and second in pair["forms"]
        }
        assert set(pair["comparisons"]) == expected
        for comparison in pair["comparisons"].values():
            assert {"p25", "median", "p75"} <= set(comparison["difference_seconds"])
            assert comparison["resolved"] in (True, False)


def test_the_pairing_alternates_and_the_archived_numbers_recompute(payload: dict[str, Any]) -> None:
    """逐周期顺序逐轮翻转（ABBA），且留档的配对差能由**逐轮原始样本**复算回来.

    分辨的是两件事：① 顺序若不再翻转（例如重构时把三元组顺序写死），"先跑的那一形态"就会稳定
    沾上同一份系统性影响，配对差不再抵消它——断言相邻两周期的顺序互为逆序；② 留档里的配对差若
    与逐轮样本对不上（错位、用了预热的样本、把形态对调），复算出来的分位数就不同——所以这里不是
    读一遍留档自己写的数字，而是**从各格的 `rounds` 重新算**，并与留档逐值比对。
    """
    for tier in ("R1", "R8"):
        pair = _pair(payload, tier)
        cycles = pair["cycles"]
        assert [item["cycle"] for item in cycles] == list(range(MEASURED_ROUNDS))
        forms = sorted(pair["forms"])
        for index, item in enumerate(cycles):
            assert sorted(item["order"]) == forms, f"第 {index} 个周期的顺序不是三形态的一个排列"
            if index:
                assert item["order"] == list(reversed(cycles[index - 1]["order"])), (
                    "周期顺序 MUST 逐轮翻转（ABBA）"
                )

        # 逐轮原始样本（各格自己的 `rounds`）——配对差与它的复算都从这一份出发
        per_round = {
            form: _ok_measurement(payload, form, int(pair["registers"]))["rounds"] for form in forms
        }
        for index, item in enumerate(cycles):
            for form in forms:
                recomputed = float(per_round[form][index]["wall_seconds"]) / pair["concurrency"]
                assert item["per_task_seconds"][form] == recomputed, (
                    f"{tier} 第 {index} 周期的 {form} 每任务耗时与各格逐轮样本对不上"
                )
        for first, second in FORM_PAIRS:
            key = comparison_key(first, second)
            if key not in pair["comparisons"]:
                continue
            differences = [
                item["per_task_seconds"][first] - item["per_task_seconds"][second]
                for item in cycles
            ]
            assert pair["comparisons"][key]["difference_seconds"] == summarize(
                differences, PAIR_PERCENTILES
            ), f"{tier} 的 {key} 配对差不是由留档的逐周期样本算出来的"

    assert _ok_measurement(payload, FORM_PURE, 1)["sampling"]["order_rule"] == ORDER_RULE


# ------------------------------------------------------------------ 3.3 规模单调


def test_the_task_cost_rises_with_frame_size_within_one_process() -> None:
    """同一进程内执行体耗时随帧规模上升（两端严格、最小两档在容差内）.

    分辨的是：档位若被张冠李戴（帧按别的规模构造），大档就不再显著高于小档，这条会红。最小两档
    刻意用容差：7→21 字节的任务差只有几微秒，而派发层开销是三种形态的**共同加数**（数百微秒，
    见 design D8 的口径说明），那一档的样本噪声足以翻转先后——断言它"必然更大"是把噪声当判据。
    """
    payload = _measured(registers=(1, 8, 125), timeout=600.0)
    assert payload["status"] == "ok", payload.get("reason")
    pids = {int(grid["measurement"]["pid"]) for grid in payload["groups"]}
    assert len(pids) == 1, "三档必须在同一轮内量（同一子进程）"

    for form in (FORM_PURE, FORM_NATIVE):
        medians = {
            registers: _ok_measurement(payload, form, registers)["end_to_end_per_task_seconds"][
                "median"
            ]
            for registers in (1, 8, 125)
        }
        assert medians[125] > medians[1] * 1.05, f"{form}：255 字节档不比 7 字节档更慢"
        assert medians[125] > medians[8], f"{form}：255 字节档不比 21 字节档更慢"
        assert medians[8] > medians[1] * 0.9, f"{form}：最小两档的差距超出容差"


# ------------------------------------------------------------------ 3.4 统计落盘保持关闭


def test_the_stats_path_is_refused_at_the_config_layer(payload: dict[str, Any]) -> None:
    """声明了统计落盘路径时该线**拒绝开跑**，一格数字也不产出（design D6 的配置层断言）.

    分辨的是：若这条禁令只剩注释，配上路径的测量会照常出数（而框架内没有 flush 调用点，
    所以"没落盘"这件事根本证明不了禁令被执行了）——那时 ``status`` 会是 ok、且有格子。
    """
    assert payload["switches"]["adaptive:statsPath"] == "", "默认组合里不该有落盘路径"
    for grid in payload["groups"]:
        config_dir = Path(grid["measurement"]["config_dir"])
        assert {path.name for path in config_dir.iterdir()} == {"config.json"}, (
            "测量目录只见那份配置——没有任何统计文件被写出来"
        )

    staged = "/tmp/zoo-bench-native-line-stats.json"
    poisoned = _measured(config={"adaptive": {"enabled": True, "statsPath": staged}})
    assert poisoned["status"] == "failed"
    assert poisoned["reason"] == "native_line.reason.child_failed", poisoned["reason"]
    assert poisoned["groups"] == [], "被拦下时不该有任何一格的数字"
    assert poisoned["derived"]["suppressed"] is True
    assert staged in poisoned["evidence"], "证据里要能读到被拒的那个路径"


# ------------------------------------------------------------------ 3.5 从公开快照取数、零插桩


def test_arm_counts_and_means_come_from_the_public_snapshot(payload: dict[str, Any]) -> None:
    """逐臂 ``n`` 与均值取自框架的公开快照：次数之和等于该格提交数，且均值是**执行**口径.

    分辨的是：① 若均值的样本窗口与提交数不符（自算窗口含预热、或漏了轮次），"n 之和 = 提交数"
    就对不上；② 若某处把臂均值换成了端到端口径的数（那正是"本仓自己插桩量一次"的形态），
    均值就不再显著小于该格的每任务端到端耗时——而框架的臂均值量的是**执行体本身**，
    端到端里还含派发层那段三种形态共同的开销（口径见 design D8）。
    """
    measurement = _ok_measurement(payload, FORM_ROUTED, 1)
    policy = measurement["policy"]
    arms = policy["arms"]
    assert set(arms) == {"native", "python"}, "两臂恒定在场（缺样本时零填充）"
    assert sum(float(entry["n"]) for entry in arms.values()) == measurement["submissions_total"]
    exercised = sorted(arm for arm, entry in arms.items() if float(entry["n"]) > 0)
    assert exercised, "路由形态跑了预热 + 正式采样，至少有一臂该攒下样本"
    assert all(float(arms[arm]["mean"]) > 0.0 for arm in exercised)
    assert policy["arms_present"] == sorted(arms), "两臂都在该类目的快照条目里"

    end_to_end = float(measurement["end_to_end_per_task_seconds"]["median"])
    for arm in exercised:
        assert float(arms[arm]["mean"]) < end_to_end, (
            f"{arm} 臂的均值不小于每任务端到端耗时——它量的不是执行体本身"
        )


def _framework_patch_offenders() -> list[str]:
    """扫出"覆盖框架对象"的语句（本仓对被测框架零新增插桩接缝，design D5）.

    判据是静态的：哪个模块 import 了框架，就不许把该名字（或 ``zoo_framework`` 本身）当赋值目标、
    也不许把它交给 ``setattr``/``delattr``。**动态地在父进程里比对象身份证明不了这件事**——插桩若
    发生，它发生在子进程里，父进程的对象根本没被碰过；而子进程里跑的正是这些模块，故这里是它的
    落点。自家对象的属性赋值（``self._done = ...``、``worker.on_failure = ...``）不在判据之内。
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {"zoo_framework"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("zoo_framework"):
                names.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.update(
                    alias.asname or alias.name.split(".")[0]
                    for alias in node.names
                    if alias.name.startswith("zoo_framework")
                )
        if len(names) == 1:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                if _root_name(node.value) in names:
                    offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("setattr", "delattr")
                and node.args
                and _root_name(node.args[0]) in names
            ):
                offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
    return offenders


def _root_name(node: ast.expr) -> str:
    """属性链的最左端名字（``a.b.c`` → ``a``）；不是名字时给空串."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else ""


def test_no_module_patches_the_framework() -> None:
    """该线的模块不覆盖框架的任何对象（零新增插桩接缝，design D5）."""
    assert _framework_patch_offenders() == [], (
        "上述语句覆盖了框架对象；计时入口被外部改写后，测的就不是框架了"
    )


# ------------------------------------------------------------------ 3.6 档位由配置声明


def test_tier_registers_come_from_the_plan_not_from_constants() -> None:
    """改计划即改测量：档位（以及帧字节数）跟着计划走，源码里没有第二处档位声明.

    分辨的是：若档位被硬编码在测量代码里，"改配置即改测量"这条就不成立——用一个矩阵里没有的
    规模跑一次，留档里的寄存器数与帧字节数必须跟着它走。
    """
    plan = NativeLinePlan(
        register_counts=(5, 9), concurrencies=(1,), warmup_rounds=1, measured_rounds=2
    )
    assert [tier.registers for tier in plan.tiers] == [5, 9]

    payload = _measured(registers=(5,))
    assert payload["status"] == "ok", payload.get("reason")
    measurement = _ok_measurement(payload, FORM_NATIVE, 5)
    assert measurement["tier"]["registers"] == 5
    assert measurement["tier"]["frame_bytes"] == 5 * 2 + 5, "帧由档位构造：registers × 2 + 5"

    # 档位清单的字面量：``"registers": [1, 8]`` / ``registers=[1, 8]`` 两种写法都要扫到（引号可选，
    # 因为留档键会写成带引号的那一种——漏掉它这条扫描就形同虚设）
    hard_coded = re.compile(r"""["']?registers["']?\s*[:=]\s*[\[(]\s*\d+\s*,\s*\d+""")
    offenders = [
        path.name
        for path in PACKAGE_DIR.glob("*.py")
        if hard_coded.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"档位清单只许出现在配置里，这些文件里出现了字面量：{offenders}"


# ------------------------------------------------------------------ 3.7 配置时序与关闭态


def test_an_empty_config_closes_the_routing_form_without_omitting_the_line() -> None:
    """不含开关的配置下：路由形态按关闭语义示人，纯解释与原生照常出数（整线不缺席）.

    分辨的是：若把"关闭态"当成失败、或因它而整线不出数，规格「开关处于关闭状态本身 MUST NOT
    导致整条线被省略」就破了——断言该线仍为 ok、两形态仍有数字、对照结论仍在，而路由那两格带
    原因在场且没有数字。
    """
    payload = _measured(config={})
    assert payload["status"] == "ok", payload.get("reason")
    assert payload["switches"]["native:enabled"] is False
    assert payload["switches"]["adaptive:enabled"] is False

    assert len(payload["closed_groups"]) == 2
    for registers in (1, 8):
        grid = _grid(payload, FORM_ROUTED, registers)
        assert grid["status"] == "closed"
        assert grid["reason"] == "native_line.reason.native_switch_off"
        assert grid["reason_params"] == {"native_enabled": False, "adaptive_enabled": False}
        assert "end_to_end_per_task_seconds" not in grid["measurement"], "关闭态不给数字"
        for form in (FORM_PURE, FORM_NATIVE):
            assert _ok_measurement(payload, form, registers)["policy"]["arms_present"] == []

    assert payload["by_tier"][0]["pure_vs_native"] is not None, "对照照常由两形态给出"
    assert payload["by_tier"][0]["forms_measured"] == [FORM_NATIVE, FORM_PURE]
    for item in payload["routing"]["per_tier"]:
        assert item["routed_status"] == "closed"
        assert item["convergence"]["reason"] == "native_line.reason.native_switch_off"
        assert item["agreement"]["reason"] == "native_line.reason.native_switch_off"
        assert item["convergence"]["total"] == 0, "关闭态不是「样本太少」"


def test_the_routed_form_records_nothing_when_only_routing_is_off() -> None:
    """原生开、路由关：路由形态照跑，但整轮快照为空（框架的关闭语义，零记账）.

    分辨的是：这是"开关只由这份配置决定"的另一态（design D13）——路由形态若静默退化成了别的
    东西，或参数没按这份配置解析，快照就会有条目，这条红。
    """
    payload = _measured(config={"adaptive": {"enabled": False}, "native": {"enabled": True}})
    assert payload["status"] == "ok", payload.get("reason")
    assert payload["switches"]["adaptive:enabled"] is False
    assert payload["switches"]["native:enabled"] is True
    assert payload["closed_groups"] == [], "原生开关为真时路由形态不该是关闭态"

    for grid in payload["groups"]:
        measurement = grid["measurement"]
        assert measurement["status"] == "ok", "路由形态照跑（这一态不是「不适用」）"
        assert measurement["policy"]["snapshot"] == {}, "整轮快照必须是空的"
        assert measurement["policy"]["arms_present"] == []
        assert measurement["policy"]["recording_expected"] is False
        assert measurement["policy"]["n_sum_matches_submissions"] is None

    # 路由形态在场（没被当作关闭态）却零记账：核验时点因此仍是"路由第一次提交之前"
    assert payload["closure"][0]["checked_at"] == "before_first_routing_submission"


def test_the_child_entry_point_keeps_the_framework_import_off_its_module_level() -> None:
    """子进程入口的模块级 import 只有标准库与 ``.config``（design D13 的结构性判据）.

    分辨的是：配置目录必须先于框架 import 就位，而"先 import 就冻死了开关"这件事只在**模块级**
    import 时发生。若有人把 `from .measure import ...` 提到 `child.py` 顶部（或让 `.config` 去碰
    框架），框架会在 `chdir` 之前被 import——那时这条红，而运行期的验证很可能仍然是绿的
    （`config_of` 是活读，看上去一切正常）。
    """
    allowed_sibling = {"config"}
    for name in ("child", "config"):
        tree = ast.parse((PACKAGE_DIR / f"{name}.py").read_text(encoding="utf-8"))
        module_level = _module_level_imports(tree)
        assert not [item for item in module_level if item.startswith("zoo_framework")], name
        siblings = module_level & set(_MODULES)
        assert siblings <= allowed_sibling, (
            f"{name}.py 的模块级 import 了兄弟模块 {sorted(siblings - allowed_sibling)}；"
            "只有 .config 是纯标准库的，其余兄弟模块一碰框架就会让配置目录来不及就位"
        )
    assert CHILD_MODULE == "zoo_bench.native_line.child"


def _module_level_imports(tree: ast.Module) -> set[str]:
    """模块级 import 到的模块名（相对 import 取被导入的那一段，如 ``from .config import X`` → config）."""
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
        elif isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
    return found
