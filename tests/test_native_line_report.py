"""该维度在报告里长什么样：与主矩阵分列、不利对称公开、版本间可对比、三格式两语言在场
（spec: native-task-line 的报告要求；tasks 5.1–5.5）。

与 `tests/test_native_line_archive.py` 的分工：那边问"结论怎么落档、失败长什么样"，本文件问
"留档进了报告层之后，读者看到什么"。故断言对象是**报告模型与渲染产物**，不是留档结构。

三处刻意安排：

1. **5.1 用一次真跑**：它要求"结论可由留档里逐臂次数与均值复算得出"。拿合成数字复算，复算与
   结论两边都是我写的常量，什么也证明不了——故这一条跑一次真实测量（真扩展、真框架），再按
   文档里的判据独立算一遍，与报告的说法对照。
2. **其余用例把合成留档嫁接到真留档上**（只换 ``native_line`` 那一个键）：主矩阵、语义、归因
   与环境都是真的，只有该线的数字由用例给定。而合成数字刻意落在**毫秒量级**：主矩阵那几个维度
   印的是微秒，5.2 的"数值集合不相交"因此是结构上必然的，不靠"没能巧合撞上"。
3. **口径声明的断言落在文本上**：``native_line.note.scope`` 是这条维度"不与对照方案横向读"的
   全部内容，它没进产物就等于没声明。
"""

from __future__ import annotations

import html as html_module
import re
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from zoo_bench import compare, environment, storage
from zoo_bench.i18n import LANG_EN, LANG_ZH, LANGS, MESSAGES, MissingTranslation, resolve_reason, t
from zoo_bench.native_line.forms import FORM_NATIVE, FORM_PURE, FORM_ROUTED
from zoo_bench.native_line.line import ARM_NATIVE, ARM_PYTHON, NativeLinePlan
from zoo_bench.render import blocks as blocks_module
from zoo_bench.render import compare as compare_renderer
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.render import report as report_renderer
from zoo_bench.render.blocks import NOTE, format_seconds
from zoo_bench.report import build_model
from zoo_bench.runner import UnitSpec, measure_matrix

#: 被测框架规格。该线拿它与扩展的安装来源比对（design D9），故它必须与本机装的那一份一致。
FRAMEWORK = "zoo-framework==0.11.0"

#: 该线自己的规模档位与并发度：三档才看得出"小帧不划算、大帧划算"这个方向，也才够撑出分界结论。
_PLAN = NativeLinePlan(
    register_counts=(1, 8, 125), concurrencies=(1,), warmup_rounds=5, measured_rounds=32
)

#: 逐档判据的样本下限（``native_line.line.MIN_JUDGMENT_SAMPLES``）。写死而不是调被测代码：那是被测
#: 常量，用它算期望值等于自证。
MIN_JUDGMENT_SAMPLES = 30

#: deck 的真实 slide 才带 ``data-screen-label``（框架 CSS 的注释里也含 ``<section class="slide">``）。
_SLIDE = re.compile(
    r'<section class="slide[^>]*data-screen-label="([^"]*)"[^>]*>(.*?)</section>', re.S
)


def _line_section_title(lang: str) -> str:
    """该线在报告里的章节标题（与渲染层同一处拼法）。"""
    return f"{blocks_module.dimension_section_prefix(lang)}{t('report.native_line.title', lang)}"


def _template_prefix(key: str, lang: str) -> str:
    """某条文案模板在**第一个参数之前**的字面前缀。

    deck 按句读把长句切段（``deck._split_text``），整句断言会因切段而假红；而这段前缀里没有句读
    符，必然完整出现。从目录里取而不是在用例里重抄一遍：文案改了这里跟着改。模板以参数开头时
    前缀为空串——**空串对断言毫无用处**，故调用侧一律挑不以 ``{`` 开头的模板。
    """
    prefix = MESSAGES[key][lang].split("{", 1)[0]
    assert prefix, f"{key} 的模板以参数开头，取不到可用前缀"
    return prefix


