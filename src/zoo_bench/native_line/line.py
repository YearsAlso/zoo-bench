"""该线在**父进程**一侧的编排与结论：把一格格测量拼成"三形态在什么规模上划算".

一个**并发度**跑在自己的子进程里（:mod:`zoo_bench.native_line.child`），进程内把全部 (形态, 规模)
组合逐轮交替采样（ABBA），父进程只负责规划、派生、收结论。粒度取并发度而不是单格，是因为规格
要求「参与对照的两侧来自同一次运行的同一次采样，MUST NOT 由两次运行的结果相减得出」：跨进程的
同形态中位数实测可差约 100 µs（进程间漂移），而小规模上两形态的真实差距只有几微秒——跨进程相减
会把漂移读成"原生更快"，并据此给出假的分界点。同进程逐轮交替后，对照变成**配对差**，共同漂移在
配对内部被消掉。

进程粒度还兼顾了统计隔离：框架按 **Worker 类名**索引统计，类目按 (形态, 规模) 划分（D4），故不同
规模不会互相污染；而**同一规模的不同并发度共用一个类目**，那正是每档一个进程的理由——否则先跑的
那一档会成为后一档的先验，"这一轮学到什么"不再只由这一轮决定（design D6 同一条理由），"逐臂次数
之和等于该组提交数"这条自洽判据也就不可证了。

**结论只由可精确比较的量得出**（spec: native-task-line「路由形态的结论 MUST 由该轮实测得出」）：
分两层，各自有自己的判据与"给不出结论"的形态：

- **收敛**（判据不依赖墙钟）：该轮内那个类目下**逐臂次数**。次数是精确量；探索所致的少数非优势臂
  执行不使结论翻转——收敛的指征恰恰是少数臂的次数与探索率相符，见
  :data:`CONVERGENCE_SHARE_LIMIT_MULTIPLE`。另一臂一次都没走过时**不判为收敛**：那时占多数的
  是框架在均值相等或未知时用的默认臂，不是这一轮作出的选择（实测确实会碰上——首次尝试另一臂
  是均值约 40 次的几何等待，预热轮数因此不是随手取的，见 `matrix.yaml`）。
- **划算**（独立对照）：**同一周期内**同规模同并发度下纯解释与原生两形态每任务耗时之差的
  **配对差**，严格小于者胜（没有容差区间——有了容差就成了"落在区间内就算平"，那正是规格禁止的
  判据）。配对差的四分位区间跨零时这份口径就分辨不出，此时如实报"分不出"（报告键
  ``native_line.reason.below_resolution``），MUST NOT 拿噪声定优势臂。**判据取配对差而不取两侧
  各自的四分位区间**：后者只反映各自窗口内的离散、不含进程间的漂移，实测因此在 21 字节档上给出过
  一个假结论（原生"更快"约 98 µs，而同一进程内的配对差是 -1 µs）。
- **一致 / 不一致**：只需要上面两层里的"划算"那一层——它取的是**独立于路由形态的**两形态对照，
  不取该组自己的逐臂样本（那正是被检验的对象）。故另一臂一次没走过也照样能判；两层都成立而
  结果相左就如实报告相左，MUST NOT 解释成噪声、MUST NOT 省略。

**判定只在最低并发度上做**，理由与 :func:`zoo_bench.runner.check_overhead_across_tiers` 同源：
并发度超过机器实际容量时，每任务墙钟里含排队等待（既有实测：同一适配器并发度 64 与 1 的开销
差 20~35 倍），那时"原生更快"与否测的就不再是执行形态。高并发度的组照常测量、照常留档，只是
不进判定——这一点写进留档。**每一形态的规模单调性判据用各组自己的样本**，与并发度无关。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from typing import Any

from .config import SWITCHES
from .forms import (
    ARM_NATIVE,
    ARM_OF_FORM,
    ARM_PYTHON,
    FORM_NATIVE,
    FORM_PURE,
    FORM_ROUTED,
    FORMS,
    class_name,
)
from .inputs import tier_specs
from .measure import ORDER_RULE, comparison_key
from .reference import TASK_NAME

#: 该线的子进程入口模块（与主矩阵的入口分开，理由见 design D13）.
CHILD_MODULE = "zoo_bench.native_line.child"

#: 判定收敛所需的最少总样本数。样本太少时"少数臂占比很小"是必然的，据此宣布收敛等于自欺.
MIN_JUDGMENT_SAMPLES = 30

#: 探索率之上允许的倍数：少数臂占比超过 ``该倍数 × 探索率`` 就判为**未收敛**。
#:
#: 取 3 的理由是可算的：ε=0.05、总样本约 100 时，纯探索所致的少数臂次数服从
#: ``Binomial(100, 0.05)``（均值 5、标准差约 2.2）；占比 3ε=15% 对应约 4.5 个标准差之上，
#: 即"仅靠探索解释不了"。该判据只用次数与探索率，**与墙钟无关**。
CONVERGENCE_SHARE_LIMIT_MULTIPLE = 3

#: 该线的子进程上限（秒）。一组是微秒级任务的千百次提交，上限只用于避免挂死时整轮无终止。
GROUP_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class NativeLinePlan:
    """该线的测量计划（由 `matrix.yaml` 的声明译出）.

    Attributes:
        register_counts: 规模档位（寄存器个数，即该任务的输入规模）。
        concurrencies: 并发度档位。
        warmup_rounds: 预热轮数，其数据不进入统计。
        measured_rounds: 正式采样轮数。
    """

    register_counts: tuple[int, ...]
    concurrencies: tuple[int, ...]
    warmup_rounds: int
    measured_rounds: int

    @property
    def tiers(self) -> tuple[Any, ...]:
        """档位对象（帧由档位构造，规格的校验在这里发生一次）."""
        return tier_specs(self.register_counts)

    @property
    def judgment_concurrency(self) -> int:
        """做判定用的并发度（最低那档）——理由见模块 docstring."""
        return min(self.concurrencies)


def plan_children(plan: NativeLinePlan, framework: str) -> list[dict[str, Any]]:
    """展开成逐子进程规格：**一个并发度一个**，进程内含全部 (形态, 规模) 组合.

    为什么不按 (形态, 规模, 并发度) 一格格派生：规格要求「参与对照的两侧来自同一次运行的同一次
    采样」，而跨进程的同形态中位数实测可差约 100 µs（进程间漂移）——小规模上它比两形态的真实
    差距还大，跨进程相减就是把漂移读成形态差（见模块 docstring）。进程内逐轮交替后，组间对照
    变成配对差，共同漂移在配对内部被消掉。

    顺序按 `matrix.yaml` 声明的并发度顺序固定，使留档的子进程序可预期（便于逐字段比对两轮留档）。
    """
    return [
        {
            "forms": list(FORMS),
            "registers": [tier.registers for tier in plan.tiers],
            "concurrency": concurrency,
            "warmup_rounds": plan.warmup_rounds,
            "measured_rounds": plan.measured_rounds,
            "framework": framework,
        }
        for concurrency in plan.concurrencies
    ]


def payload_skeleton(framework: str, plan: NativeLinePlan) -> dict[str, Any]:
    """该线留档的空壳（"还没跑"与"失败了"共用同一个形状）.

    **形状恒定**是有用的：报告、用例与 CI 摘要只读一个形状，不必先分辨"这是失败结论还是正常
    结论"再决定读哪些键。空壳里的 ``status`` 是 ``failed``——它只作兜底，正常路径会立刻覆写。
    """
    return {
        "status": "failed",
        "reason": "",
        "reason_params": {},
        "evidence": "",
        "framework": framework,
        "capability": {},
        "extension": {},
        "equivalence": {},
        "switches": {},
        "config_dir": "",
        "plan": _plan_payload(plan, framework),
        "class_map": _class_map(plan),
        "groups": [],
        "pairwise": [],
        "closure": [],
        # 口径声明**取自各格的测量留档**（:func:`_caliber_of`），本模块不另写一份：两处声明
        # 迟早漂移，而漂移的口径比没有口径更坏
        "caliber": None,
        "note": ["native_line.note.scope"],
    }


def failed_payload(
    framework: str,
    plan: NativeLinePlan,
    *,
    reason: str,
    reason_params: Mapping[str, Any] | None = None,
    evidence: str = "",
) -> dict[str, Any]:
    """一个**不产出任何数字**的失败结论（留档形状与跑通的结论一致）.

    为什么需要它：:func:`run_line` 的结论只覆盖"子进程报了什么"；父进程侧的汇总若自己抛异常，
    整轮测量会被它毁掉——那正是 design D12 明确否掉的扩散形态（一条线的失败掩盖其余版本的可信
    数据）。故那一侧把异常收成这个形状。逐格原始数据为空、"派生数字被抑制"也在这里写明，使
    "没测"与"测了没成"在读留档时同样区分得开。
    """
    params = dict(reason_params or {})
    payload = payload_skeleton(framework, plan)
    payload.update(
        status="failed",
        reason=reason,
        reason_params=params,
        evidence=evidence,
        derived={"suppressed": True, "reason": reason, "reason_params": params},
    )
    return payload


def run_line(
    framework: str,
    plan: NativeLinePlan,
    *,
    config: Mapping[str, Any] | None = None,
    run: Callable[..., dict[str, Any]] | None = None,
    timeout: float = GROUP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """跑完该线：先一次前置探查，再逐**并发度**测量，最后在父进程侧汇总.

    Args:
        framework: 被测框架规格（矩阵条目原文；用于核对扩展来源与框架同源）。
        plan: 测量计划。
        config: 摆给每个子进程的开关组合；None 取 :data:`~zoo_bench.native_line.config.SWITCHES`。
        run: 子进程执行器（缺省 :func:`zoo_bench.runner.run_in_child`）；**可注入**，
            使汇总逻辑的用例不必真跑完整条线。
        timeout: 单个子进程的上限（秒）。

    Returns:
        该线的留档：状态、前置核验结论、逐格原始数据、同进程内的配对样本与关闭语义证据，以及
        由它们复算出的对照结论（有任何一格失败时**不产出**对照结论，见 :func:`_aggregate`）。
    """
    from ..runner import run_in_child

    runner = run_in_child if run is None else run
    declared = dict(SWITCHES if config is None else config)
    base = {"framework": framework, "config": declared}

    probe = runner("probe", dict(base), timeout=timeout, module=CHILD_MODULE)
    probe_outcome = _outcome(probe)

    payload = payload_skeleton(framework, plan)
    payload.update(
        status=probe_outcome["status"],
        reason=probe_outcome["reason"],
        reason_params=probe_outcome["reason_params"],
        evidence=probe_outcome["evidence"],
        capability=probe_outcome["capability"],
        extension=probe_outcome["extension"],
        equivalence=probe_outcome["equivalence"],
        switches=probe_outcome["switches"],
        config_dir=probe_outcome["config_dir"],
    )

    if probe_outcome["status"] != "ok":
        # 能力缺席（不适用）与前置核验失败都不出任何数字，且不派生任何测量子进程——
        # 一格也测不成时，派生整批子进程只会把"为什么没数"埋进一串同样的失败里
        payload["derived"] = {"suppressed": True, "reason": "native_line.reason.not_probed"}
        return payload

    for index, spec in enumerate(plan_children(plan, framework)):
        outcome = runner("group", {**base, **spec}, timeout=timeout, module=CHILD_MODULE)
        records, pairs, closure = _child_records(spec, outcome, index)
        payload["groups"].extend(records)
        payload["pairwise"].extend(pairs)
        payload["closure"].append(closure)

    payload["caliber"] = _caliber_of(payload["groups"])
    payload.update(_aggregate(plan, payload["groups"], payload["pairwise"]))
    return payload


def _caliber_of(groups: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """该线的口径声明——取首个**成功格**自己写下的那一份.

    各格的量法出自同一段代码（:func:`zoo_bench.native_line.measure.measure_cycles` 里的字面量），
    故取哪一格都一样；在父进程侧另写一份则会在两处之间漂移。尚无成功格时返回 None
    （没有数字就没有口径要声明）。
    """
    for item in groups:
        measurement = item.get("measurement")
        if isinstance(measurement, Mapping) and isinstance(measurement.get("caliber"), Mapping):
            return dict(measurement["caliber"])
    return None


def _plan_payload(plan: NativeLinePlan, framework: str) -> dict[str, Any]:
    """计划的留档形态（含判定所用的并发度、最少样本数与采样方式——结论的适用条件要在场）."""
    tiers = plan.tiers
    return {
        "framework": framework,
        "forms": list(FORMS),
        # 负载是**真实任务**这件事要可核（spec: native_line「负载是真实任务这一事实可核」）：任务名
        # 与形态、档位一样进留档，报告章节据此说明所测任务是什么，读者才接得上自己的负载。取自
        # :data:`~zoo_bench.native_line.reference.TASK_NAME`——那是本仓对该任务的唯一一处声明。
        "task": TASK_NAME,
        "tiers": [
            {"registers": tier.registers, "frame_bytes": tier.frame_bytes, "label": tier.label}
            for tier in tiers
        ],
        "concurrencies": list(plan.concurrencies),
        "warmup_rounds": plan.warmup_rounds,
        "measured_rounds": plan.measured_rounds,
        # 采样方式的声明进留档：读者要能从留档本身看出"对照是配对样本"，而不是从本仓的代码
        "sampling": {
            "method": "interleaved_same_process",
            "granularity": "concurrency",
            "order_rule": ORDER_RULE,
        },
        "child_count": len(plan.concurrencies),
        "group_count": len(FORMS) * len(tiers) * len(plan.concurrencies),
        "judgment_concurrency": plan.judgment_concurrency,
        # 判定的适用条件进留档：读者要能自己复核"为什么这一档不判"
        "judgment_thresholds": {
            "min_judgment_samples": MIN_JUDGMENT_SAMPLES,
            "convergence_share_multiple": CONVERGENCE_SHARE_LIMIT_MULTIPLE,
        },
    }


def _class_map(plan: NativeLinePlan) -> dict[str, Any]:
    """Worker 类名 ↔ 规模的对应表（design D4 要求进留档）.

    框架按类名索引统计与决策，故"这份数字是哪一类、哪一规模"必须能对上；类数 MUST 等于
    规模档位数（每规模一个类，单类混跑多个规模会让小规模被大规模学到的东西拖走）。
    """
    mapping: dict[str, Any] = {}
    for form, tier in product(FORMS, plan.tiers):
        mapping[class_name(form, tier)] = {
            "form": form,
            "tier": tier.label,
            "registers": tier.registers,
            "frame_bytes": tier.frame_bytes,
        }
    return mapping


def _outcome(result: Mapping[str, Any]) -> dict[str, Any]:
    """子进程返回 → 统一结论（子进程自身出错时也归成一个结论，缺的键给空值）."""
    if result.get("status") != "ok":
        return {
            "status": "failed",
            "reason": "native_line.reason.child_failed",
            "reason_params": {"error": str(result.get("error", ""))[:200]},
            "evidence": str(result.get("stderr", ""))[-2000:],
            "capability": {},
            "extension": {},
            "equivalence": {},
            "switches": {},
            "config_dir": "",
            "measurement": None,
        }

    payload = dict(result.get("payload") or {})
    payload.setdefault("reason", "")
    payload.setdefault("reason_params", {})
    payload.setdefault("evidence", "")
    payload.setdefault("measurement", None)
    payload["child_elapsed_seconds"] = float(result.get("child_elapsed_seconds", 0.0))
    return payload


def _child_records(
    spec: Mapping[str, Any], result: Mapping[str, Any], index: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """一个子进程的结论 → 它内含的各格留档 + 该进程内的配对样本 + 关闭语义证据.

    **子进程整体失败时逐格在场**：它本该量到的每一格都以同一条原因留档。留档的形状要求是"读者按
    (形态, 规模, 并发度) 找数"，那几格去哪了不能靠推理补——少了几格与"这些格没跑"是两件事。

    ``child`` 块（序号、并发度、pid）进每一格：它是"这一格的对照来自哪一次运行"的凭据，同一
    子进程内的各形态共享同一个 ``child``／pid，读者据此可自行核验对照没有跨运行拼接。
    """
    outcome = _outcome(result)
    concurrency = int(spec["concurrency"])
    tiers = tier_specs([int(item) for item in spec["registers"]])
    forms = [str(item) for item in spec["forms"]]
    measurement = outcome.get("measurement")

    if not isinstance(measurement, Mapping):
        child: dict[str, Any] = {
            "index": index,
            "concurrency": concurrency,
            "pid": None,
            "elapsed_seconds": float(outcome.get("child_elapsed_seconds", 0.0)),
        }
        return (
            [
                {
                    "form": form,
                    "registers": tier.registers,
                    "concurrency": concurrency,
                    "status": "failed",
                    "reason": outcome["reason"],
                    "reason_params": dict(outcome["reason_params"]),
                    "evidence": outcome["evidence"],
                    "child": dict(child),
                    "measurement": None,
                }
                for form, tier in product(forms, tiers)
            ],
            [],
            {
                "child": dict(child),
                "status": "failed",
                "reason": outcome["reason"],
                "reason_params": dict(outcome["reason_params"]),
                "evidence": outcome["evidence"],
                "checked_at": None,
            },
        )

    child = {
        "index": index,
        "concurrency": concurrency,
        "pid": measurement.get("pid"),
        "elapsed_seconds": float(outcome.get("child_elapsed_seconds", 0.0)),
        "order_rule": str(measurement.get("order_rule", ORDER_RULE)),
        "cycle_count": int(measurement.get("cycle_count", 0)),
    }
    records = [
        {
            "form": str(item["form"]),
            "registers": int(item["tier"]["registers"]),
            "concurrency": int(item["concurrency"]),
            "status": str(item["status"]),
            "reason": str(item.get("reason", "")),
            "reason_params": dict(item.get("reason_params") or {}),
            "evidence": "",
            "child": dict(child),
            "measurement": dict(item),
        }
        for item in measurement["records"]
    ]
    pairwise = [dict(item) | {"child": dict(child)} for item in measurement.get("pairwise", [])]
    closure = dict(measurement.get("closure") or {})
    closure["child"] = dict(child)
    return records, pairwise, closure


def _aggregate(
    plan: NativeLinePlan,
    groups: Sequence[Mapping[str, Any]],
    pairwise: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """由逐格原始数据复算出该线的结论；有任何一格**失败**就不产出任何对照数字.

    **不产出数字而不是"跳过坏的算出好的"**：失败格与成功格来自不同的环境，用一部分格子得出
    "原生快 X 倍"会把失败这件事藏进一个看起来完整的结论里（design D12）。此时状态为 `failed`，
    逐格原始数据照常留档（那才是排查要看的），并附上被抑制的原因。

    **关闭态不算失败**：原生开关为假时路由形态按框架的关闭语义示人（声明了原生臂而原生未启用，
    框架在构造期显式拒绝，不许静默回退 python 臂），规格要求「开关处于关闭状态本身 MUST NOT
    导致整条线被省略」，故它不抑制对照——那一格没有数字，而"划算"层的对照（纯解释 vs 原生）
    照常由同进程内其余形态的配对样本得出。
    """
    failed = [item for item in groups if item["status"] not in ("ok", "closed")]
    closed = [
        {
            "form": item["form"],
            "registers": item["registers"],
            "concurrency": item["concurrency"],
            "status": item["status"],
            "reason": item["reason"],
            "reason_params": dict(item["reason_params"]),
        }
        for item in groups
        if item["status"] == "closed"
    ]
    if failed:
        return {
            "status": "failed",
            "reason": failed[0]["reason"] or "native_line.reason.group_failed",
            "reason_params": dict(failed[0]["reason_params"]),
            "evidence": str(failed[0]["evidence"]),
            "closed_groups": closed,
            "failed_groups": [
                {
                    "form": item["form"],
                    "registers": item["registers"],
                    "concurrency": item["concurrency"],
                    "reason": item["reason"],
                    "evidence": item["evidence"],
                }
                for item in failed
            ],
            "derived": {
                "suppressed": True,
                "reason": "native_line.reason.derived_needs_all_groups",
                "reason_params": {"failed": len(failed), "total": len(groups)},
            },
        }

    by_tier = _by_tier(plan, groups, pairwise)
    return {
        "status": "ok",
        "reason": "",
        "reason_params": {},
        "evidence": "",
        "closed_groups": closed,
        "failed_groups": [],
        "by_tier": by_tier,
        "crossover": _crossover(plan, by_tier),
        "routing": _routing(plan, groups, by_tier),
    }


def _group_item(
    groups: Sequence[Mapping[str, Any]], form: str, tier: Any, concurrency: int
) -> Mapping[str, Any] | None:
    """取某格的留档条目（含它自己的状态与原因——"这格为什么空"由它自己回答）."""
    for item in groups:
        if (
            item["form"] == form
            and item["concurrency"] == concurrency
            and int(item["registers"]) == tier.registers
        ):
            return item
    return None


def _measurement(
    groups: Sequence[Mapping[str, Any]], form: str, tier: Any, concurrency: int
) -> Mapping[str, Any] | None:
    """取某格的测量数据；该格不是 `ok` 时返回 None（调用方按"缺数"处理，不猜一个默认值）.

    关闭态的格带着原因在场但**没有数字**，故它与失败格一样返回 None。
    """
    item = _group_item(groups, form, tier, concurrency)
    if item is None or item["status"] != "ok":
        return None
    measurement = item.get("measurement")
    return measurement if isinstance(measurement, Mapping) else None


def _by_tier(
    plan: NativeLinePlan,
    groups: Sequence[Mapping[str, Any]],
    pairwise: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """逐 (规模, 并发度) 的三形态数值、相对倍数与**配对差**.

    每一列数值都取自该形态自己的逐轮样本（附分位数，供读者复核离散度）；而**先后与倍数取配对差**
    ——同一周期内两形态各跑一轮，其每任务耗时之差把共同漂移（CPU 频率、进程状态、上游负载）在
    配对内部消掉。两侧各自的分位数含**不同窗口内**的离散，用它比先后在漂移大于真实差距时会读出
    一个假的先后（本仓实测过：21 字节档上"原生更快约 98 µs"，而同进程配对差是 -1 µs）。

    倍数**只在同一次运行、同一并发度内**相除（同机同刻的两两对照），故它对该轮的整体漂移不敏感
    ——与主矩阵"相对比才是证据、绝对耗时不可跨运行比较"是同一条纪律。
    """
    index = {(str(item["tier"]), int(item["concurrency"])): item for item in pairwise}
    rows: list[dict[str, Any]] = []
    for tier in plan.tiers:
        for concurrency in plan.concurrencies:
            medians: dict[str, float | None] = {}
            quartiles: dict[str, Any] = {}
            for form in FORMS:
                measurement = _measurement(groups, form, tier, concurrency)
                summary = measurement["end_to_end_per_task_seconds"] if measurement else None
                medians[form] = float(summary["median"]) if summary else None
                quartiles[form] = (
                    {"p25": float(summary["p25"]), "p75": float(summary["p75"])}
                    if summary
                    else None
                )
            pure = medians[FORM_PURE]
            pair = index.get((tier.label, concurrency), {})
            comparisons = dict(pair.get("comparisons") or {})
            versus_native = comparisons.get(comparison_key(FORM_PURE, FORM_NATIVE))
            rows.append(
                {
                    "tier": tier.label,
                    "registers": tier.registers,
                    "frame_bytes": tier.frame_bytes,
                    "concurrency": concurrency,
                    "medians_seconds": medians,
                    "quartiles_seconds": quartiles,
                    "ratios_vs_pure": {
                        form: (medians[form] / pure)
                        if (medians[form] is not None and pure)
                        else None
                        for form in (FORM_NATIVE, FORM_ROUTED)
                    },
                    # 该 (规模, 并发度) 下的全部两两配对差（纯解释 vs 原生、纯解释 vs 路由、
                    # 原生 vs 路由），符号约定是"前者减后者"；判据用的正是第一对的区间
                    "comparisons": comparisons,
                    "pure_vs_native": versus_native,
                    # 在场的形态：关闭态的路由形态不在其中（"声明了"不等于"量到了"）
                    "forms_measured": list(pair.get("forms") or []),
                    "cycle_count": int(pair.get("cycle_count", 0)),
                    # 该口径分辨不分辨得出两形态的先后（配对差的四分位区间跨零即分辨不出）；
                    # 分辨不出时不可当结论用，故这里的先后取三值：None 是"未决"而不是"不快"
                    "faster_form": (versus_native or {}).get("faster_form"),
                    "is_judgment_concurrency": concurrency == plan.judgment_concurrency,
                }
            )
    return rows


def _crossover(plan: NativeLinePlan, by_tier: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """规模分界结论：**只在判定并发度上**读（理由见模块 docstring）.

    逐档的符号取该档**配对差**给出的先后（:func:`_advantageous_arm` 的同一份判据）：分辨不出的
    档记 ``None``（未决），它既不进"更快"也不进"更慢"，也不参与单调性——把未决当成"不快"会凭空
    造出一个分界点，那比没有分界点更坏。

    "分界"只在实测单调时给出（小规模不划算、大规模划算）。实测非单调时如实报"无分界"并给出
    各档的实际符号——那是规格要求的"与预期不符时如实报告"，不是可以解释掉的噪声。
    """
    judged = [row for row in by_tier if row["is_judgment_concurrency"]]
    flags = [
        None if row["faster_form"] is None else row["faster_form"] == FORM_NATIVE for row in judged
    ]
    decided = [flag for flag in flags if flag is not None]
    monotonic = decided == sorted(decided)
    undecided = [row["tier"] for row, flag in zip(judged, flags, strict=True) if flag is None]

    boundary: dict[str, Any] | None = None
    if monotonic and True in decided and False in decided:
        below = [row for row, flag in zip(judged, flags, strict=True) if flag is False]
        above = [row for row, flag in zip(judged, flags, strict=True) if flag is True]
        boundary = {
            "below_frame_bytes": max(int(row["frame_bytes"]) for row in below),
            "at_or_above_frame_bytes": min(int(row["frame_bytes"]) for row in above),
        }

    notes = ["native_line.note.crossover"]
    if not monotonic:
        notes.append("native_line.note.crossover_non_monotonic")
    if undecided:
        notes.append("native_line.note.crossover_undecided")

    return {
        "judgment_concurrency": plan.judgment_concurrency,
        "native_faster_tiers": [
            row["tier"] for row, flag in zip(judged, flags, strict=True) if flag
        ],
        "native_slower_tiers": [
            row["tier"] for row, flag in zip(judged, flags, strict=True) if flag is False
        ],
        "undecided_tiers": undecided,
        "monotonic": monotonic,
        "boundary": boundary,
        "note": notes,
    }


def _routing(
    plan: NativeLinePlan,
    groups: Sequence[Mapping[str, Any]],
    by_tier: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """路由形态是否按规模分流——判据全是可精确比较的量（逐臂次数、同轮内的配对差）.

    逐规模给出两个各自带结论与原因的块（见模块 docstring 的两层）：

    - ``convergence``：该轮实际路由到了哪一臂（逐臂次数的多数者，精确量、与墙钟无关），以及
      "多数臂是不是一个**决策**的结果"。后者需要另一臂至少被走过一次——一次都没走过时多数臂
      只是框架均值相等时的默认臂，此时如实标为未收敛并给出原因。
    - ``agreement``：多数臂与**独立对照**得出的划算臂是否一致。它不取该组的逐臂样本（那正是
      被检验的对象），故另一臂一次没走过也照样能给结论；给不出的情形有两类，各给各的原因：
      这份口径分辨不出两形态（见 :func:`_advantageous_arm`），或路由形态本身就处于关闭态
      （原生开关为假，框架在构造期拒绝，压根没有路由发生）。
    """
    judged_rows = {row["tier"]: row for row in by_tier if row["is_judgment_concurrency"]}
    per_tier: list[dict[str, Any]] = []
    for tier in plan.tiers:
        row = judged_rows[tier.label]
        counts = _arm_counts(plan, groups, tier)
        arms = counts["arms"]
        # **判定用正式采样窗口内的次数**，不含预热：预热的作用是让路由先收敛，它的决策不是留档
        # 里的"该轮路由结果"；把预热算进去会把收敛瞬态当成本轮的少数臂（实测：140 次里 24 次
        # 预热期的解释臂决策，能把占比从 4% 抬到 20%，于是明明收敛了却判成未收敛）
        native_count = int(arms[ARM_NATIVE])
        python_count = int(arms[ARM_PYTHON])
        total = counts["measured_total"]
        minority = min(native_count, python_count)
        # "另一臂有没有被走过"看**快照**（含预热）：它问的是多数臂是决策还是框架的默认值，
        # 而那取决于框架是否攒到过另一臂的样本，与哪个窗口无关
        untried = ARM_NATIVE if counts["snapshot"][ARM_NATIVE]["n"] == 0 else ARM_PYTHON
        routed_item = _group_item(groups, FORM_ROUTED, tier, plan.judgment_concurrency)
        routed = _measurement(groups, FORM_ROUTED, tier, plan.judgment_concurrency)
        routed_closed = bool(routed_item is not None and routed_item["status"] == "closed")
        exploration = float(routed["policy"]["exploration"]) if routed else None

        advantageous, resolvable = _advantageous_arm(row)
        if routed_closed:
            # 关闭态：根本没有路由发生，故两层都没有可给的结果——如实说"关着"，而不是把它
            # 说成"样本太少"（后者会把读者引向"再多跑几轮就有了"这个错误的下一步）
            assert routed_item is not None  # 由 routed_closed 的定义保证
            closed_params = dict(routed_item["reason_params"])
            convergence = _convergence_result(
                False,
                "native_line.reason.native_switch_off",
                closed_params,
                total=0,
                minority=0,
                exploration=None,
            )
            dominant = None
            agreement: dict[str, Any] = {
                "advantageous_arm": advantageous,
                "resolution_available": resolvable,
                "agrees": None,
                "reason": "native_line.reason.native_switch_off",
                "reason_params": closed_params,
            }
        else:
            convergence = _convergence(
                total, minority, exploration, tier.label, untried_arm=untried
            )
            dominant = (
                None
                if total == 0 or native_count == python_count
                else (ARM_NATIVE if native_count > python_count else ARM_PYTHON)
            )
            agreement = {
                "advantageous_arm": advantageous,
                "resolution_available": resolvable,
                "agrees": (dominant == advantageous)
                if (resolvable and dominant is not None)
                else None,
                "reason": "",
                "reason_params": {},
            }
            if not resolvable:
                agreement["reason"] = "native_line.reason.below_resolution"
                agreement["reason_params"] = _difference_params(tier.label, row)
            elif dominant is None:
                # 防御分支：收敛为真时两臂次数不可能相等（少数臂占比 ≤ 3ε < 50%）
                agreement["reason"] = "native_line.reason.no_strict_advantage"
                agreement["reason_params"] = {"tier": tier.label}

        per_tier.append(
            {
                "tier": tier.label,
                "registers": tier.registers,
                "frame_bytes": tier.frame_bytes,
                "concurrency": plan.judgment_concurrency,
                # 逐臂次数（正式采样窗口）与臂均值（框架快照的累计值，是**执行**耗时的另一口径）
                "counts": arms,
                "n_sum": total,
                "submissions_total": counts["snapshot_total"],
                "measured_total": total,
                "arm_means_seconds": {
                    arm: counts["snapshot"][arm]["mean"] for arm in (ARM_NATIVE, ARM_PYTHON)
                },
                "snapshot_n": {
                    arm: counts["snapshot"][arm]["n"] for arm in (ARM_NATIVE, ARM_PYTHON)
                },
                # 自洽判据的三态原样转述：True/False 是结论，None 是"对本格不适用"（关闭态）
                "n_sum_matches": (
                    routed["policy"]["n_sum_matches_submissions"] if routed else None
                ),
                # 该格自己的状态：关闭态要能从判定块里看出来（否则"零次数"会被读成"没走到"）
                "routed_status": (
                    str(routed_item["status"]) if routed_item is not None else "missing"
                ),
                "routed_reason": (str(routed_item["reason"]) if routed_item is not None else ""),
                "dominant_arm": dominant,
                "minority_samples": minority,
                "exploration": exploration,
                "convergence": convergence,
                "agreement": agreement,
            }
        )

    decided = [item for item in per_tier if item["agreement"]["agrees"] is not None]
    return {
        "per_tier": per_tier,
        "converged_tier_count": sum(1 for item in per_tier if item["convergence"]["converged"]),
        "decided_tier_count": len(decided),
        "agrees_count": sum(1 for item in decided if item["agreement"]["agrees"]),
        "all_agree": bool(decided) and all(item["agreement"]["agrees"] for item in decided),
        "arm_caliber": {
            "timed_quantity": "arm_execution_seconds",
            "unit": "worker",
            "source": "zoo_framework.core.adaptive.get_bandit_policy().snapshot()",
            "note": "native_line.note.arm_caliber",
        },
        "note": "native_line.note.routing",
    }


def _convergence(
    total: int, minority: int, exploration: float | None, tier: str, *, untried_arm: str
) -> dict[str, Any]:
    """该规模上路由是否收敛到一臂（判据只用次数与探索率，与墙钟无关）.

    三种"没有依据"的情形分开给原因：样本太少（不足 30）、另一臂一次都没走过（多数臂只是默认
    臂，不是决策结果）、少数臂占比超过探索率所能解释的上限。
    """
    if exploration is None or total < MIN_JUDGMENT_SAMPLES:
        return _convergence_result(
            False,
            "native_line.reason.insufficient_samples",
            {"tier": tier, "total": total, "required_total": MIN_JUDGMENT_SAMPLES},
            total=total,
            minority=minority,
            exploration=exploration,
        )
    if minority == 0:
        # 探索率为 0 时这是必然的；其余情形说明另一臂没被走到，多数臂即框架的默认臂
        return _convergence_result(
            False,
            "native_line.reason.arm_untried",
            {"tier": tier, "arm": untried_arm, "total": total},
            total=total,
            minority=minority,
            exploration=exploration,
        )

    share = minority / total
    limit = CONVERGENCE_SHARE_LIMIT_MULTIPLE * exploration
    if share > limit:
        return _convergence_result(
            False,
            "native_line.reason.not_converged",
            {
                "tier": tier,
                "share_pct": round(share * 100.0, 1),
                "limit_pct": round(limit * 100.0, 1),
                "exploration_pct": round(exploration * 100.0, 2),
            },
            total=total,
            minority=minority,
            exploration=exploration,
            share=share,
            limit=limit,
        )
    return _convergence_result(
        True,
        "",
        {},
        total=total,
        minority=minority,
        exploration=exploration,
        share=share,
        limit=limit,
    )


def _convergence_result(
    converged: bool,
    reason: str,
    reason_params: dict[str, Any],
    *,
    total: int,
    minority: int,
    exploration: float | None,
    share: float | None = None,
    limit: float | None = None,
) -> dict[str, Any]:
    """收敛判定的留档形态（判据所用的每个量都在场，读者可逐项复核）."""
    return {
        "converged": converged,
        "minority_share": share,
        "limit": limit,
        "total": total,
        "minority_samples": minority,
        "exploration": exploration,
        "reason": reason,
        "reason_params": reason_params,
    }


def _arm_counts(
    plan: NativeLinePlan, groups: Sequence[Mapping[str, Any]], tier: Any
) -> dict[str, Any]:
    """该规模在判定并发度上路由形态的逐臂次数（精确量，不是耗时）.

    **两次数分属两个窗口，不可互相换算**（hence 分开命名，不塞进同一个 dict）：

    - ``arms`` / ``measured_total``：**正式采样窗口内**由逐轮增量求和得出（留档里的
      ``policy.round_arms``，读者可逐项复核）。判定收敛用的是这一份。
    - ``snapshot`` / ``snapshot_total``：框架快照的**累计**值（含预热）。"另一臂有没有被走过"
      用的是这一份——它问的是框架攒到过没有，与窗口无关。

    臂均值只存在快照里（框架按累计样本增量更新，没有"只算某一窗口"的形态）；故它进留档时与
    次数分开命名，使读者不会拿"窗口内的次数"去乘"累计的均值"。
    """
    zero_stats = {arm: {"n": 0.0, "mean": 0.0} for arm in (ARM_NATIVE, ARM_PYTHON)}
    measurement = _measurement(groups, FORM_ROUTED, tier, plan.judgment_concurrency)
    if measurement is None:
        return {
            "arms": dict.fromkeys((ARM_NATIVE, ARM_PYTHON), 0),
            "measured_total": 0,
            "snapshot": zero_stats,
            "snapshot_total": 0,
        }
    rounds = measurement["policy"]["round_arms"]
    arms = {arm: sum(int(item[arm]) for item in rounds) for arm in (ARM_NATIVE, ARM_PYTHON)}
    snapshot = {
        arm: {
            "n": int(float(measurement["policy"]["arms"][arm]["n"])),
            "mean": float(measurement["policy"]["arms"][arm]["mean"]),
        }
        for arm in (ARM_NATIVE, ARM_PYTHON)
    }
    return {
        "arms": arms,
        "measured_total": sum(arms.values()),
        "snapshot": snapshot,
        "snapshot_total": sum(item["n"] for item in snapshot.values()),
    }


def _difference_params(tier_label: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """``below_resolution`` 的原因参数：**配对差本身**的分位数（微秒）.

    给配对差而不是给两侧各自的中位数：判据就是配对差，读者要复核的也正是它；两侧的中位数只说
    "两个窗口各自的中心在哪"，它们的差里含两个窗口之间的漂移，而那正是本条判据要排除的东西。
    """
    comparison = row.get("pure_vs_native")
    params: dict[str, Any] = {"tier": tier_label}
    if isinstance(comparison, Mapping):
        summary = comparison.get("difference_seconds") or {}
        for key in ("p25", "median", "p75"):
            value = summary.get(key)
            params[f"difference_{key}_us"] = None if value is None else round(float(value) * 1e6, 1)
    return params


def _advantageous_arm(row: Mapping[str, Any]) -> tuple[str | None, bool]:
    """该规模上划算的**那一臂**，以及这份口径是否分辨得出来.

    判据是该档的**配对差**（同一周期内纯解释与原生每任务耗时之差）的四分位区间不跨零，先后由
    数据本身给出——没有容差区间（有了容差就成了"落在区间内就算平"，那正是规格禁止的判据）。
    区间跨零时这份口径分辨不出，如实返回 ``(None, False)``，MUST NOT 拿噪声定优势臂。

    判据取配对差而不取两侧各自的四分位区间：后者只反映各自窗口内的离散、不含窗口之间的漂移，
    本仓实测它因此给出过一个假结论（21 字节档上"原生更快约 98 µs"，而配对差是 -1 µs）。

    Returns:
        ``(臂标识, 是否分辨得出)``。臂标识为 :data:`~zoo_bench.native_line.forms.ARM_OF_FORM`
        里的臂名（``pure`` 形态 ↔ ``python`` 臂）；分辨不出时前者为 None。
    """
    comparison = row.get("pure_vs_native")
    if not isinstance(comparison, Mapping) or not comparison.get("resolved"):
        return None, False
    faster = comparison.get("faster_form")
    if faster not in ARM_OF_FORM:
        # 防御分支：解析出的先后必是纯解释或原生之一（这一对只由这两形态构成）
        return None, False
    return ARM_OF_FORM[str(faster)], True
