"""该线的测量：一个并发度下全部 (形态, 规模) 组的量法，以及开跑前的前置核验.

**本模块 MUST 在配置目录就位之后才被 import**（design D13）：它一碰框架，框架的参数类就会把
``./config.json`` 解析成字面量。::func:`~zoo_bench.native_line.config.stage_config` 是那个
"就位"动作，子进程入口 :mod:`zoo_bench.native_line.child` 负责保证顺序——本模块模块级只 import
:mod:`.config`（纯标准库）与同为纯标准库的兄弟模块。

**三种形态在同一个子进程里逐轮交替采样**（规格：「参与对照的两侧来自同一次运行的同一次采样，
MUST NOT 由两次运行的结果相减得出」）。一个并发度一个子进程，每个周期把全部 (形态, 规模) 组合
各跑一轮，周期的顺序逐轮翻转（ABBA，见 :data:`ORDER_RULE`），故两形态的对照是**配对差**：

- 每格一个进程时，同一形态相邻两格的中位数实测可差约 100 µs（进程间漂移），而 21 字节档上两
  形态的真实差距只有约 8 µs——跨进程相减会把漂移读成"原生更快"，并据此给出一个假的分界点。
  配对差把这些共同漂移消掉，剩下的才是形态本身的差。
- 配对差的分位数（不是容差阈值）回答"这份口径分不分得出先后"，见
  :data:`~zoo_bench.native_line.line` 的判据说明。

**该组量的是什么**（口径，进留档）：

- **每任务端到端** = 一轮墙钟 ÷ 该轮提交个数。与主矩阵的 ``end_to_end_per_task_seconds``
  同定义、同一驱动层级，故两条线在这一个量上可比。
- **执行单元自报耗时不可得**：框架不给这条路径提供单元时长（``WorkerResult`` 不带时长），而本仓
  不插桩（design D5），故该线**没有**主矩阵那个"端到端减自报"的框架开销量。这是口径的局限，
  MUST 在报告里声明（:data:`CALIBER_NOTE_KEY`）。
- **计时区间** = 一轮的提交与排空之间。单元构造落在区间内（与主矩阵 zoo 档的驱动形状一致：它也在
  ``submit`` 里构造执行体）；预热轮不计入统计。
- **派发层开销是三种形态的共同加数**：口径量的是"经框架派发层跑完一个任务"，故形态之间的倍数差
  会被这段共同开销压缩（实测：同一台机器上原生比纯解释快约 1.3 倍，而框架自己记录的**执行臂**
  耗时相差数倍）。这是口径的性质，MUST 在报告里声明（:data:`DILUTION_NOTE_KEY`），否则读者会把
  "只有 1.3 倍"读成"原生没多大收益"。

**三条自洽判据**（不给数字比给错数字好）：

- **纯解释与原生形态跑完后统计快照必须是空的**：那是"零分支零锁"的可精确比较的判据，比耗时可靠
  ——它一旦非空，说明该形态并非按框架的关闭语义在跑。三形态共处一进程时快照不可能"到最后仍整个
  为空"（路由形态记录统计是它的预期行为），故这条核验**放在路由形态第一次执行之前**：先跑两个
  不记录统计的形态，此时核验整个快照为空。那也正是它最靠后仍能成立的时点，见
  :func:`_closure_stage`。
- **不记录的形态 MUST NOT 在快照里留下类目**（跑完后逐类核验）：它是上一条在路由形态记录之后的
  延续——路由形态的类目在场，不等于纯解释形态也记了账。
- **路由形态各臂次数之和必须等于该组提交数**：否则"逐臂次数"不是这份测量的完整分解，收敛结论
  就没有依据。该判据只在**预期会记录**时成立（路由形态且解析出的路由开关为真）——开关为假时
  快照本就该空，那不是错误而是证据（design D11 的"关闭态也是要示人的形态"）。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..metrics import summarize
from .config import CONFIG_FILENAME, SWITCHES, config_of, flatten, stage_config, verify_switches
from .driver import WorkerDriver, build_submission_factory
from .equivalence import check_equivalence
from .forms import (
    ARM_NATIVE,
    ARM_PYTHON,
    FORM_NATIVE,
    FORM_PURE,
    FORM_ROUTED,
    FORMS,
    class_name,
    worker_class,
)
from .inputs import TierSpec, tier_specs
from .reference import TASK_NAME

#: 该线报告的分位数。比主矩阵多取 p25/p75：该线的**逐臂对照是否可分辨**按两形态的**配对差**
#: 的四分位区间是否跨零来判（见 :mod:`zoo_bench.native_line.line`），那要用到 p25 与 p75。
PERCENTILES: tuple[int, ...] = (25, 50, 75, 95, 99)

#: 配对差的分位数（配对差的分位数区间就是"这份口径分不分得出来"的判据，故 25/50/75 是必需的）.
PAIR_PERCENTILES: tuple[int, ...] = (25, 50, 75)

#: 判定"划算"所用的两两对照（顺序即留档里的顺序；配对方法见模块 docstring）.
FORM_PAIRS: tuple[tuple[str, str], ...] = (
    (FORM_PURE, FORM_NATIVE),
    (FORM_PURE, FORM_ROUTED),
    (FORM_NATIVE, FORM_ROUTED),
)

#: 逐轮交替采样的顺序规则（进留档：读者据此从逐轮样本复算出配对，故规则本身是可核的）.
ORDER_RULE = "even_cycle_forward_odd_cycle_reverse"

#: 该线口径说明的目录键（渲染侧解析；本模块只把它写进留档）.
CALIBER_NOTE_KEY = "native_line.caliber.unit"

#: 派发层开销会压缩三形态之间的倍数——这条也要在报告里声明，否则读者会把它读成"原生只有这么点收益".
DILUTION_NOTE_KEY = "native_line.note.dilution"

#: 驱动层级标识——与主矩阵 zoo 档同层（design D2），故两条线在这一维度上可比.
DRIVE_LEVEL = "adapters.drive_level.zoo"

#: 被测框架侧契约版本符号所在的模块（把"扩展上报的版本"与"框架支持的版本"对上）.
_CONTRACT_MODULE = "zoo_framework.native.contract"

#: 能力探查的必需符号（标签 → 模块与属性）。标签进留档与报告文案，故用**能力名**而非类名。
REQUIRED_SYMBOLS: dict[str, tuple[str, str]] = {
    "dual_arm_worker": ("zoo_framework.workers", "DualArmWorker"),
    "native_task_worker": ("zoo_framework.native.worker", "NativeTaskWorker"),
    "adaptive_params": ("zoo_framework.params", "AdaptiveParams"),
    "native_params": ("zoo_framework.params", "NativeParams"),
}


# ------------------------------------------------------------------ 前置条件探查


def probe_capability_symbols() -> dict[str, Any]:
    """被测框架是否具备该线所依赖的能力（只看符号在不在，不碰扩展）.

    Returns:
        ``{"present", "checked", "missing"}``；``missing`` 非空即"该版本不具备该能力"
        （不适用），与"具备但跑不起来"（失败）是两件事（design D11）。
    """
    import importlib

    missing: list[str] = []
    for label, (module_name, attribute) in REQUIRED_SYMBOLS.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            missing.append(label)
            continue
        if not hasattr(module, attribute):
            missing.append(label)
    return {"present": not missing, "checked": sorted(REQUIRED_SYMBOLS), "missing": sorted(missing)}


def expected_extension_revision(framework_spec: str) -> str | None:
    """被测框架规格 → 扩展**应当**来自的版本标识（design D9：与框架同 tag）.

    PyPI 目标的约定是 tag ``v<版本>``；git 目标即它自己的引用名。约定取不到时返回 None，
    此时只记录来源、不判否——**判否权交给 CI 的安装步骤**：标签写错的话 pip 自己就装不上，
    该线连跑都跑不到这里。

    Args:
        framework_spec: 矩阵条目原文（如 ``zoo-framework==0.11.0``）。
    """
    from ..targets import MatrixError, parse_target

    try:
        target = parse_target(framework_spec)
    except MatrixError:
        return None
    if target.kind == "pypi":
        return f"v{target.version}" if target.version else None
    return target.ref


def extension_source() -> dict[str, Any]:
    """扩展的安装来源（读 dist-info 记录的 ``direct_url.json``，不是自述的声明）."""
    import importlib.metadata
    import json

    package = "zoo-framework-native"
    try:
        distribution = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        return {"package": package, "installed": False}

    source: dict[str, Any] = {
        "package": package,
        "installed": True,
        "version": distribution.version,
    }
    raw = distribution.read_text("direct_url.json")
    if not raw:
        return source
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return source
    vcs = payload.get("vcs_info") or {}
    return source | {
        "url": payload.get("url"),
        "subdirectory": payload.get("subdirectory"),
        "requested_revision": vcs.get("requested_revision"),
        "commit_id": vcs.get("commit_id"),
    }


def probe_extension(framework_spec: str | None) -> dict[str, Any]:
    """扩展层的核验：可加载与握手 → 任务在场 → 来源可追溯 → 来源与被测框架对应.

    Returns:
        ``{"ok", "reason", "reason_params", "evidence", "descriptor"}``；``ok`` 为 False 时
        ``reason`` 是进报告的目录键，``evidence`` 是中文证据（供排查，报告不渲染它）。
    """
    import importlib

    from zoo_framework.native import get_native_adapter
    from zoo_framework.native.contract import NativeInvalidInput

    adapter = get_native_adapter()
    try:
        adapter.ensure_ready()
    except NativeInvalidInput as exc:
        # 未安装 / 契约版本不匹配 / 声明了不支持的能力——适配器已给出含双方版本的说明
        return {
            "ok": False,
            "reason": "native_line.reason.extension_unavailable",
            "reason_params": {"detail": str(exc)},
            "evidence": str(exc),
            "descriptor": {},
        }

    module = importlib.import_module(adapter.extension_module)
    contract_module = importlib.import_module(_CONTRACT_MODULE)
    descriptor: dict[str, Any] = {
        "module": adapter.extension_module,
        "contract_version": int(module.contract_version()),
        "framework_contract_version": int(contract_module.CONTRACT_VERSION),
        "capabilities": [str(item) for item in module.capabilities()],
        "tasks": sorted(str(name) for name in module.tasks()),
    }
    if TASK_NAME not in descriptor["tasks"]:
        return {
            "ok": False,
            "reason": "native_line.reason.task_missing",
            "reason_params": {"task": TASK_NAME, "tasks": ", ".join(descriptor["tasks"])},
            "evidence": f"扩展已注册的任务里没有 {TASK_NAME!r}：{descriptor['tasks']}",
            "descriptor": descriptor,
        }

    source = extension_source()
    expected = expected_extension_revision(framework_spec) if framework_spec else None
    descriptor["source"] = source
    descriptor["expected_revision"] = expected

    if not source.get("url") or not source.get("requested_revision"):
        return {
            "ok": False,
            "reason": "native_line.reason.source_untraceable",
            "reason_params": {"module": str(descriptor["module"])},
            "evidence": (
                "读不到扩展的安装来源（dist-info 的 direct_url.json 缺失或字段不全）；"
                "来源不可追溯时不出数——读者无法判断这份数字是用哪一份扩展得出的"
            ),
            "descriptor": descriptor,
        }
    if expected is not None and source["requested_revision"] != expected:
        return {
            "ok": False,
            "reason": "native_line.reason.source_mismatch",
            "reason_params": {"expected": expected, "actual": str(source["requested_revision"])},
            "evidence": (
                f"扩展取自 {source['requested_revision']!r}，而被测框架是 {expected!r}——"
                "不同源的扩展会让「原生更快」这句话指向另一个版本的原生实现"
            ),
            "descriptor": descriptor,
        }

    return {"ok": True, "reason": "", "reason_params": {}, "evidence": "", "descriptor": descriptor}


def run_gate() -> dict[str, Any]:
    """对**真扩展**跑一遍等价性门禁（该线给出任何数字之前的前置条件）."""
    from zoo_framework.native import get_native_adapter

    adapter = get_native_adapter()
    return check_equivalence(lambda frame: bytes(adapter.execute(TASK_NAME, frame)))


def probe_capability(spec: Mapping[str, Any]) -> dict[str, Any]:
    """该线开跑前的全部前置条件：能力 → 扩展 → 来源 → 等价性门禁.

    **顺序是判据的一部分**（design D11）：能力缺席是"不适用"（该版本没有这项能力），扩展不可
    加载 / 来源不对 / 不等价是"失败"。三者混为一谈会让旧版本持续报假红。

    能力探查**先于**开关核验：不具备该能力的版本没有这套参数类，开关核验会先抛 ImportError，
    那样"不适用"就变成了"失败"。

    Args:
        spec: 规格（含 ``framework`` 与 ``config``）。

    Returns:
        ``status`` 为 ``ok`` / ``not_applicable`` / ``failed`` 的结论，含 ``reason`` +
        ``reason_params``（进报告的目录键与参数）、``evidence``（中文证据）、
        ``capability`` / ``extension`` / ``equivalence`` / ``switches``。
    """
    capability = probe_capability_symbols()
    if not capability["present"]:
        return {
            "status": "not_applicable",
            "reason": "native_line.reason.capability_missing",
            "reason_params": {"missing": ", ".join(capability["missing"])},
            "evidence": f"框架里没有这些符号：{capability['missing']}",
            "capability": capability,
        }

    switches = verify_switches(config_of(spec))

    extension = probe_extension(spec.get("framework"))
    if not extension["ok"]:
        return {
            "status": "failed",
            "reason": extension["reason"],
            "reason_params": extension["reason_params"],
            "evidence": extension["evidence"],
            "capability": capability,
            "extension": extension["descriptor"],
            "switches": switches,
        }

    outcome = run_gate()
    if not outcome["ok"]:
        return {
            "status": "failed",
            "reason": "native_line.reason.equivalence_failed",
            "reason_params": {
                "count": len(outcome["mismatches"]),
                "keys": ", ".join(str(item["key"]) for item in outcome["mismatches"]),
            },
            "evidence": f"不等价的输入：{[item['key'] for item in outcome['mismatches']]}",
            "capability": capability,
            "extension": extension["descriptor"],
            "equivalence": outcome,
            "switches": switches,
        }

    return {
        "status": "ok",
        "reason": "",
        "reason_params": {},
        "evidence": "",
        "capability": capability,
        "extension": extension["descriptor"],
        "equivalence": outcome,
        "switches": switches,
    }


# ------------------------------------------------------------------ 一个子进程的测量


def tiers_of(spec: Mapping[str, Any]) -> tuple[TierSpec, ...]:
    """由规格里的寄存器个数取档位（经声明校验，故档位不在源码里硬编码）.

    ``registers`` 收单个数或列表两种写法：单个数用于"只量一格"的排查用法，列表是该线的正常形态
    （一个并发度一个子进程，一次量完全部规模档位）。
    """
    raw = spec["registers"]
    counts = [int(item) for item in raw] if isinstance(raw, (list, tuple)) else [int(raw)]
    return tier_specs(counts)


def declared_forms(spec: Mapping[str, Any]) -> tuple[str, ...]:
    """规格里声明的形态（保序去重；缺省即全部三形态）.

    Raises:
        ValueError: 声明了未知形态，或一个形态都没声明（后者量不出任何东西）。
    """
    raw = spec.get("forms", FORMS)
    forms = tuple(dict.fromkeys(str(item) for item in raw))
    unknown = [form for form in forms if form not in FORMS]
    if unknown:
        raise ValueError(f"未知形态 {unknown}；可用：{list(FORMS)}")
    if not forms:
        raise ValueError("至少声明一个形态，否则这个子进程量不出任何东西")
    return forms


def comparison_key(first: str, second: str) -> str:
    """两形态对照的留档键（形如 ``pure-vs-native``）."""
    return f"{first}-vs-{second}"


def policy_snapshot() -> dict[str, Any]:
    """框架的公开统计快照（逐类目 → 逐臂 ``{n, mean}``，design D5 的零插桩来源）."""
    from zoo_framework.core.adaptive import get_bandit_policy

    return get_bandit_policy().snapshot()


def arms_of(snapshot: Mapping[str, Any], name: str) -> dict[str, Any]:
    """某类目的两臂统计.

    缺该类目时给**两臂零样本**而不是空 dict：空 dict 会把"这个类目没执行过"与"这类目不存在"
    混成同一个形状，而下游要分别处理（前者是纯解释形态的正确结果，后者是故障）。
    """
    zero = {"n": 0.0, "mean": 0.0}
    entry = snapshot.get(name)
    if not isinstance(entry, Mapping):
        return {arm: dict(zero) for arm in (ARM_NATIVE, ARM_PYTHON)}
    filled = {arm: dict(zero) for arm in (ARM_NATIVE, ARM_PYTHON)}
    filled.update({str(arm): dict(stats) for arm, stats in entry.items()})
    return filled


def base_class_label(form: str) -> str:
    """该形态所用框架基类的坐标（进留档，使读者能回答"驱动的是框架的哪个执行体"）."""
    if form == FORM_NATIVE:
        from zoo_framework.native.worker import NativeTaskWorker

        cls = NativeTaskWorker
    else:
        from zoo_framework.workers import DualArmWorker

        cls = DualArmWorker
    return f"{cls.__module__}:{cls.__qualname__}"


def measure_cycles(spec: Mapping[str, Any]) -> dict[str, Any]:
    """量一个并发度下的全部 (形态, 规模) 组合：三形态逐轮交替，故两两对照是同轮配对.

    Args:
        spec: 规格：``forms`` / ``registers``（规模档位清单）/ ``concurrency`` /
            ``warmup_rounds`` / ``measured_rounds`` / ``config``（开关组合）。

    Returns:
        ``{"concurrency", "forms", "registers", "order_rule", "cycle_count", "closure",
        "records", "pairwise", "pid"}``：``records`` 逐 (形态, 规模) 一条（留档里的"组"），
        ``pairwise`` 逐规模一条（同轮两两形态的配对差与其分位数），``closure`` 是关闭语义的核验证据。

    Raises:
        ValueError: 形态或档位不合法、并发度或采样轮数 <1。
        RuntimeError: 开关未生效、驱动面世代未适配、统计自洽判据不过、关闭语义核验不过。
        BaseException: 任一轮里有单元执行失败时，其首个异常（失败轮的时长是假样本）。
    """
    forms = declared_forms(spec)
    tiers = tiers_of(spec)
    concurrency = int(spec["concurrency"])
    warmup_rounds = int(spec.get("warmup_rounds", 0))
    measured_rounds = int(spec["measured_rounds"])
    if concurrency < 1 or measured_rounds < 1:
        raise ValueError("并发度与正式采样轮数都必须 ≥1，否则这个子进程量不出任何东西")

    # 开关核验必须在**任何驱动之前**：配置没生效时路由形态会静默退化成纯解释形态，
    # 那正是"打开开关重跑一遍"最可能的静默失败形态
    switches = verify_switches(config_of(spec))

    by_label = {tier.label: tier for tier in tiers}

    def name_of(form: str, label: str) -> str:
        """该 (形态, 规模标签) 的 Worker 类名（框架按类名索引统计与决策，故它是查快照的键）."""
        return class_name(form, by_label[label])

    # 键用 (形态, 规模标签)：标签是档位在留档与报告里的名字，TierSpec 只在构造执行体时要
    keys = tuple((form, tier.label) for tier in tiers for form in forms)
    recording_keys = tuple(key for key in keys if key[0] == FORM_ROUTED)
    quiet_keys = tuple(key for key in keys if key[0] != FORM_ROUTED)
    # 原生开关为假时**不去跑一次必然失败的构造**：框架对"声明了原生臂而原生未启用"是构造期显式
    # 拒绝（不许静默回退 python 臂），故该形态在这里就按**关闭态**示人——它仍要在留档里在场
    # （规格：「开关处于关闭状态本身 MUST NOT 导致整条线被省略」），但不是一组失败的数字。
    # 另一个开关（adaptive）为假时无需特殊处理：框架走关闭语义（零分支零锁），照跑、照留档，
    # 跑完快照为空正是证据。
    routed_enabled = bool(switches["native:enabled"])
    live_keys = keys if routed_enabled else quiet_keys
    live_recording_keys = recording_keys if routed_enabled else ()
    # 不记录的形态的预热轮：它们没有要收敛的东西，预热只为**关闭语义的核验**提供提交量，
    # 故至少一轮——否则"跑完快照为空"是在"还没跑过任何东西"上成立的空结论
    quiet_warmup = max(warmup_rounds, 1)

    driver = WorkerDriver(workers=concurrency)
    factories = {
        (form, label): build_submission_factory(form, by_label[label], driver)
        for form, label in live_keys
    }
    samples: dict[tuple[str, str], list[dict[str, Any]]] = {key: [] for key in live_keys}
    arm_deltas: dict[tuple[str, str], list[dict[str, Any]]] = {
        key: [] for key in live_recording_keys
    }
    pairs: dict[str, list[dict[str, Any]]] = {tier.label: [] for tier in tiers}
    try:
        # 阶段 0：先跑不记录统计的形态，并在路由形态第一次执行**之前**核验整个快照为空
        closure = _closure_stage(
            driver,
            factories,
            quiet_keys,
            quiet_warmup,
            concurrency,
            checked_at=("before_first_routing_submission" if live_recording_keys else "end_of_run"),
        )
        # 路由形态的预热：它的决策要有收敛过程，预热数据不进正式样本（收敛瞬态不是本轮的路由结果）
        for _ in range(warmup_rounds):
            for key in live_recording_keys:
                _measuring_round(driver, factories[key], concurrency)

        for cycle in range(measured_rounds):
            order = live_keys if cycle % 2 == 0 else tuple(reversed(live_keys))
            for key in order:
                samples[key].append(
                    _measuring_round(
                        driver,
                        factories[key],
                        concurrency,
                        cycle=cycle,
                        name=name_of(*key) if key in live_recording_keys else None,
                        deltas=arm_deltas.get(key),
                    )
                )
            # 本周期的配对样本：同周期内各形态各跑了一轮，两两之差即是把共同漂移消掉的对照
            for tier in tiers:
                pairs[tier.label].append(_pair_of(tier.label, cycle, order, samples, concurrency))
    finally:
        driver.teardown()

    snapshot = policy_snapshot()
    _snapshot_consistency(
        [(key[0], name_of(*key)) for key in quiet_keys],
        [
            (name_of(*key), (warmup_rounds + measured_rounds) * concurrency)
            for key in live_recording_keys
        ],
        snapshot,
        switches=switches,
    )

    def build_record(form: str, tier: TierSpec) -> dict[str, Any]:
        """该 (形态, 规模) 格的留档（逐轮样本、逐臂统计、口径与采样方式；关闭态则只给状态）。"""
        key = (form, tier.label)
        name = name_of(form, tier.label)
        quiet = form != FORM_ROUTED
        declared_warmup = quiet_warmup if quiet else warmup_rounds
        rounds = samples.get(key, [])
        per_task = [float(item["wall_seconds"]) / concurrency for item in rounds]
        arms = arms_of(snapshot, name)
        n_sum = int(sum(float(entry["n"]) for entry in arms.values()))
        submissions_total = (declared_warmup + measured_rounds) * concurrency
        tier_block = {
            "registers": tier.registers,
            "frame_bytes": tier.frame_bytes,
            "label": tier.label,
        }
        if not routed_enabled and not quiet:
            # 关闭态：没有数字，但有状态与原因（报告按目录键渲染，读者据此知道这一格为什么空）
            return {
                "form": form,
                "class_name": name,
                "worker_class": _class_coordinate(form, tier),
                "base_class": base_class_label(form),
                "native_task_declared": True,
                "task": TASK_NAME,
                "tier": tier_block,
                "concurrency": concurrency,
                "status": "closed",
                "reason": "native_line.reason.native_switch_off",
                "reason_params": {
                    "native_enabled": bool(switches["native:enabled"]),
                    "adaptive_enabled": bool(switches["adaptive:enabled"]),
                },
                "switches": switches,
                "config_dir": str(spec.get("config_dir", "")),
                "pid": os.getpid(),
            }
        return {
            "form": form,
            "class_name": name,
            "worker_class": _class_coordinate(form, tier),
            "base_class": base_class_label(form),
            "native_task_declared": form == FORM_ROUTED,
            "task": TASK_NAME,
            "tier": tier_block,
            "concurrency": concurrency,
            "status": "ok",
            "reason": "",
            "reason_params": {},
            "warmup_rounds": declared_warmup,
            "measured_rounds": measured_rounds,
            "submissions_total": submissions_total,
            "submissions_measured": measured_rounds * concurrency,
            "rounds": rounds,
            "end_to_end_per_task_seconds": summarize(per_task, PERCENTILES),
            "round_wall_seconds": summarize([float(item["wall_seconds"]) for item in rounds]),
            "throughput_per_second": concurrency / float(summarize(per_task)["median"]),
            "policy": {
                "snapshot": snapshot,
                "arms": arms,
                "arms_present": sorted(snapshot.get(name, {})),
                "n_sum": n_sum,
                "submissions_total": submissions_total,
                # 三态：True/False 是判据的结论，None 是"这条判据对本格不适用"——不记录的形态
                # 本就**不该**记账，那里的 n_sum ≠ 提交数不是错误。用 False 表示"不适用"会让
                # 读者把正常形态读成故障（这也正是判据只在预期记账时才成立的那条的要求）
                "n_sum_matches_submissions": (
                    (n_sum == submissions_total)
                    if ((not quiet) and bool(switches["adaptive:enabled"]))
                    else None
                ),
                "recording_expected": (not quiet) and bool(switches["adaptive:enabled"]),
                "exploration": switches["adaptive:exploration"],
                "round_arms": arm_deltas.get(key, []),
            },
            "switches": switches,
            "generation": driver.generation,
            # 采样方式进留档：它是"这份数字怎么来的"的一部分（同进程逐轮交替，故与同进程的其余
            # 形态是配对样本，而不是两次运行的结果相减）
            "sampling": {
                "method": "interleaved_same_process",
                "order_rule": ORDER_RULE,
                "cycle_count": measured_rounds,
                "forms_in_process": [key[0] for key in live_keys],
            },
            "caliber": {
                "timed_quantity": "end_to_end_per_task_seconds",
                "unit": "worker",
                "drive_level": DRIVE_LEVEL,
                "unit_self_report": False,
                "sampling": "interleaved_same_process",
                "percentiles": list(PERCENTILES),
                "notes": [CALIBER_NOTE_KEY, DILUTION_NOTE_KEY],
            },
            "config_dir": str(spec.get("config_dir", "")),
            "pid": os.getpid(),
        }

    return {
        "concurrency": concurrency,
        "forms": list(forms),
        "measured_forms": [form for form in forms if routed_enabled or form != FORM_ROUTED],
        "registers": [tier.registers for tier in tiers],
        "order_rule": ORDER_RULE,
        "cycle_count": measured_rounds,
        "quiet_warmup_rounds": quiet_warmup,
        "closure": closure,
        "records": [build_record(form, by_label[label]) for form, label in keys],
        "pairwise": _pairwise_of(tiers, pairs, concurrency),
        "pid": os.getpid(),
    }


def _measuring_round(
    driver: WorkerDriver,
    factory: Callable[[int], Any],
    concurrency: int,
    *,
    cycle: int | None = None,
    name: str | None = None,
    deltas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """一轮：提交 ``concurrency`` 个单元、排空，返回该轮的墙钟样本.

    ``name`` 给出时（路由形态）在计时区间**之外**读两次快照，把该轮的逐臂增量记进 ``deltas``：
    快照读取是本仓对框架公开接口的读取，不该计入被测耗时。逐轮增量进留档是因为**收敛过程本身
    是证据**——报告给出逐臂次数与探索率后，读者能复核"探索所致的少数非优势臂执行不使结论翻转"。
    """
    before = arms_of(policy_snapshot(), name) if name is not None else None
    started = time.perf_counter()
    driver.submit_many(factory, concurrency)
    driver.drain()
    wall = time.perf_counter() - started
    if before is not None and deltas is not None:
        after = arms_of(policy_snapshot(), name)
        deltas.append(
            {
                "round": cycle,
                **{
                    arm: int(after[arm]["n"] - before[arm]["n"]) for arm in (ARM_NATIVE, ARM_PYTHON)
                },
            }
        )
    return {"round": cycle, "batch": concurrency, "wall_seconds": wall}


def _pair_of(
    tier_label: str,
    cycle: int,
    order: Sequence[tuple[str, str]],
    samples: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    concurrency: int,
) -> dict[str, Any]:
    """一个周期里某规模的配对样本（同周期的顺序一并留档，读者据此复核配对本身）."""
    at_tier = [(form, label) for form, label in order if label == tier_label]
    return {
        "cycle": cycle,
        "order": [form for form, _ in at_tier],
        "per_task_seconds": {
            form: float(samples[(form, tier_label)][-1]["wall_seconds"]) / concurrency
            for form, _ in at_tier
        },
    }


def _pairwise_of(
    tiers: Sequence[TierSpec],
    pairs: Mapping[str, Sequence[Mapping[str, Any]]],
    concurrency: int,
) -> list[dict[str, Any]]:
    """逐规模的配对差：同周期内两形态每任务耗时之差（"同一次采样"是结构性的，不是约定）.

    ``difference_seconds`` 的符号约定是**前者减后者**；其 ``p25``/``p75`` 不跨零即这份口径分辨得
    出先后（``resolved``），此时的先后由数据本身给出，不是"差距小于某阈值就算平"的约定
    （规格禁的正是后者）。

    **逐周期的原始配对样本一并留档**（``cycles``：该周期的形态顺序与该周期各形态的每任务耗时）：
    判据是"这份配对差"算出来的，读者要能自己把它算回去——顺序规则（``order_rule``）与逐轮样本
    分处两处，只留规则的话"顺序真的逐轮翻转了吗"就只能靠信，而那是判据的前提。
    """
    out: list[dict[str, Any]] = []
    for tier in tiers:
        items = pairs[tier.label]
        # 实际在场的形态：关闭态的路由形态没有样本，故"声明了"不等于"量到了"
        present = {form for item in items for form in item["per_task_seconds"]}
        comparisons: dict[str, Any] = {}
        for first, second in FORM_PAIRS:
            if first not in present or second not in present:
                continue
            differences = [
                float(item["per_task_seconds"][first]) - float(item["per_task_seconds"][second])
                for item in items
            ]
            summary = summarize(differences, PAIR_PERCENTILES)
            low, high = float(summary["p25"]), float(summary["p75"])
            resolved = low > 0.0 or high < 0.0
            comparisons[comparison_key(first, second)] = {
                "first": first,
                "second": second,
                "difference_seconds": summary,
                "resolved": resolved,
                "faster_form": (second if low > 0.0 else first) if resolved else None,
            }
        out.append(
            {
                "tier": tier.label,
                "registers": tier.registers,
                "frame_bytes": tier.frame_bytes,
                "concurrency": concurrency,
                "cycle_count": len(items),
                "forms": sorted(present),
                # 逐周期的原始配对样本：配对差由它们算出，故它们 MUST 在场（见 docstring）
                "cycles": [
                    {
                        "cycle": int(item["cycle"]),
                        "order": [str(form) for form in item["order"]],
                        "per_task_seconds": {
                            str(form): float(value)
                            for form, value in dict(item["per_task_seconds"]).items()
                        },
                    }
                    for item in items
                ],
                "comparisons": comparisons,
            }
        )
    return out


def _closure_stage(
    driver: WorkerDriver,
    factories: Mapping[tuple[str, str], Callable[[int], Any]],
    keys: Sequence[tuple[str, str]],
    rounds: int,
    concurrency: int,
    *,
    checked_at: str,
) -> dict[str, Any]:
    """关闭语义的核验：先跑**不记录统计**的形态，再核验整个统计快照为空.

    时点是有讲究的：路由形态一执行就会记录统计（那是它的预期行为），故这条核验 MUST 发生在路由
    形态第一次执行**之前**——那正是它最靠后仍能成立的时点（``checked_at`` 记下这个时点：路由形态
    在场时是"路由第一次提交之前"，它自己也处于关闭态时则是"跑完之后"，那时快照本就该整个为空）。
    核验的覆盖面因此不是"一轮"：默认预热 100 轮 × 并发度 × 不记录形态数 的提交都在核验之内。

    规格里只声明了路由形态时这一档不记录统计的形态一个都没有，本条无从核验，如实记
    ``available: False``——不给一个恒真的空结论。

    Args:
        driver: 驱动面（池大小即该组的并发度）。
        factories: 逐 (形态, 规模) 的提交工厂。
        keys: 参与核验的 (形态, 规模) 组合（不记录统计的那些）。
        rounds: 核验前的提交轮数（每轮 ``concurrency`` 个提交）。
        concurrency: 并发度。
        checked_at: 核验时点的标识（进留档）。

    Returns:
        ``{"available", "checked_at", "rounds", "submissions", "snapshot_empty", "forms"}``。

    Raises:
        RuntimeError: 快照非空——纯解释形态的"零分支零锁"不成立，或原生形态额外记了账。
    """
    if not keys:
        return {
            "available": False,
            "checked_at": checked_at,
            "rounds": 0,
            "submissions": 0,
            "snapshot_empty": None,
            "forms": [],
        }

    for _ in range(rounds):
        for key in keys:
            _measuring_round(driver, factories[key], concurrency)

    snapshot = policy_snapshot()
    forms = sorted({key[0] for key in keys})
    if snapshot:
        raise RuntimeError(
            f"关闭语义核验不过：{'/'.join(forms)} 形态在路由形态执行之前跑了 "
            f"{rounds * concurrency * len(keys)} 次提交，统计快照却非空：{snapshot}；"
            "这两个形态按框架的关闭语义应当零记账，快照非空说明它们并非按该语义在跑，"
            "此时它们的耗时不可与其余形态比较"
        )
    return {
        "available": True,
        "checked_at": checked_at,
        "rounds": rounds,
        "submissions": rounds * concurrency * len(keys),
        "snapshot_empty": True,
        "forms": forms,
    }


def _class_coordinate(form: str, tier: TierSpec) -> str:
    """该格的 Worker 类坐标（``模块:类名``，进留档）."""
    cls = worker_class(form, tier)
    return f"{cls.__module__}:{cls.__qualname__}"


def _snapshot_consistency(
    quiet_classes: Sequence[tuple[str, str]],
    recording_expectations: Sequence[tuple[str, int]],
    snapshot: Mapping[str, Any],
    switches: Mapping[str, Any],
) -> None:
    """跑完后的统计自洽判据（见模块 docstring 的后两条）；不成立时**不出数**.

    关闭语义那条（快照整个为空）在上面的 :func:`_closure_stage` 里、路由形态执行**之前**核过；
    这里补的是它之后的形态：路由形态的类目在场，不等于纯解释形态也记了账，故逐类核验"不记录的
    形态 MUST NOT 出现在快照里"。

    Args:
        quiet_classes: 不记录统计的形态与它们的类名（``(形态, 类名)``）——留档里要说清是哪个形态
            记了账，故形态名与类名都带着。
        recording_expectations: 记录统计的形态的 ``(类名, 该组提交数)``。
        snapshot: 跑完后的统计快照。
        switches: 解析后的开关组合。

    Raises:
        RuntimeError: 不记录的形态在快照里留了类目；或路由形态两臂次数之和与提交数不符；
            或路由开关为假时快照非空（该组并非按这份配置在跑，design D13 的故障形态）。
    """
    for form, name in quiet_classes:
        if name in snapshot:
            raise RuntimeError(
                f"{form} 形态（类目 {name}）跑完后 MUST NOT 出现在统计快照里"
                f"（框架的关闭语义：零分支零锁），实得 {snapshot[name]}；"
                "它一旦记账，这一格的耗时就不再是「零开销」的对照组"
            )

    recording_expected = bool(switches["adaptive:enabled"])
    for name, expected in recording_expectations:
        n_sum = int(sum(float(entry["n"]) for entry in arms_of(snapshot, name).values()))
        if recording_expected and n_sum != int(expected):
            raise RuntimeError(
                f"路由形态的逐臂次数之和 {n_sum} ≠ 该组提交数 {expected}"
                f"（类目 {name} 的条目：{snapshot.get(name)}）；"
                "逐臂次数不是这份测量的完整分解时，收敛结论没有依据"
            )
        if not recording_expected and snapshot:
            # 路由开关为假时框架走的是关闭语义（零分支零锁、不记录）。快照非空说明"这一格是
            # 在另一套开关下跑的"——那正是配置时序出错的形态，且它看起来一切正常
            raise RuntimeError(
                f"路由形态的路由开关解析为假，统计快照却非空：{snapshot}；"
                "说明该组并非按这份配置在跑（design D13 的故障形态）"
            )


__all__ = [
    "CALIBER_NOTE_KEY",
    "CONFIG_FILENAME",
    "DILUTION_NOTE_KEY",
    "DRIVE_LEVEL",
    "FORM_PAIRS",
    "ORDER_RULE",
    "PAIR_PERCENTILES",
    "PERCENTILES",
    "REQUIRED_SYMBOLS",
    "SWITCHES",
    "arms_of",
    "base_class_label",
    "comparison_key",
    "config_of",
    "declared_forms",
    "expected_extension_revision",
    "extension_source",
    "flatten",
    "measure_cycles",
    "policy_snapshot",
    "probe_capability",
    "probe_capability_symbols",
    "probe_extension",
    "run_gate",
    "stage_config",
    "tiers_of",
    "verify_switches",
]