def _summary(value: float) -> dict[str, float | int]:
    return {
        "n": 32,
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


def _pdf_text(path: str | Path) -> str:
    return "".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _sections(text: str) -> dict[str, str]:
    """Markdown 按二级标题切成 ``标题 -> 正文``。"""
    parts: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            parts[current] = ""
        elif current is not None:
            parts[current] += line + "\n"
    return parts


@pytest.fixture(scope="module")
def real_archive(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """一次真实测量（真扩展 + 真框架）并**经留档往返**取回的那份数据.

    走 ``save_run`` / ``load_run`` 而不是把内存里的结果直接用掉：报告读的是留档，而"留档里存得下
    的东西"与"内存里的对象"不完全是一回事（两边各自取到 JSON 的那一份）。该线跑不出结论时在这里
    就红——那样下面每条用例报的都是同一条根因。
    """
    root = tmp_path_factory.mktemp("real")
    result = measure_matrix(
        [
            UnitSpec(
                adapter="thread_pool",
                framework=FRAMEWORK,
                concurrency=1,
                body_tier_us=300.0,
                warmup_rounds=1,
                measured_rounds=2,
            )
        ],
        verify_adapters=False,
        native_line=_PLAN,
    )
    # 真 `zoo-bench run` 会并存环境自述；缺它时渲染层拒绝发布，故这里照同一条路走
    result["environment"] = environment.collect(command=["zoo-bench", "run"])
    line = result["native_line"]
    assert line["status"] == "ok", f"真环境里该线应为 ok，实得 {line['status']}：{line['reason']}"
    path = storage.save_run(result, framework=FRAMEWORK, root=root / "results")
    return storage.load_run(path)


# ------------------------------------------------------------------ 合成留档的两件零件


def _comparison(first: str, second: str, faster: str | None) -> dict[str, Any]:
    """一格里的两形态配对差（报告只读 ``first`` / ``second`` / ``faster_form`` 三项）."""
    return {
        "first": first,
        "second": second,
        "difference_seconds": {"n": 32, "median": 0.0, "p25": -1e-6, "p75": 1e-6, "iqr": 2e-6},
        "resolved": faster is not None,
        "faster_form": faster,
    }


def _tier_row(
    tier: str,
    registers: int,
    frame_bytes: int,
    pure_us: float,
    native_us: float,
    routed_us: float,
    *,
    pure_vs_native: str | None,
    pure_vs_routed: str | None,
) -> dict[str, Any]:
    """一格留档。两个 ``*_vs_*`` 形参是该配对差给出的**更快的一方**（None 为未决）."""
    medians = {
        FORM_PURE: pure_us / 1e6,
        FORM_NATIVE: native_us / 1e6,
        FORM_ROUTED: routed_us / 1e6,
    }
    return {
        "tier": tier,
        "registers": registers,
        "frame_bytes": frame_bytes,
        "concurrency": 1,
        "medians_seconds": medians,
        "quartiles_seconds": {
            form: {"p25": value * 0.98, "p75": value * 1.02} for form, value in medians.items()
        },
        "ratios_vs_pure": {
            FORM_NATIVE: native_us / pure_us,
            FORM_ROUTED: routed_us / pure_us,
        },
        "comparisons": {
            "pure-vs-native": _comparison(FORM_PURE, FORM_NATIVE, pure_vs_native),
            "pure-vs-routed": _comparison(FORM_PURE, FORM_ROUTED, pure_vs_routed),
        },
        "forms_measured": [FORM_NATIVE, FORM_PURE, FORM_ROUTED],
        "cycle_count": 32,
        "faster_form": pure_vs_native,
        "is_judgment_concurrency": True,
    }


def _routing_tier(tier: str, frame_bytes: int, native_n: int, python_n: int) -> dict[str, Any]:
    """一格的路由结论（取多数臂、已收敛，故两个"给不出"的原因都是空）."""
    total = native_n + python_n
    arm = ARM_NATIVE if native_n > python_n else ARM_PYTHON
    minority = min(native_n, python_n)
    return {
        "tier": tier,
        "frame_bytes": frame_bytes,
        "concurrency": 1,
        "counts": {ARM_NATIVE: native_n, ARM_PYTHON: python_n},
        "n_sum": total,
        "submissions_total": total,
        "measured_total": total,
        "arm_means_seconds": {ARM_NATIVE: 0.0004, ARM_PYTHON: 0.00035},
        "snapshot_n": {ARM_NATIVE: native_n, ARM_PYTHON: python_n},
        "n_sum_matches": True,
        "routed_status": "ok",
        "routed_reason": "",
        "dominant_arm": arm,
        "minority_samples": minority,
        "exploration": 0.05,
        "convergence": {
            "converged": True,
            "minority_share": minority / total,
            "limit": 0.15,
            "total": total,
            "minority_samples": minority,
            "exploration": 0.05,
            "reason": "",
            "reason_params": {},
        },
        "agreement": {
            "advantageous_arm": ARM_NATIVE,
            "resolution_available": True,
            "agrees": arm == ARM_NATIVE,
            "reason": "",
            "reason_params": {},
        },
    }


def _payload(
    rows: list[dict[str, Any]],
    routing_per_tier: list[dict[str, Any]],
    crossover: dict[str, Any],
    *,
    plan_tiers: list[dict[str, Any]],
) -> dict[str, Any]:
    """一份形状与真留档一致的 ``native_line`` 块（字段逐个照真档给——缺一个渲染就会 KeyError）."""
    return {
        "status": "ok",
        "reason": "",
        "reason_params": {},
        "framework": FRAMEWORK,
        "capability": {"present": True, "missing": []},
        "extension": {
            "module": "zoo_framework_native",
            "contract_version": 1,
            "expected_revision": "v0.11.0",
            "source": {"requested_revision": "v0.11.0"},
        },
        "equivalence": {
            "ok": True,
            "comparison": "byte_identical",
            "checked": 15,
            "groups": {"valid": 8, "invalid": 4, "error": 3},
            "input_envelope_bytes": 256,
            "mismatches": [],
        },
        "switches": {"native:enabled": True, "adaptive:enabled": True},
        "plan": {
            "tiers": plan_tiers,
            "concurrencies": [1],
            "judgment_concurrency": 1,
            "warmup_rounds": 5,
            "measured_rounds": 32,
            "child_count": 1,
            "sampling": {
                "granularity": "concurrency",
                "order_rule": "even_cycle_forward_odd_cycle_reverse",
            },
        },
        "groups": [],
        "pairwise": [],
        "closure": [],
        "closed_groups": [],
        "failed_groups": [],
        "by_tier": rows,
        "crossover": crossover,
        "routing": {
            "per_tier": routing_per_tier,
            "converged_tier_count": sum(
                1 for item in routing_per_tier if item["convergence"]["converged"]
            ),
            "decided_tier_count": len(routing_per_tier),
            "agrees_count": sum(1 for item in routing_per_tier if item["agreement"]["agrees"]),
            "all_agree": bool(routing_per_tier),
            "note": "native_line.note.routing",
        },
        "note": ["native_line.note.crossover", "native_line.note.routing"],
    }


#: 合成档位 ``(档位, 寄存器数, 帧字节, 纯解释微秒, 原生微秒, 路由微秒, 纯vs原生谁快, 纯vs路由谁快)``。
#: 中位数刻意落在**毫秒量级**（7.771 毫秒起）：主矩阵那几个维度印的是微秒，5.2 的"数值集合不相交"
#: 因此是结构上必然的，而不是"这一轮没能巧合撞上"。
_MIXED = (
    ("R1", 1, 7, 7771.0, 8023.0, 10119.0, FORM_PURE, FORM_PURE),
    ("R8", 8, 61, 19405.5, 20612.4, 25987.5, FORM_PURE, FORM_PURE),
    ("R125", 125, 260, 84021.6, 73508.2, 104980.3, FORM_NATIVE, FORM_PURE),
)


def _mixed_line() -> dict[str, Any]:
    """小帧上**原生形态更慢**、路由形态也慢于纯解释的那一份合成留档（5.3 的靶子）."""
    rows = [
        _tier_row(tier, registers, frames, pure, native, routed, pure_vs_native=a, pure_vs_routed=b)
        for tier, registers, frames, pure, native, routed, a, b in _MIXED
    ]
    return _payload(
        rows,
        [
            _routing_tier("R1", 7, 32, 0),
            _routing_tier("R8", 61, 0, 32),
            _routing_tier("R125", 260, 32, 0),
        ],
        {
            "judgment_concurrency": 1,
            "native_faster_tiers": ["R125"],
            "native_slower_tiers": ["R1", "R8"],
            "undecided_tiers": [],
            "monotonic": True,
            "boundary": {"below_frame_bytes": 61, "at_or_above_frame_bytes": 260},
            "note": ["native_line.note.crossover"],
        },
        plan_tiers=[
            {"label": tier, "registers": registers, "frame_bytes": frames}
            for tier, registers, frames, *_ in _MIXED
        ],
    )


def _many_tier_line(count: int) -> dict[str, Any]:
    """档位很多的一份留档：用来把 deck 的"本章装不下就出一页指引"那条路走出来."""
    labels = [f"R{index + 1}" for index in range(count)]
    frames = [7 + index * 8 for index in range(count)]
    rows = [
        _tier_row(
            label,
            index + 1,
            frame,
            400.0 + index * 100.0,
            380.0 + index * 100.0,
            520.0 + index * 100.0,
            pure_vs_native=FORM_NATIVE,
            pure_vs_routed=FORM_PURE,
        )
        for index, (label, frame) in enumerate(zip(labels, frames, strict=True))
    ]
    return _payload(
        rows,
        [_routing_tier(label, frame, 30, 2) for label, frame in zip(labels, frames, strict=True)],
        {
            "judgment_concurrency": 1,
            "native_faster_tiers": labels,
            "native_slower_tiers": [],
            "undecided_tiers": [],
            "monotonic": True,
            "boundary": None,
            "note": ["native_line.note.crossover"],
        },
        plan_tiers=[
            {"label": label, "registers": index + 1, "frame_bytes": frame}
            for index, (label, frame) in enumerate(zip(labels, frames, strict=True))
        ],
    )


def _with_line(archive: dict[str, Any], line: dict[str, Any]) -> dict[str, Any]:
    """把合成的那一个键嫁接到真留档上（主矩阵、语义、归因、环境都还是真的）."""
    return {**archive, "native_line": line}


def _model(archive: dict[str, Any], lang: str) -> dict[str, Any]:
    return build_model(archive, lang=lang, source={"path": "results/test.json"})


def _line_medians(model: dict[str, Any], lang: str) -> set[str]:
    """该维度每一格三形态的中位数，按报告自己的换算印一遍（5.2 的判据就是它们）."""
    return {
        format_seconds(value, lang)
        for row in model["dimensions"]["native_line"]["rows"]
        for value in row["medians_seconds"].values()
    }


# ------------------------------------------------------------------ 5.1 该维度与它的结论


def test_the_dimension_gives_three_forms_with_ratios_and_a_crossover_verdict(
    real_archive: dict[str, Any],
) -> None:
    """该维度按规模给出三形态的数值与相对倍数，并给出规模分界结论与所用探索率.

    三形态**都要**在数字里：只给"原生 vs 纯解释"的话，路由形态的开销无处可看，而它正是这条线要
    回答的一半（框架把决策权交给了哪个形态）。
    """
    for lang in LANGS:
        model = _model(real_archive, lang)
        dimension = model["dimensions"]["native_line"]
        assert dimension["status"] == "ok", lang
        assert dimension["rows"], lang

        row = dimension["rows"][0]
        assert set(row["medians_seconds"]) == {FORM_PURE, FORM_NATIVE, FORM_ROUTED}, lang
        assert set(row["ratios_vs_pure"]) == {FORM_NATIVE, FORM_ROUTED}, lang

        assert dimension["crossover"]["judgment_concurrency"] == 1, lang
        assert set(dimension["crossover"]) >= {
            "native_faster_tiers",
            "native_slower_tiers",
            "undecided_tiers",
            "boundary",
        }, lang

        # 探索率是框架解析后的实际值，出现在逐档的路由结论里
        exploration = dimension["routing"]["per_tier"][0]["exploration"]
        assert exploration is not None, lang
        text = markdown_renderer.render_markdown(model, [])
        assert f"{exploration * 100.0:.2f}%" in text, f"{lang}: 报告里没有该轮实际用的探索率"


def test_the_routing_verdict_is_recomputable_from_the_archived_arm_counts(
    real_archive: dict[str, Any],
) -> None:
    """报告里的路由结论能由留档**独立复算**出来：逐臂次数的多数者 + 三态判据.

    这是 5.1 的鉴别力所在。若报告层自己算一遍（或把结论写死），这条断言与留档对不上；而只断言
    "报告里有句话"，实现把结论写错也照样绿。
    """
    line = real_archive["native_line"]
    recomputed_arms: dict[str, str] = {}
    for tier in line["routing"]["per_tier"]:
        counts = tier["counts"]
        native_n, python_n = int(counts[ARM_NATIVE]), int(counts[ARM_PYTHON])
        total = native_n + python_n
        # 判据一：次数是精确量，多数者即"实际走的臂"（两臂相等时留档不给结论）
        if native_n != python_n:
            recomputed_arms[str(tier["tier"])] = ARM_NATIVE if native_n > python_n else ARM_PYTHON
        # 判据二：样本够、另一臂被走过、少数臂占比不超过仅靠探索率能解释的上限
        minority = min(native_n, python_n)
        exploration = float(tier["exploration"])
        converged = (
            total >= MIN_JUDGMENT_SAMPLES and minority > 0 and minority / total <= 3 * exploration
        )
        assert bool(tier["convergence"]["converged"]) is converged, tier["tier"]
        assert int(tier["convergence"]["total"]) == total, tier["tier"]
        assert int(tier["convergence"]["minority_samples"]) == minority, tier["tier"]
        if converged:
            assert tier["dominant_arm"] == recomputed_arms[str(tier["tier"])], tier["tier"]

    for lang in LANGS:
        reported = _model(real_archive, lang)["dimensions"]["native_line"]["routing"]["per_tier"]
        assert {str(item["tier"]) for item in reported} == set(recomputed_arms), lang
        for item in reported:
            tier = str(item["tier"])
            assert item["dominant_arm"] == recomputed_arms[tier], f"{lang} {tier}"


def test_an_unconverged_tier_names_why_instead_of_stating_an_arm(
    real_archive: dict[str, Any],
) -> None:
    """收敛判不过时报告给的是**原因**与判据数字，而不是一个臂名.

    "给不出结论"与"收敛到某臂"是两件事：前者的读者该去看探索率与采样数，后者该去看形态对照。
    合成一份三档都是少数臂占 6/32、探索率仅 5% 的留档：那时 18.8% > 3×5%，路由确实一档都没
    收敛。于是"已收敛到某臂"这句话（无论哪个臂）整篇都不该出现，而判据数字必须在。
    """
    line = _mixed_line()
    for item in line["routing"]["per_tier"]:
        item["counts"] = {ARM_NATIVE: 6, ARM_PYTHON: 26}
        item["minority_samples"] = 6
        item["dominant_arm"] = ARM_PYTHON
        item["convergence"] = {
            "converged": False,
            "minority_share": 0.1875,
            "limit": 0.15,
            "total": 32,
            "minority_samples": 6,
            "exploration": 0.05,
            "reason": "native_line.reason.not_converged",
            "reason_params": {
                "tier": str(item["tier"]),
                "share_pct": 18.8,
                "limit_pct": 15.0,
                "exploration_pct": 5.0,
            },
        }
    archive = _with_line(real_archive, line)

    for lang in LANGS:
        text = markdown_renderer.render_markdown(_model(archive, lang), [])
        assert "R1" in text, f"{lang}: 逐档结论没落到档位上"
        assert "18.8" in text and "15.0" in text, f"{lang}: 没给出少数臂占比与它要过的上限"
        for arm in (ARM_NATIVE, ARM_PYTHON):
            assert (
                t(
                    "blocks.native_line.routing_converged",
                    lang,
                    arm=t(f"native_line.arm.{arm}", lang),
                )
                not in text
            ), f"{lang}: 三档都没收敛，却报了「已收敛到{arm}臂」"


# ------------------------------------------ 所测任务与它的输入形态（规格：负载可核）


def test_the_chapter_states_the_task_and_the_input_shape(real_archive: dict[str, Any]) -> None:
    """报告章节说明所测**是什么任务**、输入是什么形态，使读者能把结论对应到自己的负载.

    判据从**留档里的任务名**拼出，而不是在这里重抄一份文案：名字若不是这一轮实际跑的那个，读者接上
    的就不是这份数字的负载。输入形态那一句还须点名帧的形态（RTU ADU 响应帧）——只给一个任务名，
    读者仍然不知道自己的负载与它可不可比。缺这一项的留档（早于该字段的）宁可不印，也不许把
    ``None`` 印进产物。
    """
    task = real_archive["native_line"]["plan"].get("task")
    assert task, "留档里没有所测任务名：负载是真实任务这件事就不可核"
    for lang in LANGS:
        text = markdown_renderer.render_markdown(_model(real_archive, lang), [])
        assert t("blocks.native_line.task", lang, task=task) in text, (
            f"{lang}: 报告没说明所测任务是什么"
        )
        assert "RTU ADU" in text, f"{lang}: 报告没说明输入是什么形态"

    # 前置：这一份合成留档的 plan 里确实没有该键，才谈得上"缺字段时印什么"
    legacy = _mixed_line()
    assert "task" not in legacy["plan"]
    for lang in LANGS:
        text = markdown_renderer.render_markdown(_model(_with_line(real_archive, legacy), lang), [])
        assert _template_prefix("blocks.native_line.task", lang) not in text, (
            f"{lang}: 留档没有任务名，报告却印了这一段——多半是印了一个 None 给读者"
        )


# ------------------------------------------------------------------ 5.2 口径声明与分列


def test_the_line_declares_its_scope_and_keeps_its_numbers_out_of_the_main_matrix(
    real_archive: dict[str, Any],
) -> None:
    """该维度开头声明"不与主矩阵对照方案横向读"，且它的数值**不出现在**其余章节里.

    "不相交"的判据取**同一套换算后的数字形态**：该维度每一格三形态的中位数按报告自己的换算印一遍
    （``format_seconds``），这些字符串必须出现在它自己那一节里、且一处都不出现在别的章节里。只断言
    "主矩阵的表里没有 R1 这个档位名"是没有牙齿的——两边的档位词汇本来就不同（方案名 vs 规模名），
    永远不相交。
    """
    archive = _with_line(real_archive, _mixed_line())
    for lang in LANGS:
        model = _model(archive, lang)
        sections = _sections(markdown_renderer.render_markdown(model, []))
        title = _line_section_title(lang)
        assert title in sections, f"{lang}: 报告里没有该维度的章节"

        # 口径声明在同一节里（标题下那一段）
        assert t("native_line.note.scope", lang) in sections[title], f"{lang}: 没有口径声明"

        values = _line_medians(model, lang)
        assert values, lang
        elsewhere = "\n".join(body for name, body in sections.items() if name != title)
        for value in values:
            assert value in sections[title], f"{lang}: 该线的数值没印在它自己那一节里：{value}"
            assert value not in elsewhere, f"{lang}: 该线的数值漏进了别的章节：{value}"


def test_the_main_matrix_sections_never_carry_the_lines_tier_labels(
    real_archive: dict[str, Any],
) -> None:
    """该线的档位名（R1/R8/R125）只在该线自己的章节与不利章节里出现：主矩阵不会拿它们当一列.

    「不利数据」那一节是**唯一**的例外，且是刻意的：5.3 要求该线的劣势在那一节被指名（一行指针），
    而指名就得说出是哪一档。其余章节（结论、图表、别的维度、附录）一个都不该有——那才是"分列"。
    """
    archive = _with_line(real_archive, _mixed_line())
    for lang in LANGS:
        sections = _sections(markdown_renderer.render_markdown(_model(archive, lang), []))
        allowed = {_line_section_title(lang), blocks_module.unfavorable_section_title(lang)}
        assert _line_section_title(lang) in sections, lang
        for label in ("R1", "R8", "R125"):
            assert label in sections[_line_section_title(lang)], (
                f"{lang}: 该线章节里没有档位 {label}"
            )
            for name, body in sections.items():
                if name not in allowed:
                    assert label not in body, f"{lang}: 档位 {label} 出现在了「{name}」里"


# ------------------------------------------------------------------ 5.3 不利对称公开


def test_slower_native_and_routing_tiers_are_shown_in_the_dimension_and_pointed_at(
    real_archive: dict[str, Any],
) -> None:
    """ "原生更慢"与"路由没被收益抵回"两个方向都公开，且在**两处**出现：本维度与不利章节.

    本维度内是与有利档位同一句话、同一张表对等地给出（三向分界结论就写在表前面）；不利章节里是
    一行指向该节的指针。两处缺一，读者都会以为这条线只有好话。
    """
    archive = _with_line(real_archive, _mixed_line())
    for lang in LANGS:
        model = _model(archive, lang)
        sections = _sections(markdown_renderer.render_markdown(model, []))
        title = _line_section_title(lang)
        unfavorable = blocks_module.unfavorable_section_title(lang)
        assert unfavorable in sections, lang

        slower = "R1、R8" if lang == LANG_ZH else "R1, R8"
        # 本维度：三向结论同一句给出更快、更慢与未决
        assert _template_prefix("blocks.native_line.crossover", lang) in sections[title], lang
        assert slower in sections[title], f"{lang}: 维度内没给出原生更慢的档位"
        assert "R125" in sections[title], f"{lang}: 维度内没给出原生更快的档位"

        # 不利章节：一行指针，既点名原生更慢的档位，也点名路由形态更慢的档位
        assert slower in sections[unfavorable], f"{lang}: 不利章节里没有原生更慢的档位"
        assert t("report.native_line.title", lang) in sections[unfavorable], (
            f"{lang}: 不利章节里没有指向该节的指针"
        )


def test_the_pointer_is_there_even_when_the_main_matrix_has_no_unfavourable_data(
    real_archive: dict[str, Any],
) -> None:
    """主矩阵这一轮没有劣势时，不利章节**照样**给该线的指针.

    这是最容易漏的一格：该节的表为空、只剩一句"所测档位内未出现被测框架处于劣势"，读者会据此收工
    ——而这条线的劣势就在别处。故这里先把主矩阵的不利数据清空，再断言指针仍在。
    """
    archive = _with_line(real_archive, _mixed_line())
    for lang in LANGS:
        model = _model(archive, lang)
        model["unfavorable"] = {**model["unfavorable"], "items": [], "found": False}
        sections = _sections(markdown_renderer.render_markdown(model, []))
        unfavorable = blocks_module.unfavorable_section_title(lang)
        assert t("blocks.unfavourable.empty", lang) in sections[unfavorable], lang
        assert t("report.native_line.title", lang) in sections[unfavorable], (
            f"{lang}: 主矩阵没劣势时该线的指针被省掉了"
        )
        assert "R1" in sections[unfavorable], lang


def test_the_unfavourable_pointer_is_a_single_line_that_does_not_copy_the_numbers(
    real_archive: dict[str, Any],
) -> None:
    """指针只指路、不复制数字（数字留在该线自己的节里，两处各说一遍迟早对不上）."""
    archive = _with_line(real_archive, _mixed_line())
    model = _model(archive, LANG_ZH)
    blocks = blocks_module.build_blocks(model, [], lang=LANG_ZH)

    pointers = [
        block
        for block in blocks
        if block.kind == NOTE and t("report.native_line.title", LANG_ZH) in (block.text or "")
    ]
    assert len(pointers) == 1, f"该只有一行指针，实得 {len(pointers)}"
    for value in _line_medians(model, LANG_ZH):
        assert value not in pointers[0].text, f"指针里复制了该线的数字：{value}"


# ------------------------------------------------------------------ 5.4 版本间对比


def test_two_capable_versions_are_compared_on_this_line(real_archive: dict[str, Any]) -> None:
    """两侧都具备该能力时，对比里含这一维度：逐档逐形态的同运行内倍数变化 + 两侧各自的分界结论.

    比的是**同运行内的相对倍数**（与对比其余部分同一条纪律）：每任务中位数跨运行不可比，故这一节
    里一个中位数都不该有。
    """
    after_line = _mixed_line()
    for row in after_line["by_tier"]:
        row["ratios_vs_pure"][FORM_NATIVE] *= 0.8

    for lang in LANGS:
        result = compare.compare_versions(
            _with_line(real_archive, _mixed_line()),
            _with_line(real_archive, after_line),
            before_label="v0.10.0",
            after_label="v0.11.0",
            lang=lang,
        )
        block = result["native_line"]
        assert block["comparable"] is True, lang
        assert block["before"]["applicable"] is True and block["after"]["applicable"] is True, lang
        row = next(item for item in block["rows"] if item["form"] == FORM_NATIVE)
        assert row["before"] is not None and row["after"] is not None, lang
        assert row["ratio"] == pytest.approx(0.8), lang
        assert block["verdict_same"] is True, lang
        assert block["only_in_one"] == [], lang

        text = compare_renderer.render_markdown(result)
        assert t("compare.section.native_line", lang) in text, lang
        assert t("compare.metric.native_line_ratio", lang) in text, lang
        assert "0.800x" in text, lang


def test_a_side_without_the_capability_is_named_not_applicable_and_gives_no_figures(
    real_archive: dict[str, Any],
) -> None:
    """一侧不具备该能力：该侧明确标为**不适用**，且不产出任何以其缺失充数的数字.

    这是这条要求的关键一格。把"旧版本没有这项能力"与"这一轮测砸了"合成一句"无数据"，或者拿有
    数据的一侧去和缺失比出一个"变化"，读者都会得到一份看着完整、其实是编出来的对比。
    """
    before_line = _mixed_line()
    before_line["status"] = "not_applicable"
    before_line["reason"] = "native_line.reason.capability_missing"
    before_line["reason_params"] = {"missing": "DualArmWorker, NativeTaskWorker"}
    for key in ("by_tier", "crossover", "routing"):
        before_line.pop(key)

    for lang in LANGS:
        result = compare.compare_versions(
            _with_line(real_archive, before_line),
            _with_line(real_archive, _mixed_line()),
            before_label="v0.10.0",
            after_label="v0.11.0",
            lang=lang,
        )
        block = result["native_line"]
        assert block["comparable"] is False, lang
        assert block["rows"] == [], f"{lang}: 一侧不具备能力时不得产出任何变化数"
        assert block["verdict_same"] is None, lang
        assert block["only_in_one"] == [], lang
        assert block["before"]["status"] == "not_applicable", lang
        assert block["before"]["applicable"] is False, lang

        text = compare_renderer.render_markdown(result)
        assert t("compare.section.native_line", lang) in text, lang
        assert (
            t(
                "compare.native_line.side_unusable",
                lang,
                label="v0.10.0",
                status="not_applicable",
                reason=resolve_reason(
                    "native_line.reason.capability_missing",
                    lang,
                    {"missing": "DualArmWorker, NativeTaskWorker"},
                ),
            )
            in text
        ), f"{lang}: 不适用的一侧没有明确说出原因"
        # 变化表整张都不该出现：不适用的一侧给不出倍数
        assert t("compare.metric.native_line_ratio", lang) not in text, lang


def test_a_side_whose_archive_has_no_line_says_so_instead_of_being_omitted(
    real_archive: dict[str, Any],
) -> None:
    """旧留档里压根没有这个键（那一轮还没测这条线）→ 明说"那一轮未测"，而不是整节省掉.

    与"不适用"分属两句不同的话：前者是那次运行没量它，后者是这个版本没有这项能力。
    """
    before = {key: value for key, value in real_archive.items() if key != "native_line"}

    for lang in LANGS:
        result = compare.compare_versions(
            before,
            _with_line(real_archive, _mixed_line()),
            before_label="v0.10.0",
            after_label="v0.11.0",
            lang=lang,
        )
        block = result["native_line"]
        assert block["before"]["present"] is False, lang
        assert block["before"]["status"] == "not_in_run", lang
        assert block["comparable"] is False and block["rows"] == [], lang

        text = compare_renderer.render_markdown(result)
        assert t("compare.section.native_line", lang) in text, lang
        assert t("compare.native_line.side_absent", lang, label="v0.10.0") in text, lang


def test_a_moved_crossover_verdict_is_called_out(real_archive: dict[str, Any]) -> None:
    """两侧的分界结论不同时必须点出来：那是这一节的结论，不能埋在各档的变化里让读者自己拼.

    合成的两侧只差一档的符号（R8 从"原生更慢"变成"未决"），照着倍数看两边一模一样，结论却已经
    不同——正是"只看倍数会漏掉"的那种情形。
    """
    after_line = _mixed_line()
    after_line["crossover"]["native_slower_tiers"] = ["R1"]
    after_line["crossover"]["undecided_tiers"] = ["R8"]

    for lang in LANGS:
        result = compare.compare_versions(
            _with_line(real_archive, _mixed_line()),
            _with_line(real_archive, after_line),
            before_label="v0.10.0",
            after_label="v0.11.0",
            lang=lang,
        )
        assert result["native_line"]["verdict_same"] is False, lang
        text = compare_renderer.render_markdown(result)
        assert t("compare.native_line.verdict_moved", lang) in text, lang
        assert t("compare.native_line.verdict_same", lang) not in text, lang


# ------------------------------------------------------------------ 5.5 三格式两语言


def test_all_three_formats_carry_the_line_in_both_languages(
    real_archive: dict[str, Any], tmp_path: Path
) -> None:
    """deck / Markdown / PDF 三份都在场，两种语言各有一次，且**同一个数字三处一致**.

    数字的期望值取自**留档**（经 :func:`format_seconds`，与报告同一处换算），不是从渲染产物里反抄
    ——反抄的话三份产物一起错也照样绿。
    """
    row = real_archive["native_line"]["by_tier"][0]
    for lang in LANGS:
        model = _model(real_archive, lang)
        outcome = report_renderer.render(model, tmp_path / lang, threshold=0.15)

        markdown = Path(outcome["markdown"]).read_text(encoding="utf-8")
        html = Path(outcome["html"]).read_text(encoding="utf-8")
        pdf = _pdf_text(outcome["pdf"])
        # 章节名在 PDF 里可能被抽成断行，故取它在第一个分隔符之前的短句
        heading = t("report.native_line.title", lang).split(":")[0].split("：")[0]
        assert heading in markdown and heading in html and heading in pdf, f"{lang}: {heading}"

        for form in (FORM_PURE, FORM_NATIVE, FORM_ROUTED):
            value = format_seconds(row["medians_seconds"][form], lang)
            assert value in markdown, f"{lang}: Markdown 里没有 {form} 的数值"
            assert value in html, f"{lang}: deck 里没有 {form} 的数值"
        # PDF 抽文字会把段落折行，故只核数字本身（单位由上面两处与标题证明在场）。
        # 取换算后的数字而非"微秒"分支的写法：中位数落在哪个量级由实测决定，写死单位就会随机器变红。
        number = format_seconds(row["medians_seconds"][FORM_PURE], lang).split()[0]
        assert number in pdf, f"{lang}: PDF 里没有该线的数值"


def test_the_deck_carries_the_conclusion_on_a_page_it_shows(
    real_archive: dict[str, Any], tmp_path: Path
) -> None:
    """该结论落在 deck **实际展示**的页上，且该线的数字也在展示页里——不是被裁掉、也没只留在全文.

    5.5 要的是"deck 含承载该结论的一页（明细放不下时给指针）"，故这里不要求结论与表同页：实测
    分页随留档而变（交叉点那句有没有，决定结论这一段占几行、表落到哪一页），那是版面的事。要断言
    的是结论与数字**都进了读者能翻到的页**——少一样都等于 deck 没把它讲清楚，只能回全文里找。
    """
    title_key = "report.native_line.title"
    for lang in LANGS:
        model = _model(real_archive, lang)
        outcome = report_renderer.render(model, tmp_path / f"deck-{lang}", threshold=0.15)
        deck_html = html_module.unescape(Path(outcome["html"]).read_text(encoding="utf-8"))

        slides = [
            (label, body)
            for label, body in _SLIDE.findall(deck_html)
            if t(title_key, lang) in label
        ]
        assert slides, f"{lang}: deck 里没有该维度的页"
        prefix = _template_prefix("blocks.native_line.crossover", lang)
        pages = [body for _, body in slides if prefix in body]
        assert len(pages) == 1, f"{lang}: 分界结论该只在一页上给出，实得 {len(pages)} 页"
        for value in _line_medians(model, lang):
            assert any(value in body for _, body in slides), (
                f"{lang}: 该线的数值 {value} 一页都没进（装不下时该出指引页，而不是安静地少一截）"
            )


def test_the_deck_points_at_the_full_report_when_the_detail_does_not_fit(
    real_archive: dict[str, Any], tmp_path: Path
) -> None:
    """档位多到一节的页数装不下时，deck 出一页**点名该章节**的指引，而不是把明细悄悄裁掉."""
    archive = _with_line(real_archive, _many_tier_line(12))
    for lang in LANGS:
        model = _model(archive, lang)
        outcome = report_renderer.render(model, tmp_path / f"pointer-{lang}", threshold=0.15)
        deck_html = html_module.unescape(Path(outcome["html"]).read_text(encoding="utf-8"))

        title = _line_section_title(lang)
        suffix = MESSAGES["deck.pointer.label"][lang].split("{section}")[1]
        labels = [label for label, _ in _SLIDE.findall(deck_html)]
        assert any(label.endswith(suffix) and title in label for label in labels), (
            f"{lang}: 该维度明细装不下时没有出指引页"
        )
        assert t("deck.pointer.heading", lang) in deck_html, lang


def test_deleting_one_translation_of_a_message_makes_rendering_fail_and_name_the_key(
    real_archive: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """删掉该维度一个译文键后渲染**失败**并指出缺的是哪个键.

    缺译文若回落或者留白，报告照样生成、只是某一段变了语言——故"渲染失败"与"点名缺哪个键"两条都要
    断言。模型先建好再删键：要验的是**渲染**那一步抛，不是建模那一步。
    """
    archive = _with_line(real_archive, _mixed_line())
    key = "blocks.native_line.crossover"
    model_zh = _model(archive, LANG_ZH)
    monkeypatch.delitem(MESSAGES[key], LANG_ZH)

    with pytest.raises(MissingTranslation) as failure:
        markdown_renderer.render_markdown(model_zh, [])
    assert key in str(failure.value)

    # 另一种语言的译文还在，故同一份留档仍能出英文报告——缺的是这一条译文，不是这条线
    assert markdown_renderer.render_markdown(_model(archive, LANG_EN), [])
