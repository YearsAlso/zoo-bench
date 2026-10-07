"""报告的双语消息目录。

**为什么要有这层**：报告原来的文案是散在各模块里的中文字面量（`blocks.py` 96 处、`deck.py` 64、
`report.py` 37 处……），要出英文版就得把它们集中到一处——否则"哪句翻过、哪句没翻"没有任何判据。

**为什么不用 gettext / babel**：只有几百条文案，一个 dict 就够；而 `.po` 的键在静态检查里不是
可查的（拼错只在运行时暴露），还要多一个编译步骤与新依赖。

**中文侧必须与改造前逐字相同**：这些句子是从源码里原样搬过来的，翻译不是"顺手润色"的机会——
措辞一变，此前的报告与今后的报告就无法对照了。

**缺译文即抛错**：留白与"回落到另一种语言"都是**看不出是错**的失败形态——报告照样生成，只是
某一段变成中文或空白。故 :func:`t` 对缺键、缺译文、模板参数对不上三种情况都抛
:class:`MissingTranslation`。
"""

from __future__ import annotations

import string
from typing import Any

LANG_EN = "en"
LANG_ZH = "zh"
LANGS: tuple[str, ...] = (LANG_EN, LANG_ZH)

#: 站点的主语言＝根路径的语言（design D3）。选英文为主：这份报告的用途是对外选型举证。
DEFAULT_LANG = LANG_EN

#: 各语言在站点里的子目录。主语言占根路径，其余各占一个以语言命名的子目录。
LANGUAGE_DIRS: dict[str, str] = {LANG_EN: "", LANG_ZH: "zh"}


class MissingTranslation(LookupError):
    """缺译文，或模板参数与调用点对不上。

    刻意抛而不是回落：报告照样生成、只是某段变了语言或留白，这种失败在页面上几乎看不出来。
    """


#: 键 → 各语言文案。键用点分命名（`<模块>.<用途>.<名字>`），值用 `str.format` 模板承载数字。
#: **模板里的字段集合必须与 `t()` 的调用点严格一致**（多一个少一个都抛错，见 :func:`t`）。
#:
#: **术语表**（`unit.*` 等键是新增文案的基准，术语漂移是双语报告里最难被断言抓住的失败模式）：
#: 微秒 = microseconds、毫秒 = milliseconds、秒 = seconds、任务/秒 = tasks/s、秒/任务 = s/task、
#: 并发度 = concurrency、执行体档位 = body tier、被测框架 = the framework under test、
#: 对照方案 = baseline。**单位一律 ASCII**——`µ` 不在中文字体可靠覆盖的字符集里（实测 SimHei
#: 缺它），而英文产物要求全文 ASCII（design D6）。
MESSAGES: dict[str, dict[str, str]] = {
    # ── 测量侧文案（design D8：进报告的存键，渲染侧解析）──
    "report.absolute_note": {
        LANG_EN: "valid only within a single run; do not compare across runs",
        LANG_ZH: "仅在同一次运行内部有效，不可跨运行比较",
    },
    "report.attribution.diagnosis_note": {
        LANG_EN: "this dimension is a **diagnosis**, not the main evidence: the sampled tiers and concurrencies are a fixed small batch, not the whole matrix. The concurrency ceiling is the machine's parallel capacity - beyond it, what is measured is contention;",
        LANG_ZH: "本维度是**诊断**不是主证据：抽样的档位与并发度固定为一小批，不覆盖矩阵里的全部单元。并发度上界取机器的并行能力——超过它时量到的是争用；",
    },
    "report.attribution.oversubscribed_note": {
        LANG_EN: "this machine's parallel capacity is below the matrix's lowest concurrency, so {concurrencies} was still measured under over-subscription.",
        LANG_ZH: "本轮机器并行能力低于矩阵里最低的并发度，故 {concurrencies} 仍是在超订下量的。",
    },
    "report.attribution.drill_note": {
        LANG_EN: "the sub-measurements are **independent measurements**: each one's duration contains its own preemption waits, so their sum can exceed the submit side they all live in (about 3% measured). Do not read them as an additive partition.",
        LANG_ZH: "细分各项是**独立测量**：它们各自的耗时里都含自己被抢占的等待，故之和可能超过它们共同所在的提交侧（实测约 3%）。读的时候不要把它们当成一个可以相加的划分。",
    },
    "attribution.reason.cross_process": {
        LANG_EN: "the body's start/end timestamps are unavailable - it did not run in this process (cross-process units cannot be split this way)",
        LANG_ZH: "执行体的起止时刻读不到——它没有在本进程里运行（跨进程档位无法这样分段）",
    },
    "attribution.seal.scheduling_round": {
        LANG_EN: "BaseWaiter.execute_service (one scheduling round)",
        LANG_ZH: "BaseWaiter.execute_service（走一次调度轮）",
    },
    "attribution.seal.dispatch": {
        LANG_EN: "BaseWaiter._dispatch_worker (handing a worker to the scheduling model)",
        LANG_ZH: "BaseWaiter._dispatch_worker（把 worker 交给调度模型）",
    },
    "attribution.seal.policy_lookup": {
        LANG_EN: "WorkerDispatchCore.resolve_period / resolve_phase / resolve_run_timeout (per-round policy lookups)",
        LANG_ZH: "WorkerDispatchCore.resolve_period / resolve_phase / resolve_run_timeout（每轮策略查询）",
    },
    "semantics.item.timeout": {
        LANG_EN: "timeout",
        LANG_ZH: "超时",
    },
    "semantics.item.priority": {
        LANG_EN: "priority",
        LANG_ZH: "优先级",
    },
    "semantics.item.retry": {
        LANG_EN: "retry",
        LANG_ZH: "重试",
    },
    "semantics.layer.dispatch": {
        LANG_EN: "dispatch layer",
        LANG_ZH: "派发层",
    },
    "semantics.layer.event": {
        LANG_EN: "event layer",
        LANG_ZH: "事件层",
    },
    "semantics.probe.behavioral": {
        LANG_EN: "behavioral probe (driven repeatedly through scheduling rounds; measures the moment the worker leaves the in-flight table)",
        LANG_ZH: "行为探查（按调度轮反复驱动，量在飞表摘除时刻）",
    },
    "semantics.probe.structural": {
        LANG_EN: "structural probe (checks whether the symbols required for execution exist)",
        LANG_ZH: "结构探查（检查执行所需的符号是否存在）",
    },
    "semantics.location.unavailable": {
        LANG_EN: "source location unavailable",
        LANG_ZH: "源码坐标不可得",
    },
    "semantics.evidence.reaped": {
        LANG_EN: "timeout reap observed: the worker left the in-flight table before the body ended ({observed:.4f}s) (body {body}s, run_timeout {limit}s); judgment action at {location}{note}",
        LANG_ZH: "已观测到超时摘除：在执行体结束之前离开在飞表（{observed:.4f}s）（执行体 {body}s、run_timeout {limit}s）；判定动作在 {location}{note}",
    },
    "semantics.evidence.natural": {
        LANG_EN: "no timeout reap observed: the worker left the in-flight table only when the body ended naturally ({observed:.4f}s) (body {body}s, run_timeout {limit}s); judgment action at {location}{note}",
        LANG_ZH: "未观测到超时摘除：直到执行体自然结束才离开在飞表（{observed:.4f}s）（执行体 {body}s、run_timeout {limit}s）；判定动作在 {location}{note}",
    },
    "semantics.evidence.deadline": {
        LANG_EN: "no timeout reap observed: the worker never left the in-flight table within the probe deadline ({deadline:.2f}s) (body {body}s, run_timeout {limit}s); judgment action at {location}{note}",
        LANG_ZH: "未观测到超时摘除：探查上限（{deadline:.2f}s）内始终未离开在飞表（执行体 {body}s、run_timeout {limit}s）；判定动作在 {location}{note}",
    },
    "semantics.reap_note": {
        LANG_EN: "; what is in effect is observation and circuit-breaking (recording, marking unhealthy, removing the in-flight registration, stopping dispatch), **not termination** of the still-running worker - CPython cannot safely interrupt a running thread",
        LANG_ZH: "；生效的是观测与熔断（记录、标记不健康、摘除在飞登记、停止派发），**不是终止**仍在执行的 worker——CPython 无法安全中断线程",
    },
    "semantics.reason.current_not_observed": {
        LANG_EN: "no reap was observed this round: the timeout judgment is triggered by the scheduling round, and a worker only qualifies for judgment while it stays in the scheduling list",
        LANG_ZH: "本轮未观测到摘除：超时判定由调度轮触发，worker 须留在调度列表里才谈得上被判定",
    },
    "semantics.reason.previous_no_action": {
        LANG_EN: "the judgment exists but **its action is not implemented**: this probe drives through scheduling rounds and the probe worker declares a loop, so the rounds did run - a missing reap can only mean the judgment itself has no action. Enabling it would just add one subtraction and measure nothing of the semantics' cost",
        LANG_ZH: "判定存在但**执行动作未实现**：本探查按调度轮驱动、探查 worker 也声明了循环，调度轮确实跑过，故未摘除只能是判定本身没有执行动作——开启它只多算一次减法，量到的不是语义的代价",
    },
    "semantics.reason.none_usable": {
        LANG_EN: "on the version under test, **none of the enumerated semantic items is measurable**: either its action is not implemented, or its layer cannot execute the reactor. Per design D7's escape hatch, such items are dropped instead of being replaced with an approximate status",
        LANG_ZH: "在当前被测版本上，枚举出的语义项**没有一项可测**：要么其执行动作未实现，要么所在层无法执行反应器。按 design D7 的逃生口，此类项被剔除而非用近似状态代替",
    },
    "semantics.recheck_note": {
        LANG_EN: "this conclusion was reached on the version under test and its coordinates hold for that version only; after appending a version to matrix.yaml, re-run this probe",
        LANG_ZH: "本结论按被测版本得出，坐标只对该版本成立；往 matrix.yaml 追加版本后须重跑本探查",
    },
    "semantics.evidence.no_perform": {
        LANG_EN: "executing reactors on the event layer requires a perform symbol: EventWorker._execute calls reactor.perform (workers/event_worker.py), while EventReactor only defines execute (reactor/event_reactor.py) - a matched reactor would raise AttributeError at once",
        LANG_ZH: "事件层执行反应器要先有 perform：EventWorker._execute 调 reactor.perform（workers/event_worker.py），而 EventReactor 只定义了 execute（reactor/event_reactor.py）——反应器一旦匹配即抛 AttributeError",
    },
    "semantics.evidence.priority_not_wired": {
        LANG_EN: "EventPriorityCalculator exists (fifo/node/event_fifo_node.py) but is not wired into the drain loop - 'sort by priority' in workers/event_worker.py is only a comment",
        LANG_ZH: "EventPriorityCalculator 存在（fifo/node/event_fifo_node.py）但未接入 drain 循环——workers/event_worker.py 里“根据优先级排序”只有一句注释",
    },
    "semantics.evidence.retry_clean_default": {
        LANG_EN: "retry_times defaults to 0, a clean off state, but its trigger is 'no reactor matched' - and the reactor path itself cannot execute",
        LANG_ZH: "retry_times 的默认 0 是干净的关闭态，但触发条件是“没有匹配到反应器”，而反应器路径本身不可执行",
    },
    "semantics.evidence.unchecked": {
        LANG_EN: "this item's conclusion and its coordinates were reached on the '{derived_on}' generation, while the installed one is '{current}' - the two generations wire the event layer differently, so this item has not been re-checked on the current generation",
        LANG_ZH: "本项的结论与其坐标是在「{derived_on}」代上得出的，当前装着的是「{current}」代——两代的事件层接线不同，本项未在当前代上复核",
    },
    "semantics.reason.unchecked": {
        LANG_EN: "not re-checked: this item's conclusion was reached on the '{derived_on}' generation; taking it as this generation's fact asserts wiring in the report that the current generation does not have. Re-deriving it needs a probe design of its own (each item needs clean on/off states before it can be priced)",
        LANG_ZH: "未复核：本项的结论是在「{derived_on}」代上得出的，把它当成本代的事实等于在报告里断言一个当前代并不具备的接线。重新推导需要单独一次探查设计（每项要有干净的开/关态才谈得上定价）",
    },
    "unit.microseconds": {LANG_EN: "microseconds", LANG_ZH: "微秒"},
    "unit.milliseconds": {LANG_EN: "milliseconds", LANG_ZH: "毫秒"},
    "unit.seconds": {LANG_EN: "seconds", LANG_ZH: "秒"},
    "unit.tasks_per_second": {LANG_EN: "tasks/s", LANG_ZH: "任务/秒"},
    "unit.seconds_per_task": {LANG_EN: "s/task", LANG_ZH: "秒/任务"},
    # ── 负载与结论（report.py）──────────────────────────────────────────
    "report.load.composition": {
        LANG_EN: "one unit of work = JSON serialize + deserialize + string concatenation, "
        "repeated until the target duration is reached; the tiers are declared by "
        "body_tiers_us in matrix.yaml",
        LANG_ZH: "单位工作 = JSON 序列化 + 反序列化 + 字符串拼接，重复若干次以达到目标耗时；"
        "档位由 matrix.yaml 的 body_tiers_us 声明",
    },
    "report.load.caveat": {
        LANG_EN: "**the workload is a stand-in, not a real trace**: its shape resembles business "
        "actions such as 'take a frame of data -> serialize -> parse -> transform strings', but "
        "this repository holds no real trace, so every conclusion is conditional on that",
        LANG_ZH: "**负载是替身，不是真实 trace**：形状接近“取一帧数据 -> 序列化 -> 解析 -> 字符串处理”"
        "这类业务动作，但仓库内没有真实样本，结论的适用性以此为前提",
    },
    "report.crossing.polluted": {
        LANG_EN: "this group's concurrency exceeds the environment's capacity and its end-to-end "
        "contains queueing waits, so its overhead figure must not be read as framework overhead "
        "and no crossing point is given (the raw values stay in place for manual reading)",
        LANG_ZH: "该组并发度超出环境容量，端到端含排队等待，其开销数字不可当作框架开销，"
        "故不给交叉点（原值仍在场供人工判读）",
    },
    "report.crossing.smallest_match": {
        LANG_EN: "this is the smallest measured tier that satisfies the condition; the true "
        "crossing point lies between it and the previous tier",
        LANG_ZH: "该档位是所测档位中最小的满足者；真实交叉点落在它与前一档之间",
    },
    "report.crossing.none_below_threshold": {
        LANG_EN: "no measured tier brought the overhead share below the threshold",
        LANG_ZH: "所测档位内没有一档的开销占比降到阈值以下",
    },
    "report.turning.smallest_match": {
        LANG_EN: "this is the smallest measured tier that satisfies the condition",
        LANG_ZH: "该档位是所测档位中最小的满足者",
    },
    "report.turning.never_slower": {
        LANG_EN: "**not observed**: within the measured tiers this baseline is always faster "
        "than the framework under test",
        LANG_ZH: "**未观测到**：所测档位内该对照方案始终快于被测框架",
    },
    "report.summary.crossing_found": {
        LANG_EN: "Once the framework under test {subject} runs a body of about {tier:g} "
        "microseconds or more, its framework overhead drops below {threshold:.0%} of end-to-end; "
        "below that tier, the main price of choosing it is the framework overhead itself.",
        LANG_ZH: "被测框架 {subject} 的执行体时长达到约 {tier:g} 微秒 及以上时，其框架开销占端到端的"
        "比例降到 {threshold:.0%} 以下；短于此档位，选用它的主要代价就是框架开销本身。",
    },
    "report.summary.crossing_missing": {
        LANG_EN: "Within the measured tiers, no tier brought the framework overhead share of "
        "{subject} below {threshold:.0%}; the measured tiers are {tiers}.",
        LANG_ZH: "在所测档位范围内，被测框架 {subject} 没有任何一档的框架开销占比降到 "
        "{threshold:.0%} 以下；所测档位为 {tiers}。",
    },
    "report.summary.overhead_all_unreadable": {
        LANG_EN: "Every overhead figure of the framework under test {subject} falls at a "
        "concurrency beyond this machine's parallel capacity, so this report gives no overhead "
        "crossing point for it  -  end-to-end and throughput remain valid, see the methodology "
        "section.",
        LANG_ZH: "被测框架 {subject} 的开销数字全部落在超出该机器并行能力的并发度上，"
        "故本报告不给它的开销交叉点——端到端与吞吐仍然有效，见口径章节。",
    },
    "report.summary.favourable_best": {
        LANG_EN: "At concurrency {concurrency} and a {tier:g} microsecond body, the framework "
        "under test is {multiple:.2f}x faster than {baseline} (the fastest measured tier at that "
        "concurrency).",
        LANG_ZH: "并发度 {concurrency}、执行体 {tier:g} 微秒 下，被测框架比 {baseline} 快 "
        "{multiple:.2f}x（该并发度所测档位中最快的一档）。",
    },
    "report.summary.turning_found": {
        LANG_EN: "At concurrency {concurrency}, once the body exceeds about {tier:g} "
        "microseconds, {baseline} is no longer faster than the framework under test.",
        LANG_ZH: "并发度 {concurrency} 下，执行体时长超过约 {tier:g} 微秒 后，{baseline} "
        "不再快于被测框架。",
    },
    "report.summary.turning_missing": {
        LANG_EN: "At concurrency {concurrency}, within the measured tiers {baseline} is "
        "**always faster** than the framework under test  -  tiers like these are exactly the "
        "openly published unfavourable data of this report.",
        LANG_ZH: "并发度 {concurrency} 下，所测档位内 {baseline} "
        "**始终快于**被测框架——这类档位正是本报告的公开不利数据。",
    },
    "report.classify.no_rounds": {
        LANG_EN: "the per-round samples are missing (or too few), so this difference cannot be "
        "judged distinguishable",
        LANG_ZH: "缺少逐轮样本（或样本太少），无法判定这点差异是否可分辨",
    },
    "report.classify.margin": {
        LANG_EN: "the framework under test is {multiple:.2f}x faster than {baseline}",
        LANG_ZH: "被测框架比 {baseline} 快 {multiple:.2f}x",
    },
    "report.classify.gap": {
        LANG_EN: "{baseline} is {multiple:.2f}x faster than {subject}",
        LANG_ZH: "{baseline} 比 {subject} 快 {multiple:.2f}x",
    },
    "report.classify.tied": {
        LANG_EN: "the two sides differ by {difference:.1%}, less than the band of {band:.1%}  -  "
        "no winner",
        LANG_ZH: "两侧差异 {difference:.1%} 小于带宽 {band:.1%}，分不出胜负",
    },
    "report.favourable.note": {
        LANG_EN: "Tiers where the framework under test is ahead (the two sides differ by more "
        "than that tier's band). This comes from the **same within-run comparison** as the "
        "openly published unfavourable data  -  one side takes the faster tiers, the other the "
        "slower ones, so both cover the same ground and neither can pick its own tiers. "
        "**This section being empty is not a failure**: it should be empty when nothing is "
        "distinguishable or when the framework loses everywhere.",
        LANG_ZH: "被测框架处于优势的档位（两侧差异大于该档位的带宽）。它与「公开的不利数据」出自"
        "**同一份同运行内的相对比**，只是一个取更快的、一个取更慢的——两者的覆盖面相同，"
        "故谁都挑不了档位。**本节的缺席不构成不合格**：分不出胜负或处处更慢时它就应当是空的",
    },
    "report.unfavourable.note": {
        LANG_EN: "Tiers where the framework under test is behind (the two sides differ by more "
        "than that tier's band). **A report missing this part is not acceptable**  -  a report "
        "that only shows where it wins gives the reader every reason to distrust it. Tiers whose "
        "difference is smaller than the band are not here but in the 'no winner' section: that "
        "is the verdict's outcome, not a place to hide things.",
        LANG_ZH: "被测框架处于劣势的档位（两侧差异大于该档位的带宽）。**这部分缺失的报告不合格**"
        "——一份只展示自己赢的报告，读者有理由认为它不可信。差异小于带宽的档位不在本节，"
        "而在「分不出胜负」那一节：那是判据的结果，不是把它们藏起来",
    },
    "report.tied.note": {
        LANG_EN: "Tiers where the two sides **cannot be told apart**: the difference is smaller "
        "than that tier's band. The band comes from the two sides' own across-round spread "
        "(stdev / median of the per-task medians) **within the same run**, so it varies with the "
        "sample and the reader can recompute it from the archived per-round samples. It also "
        "covers every measured tier and states a reason for each  -  **this section is not a place "
        "to hide things**: reporting noise-level differences as '1.00x faster' is.",
        LANG_ZH: "两侧**分不出胜负**的档位：差异小于该档位的带宽。带宽取自**同一次运行**内两侧各自的"
        "跨轮离散度（每任务中位数的标准差 / 中位数）之和，故它随样本变化，读者可用留档里的"
        "逐轮样本自行复核。它同样覆盖全部所测档位、逐项写明原因——**本节不是藏东西的地方**："
        "把噪声级差异报成「快 1.00x」才是",
    },
    "report.tied.band_definition": {
        LANG_EN: "band = the framework's own across-round relative spread plus the baseline's; a "
        "single side's across-round relative spread = stdev / median of that unit's per-round "
        "per-task medians",
        LANG_ZH: "带宽 = 被测框架侧与对照侧各自的跨轮相对离散度之和；"
        "单侧的跨轮相对离散度 = 该单元逐轮的「每任务中位数」的标准差 / 中位数",
    },
    # 口径局限（caveats）逐条的 kind 与正文
    "report.caveat.level.kind": {
        LANG_EN: "measured layer",
        LANG_ZH: "被测层级",
    },
    "report.caveat.incomparable.kind": {
        LANG_EN: "not directly comparable",
        LANG_ZH: "不可直接对标",
    },
    "report.caveat.scope.kind": {
        LANG_EN: "scope note",
        LANG_ZH: "口径说明",
    },
    "report.caveat.failed_units.kind": {
        LANG_EN: "incomplete units",
        LANG_ZH: "未完成的单元",
    },
    "report.caveat.failed_units.text": {
        LANG_EN: "these units failed and their data does not appear in the report; the reasons "
        "are in the raw archive",
        LANG_ZH: "这些单元失败，其数据不出现在报告中；失败原因见原始数据",
    },
    "report.caveat.equivalence.kind": {
        LANG_EN: "adapters that failed equivalence verification",
        LANG_ZH: "未通过等价性验证的适配器",
    },
    "report.caveat.equivalence.text": {
        LANG_EN: "these did not pass the 'every submitted task runs, and each exactly once' "
        "verification, so their data cannot be trusted",
        LANG_ZH: "未通过“提交的任务全部执行且各执行一次”的验证，其数据不可信",
    },
    "report.caveat.queueing.kind": {
        LANG_EN: "overhead figures contaminated by queueing",
        LANG_ZH: "受排队污染的开销数字",
    },
    "report.caveat.queueing.text": {
        LANG_EN: "for the (adapter, concurrency) groups below, concurrency exceeds the "
        "environment's capacity and the end-to-end contains queueing waits, so their "
        "**framework overhead and that group's crossing point must not be read as framework "
        "overhead**  -  the raw values stay in the report, they just do not take part in the "
        "conclusions. Measured, the across-tier overhead difference in such groups reaches tens "
        "of times, all of it queueing",
        LANG_ZH: "以下 (适配器, 并发度) 组的并发度超出环境容量，端到端里含排队等待，"
        "故其**框架开销与该组的交叉点不可当作框架开销来读**——原始值仍在报告里，只是不参与结论。"
        "实测这类组的跨档位开销差异可达数十倍，全部来自排队",
    },
    "report.caveat.withheld.kind": {
        LANG_EN: "groups with no overhead figure",
        LANG_ZH: "不给出开销数字的组",
    },
    "report.caveat.withheld.text": {
        LANG_EN: "for the (adapter, concurrency) groups below, concurrency exceeds the parallel "
        "capacity of the machine this run was on: the body's self-reported duration contains "
        "scheduling waits from oversubscription, which is not the same thing as 'wall clock / "
        "concurrency', so subtracting them is meaningless (measured, it produced negative values "
        "and meaningless change factors). This report therefore **gives them no framework "
        "overhead and no crossing point**; end-to-end, throughput and the body's self-reported "
        "values remain valid and not one field has been dropped from the raw archive. The "
        "criterion is the logical core count from the environment self-report, plus one "
        "evidential backstop: a computed negative overhead is likewise treated as unreadable  -  a "
        "framework can only add time, never remove it",
        LANG_ZH: "以下 (适配器, 并发度) 组的并发度超过这次运行所在机器的并行能力："
        "执行体自报的耗时里含超订带来的调度等待，与「墙钟 / 并发度」不是同一件事，相减的结果没有"
        "意义（实测出现过负值，并衍生出无意义的变化倍数）。故本报告对它们**不给框架开销、也不给"
        "交叉点**；端到端、吞吐与执行体自报值仍然有效，原始留档里的字段也一个没删。判据取自环境"
        "自述的逻辑核数，另有一条证据性兜底：算出来的开销为负同样判为不可读——框架只会加时间"
        "不会减时间",
    },
    "report.caveat.ungated.kind": {
        LANG_EN: "self-check items excluded from the verdict",
        LANG_ZH: "未参与判定的自检项",
    },
    "report.caveat.ungated.text": {
        LANG_EN: "for these units the body-tier deviation did not take part in the verdict "
        "(concurrency above the lowest, or a tier so short that wall clock is dominated by "
        "scheduling granularity). The raw values are still in place, but the wall clock at those "
        "two places is not enough to conclude whether the calibration is right  -  **factor this "
        "in when reading those tiers' numbers**",
        LANG_ZH: "这些单元的执行体档位偏差未参与判定（并发度高于最低档，或档位短到墙钟受调度"
        "颗粒度支配）。原始值仍在场，但那两处的墙钟不足以断定校准是否正确——"
        "**读这些档位的数字时要把它算进去**",
    },
    # 度量维度的标题 / 单位 / 口径注
    "report.dimension.latency.title": {
        LANG_EN: "Latency percentiles and jitter",
        LANG_ZH: "延迟分位数与抖动",
    },
    "report.dimension.latency.note": {
        LANG_EN: "end-to-end / concurrency; percentiles and relative spread are given in the "
        "same table  -  a metric that only gives the median is not acceptable",
        LANG_ZH: "端到端 / 并发度；分位数与相对离散度同表给出，只给中位数的度量不合格",
    },
    "report.dimension.overhead.title": {
        LANG_EN: "Framework overhead share",
        LANG_ZH: "框架自身开销占比",
    },
    "report.dimension.overhead.note": {
        LANG_EN: "overhead = end-to-end / concurrency - the body duration measured within the "
        "same run (no cross-run subtraction)",
        LANG_ZH: "开销 = 端到端/并发度 - 同一次运行内实测的执行体耗时（不做跨运行减法）",
    },
    "report.dimension.throughput.title": {
        LANG_EN: "Throughput and concurrency scaling",
        LANG_ZH: "吞吐与并发伸缩",
    },
    "report.dimension.throughput.note": {
        LANG_EN: "throughput = concurrency / median end-to-end; whether it keeps rising with "
        "concurrency is the scaling behaviour",
        LANG_ZH: "吞吐 = 并发度 / 端到端中位数；随并发度是否继续上升即为伸缩性",
    },
    # 归因的四段名与提交侧细分桶名
    "report.attribution.segment.submit_side": {LANG_EN: "submit side", LANG_ZH: "提交侧"},
    "report.attribution.segment.handoff": {LANG_EN: "handoff", LANG_ZH: "手交"},
    "report.attribution.segment.body": {LANG_EN: "body", LANG_ZH: "执行体"},
    "report.attribution.segment.return": {LANG_EN: "return", LANG_ZH: "回程"},
    "report.attribution.drill.scheduling_round": {
        LANG_EN: "rest of the scheduling round (due checks, locks, list upkeep)",
        LANG_ZH: "调度轮其余（判定、锁与调度列表维护）",
    },
    "report.attribution.drill.dispatch": {
        LANG_EN: "dispatch (handed to the scheduling model, incl. pool enqueue)",
        LANG_ZH: "派发（交给调度模型，含池的入队）",
    },
    "report.attribution.drill.policy_lookup": {
        LANG_EN: "per-round policy lookups (period / phase / timeout each read config)",
        LANG_ZH: "每轮策略查询（周期/相位/超时各查一次配置）",
    },
    "report.attribution.drill.submit_side_other": {
        LANG_EN: "submit side, other (the adapter's own bookkeeping and completion-signal state)",
        LANG_ZH: "提交侧其他（适配器自己的记账与完成信号状态）",
    },
    "report.attribution.finding_not_slower": {
        LANG_EN: "At concurrency {concurrency} and a {tier:g} microsecond body, the framework "
        "under test is not slower overall than {baseline} ({excess:+.0f} microseconds per task): "
        "the two sides have different timing structures, and the per-segment differences have "
        "both signs, so no 'which segment' is given.",
        LANG_ZH: "并发度 {concurrency}、执行体 {tier:g} 微秒 下，被测框架整体不慢于 {baseline}"
        "（每任务 {excess:+.0f} 微秒）：两边的时序结构不同，逐段差额有正有负，"
        "故不给「落在哪一段」。",
    },
    "report.attribution.finding_slower": {
        LANG_EN: "At concurrency {concurrency} and a {tier:g} microsecond body, the framework "
        "under test spends {excess:.0f} microseconds more per task than {baseline}, most of it on "
        "'{segment}' ({share:+.0f} microseconds).",
        LANG_ZH: "并发度 {concurrency}、执行体 {tier:g} 微秒 下，被测框架相对 {baseline} "
        "每任务多花 {excess:.0f} 微秒，其中主要落在「{segment}」（{share:+.0f} 微秒）。",
    },
    "report.attribution.finding_mixed": {
        LANG_EN: "  note: the per-segment differences in this group do not all share a sign  - "
        "'{segment}' is {share:+.0f} microseconds, so read them together.",
        LANG_ZH: "　注意：该组逐段差额并非同号——「{segment}」为 {share:+.0f} 微秒，"
        "读的时候要一并看。",
    },
    "report.attribution.submit_side_composition": {
        LANG_EN: "In this group the framework's submit side consists of (microseconds): {parts}.",
        LANG_ZH: "被测框架在该组的提交侧由以下部分构成（微秒）：{parts}。",
    },
    "report.attribution.title": {LANG_EN: "Overhead attribution", LANG_ZH: "开销归因"},
    "report.attribution.scope_note": {
        LANG_EN: "splits the per-task end-to-end into submit side / handoff / body / return and, "
        "at the same tier and concurrency, **compares each segment against every baseline**, "
        "answering 'which segment the excess falls on'. It does **not** compare total overhead "
        "against the baselines  -  that already exists; this dimension only answers 'where to look "
        "next'",
        LANG_ZH: "把每任务端到端拆成提交侧 / 手交 / 执行体 / 回程四段，并在同一档位与并发度下"
        "**与各对照方案逐段对照**，回答「超出对照的部分落在哪一段」。它**不与对照方案比总开销**"
        "——总开销已经有了；这一维只回答「下一步该看哪里」",
    },
    "report.attribution.detail_note": {
        LANG_EN: "splits the per-task end-to-end into submit side / handoff / body / return and, "
        "at the same tier and concurrency, **compares each segment against every baseline**, "
        "answering 'which segment the excess falls on'. It is **diagnostic, not the main "
        "evidence**: it samples a small set of tiers, and does not compare total overhead against "
        "the baselines. **A negative handoff is not an error**: it means the submit call had not "
        "returned yet while the body was already running  -  schemes that count thread creation as "
        "submit side behave this way (measured: bare_thread handoff -343 microseconds). **The "
        "return segment includes waiting for the completion signal** (the last stretch before "
        "draining returns); it shares the main measurement's scope",
        LANG_ZH: "把每任务端到端拆成提交侧 / 手交 / 执行体 / 回程四段，并在同一档位与并发度下"
        "**与各对照方案逐段对照**，回答「超出对照的部分落在哪一段」。它是**诊断**而非主证据："
        "抽样一小批档位，且不与对照方案比总开销。**手交为负不是错误**：那表示提交调用还没返回、"
        "执行体就已经开跑——把建线程一类工作算进提交侧的方案就会这样（实测 bare_thread 手交 "
        "-343 微秒）。**回程含完成信号的等待**（排空返回前的最后一段），它与主测量是同一口径",
    },
    "report.attribution.not_in_run": {
        LANG_EN: "this run did not include the attribution probe (run with with_attribution=False)",
        LANG_ZH: "本轮运行未包含开销归因探查（以 with_attribution=False 运行）",
    },
    "report.attribution.none_measured": {
        LANG_EN: "every group in this run failed to measure; see the per-group reasons",
        LANG_ZH: "本轮归因的每一组都没能量成，见逐组原因",
    },
    "report.semantics.title": {
        LANG_EN: "The cost of scheduling semantics",
        LANG_ZH: "调度语义的代价",
    },
    "report.semantics.scope_note": {
        LANG_EN: "this dimension uses an **internal switch comparison** (same workload, semantics "
        "all off vs. enabled one by one) and **does not compare across schemes**  -  a bare writeup "
        "has no counterpart for priority / timeout / retry, so it can only answer 'what is this "
        "framework's semantics worth', not 'how much more expensive than a bare writeup'",
        LANG_ZH: "本维度采用**内部开关对照**（同一 workload 下语义全关 vs 逐个开启），"
        "**不与对照方案横向比较**——裸写法没有优先级/超时/重试的对应物，故只能回答"
        "“本框架的语义值多少钱”，不能回答“比裸写法贵多少”",
    },
    "report.semantics.not_in_run": {
        LANG_EN: "this run did not include the semantics probe (run with with_semantics=False)",
        LANG_ZH: "本轮运行未包含语义维度探查（以 with_semantics=False 运行）",
    },
    "report.conclusion.note": {
        LANG_EN: "a single-point speedup has no selection meaning; the conclusion is phrased as "
        "'which scheme to choose at what body duration' (the same framework overhead is 70% of a "
        "40 microsecond task and a few percent of a 10 millisecond one)",
        LANG_ZH: "单点加速比没有选型含义；结论以“多大的执行体时长下选哪个方案”表述"
        "（同一份框架开销，在 40 微秒 的任务上占 70%，在 10 ms 上只占百分之几）",
    },
    # ── 运行环境（environment.py）────────────────────────────────────────
    "env.cpu.proc_unreadable": {
        LANG_EN: "reading /proc/cpuinfo failed: {error}",
        LANG_ZH: "读取 /proc/cpuinfo 失败：{error}",
    },
    "env.cpu.proc_no_model": {
        LANG_EN: "/proc/cpuinfo has no model name field",
        LANG_ZH: "/proc/cpuinfo 中没有 model name 字段",
    },
    "env.cpu.registry_read": {
        LANG_EN: "taken from the registry: ProcessorNameString",
        LANG_ZH: "注册表 ProcessorNameString",
    },
    "env.cpu.registry_failed": {
        LANG_EN: "reading the registry failed: {error}",
        LANG_ZH: "读取注册表失败：{error}",
    },
    "env.cpu.no_sysctl": {LANG_EN: "sysctl not found", LANG_ZH: "找不到 sysctl"},
    "env.cpu.sysctl_failed": {
        LANG_EN: "sysctl returned {code}",
        LANG_ZH: "sysctl 返回 {code}",
    },
    "env.cpu.unsupported_system": {
        LANG_EN: "reading the CPU model from {system} is not supported yet",
        LANG_ZH: "暂不支持从 {system} 取 CPU 型号",
    },
    "env.git.not_found": {LANG_EN: "git not found", LANG_ZH: "找不到 git"},
    "env.git.not_a_repo": {
        LANG_EN: "no git repository along this path: {attempts}",
        LANG_ZH: "以下路径都不是 git 仓库：{attempts}",
    },
    "env.subject.version_note": {
        LANG_EN: "dist_version (the distribution metadata) is the source of truth; "
        "module_version is only a footnote",
        LANG_ZH: "以 dist_version（发行元数据）为版本真源；module_version 仅作附注",
    },
    # ── 撤下开销数字的原因（caliber.py）──────────────────────────────────
    "caliber.uninterpretable_reason": {
        LANG_EN: "this concurrency exceeds the parallel capacity of the machine this run was on: "
        "the body's self-reported duration contains scheduling waits from oversubscription, "
        "which is not the same thing as 'wall clock / concurrency', so subtracting them is "
        "meaningless (measured, it produced negative values). End-to-end, throughput and the "
        "body's self-reported values remain valid, so they are given as usual.",
        LANG_ZH: "该并发度超过这次运行所在机器的并行能力：执行体自报的耗时里含超订带来的调度等待，"
        "与「墙钟 / 并发度」不是同一件事，相减的结果没有意义（实测出现过负值）。"
        "端到端、吞吐与执行体自报值仍然有效，故照旧给出。",
    },
    # ── 块层（render/blocks.py）──────────────────────────────────────────
    "blocks.section.conclusion": {LANG_EN: "Conclusion summary", LANG_ZH: "结论摘要"},
    "blocks.section.charts": {LANG_EN: "Charts", LANG_ZH: "图表"},
    "blocks.section.dimension_prefix": {LANG_EN: "Dimension: ", LANG_ZH: "维度："},
    "blocks.section.appendix_prefix": {LANG_EN: "Appendix: ", LANG_ZH: "附录："},
    "blocks.section.unfavourable": {
        LANG_EN: "Openly published unfavourable data",
        LANG_ZH: "公开的不利数据",
    },
    "blocks.empty_marker": {LANG_EN: "-", LANG_ZH: "—"},
    "blocks.report.title": {
        LANG_EN: "zoo-bench performance report",
        LANG_ZH: "zoo-bench 性能报告",
    },
    "blocks.report.source": {
        LANG_EN: "raw data: `{path}`",
        LANG_ZH: "原始数据：`{path}`",
    },
    "blocks.env.title": {LANG_EN: "Run environment", LANG_ZH: "运行环境"},
    "blocks.env.missing": {
        LANG_EN: "**this report lacks the environment self-description and must not be published.**",
        LANG_ZH: "**本报告缺少环境自述，不应被发布。**",
    },
    "blocks.env.header_item": {LANG_EN: "item", LANG_ZH: "项"},
    "blocks.env.header_value": {LANG_EN: "value", LANG_ZH: "值"},
    "blocks.env.cpu_model": {LANG_EN: "CPU model", LANG_ZH: "CPU 型号"},
    "blocks.env.cpu_unavailable": {
        LANG_EN: "unavailable ({source})",
        LANG_ZH: "不可用（{source}）",
    },
    "blocks.env.logical_cores": {LANG_EN: "logical cores", LANG_ZH: "逻辑核数"},
    "blocks.env.platform": {LANG_EN: "platform", LANG_ZH: "平台"},
    "blocks.env.os": {LANG_EN: "operating system", LANG_ZH: "操作系统"},
    "blocks.env.os_value": {
        LANG_EN: "{system} {release} ({version})",
        LANG_ZH: "{system} {release}（{version}）",
    },
    "blocks.env.python_label": {LANG_EN: "Python", LANG_ZH: "Python"},
    "blocks.env.interpreter": {LANG_EN: "interpreter", LANG_ZH: "解释器"},
    "blocks.env.gil_label": {LANG_EN: "GIL", LANG_ZH: "GIL"},
    "blocks.env.subject_dist": {
        LANG_EN: "framework under test (distribution metadata)",
        LANG_ZH: "被测框架（发行元数据）",
    },
    "blocks.env.subject_module": {
        LANG_EN: "framework under test (module __version__)",
        LANG_ZH: "被测框架（模块 __version__）",
    },
    "blocks.env.module_note": {
        LANG_EN: " (footnote only, not the version criterion)",
        LANG_ZH: "（仅附注，不作版本判据）",
    },
    "blocks.env.drive_generation_label": {
        LANG_EN: "drive-surface generation of the framework under test",
        LANG_ZH: "被测框架的驱动面世代",
    },
    "blocks.env.drive_generation_undetermined": {
        LANG_EN: "undetermined ({detail})",
        LANG_ZH: "无法判定（{detail}）",
    },
    "blocks.env.drive_generation_not_declared": {
        LANG_EN: "not in the environment self-description",
        LANG_ZH: "自述里没有这一项",
    },
    "blocks.env.install_source": {LANG_EN: "install source", LANG_ZH: "安装来源"},
    "blocks.env.install_source_value": {
        LANG_EN: "{ref} via {vcs}",
        LANG_ZH: "{vcs} 的 {ref}",
    },
    "blocks.env.ref_not_recorded": {
        LANG_EN: "(reference not recorded)",
        LANG_ZH: "（未记录引用）",
    },
    "blocks.env.commit": {LANG_EN: "commit", LANG_ZH: "提交"},
    "blocks.env.reproduce": {
        LANG_EN: "command to reproduce",
        LANG_ZH: "复现命令",
    },
    "blocks.load.title": {LANG_EN: "Workload", LANG_ZH: "负载"},
    "blocks.load.composition": {
        LANG_EN: "composition: {composition}",
        LANG_ZH: "构成：{composition}",
    },
    "blocks.methodology.title": {
        LANG_EN: "Measurement methodology",
        LANG_ZH: "测量口径",
    },
    "blocks.methodology.in_run": {
        LANG_EN: "**the body duration is instrumented within the same run**: the body measures "
        "itself internally and reports back; framework overhead = end-to-end - it. "
        "**No cross-run subtraction**  -  two runs differ in state (caches, frequencies, "
        "scheduling noise), so subtracting introduces systematic bias.",
        LANG_ZH: "**执行体耗时在同一次运行内埋点**：由执行体在自身内部测量并回传，框架开销 ="
        "端到端 - 它。**不做跨运行减法**——两次运行的状态不同（缓存、频率、调度噪声），"
        "相减引入的是系统性偏差。",
    },
    "blocks.methodology.warmup": {
        LANG_EN: "**warmups are excluded from statistics**: each unit first runs {warmups} "
        "warmup rounds that are discarded, then only the following {rounds} measured rounds "
        "are counted; the sample count equals 'measured rounds x concurrency'.",
        LANG_ZH: "**预热不入统计**：每个单元先跑 {warmups} 轮预热并丢弃，只统计随后的"
        " {rounds} 轮正式采样；采样数等于「正式轮数 x 并发度」。",
    },
    "blocks.methodology.absolute": {
        LANG_EN: "**absolute durations are not comparable across runs**: they only show the "
        "order of magnitude; across versions/machines read the **relative quantities within "
        "the same run** (overhead share, multiples against the baselines), which are "
        "insensitive to overall speed.",
        LANG_ZH: "**绝对耗时不可跨运行比较**：它只用于看量级；跨版本/跨机器要看的是**同一次运行内"
        "的相对量**（开销占比、相对各对照方案的倍数），它们对整体快慢不敏感。",
    },
    "blocks.crossing.title": {
        LANG_EN: "Overhead threshold crossings",
        LANG_ZH: "开销阈值交叉点",
    },
    "blocks.turning.title": {
        LANG_EN: "Relative turning points",
        LANG_ZH: "相对转折点",
    },
    "blocks.header.scheme": {LANG_EN: "scheme", LANG_ZH: "方案"},
    "blocks.header.baseline": {LANG_EN: "baseline", LANG_ZH: "对照方案"},
    "blocks.header.concurrency": {LANG_EN: "concurrency", LANG_ZH: "并发度"},
    "blocks.header.threshold": {LANG_EN: "threshold", LANG_ZH: "阈值"},
    "blocks.header.note": {LANG_EN: "note", LANG_ZH: "说明"},
    "blocks.header.status": {LANG_EN: "status", LANG_ZH: "状态"},
    "blocks.header.first_tier_below": {
        LANG_EN: "first tier at or below the threshold (microseconds)",
        LANG_ZH: "首个不高于阈值的档位（微秒）",
    },
    "blocks.header.measured_tiers": {
        LANG_EN: "measured tiers (microseconds)",
        LANG_ZH: "所测档位（微秒）",
    },
    "blocks.header.first_tier_not_faster": {
        LANG_EN: "first tier where it is no longer faster (microseconds)",
        LANG_ZH: "该方案不再更快的档位（微秒）",
    },
    "blocks.header.body_tier": {
        LANG_EN: "body tier (microseconds)",
        LANG_ZH: "执行体档位（微秒）",
    },
    "blocks.header.tier": {
        LANG_EN: "tier (microseconds)",
        LANG_ZH: "档位（微秒）",
    },
    "blocks.header.gap": {LANG_EN: "gap", LANG_ZH: "差距"},
    "blocks.header.ratio": {LANG_EN: "ratio", LANG_ZH: "比值"},
    "blocks.header.reason": {LANG_EN: "reason", LANG_ZH: "原因"},
    "blocks.header.subject_median": {
        LANG_EN: "median of the framework under test",
        LANG_ZH: "被测框架中位数",
    },
    "blocks.header.median": {LANG_EN: "median", LANG_ZH: "中位数"},
    "blocks.header.p95": {LANG_EN: "p95", LANG_ZH: "p95"},
    "blocks.header.p99": {LANG_EN: "p99", LANG_ZH: "p99"},
    "blocks.header.relative_spread": {
        LANG_EN: "relative spread",
        LANG_ZH: "相对离散度",
    },
    "blocks.header.overhead": {
        LANG_EN: "framework overhead",
        LANG_ZH: "框架开销",
    },
    "blocks.header.overhead_share": {
        LANG_EN: "overhead share",
        LANG_ZH: "开销占比",
    },
    "blocks.header.body_measured": {
        LANG_EN: "body as measured",
        LANG_ZH: "执行体实测",
    },
    "blocks.header.throughput": {
        LANG_EN: "throughput (tasks/s)",
        LANG_ZH: "吞吐（任务/秒）",
    },
    "blocks.header.excess_per_task": {
        LANG_EN: "excess per task",
        LANG_ZH: "每任务超出",
    },
    "blocks.header.dominant_segment": {
        LANG_EN: "mostly on",
        LANG_ZH: "主要落在",
    },
    "blocks.header.segment_gap": {
        LANG_EN: "gap in that segment",
        LANG_ZH: "该段之差",
    },
    "blocks.header.semantics_item": {
        LANG_EN: "semantics item",
        LANG_ZH: "语义项",
    },
    "blocks.header.layer": {LANG_EN: "layer", LANG_ZH: "层级"},
    "blocks.header.probe": {LANG_EN: "how probed", LANG_ZH: "探查方式"},
    "blocks.header.usable": {LANG_EN: "measurable", LANG_ZH: "可测"},
    "blocks.header.evidence_or_reason": {
        LANG_EN: "evidence / reason",
        LANG_ZH: "证据 / 原因",
    },
    "blocks.yes": {LANG_EN: "yes", LANG_ZH: "是"},
    "blocks.no": {LANG_EN: "no", LANG_ZH: "否"},
    "blocks.scope_note": {
        LANG_EN: "scope: {note}",
        LANG_ZH: "口径：{note}",
    },
    "blocks.overhead.withheld": {
        LANG_EN: ". **cells with -**: that concurrency exceeds this machine's parallel "
        "capacity; the body's self-reported duration contains scheduling waits from "
        "oversubscription and is not comparable with the row's end-to-end per task, so no "
        "overhead figure is given (end-to-end and throughput live in their own dimensions)",
        LANG_ZH: "。**带 — 的格**：该并发度超出这台机器的并行能力，执行体自报耗时含超订的调度等待，"
        "与该行的每任务端到端不可比，故不给开销数字（端到端与吞吐见各自的维度）",
    },
    "blocks.semantics.status": {
        LANG_EN: "**status: {status}**",
        LANG_ZH: "**状态：{status}**",
    },
    "blocks.semantics.derived_on": {
        LANG_EN: " (the conclusion was reached on the '{generation}' drive-surface generation "
        "installed during this measurement)",
        LANG_ZH: "（结论在该次测量装着的「{generation}」代驱动面上得出）",
    },
    "blocks.attribution.sampled": {
        LANG_EN: "sampled tiers (microseconds): {tiers}; concurrency: {concurrencies}; "
        "this machine's parallel capacity: {cores}",
        LANG_ZH: "抽样档位（微秒）：{tiers}；并发度：{concurrencies}；该机器并行能力：{cores}",
    },
    "blocks.attribution.status_reason": {
        LANG_EN: "{status}: {reason}",
        LANG_ZH: "{status}：{reason}",
    },
    "blocks.attribution.findings_title": {
        LANG_EN: "where the excess over each baseline falls",
        LANG_ZH: "超出各对照方案的部分落在哪一段",
    },
    "blocks.attribution.summary_title": {
        LANG_EN: "Attribution conclusions",
        LANG_ZH: "归因结论",
    },
    "blocks.attribution.seals": {
        LANG_EN: "instrumentation seams used (temporarily wrapped on the harness side, no "
        "framework code changed): {seals}",
        LANG_ZH: "用到的插桩接缝（在 harness 侧临时包装，不改框架代码）：{seals}",
    },
    "blocks.attribution.seals_missing": {
        LANG_EN: "**seams not measured this time** (this generation of the framework under "
        "test has no such entry point, so the related sub-measurements are absent): {seals}",
        LANG_ZH: "**本次未能量到的接缝**（被测框架的这一代没有对应入口，故相关细分项不在场）："
        "{seals}",
    },
    "blocks.attribution.instrumentation_cost": {
        LANG_EN: "**the cost of instrumentation itself** (on the framework-under-test side, "
        "paired from two rounds in the same subprocess, so it can be discounted honestly): "
        "{costs}",
        LANG_ZH: "**插桩自身的成本**（被测框架那一侧，由同一子进程里插桩前后两轮配对得到，"
        "可如实折价）：{costs}",
    },
    "blocks.attribution.instrumentation_item": {
        LANG_EN: "{scheme} {tier:g} microseconds / concurrency {concurrency}: without "
        "instrumentation {before} vs with {after}, a difference of {delta}",
        LANG_ZH: "{scheme} {tier:g} 微秒 / 并发 {concurrency}："
        "不插桩 {before} 对插桩后 {after}，差 {delta}",
    },
    "blocks.attribution.instrumentation_drift": {
        LANG_EN: " (faster with instrumentation: this round's machine drift exceeded the "
        "instrumentation cost)",
        LANG_ZH: "（插桩后反而更快：这轮里机器漂移比插桩成本还大）",
    },
    "blocks.attribution.additivity": {
        LANG_EN: "**additivity of the sub-measurements**: {note}measured overlap this round: "
        "{overlaps}",
        LANG_ZH: "**细分的可加性**：{note}本轮实测超出量：{overlaps}",
    },
    "blocks.attribution.additivity_item": {
        LANG_EN: "{scheme} {tier:g} microseconds / concurrency {concurrency} overlaps "
        "{overlap:.1%}",
        LANG_ZH: "{scheme} {tier:g} 微秒 / 并发 {concurrency} 超出 {overlap:.1%}",
    },
    "blocks.attribution.drills_title": {
        LANG_EN: "what the submit side of the framework under test consists of",
        LANG_ZH: "被测框架的提交侧由什么构成",
    },
    "blocks.attribution.drill_item": {
        LANG_EN: "concurrency {concurrency}, tier {tier:g} microseconds: {parts}",
        LANG_ZH: "并发度 {concurrency}、档位 {tier:g} 微秒：{parts}",
    },
    "blocks.favourable.title": {
        LANG_EN: "tiers where the framework under test is faster",
        LANG_ZH: "被测框架在哪些档位更快",
    },
    "blocks.favourable.empty": {
        LANG_EN: "no tier within the measured set has the framework under test ahead.",
        LANG_ZH: "所测档位内未出现被测框架处于优势的情形。",
    },
    "blocks.unfavourable.empty": {
        LANG_EN: "no tier within the measured set has the framework under test behind.",
        LANG_ZH: "所测档位内未出现被测框架处于劣势的情形。",
    },
    "blocks.tied.title": {
        LANG_EN: "tiers with no winner",
        LANG_ZH: "分不出胜负的档位",
    },
    "blocks.tied.empty": {
        LANG_EN: "every measured tier has a winner.",
        LANG_ZH: "所测档位内每一档都分得出胜负。",
    },
    "blocks.tied.criterion": {
        LANG_EN: "criterion: {definition}",
        LANG_ZH: "判据：{definition}",
    },
    "blocks.tied.band_now": {
        LANG_EN: "band this run: {low:.1%} to {high:.1%} (it varies with each tier's spread).",
        LANG_ZH: "本期带宽：{low:.1%} 到 {high:.1%}（随各档位的离散度不同而不同）。",
    },
    "blocks.appendix.all_values": {
        LANG_EN: "all values",
        LANG_ZH: "全部数值",
    },
    "blocks.appendix.pointer": {
        LANG_EN: "per-unit values (percentiles, spread, raw per-round samples) are in "
        "'Appendix: all values' at the end.",
        LANG_ZH: "逐单元数值（分位数、离散度、各轮原始样本）见文末「附录：全部数值」。",
    },
    "blocks.appendix.note": {
        LANG_EN: "the body carries only pivot tables for readability; this appendix gives "
        "**every field as measured before any trimming** per unit (percentiles, spread, "
        "measured body durations, ...). **Not one value is dropped**: the details live inside "
        "the report body itself, not behind links, folds or attachments  -  'verifiable' that "
        "requires a separate lookup is not verifiable",
        LANG_ZH: "正文为可读性只给透视表；这里逐单元给出**改动前的全部字段**（分位数、离散度、"
        "执行体实测等）。**数值一个不少**：明细放在报告主体内，而不是外链、折叠或附件——"
        "「可查」一旦要另外去找，就等于不可查",
    },
    "blocks.caveats.title": {
        LANG_EN: "caliber limitations and sources of bias",
        LANG_ZH: "口径局限与偏差来源",
    },
    "blocks.caveats.none": {LANG_EN: "(none)", LANG_ZH: "（无）"},
    "blocks.caveat.line": {
        LANG_EN: "**{kind}**{adapter}: {text}",
        LANG_ZH: "**{kind}**{adapter}：{text}",
    },
    "blocks.caveat.adapter": {
        LANG_EN: " ({adapter})",
        LANG_ZH: "（{adapter}）",
    },
    "blocks.caveats.absolute": {
        LANG_EN: "absolute durations: {note}",
        LANG_ZH: "绝对耗时：{note}",
    },
    "blocks.self_check.title": {LANG_EN: "self-check", LANG_ZH: "自检"},
    "blocks.self_check.overall": {
        LANG_EN: "overall measurement self-check: {verdict}",
        LANG_ZH: "测量自检整体：{verdict}",
    },
    "blocks.self_check.passed": {LANG_EN: "passed", LANG_ZH: "通过"},
    "blocks.self_check.failed": {LANG_EN: "**failed**", LANG_ZH: "**未通过**"},
    "blocks.self_check.isolation": {
        LANG_EN: "process isolation: {value}",
        LANG_ZH: "进程隔离：{value}",
    },
    "blocks.extension.title": {
        LANG_EN: "bring your own baseline",
        LANG_ZH: "加入你自己的对照",
    },
    "blocks.extension.note": {
        LANG_EN: "the baselines of this harness are **open**: implement the four methods of "
        "`zoo_bench.adapters.BaseAdapter` (`setup` / `submit` / `drain` / `teardown`), "
        "register with `zoo_bench.adapters.registry.register`, or list your module path in "
        "`extra_modules` of `matrix.yaml`  -  **no file in this repository needs to change**. "
        "Your implementation goes through the same equivalence verification (submit N bodies, "
        "assert all ran and each exactly once); failing it means the data is not trusted. "
        "**We deliberately write this path into the report**: a report written by the "
        "framework's own maintainer, measuring that framework, gives every reason to suspect "
        "a slowed baseline or a chosen caliber; letting anyone submit their own baseline is "
        "what makes this report challengeable and falsifiable.",
        LANG_ZH: "本 harness 的对照方案是**开放**的：实现 `zoo_bench.adapters.BaseAdapter` 的四个方法"
        "（`setup` / `submit` / `drain` / `teardown`），用 `zoo_bench.adapters.registry.register` "
        "登记，或在 `matrix.yaml` 的 `extra_modules` 里列出你的模块路径——**无需改动本仓库任何"
        "文件**。你的实现会通过同一套等价性验证（提交 N 个执行体、断言全部执行且各执行一次），"
        "不通过的不会被采信。"
        "**我们主动把这条路径写进报告**：一份由被测框架维护者撰写、测量被测框架的报告，读者有理由"
        "怀疑对照被写慢或口径被挑选；让任何人能提交自己的对照，是这份报告可被质疑、也可被证伪的前提。",
    },
    # ── 图表（charts.py）────────────────────────────────────────────
    "charts.title.overhead": {
        LANG_EN: "Framework overhead as a share of end-to-end (concurrency {concurrency})",
        LANG_ZH: "框架开销占端到端比例（并发度 {concurrency}）",
    },
    "charts.xlabel.body_tier": {
        LANG_EN: "Body duration (microseconds, log axis)",
        LANG_ZH: "执行体时长（微秒，对数轴）",
    },
    "charts.ylabel.overhead_ratio": {
        LANG_EN: "framework overhead / end-to-end",
        LANG_ZH: "框架开销 / 端到端",
    },
    "charts.threshold": {
        LANG_EN: "overhead threshold {threshold}",
        LANG_ZH: "开销阈值 {threshold}",
    },
    "charts.scope.overhead": {
        LANG_EN: "Body tiers at concurrency {concurrency} only; ratios hold within a single run",
        LANG_ZH: "仅并发度 {concurrency} 的档位；比值仅在同一次运行内成立",
    },
    "charts.scope.overhead_withheld": {
        LANG_EN: ". The overhead numbers of concurrency {withheld} were withheld (beyond this "
        "machine's parallelism; see the caliber section) and are not charted",
        LANG_ZH: "。并发度 {withheld} 的开销数字已撤下（超出该机器并行能力，见口径章节），"
        "故不在图上",
    },
    "charts.title.throughput": {
        LANG_EN: "Throughput vs concurrency (body tier {tier:g} microseconds)",
        LANG_ZH: "吞吐随并发度的变化（执行体 {tier:g} 微秒档）",
    },
    "charts.xlabel.concurrency": {
        LANG_EN: "Concurrency (log axis)",
        LANG_ZH: "并发度（对数轴）",
    },
    "charts.ylabel.throughput": {
        LANG_EN: "tasks / second",
        LANG_ZH: "任务 / 秒",
    },
    "charts.scope.throughput": {
        LANG_EN: "Body tier {tier:g} microseconds only; throughput holds within a single run",
        LANG_ZH: "仅执行体 {tier:g} 微秒 档；吞吐仅在同一次运行内成立",
    },
    "charts.title.relative": {
        LANG_EN: "Multiple of each baseline (above 1.0 means the framework under test is faster)",
        LANG_ZH: "相对各对照方案的倍数（大于 1.0 即被测框架更快）",
    },
    "charts.ylabel.relative": {
        LANG_EN: "baseline duration / framework-under-test duration",
        LANG_ZH: "对照方案耗时 / 被测框架耗时",
    },
    "charts.tie_line": {
        LANG_EN: "tie line 1.0",
        LANG_ZH: "打平线 1.0",
    },
    "charts.panel.title": {
        LANG_EN: "concurrency {concurrency}",
        LANG_ZH: "并发度 {concurrency}",
    },
    "charts.scope.relative": {
        LANG_EN: "One panel per measured concurrency ({count} in total); multiples hold within "
        "a single run",
        LANG_ZH: "每个所测并发度各一个面板（共 {count} 个）；倍数仅在同一次运行内成立",
    },
    # ── PDF 专属（pdf.py）──────────────────────────────────────────
    "pdf.doc_title": {
        LANG_EN: "zoo-bench performance report",
        LANG_ZH: "zoo-bench 性能报告",
    },
    "pdf.missing_figure": {
        LANG_EN: "[missing figure: {src}]",
        LANG_ZH: "[缺图：{src}]",
    },
    # ── deck 页自身文案（deck.py）──────────────────────────────────
    "deck.title.versioned": {
        LANG_EN: "zoo-framework {version} benchmark",
        LANG_ZH: "zoo-framework {version} 性能评测",
    },
    "deck.title.plain": {
        LANG_EN: "zoo-framework benchmark",
        LANG_ZH: "zoo-framework 性能评测",
    },
    "deck.cover.heading.versioned": {
        LANG_EN: "zoo-framework {version}\nbenchmark",
        LANG_ZH: "zoo-framework {version}\n性能评测",
    },
    "deck.cover.heading.plain": {
        LANG_EN: "zoo-framework\nbenchmark",
        LANG_ZH: "zoo-framework\n性能评测",
    },
    "deck.cover.label": {LANG_EN: "cover", LANG_ZH: "封面"},
    "deck.cover.lead.with_others": {
        LANG_EN: "Same-caliber comparison against {count} baselines: {baselines}. Every number "
        "was re-measured on this machine by zoo-bench and published per tier, including the "
        "tiers the framework under test loses.",
        LANG_ZH: "与 {baselines} 共 {count} 个对照方案的同口径对比；全部数值由 zoo-bench 在本机"
        "重新测量、逐档公开，包括本框架输掉的档位。",
    },
    "deck.cover.lead.no_others": {
        LANG_EN: "The baseline list of this run is empty: only the framework under test was "
        "measured, which cannot support a cross-comparison.",
        LANG_ZH: "本轮的对照方案清单为空：只有被测框架自己的数，不足以支撑横向对比。",
    },
    "deck.cover.source": {
        LANG_EN: "raw data: `{path}`",
        LANG_ZH: "原始数据：`{path}`",
    },
    "deck.cover.source.unrecorded": {
        LANG_EN: "the origin of the raw data is not recorded",
        LANG_ZH: "原始数据出处未记录",
    },
    "deck.cover.notes": {
        LANG_EN: "Opening: same caliber, reproducible - the ground on which every later "
        "number stands.",
        LANG_ZH: "开场：强调同口径、可复现，为后面所有数字建立可信度。",
    },
    "deck.content.suffix": {LANG_EN: " (cont.)", LANG_ZH: "（续）"},
    "deck.content.label.fallback": {LANG_EN: "body", LANG_ZH: "正文"},
    "deck.content.eyebrow.default": {
        LANG_EN: "zoo-bench report",
        LANG_ZH: "zoo-bench 报告",
    },
    "deck.content.source": {
        LANG_EN: "data: {section}",
        LANG_ZH: "数据：{section}",
    },
    "deck.pointer.label": {
        LANG_EN: "{section} - detail pointer",
        LANG_ZH: "{section}·明细指引",
    },
    "deck.pointer.heading": {
        LANG_EN: "the rest of this section's details are in the full report",
        LANG_ZH: "本章其余明细在全文里",
    },
    "deck.pointer.lead": {
        LANG_EN: "For one conclusion per slide, only the first {cap} slides of this section "
        "are shown; the remaining {dropped} slides of per-unit numbers, percentiles and raw "
        "samples sit in the same section of the full report, verbatim checkable.",
        LANG_ZH: "幻灯片为了「一页一个结论」只放了本章的前 {cap} 页；余下 {dropped} 页的逐单元"
        "数值、分位数与原始样本都在全文的同一章节里，逐字可核。",
    },
    "deck.pointer.source": {
        LANG_EN: 'full report: the "{section}" section of report.md in the same directory',
        LANG_ZH: "全文：同目录 `report.md` 的「{section}」章节",
    },
    "deck.pointer.notes": {
        LANG_EN: "only the layout is compressed, never the numbers.",
        LANG_ZH: "被压缩的只有排版，没有数值。",
    },
    "deck.chart.group_tier": {
        LANG_EN: "body tier {tier:g} microseconds",
        LANG_ZH: "执行体 {tier:g} 微秒",
    },
    "deck.chart.group_part": {
        LANG_EN: "{label} ({index}/{total})",
        LANG_ZH: "{label}（{index}/{total}）",
    },
    "deck.chart.eyebrow": {
        LANG_EN: "concurrency {concurrency} - {legend}",
        LANG_ZH: "并发度 {concurrency} · {legend}",
    },
    "deck.legend.latency": {
        LANG_EN: "end-to-end / concurrency",
        LANG_ZH: "端到端 / 并发度",
    },
    "deck.legend.overhead": {
        LANG_EN: "framework overhead",
        LANG_ZH: "框架自身开销",
    },
    "deck.legend.throughput": {LANG_EN: "throughput", LANG_ZH: "吞吐"},
    "deck.chart.chip.subject": {
        LANG_EN: "framework under test",
        LANG_ZH: "被测框架",
    },
    "deck.chart.chip.baseline": {LANG_EN: "baseline", LANG_ZH: "对照方案"},
    "deck.chart.chip.meaning": {
        LANG_EN: "bar length = multiple relative to the framework under test; 1.0 is parity; "
        "longer is worse",
        LANG_ZH: "条形 = 相对被测框架的倍数，1.0 为持平，越长越差",
    },
    "deck.chart.note.bars": {
        LANG_EN: "bar length is only relative; the number beside each bar is the measured value.",
        LANG_ZH: "条形只表达相对长短，每根旁边的数字才是该点的实测值。",
    },
    "deck.chart.note.withheld": {
        LANG_EN: "{count} more cells have no readable overhead and are not charted (see "
        "caliber limitations).",
        LANG_ZH: "另有 {count} 个单元格无可读开销，未入图（见口径局限）。",
    },
    "deck.chart.heading": {
        LANG_EN: "{title} at concurrency {concurrency}",
        LANG_ZH: "{title}：并发度 {concurrency}",
    },
    "deck.chart.source": {
        LANG_EN: 'data: zoo-bench "{title}" at concurrency {concurrency} - per-unit numbers '
        "and percentiles are in the same section of report.md",
        LANG_ZH: "数据：zoo-bench「{title}」· 并发度 {concurrency} · "
        "逐单元数值与分位数见 report.md 的同一章节",
    },
    "deck.stat.label": {LANG_EN: "key takeaway", LANG_ZH: "核心结论"},
    "deck.stat.eyebrow": {
        LANG_EN: "framework overhead share",
        LANG_ZH: "框架自身开销占比",
    },
    "deck.stat.verdict.lowest": {
        LANG_EN: "the framework under test has the lowest share at this tier.",
        LANG_ZH: "该档位被测框架的占比最低。",
    },
    "deck.stat.verdict.lower": {
        LANG_EN: "{count} baselines are lower at this tier: {adapters}.",
        LANG_ZH: "该档位有 {count} 个方案更低：{adapters}。",
    },
    "deck.stat.caption": {
        LANG_EN: "Framework overhead share of {subject} at body tier {tier:g} microseconds.",
        LANG_ZH: "执行体 {tier:g} 微秒 时，{subject} 的框架自身开销占比。",
    },
    "deck.stat.same_tier": {
        LANG_EN: "same-tier comparison: {comparison}",
        LANG_ZH: "同档对比：{comparison}",
    },
    "deck.stat.source": {
        LANG_EN: 'data: zoo-bench "framework overhead share" at concurrency {concurrency}, '
        "body tier {tier:g} microseconds",
        LANG_ZH: "数据：zoo-bench「框架自身开销占比」· 并发度 {concurrency} · "
        "执行体 {tier:g} 微秒 档",
    },
    "deck.stat.notes": {
        LANG_EN: "the largest measured body tier is used: the report's own selection framework "
        'is "how large a task before overhead stops mattering".',
        LANG_ZH: "取所测最大的执行体档位：报告自己的选型框架就是「多大的任务才划算」。",
    },
    "deck.unfavorable.cell": {
        LANG_EN: "concurrency {concurrency} at {tier:g} microseconds",
        LANG_ZH: "并发 {concurrency} · {tier:g} 微秒",
    },
    "deck.unfavorable.card.title": {
        LANG_EN: "{baseline} is faster at {count} tiers",
        LANG_ZH: "{baseline} · {count} 个档位更快",
    },
    "deck.unfavorable.card.body": {
        LANG_EN: 'worst <span class="num">{gap}x</span> (concurrency {concurrency} at '
        "{tier:g} microseconds). All tiers: {cells}",
        LANG_ZH: '最差 <span class="num">{gap}x</span>（并发 {concurrency} · {tier:g} 微秒）。'
        "全部档位：{cells}",
    },
    "deck.unfavorable.label": {
        LANG_EN: "where the subject loses",
        LANG_ZH: "劣势归类",
    },
    "deck.unfavorable.eyebrow": {LANG_EN: "summary", LANG_ZH: "归类"},
    "deck.unfavorable.heading": {
        LANG_EN: "the framework under test is slower at {count} tiers, across {rivals} baselines.",
        LANG_ZH: "被测框架在 {count} 个档位上更慢，来自 {rivals} 个对照方案。",
    },
    "deck.unfavorable.source": {
        LANG_EN: 'data: zoo-bench "published unfavorable data" - per-tier details follow on '
        "later slides and in report.md",
        LANG_ZH: "数据：zoo-bench「公开的不利数据」· 逐档明细见其后的页与 report.md",
    },
    "deck.unfavorable.notes": {
        LANG_EN: "the losses are summarized first, then listed per tier - nothing is cut by "
        "importance.",
        LANG_ZH: "先把劣势归纳清楚，再逐档列出——不按重要性截断。",
    },
    "deck.pointer_page.item.markdown": {
        LANG_EN: "per-unit details (percentiles, spread, raw samples per round): report.md in "
        "the same directory",
        LANG_ZH: "逐单元明细（分位数、离散度、每轮原始样本）：同目录的 `report.md`",
    },
    "deck.pointer_page.item.figures": {
        LANG_EN: "figures (SVG and PNG): figures/ in the same directory; also report.pdf",
        LANG_ZH: "图表（SVG 与 PNG）：同目录的 `figures/`，另见 `report.pdf`",
    },
    "deck.pointer_page.item.archive": {
        LANG_EN: "raw archive: `{path}`",
        LANG_ZH: "原始留档：`{path}`",
    },
    "deck.pointer_page.item.units": {
        LANG_EN: "{total} valid units in this run, {failed} failed",
        LANG_ZH: "本轮有效单元 {total}，失败单元 {failed}",
    },
    "deck.pointer_page.label": {
        LANG_EN: "full report and raw data",
        LANG_ZH: "全文与原始数据",
    },
    "deck.pointer_page.eyebrow": {LANG_EN: "appendix", LANG_ZH: "附录"},
    "deck.pointer_page.heading": {
        LANG_EN: "every slide can be checked verbatim in the full report",
        LANG_ZH: "每一页都可在全文里逐字复核",
    },
    "deck.pointer_page.lead": {
        LANG_EN: "slides are compressed for one conclusion per page; only the layout is "
        "compressed, never the numbers.",
        LANG_ZH: "幻灯片为了「一页一个结论」而压缩了篇幅；被压缩的只有排版，没有数值。",
    },
    "deck.pointer_page.source": {
        LANG_EN: "the details run over a hundred rows; squeezing them into slides would strip "
        "readers of the means to verify.",
        LANG_ZH: "明细有一百多行，压进幻灯片等于让读者失去核对的手段。",
    },
    # ── 语言自身的名字：语言切换链接的标签按惯例用目标语言的本名（英文页上写「中文」，
    #    中文页上写 English）。它是导航不是正文——英文页的 ASCII 门禁豁免这一个元素。
    "lang_name.en": {LANG_EN: "English", LANG_ZH: "English"},
    "lang_name.zh": {LANG_EN: "中文", LANG_ZH: "中文"},
    # ── 站点首页（render/index.py）
    "site.index.title": {
        LANG_EN: "zoo-bench performance reports",
        LANG_ZH: "zoo-bench 性能报告",
    },
    "site.index.tagline": {
        LANG_EN: "Maintained performance evidence for Zoo Framework - every version "
        "re-measured, raw data published, and the tiers where this framework loses reported "
        "as they are.",
        LANG_ZH: "Zoo Framework 的维护中性能证据 —— 每个版本重新测量、公开原始数据，"
        "并如实列出本框架输掉的档位。",
    },
    "site.index.empty": {
        LANG_EN: "No rendered reports yet.",
        LANG_ZH: "还没有任何已渲染的报告。",
    },
    "site.card.measured_at": {LANG_EN: "measured at", LANG_ZH: "测量于"},
    "site.card.units": {LANG_EN: "valid units", LANG_ZH: "有效单元"},
    "site.card.failed_units": {LANG_EN: "failed units", LANG_ZH: "失败单元"},
    "site.card.self_check.passed": {LANG_EN: "self-check passed", LANG_ZH: "自检通过"},
    "site.card.self_check.failed": {LANG_EN: "self-check failed", LANG_ZH: "自检未通过"},
    "site.card.action.report": {LANG_EN: "report", LANG_ZH: "报告"},
    "site.card.action.markdown": {LANG_EN: "full text (Markdown)", LANG_ZH: "全文（Markdown）"},
    "site.compare.title": {LANG_EN: "Cross-version comparisons", LANG_ZH: "跨版本对比"},
    "site.compare.shared_units": {LANG_EN: "shared units", LANG_ZH: "共有单元"},
    "site.compare.both_passed": {
        LANG_EN: "both sides passed self-check",
        LANG_ZH: "两侧自检均通过",
    },
    "site.compare.one_failed": {
        LANG_EN: "one side failed self-check - this comparison does not count",
        LANG_ZH: "有一侧自检未通过——本对比不作数",
    },
    "site.compare.action": {LANG_EN: "compare", LANG_ZH: "对比"},
    "site.compare.unlabelled": {
        LANG_EN: "cross-version comparison (side labels unavailable)",
        LANG_ZH: "跨版本对比（两侧标签读不到）",
    },
    # ── 跨版本对比页（render/compare.py 的块层文案）
    "compare.heading": {
        LANG_EN: "zoo-bench version comparison: {before} -> {after}",
        LANG_ZH: "zoo-bench 版本对比：{before} -> {after}",
    },
    "compare.side.line": {
        LANG_EN: "{label}: framework {framework}, {unit_count} valid units, {mark}",
        LANG_ZH: "{label}：框架 {framework}，有效单元 {unit_count} 个，{mark}",
    },
    "compare.side.passed": {LANG_EN: "self-check passed", LANG_ZH: "自检通过"},
    "compare.side.failed": {
        LANG_EN: "**self-check failed - the numbers on this side cannot be trusted**",
        LANG_ZH: "**自检未通过——这一侧的数字不可信**",
    },
    "compare.self_check_failed.note": {
        LANG_EN: "**One side failed self-check, so this comparison does not count**: "
        "self-check covers body-tier deviation, cross-tier overhead consistency and adapter "
        "equivalence; its failure means that side's numbers themselves cannot be trusted.",
        LANG_ZH: "**有一侧的自检未通过，本对比不作数**：自检覆盖执行体档位偏差、跨档位开销"
        "一致性与适配器等价性，它失败意味着那一侧的数字本身不可信。",
    },
    "compare.section.environment": {
        LANG_EN: "Run environment and measurement calibers",
        LANG_ZH: "运行环境与读数口径",
    },
    "compare.section.only_in_one": {
        LANG_EN: "Units present on only one side",
        LANG_ZH: "只在一侧出现的单元",
    },
    "compare.only_in_one.note": {
        LANG_EN: "These units exist on only one side, so the difference cannot be attributed "
        "- **list them rather than silently dropping them via the intersection**.",
        LANG_ZH: "这些单元不是两侧都有，差异无法归因——**列出而不是取交集悄悄丢掉**。",
    },
    "compare.only_in_one.none": {
        LANG_EN: "The units on both sides match exactly.",
        LANG_ZH: "两侧的单元完全一致。",
    },
    "compare.section.overhead_ratio": {
        LANG_EN: "Changes in framework overhead share",
        LANG_ZH: "框架开销占比的变化",
    },
    "compare.withheld.note": {
        LANG_EN: '**Rows marked "unreadable"**: {note}. These groups get no "change" value on '
        "either side - it would compare the difference of two unreadable numbers; end-to-end "
        "and throughput are in the absolute-durations table below and in each version's report.",
        LANG_ZH: "**标为「不可读」的行**：{note}。这些组的两侧「变化」都不给——它比的是两个"
        "不可读的数之差；端到端与吞吐见下面的绝对耗时表与各版本的报告。",
    },
    "compare.header.adapter": {LANG_EN: "adapter", LANG_ZH: "适配器"},
    "compare.header.item": {LANG_EN: "item", LANG_ZH: "项"},
    "compare.header.change": {LANG_EN: "change", LANG_ZH: "变化"},
    "compare.header.present_in": {LANG_EN: "present in", LANG_ZH: "出现在"},
    "compare.section.speedup": {
        LANG_EN: "Changes in multiples versus each baseline",
        LANG_ZH: "相对各对照方案的倍数的变化",
    },
    "compare.section.absolute": {
        LANG_EN: "Absolute durations (not comparable across runs)",
        LANG_ZH: "绝对耗时（不可跨运行比较）",
    },
    "compare.cell.microseconds": {
        LANG_EN: "{value} microseconds",
        LANG_ZH: "{value} 微秒",
    },
    # ── 对比的计算层（compare.py 写进对比结果的散文，随 lang 落值）
    "compare.absolute_note": {
        LANG_EN: "absolute durations are not comparable across runs: read them for magnitude "
        "only, never for version differences",
        LANG_ZH: "绝对耗时不可跨运行比较：仅用于看量级，不要用它读版本差异",
    },
    "compare.metric.overhead_ratio": {
        LANG_EN: "framework overhead as a share of end-to-end; a drop means overhead is "
        "amortizing better",
        LANG_ZH: "被测框架的框架开销占端到端比例；下降即开销摊薄得更好",
    },
    "compare.metric.speedup_vs_baseline": {
        LANG_EN: "baseline median / framework-under-test median; a rise means the framework "
        "under test got relatively faster",
        LANG_ZH: "对照方案中位数 / 被测框架中位数；上升即被测框架相对更快",
    },
    "compare.direction.missing": {LANG_EN: "missing", LANG_ZH: "缺失"},
    "compare.direction.zero_baseline": {
        LANG_EN: "baseline is 0; ratio undefined",
        LANG_ZH: "基准为 0，无法求比值",
    },
    "compare.direction.flat": {LANG_EN: "flat", LANG_ZH: "持平"},
    "compare.direction.up": {LANG_EN: "up", LANG_ZH: "上升"},
    "compare.direction.down": {LANG_EN: "down", LANG_ZH: "下降"},
    "compare.direction.unreadable": {LANG_EN: "unreadable", LANG_ZH: "不可读"},
    "compare.environment.same": {
        LANG_EN: "the two runs ran on identical environments",
        LANG_ZH: "两次运行的环境一致",
    },
    "compare.environment.different": {
        LANG_EN: "**The two runs ran on different environments**: version differences are "
        "mixed with machine differences and cannot be attributed to the version alone. The "
        "relative measures below are insensitive to overall machine speed, but still read "
        "them alongside this.",
        LANG_ZH: "**两次运行的环境不同**：版本差异与机器差异混在一起，不能只归因于版本。"
        "下面的相对量对整体快慢不敏感，但仍应结合本项判断",
    },
    "compare.generation.missing": {
        LANG_EN: "at least one side's archive lacks the drive generation (recorded by "
        "zoo-bench's environment self-description), so whether the two sides' reading calibers "
        "match cannot be judged - **complete the environment self-description before reading "
        "the differences**",
        LANG_ZH: "至少一侧的留档里没有驱动面世代（该字段由 zoo-bench 的环境自述记下），"
        "无法判断两侧的读数口径是否一致——**先补齐环境自述再看差异**",
    },
    "compare.generation.same": {
        LANG_EN: 'both sides measured with a generation "{generation}" drive surface; the '
        "reading calibers match",
        LANG_ZH: "两侧同为「{generation}」代驱动面，读数口径一致",
    },
    "compare.generation.different": {
        LANG_EN: "**The two sides use different drive-surface mechanisms** ({before_gen} vs "
        "{after_gen}): one generation takes the completion signal from the framework's "
        "internal callback, the other from the framework's own result responder, and both "
        "costs are counted in the framework's end-to-end time. Part of the difference between "
        "the sides is therefore **the cost of the drive mechanism itself** - it is part of "
        "this version change, but must not be read as performance improvement in general.",
        LANG_ZH: "**两侧的驱动面机制不同**（{before_gen} 与 {after_gen}）："
        "完成信号一代取自框架内部回调、一代取自框架自身的结果响应器，而两者的开销都计入"
        "被测框架的端到端。故两侧的差异里**有一部分是驱动机制自身的成本**——它是这次版本"
        "变化的一部分，但不能整体读成一般意义的性能改进",
    },
    # ── HTML 序列化器（render/html.py，站点首页与对比页共用）
    "html.toc.title": {LANG_EN: "On this page", LANG_ZH: "本页内容"},
    "html.default_title": {
        LANG_EN: "zoo-bench performance report",
        LANG_ZH: "zoo-bench 性能报告",
    },
    # ── CLI 写进模型的发布豁免注：它随模型进报告，两种语言的产物各要一份自己语言的
    "report.caveat.exemption.kind": {
        LANG_EN: "publication exemption",
        LANG_ZH: "发布豁免",
    },
    "report.caveat.exemption.text": {
        LANG_EN: "this run contains neither a tier where the framework under test is at a "
        "disadvantage nor a tied tier - every tier shows it ahead. Published via an explicit "
        "--allow-empty-unfavorable exemption. The exemption is an audit trail: when the same "
        "situation recurs, check tier selection and measurement health first instead of "
        "exempting out of habit.",
        LANG_ZH: "本轮测量既不含被测框架处于劣势的档位、也不含分不出胜负的档位，"
        "经 --allow-empty-unfavorable "
        "显式豁免发布。该豁免是审计线索：再次出现同样情形时应先检查档位选择与"
        "测量是否正常，而不是习惯性地豁免。",
    },
    # ── 测量侧的自述散文（design D8）──
    # 这些文本由**测量进程**写进留档，渲染时才按语言解析。留档因此是语言无关的数据：
    # 同一份留档能出两种语言的报告，而不是把当年的语言冻结进去。
    "adapters.notes.thread_pool": {
        LANG_EN: "concurrent.futures.ThreadPoolExecutor, pool size = concurrency; threads are "
        "reused, with no task-queue semantics",
        LANG_ZH: "concurrent.futures.ThreadPoolExecutor，池大小 = 并发度；线程复用、无任务队列语义",
    },
    "adapters.notes.bare_thread": {
        LANG_EN: "one threading.Thread per task, no pooling and no reuse; thread creation cost "
        "counts toward end-to-end. Bodies' self-reported timings are collected with a lock-free "
        "list.append (atomic under the GIL) - **instrumentation must not charge one tier a cost "
        "other tiers do not pay**, or the comparison becomes a comparison of instrumentation",
        LANG_ZH: "每任务派生一个 threading.Thread，无池化、无复用；线程创建成本计入端到端。"
        "收集执行体自报耗时用无锁的 list.append（GIL 下本身原子）——"
        "**插桩不得给某一档加别档没有的成本**，否则对照就成了插桩的对照",
    },
    "adapters.notes.asyncio_pool": {
        LANG_EN: "the asyncio event loop dispatches **synchronous** bodies (equivalent to "
        "loop.run_in_executor), not native coroutine concurrency; the difference from a thread "
        "pool is the event loop's own cost",
        LANG_ZH: "asyncio 事件循环调度**同步**执行体（等价于 loop.run_in_executor），"
        "非原生协程并发；与线程池的差别是事件循环自身开销",
    },
    "adapters.notes.process_pool": {
        LANG_EN: "ProcessPoolExecutor: pickling the body and its return value across processes "
        "counts toward end-to-end, so this tier is not directly comparable to the multi-threaded "
        "tiers",
        LANG_ZH: "ProcessPoolExecutor：执行体与返回值跨进程序列化的成本计入端到端，"
        "与多线程档位不可直接等价比较",
    },
    "adapters.notes.apscheduler_pool": {
        LANG_EN: "APScheduler's scheduled-job model, including job registration and "
        "trigger-decision cost; the library swallows exceptions raised inside jobs, so this tier "
        "re-raises them during drain. **Caliber note**: the library offers no job-completion "
        "notification, so the completion signal comes from this tier's Condition, and the cost "
        "of one notification per task counts toward its end-to-end",
        LANG_ZH: "APScheduler 的定时作业模型，含作业登记与触发判定成本；"
        "该库会吞掉作业内异常，本档已在 drain 时重新抛出。"
        "**口径说明**：该库不提供作业完成通知，故完成信号由本档的 Condition 给出，"
        "每任务一次通知的开销计入其端到端",
    },
    "adapters.notes.celery_pool": {
        LANG_EN: "Celery is a distributed approach: it needs a broker and separate worker "
        "processes, and both submission and result return cross process boundaries, so it "
        "differs from in-process dispatch and is not directly comparable; its numbers do not "
        "feed the crossing-point derivation (design D3)",
        LANG_ZH: "Celery 是分布式方案：需要 broker 与独立 worker 进程，投递与结果回传都跨进程，"
        "与进程内派发架构不同、不可直接对标；其数字不参与交叉点推导（design D3）",
    },
    "adapters.drive_level.zoo": {
        LANG_EN: "dispatch layer: drives BaseWaiter.execute_service() directly (thread-pool mode) "
        "instead of Master's full run loop - the latter's service loop fires once per second, "
        "which would lift the latency scale from microseconds to seconds. **One scheduling round "
        "per submitted task**, so that whole round's bookkeeping is charged to this single task; "
        "Master's usage serves several workers per round, which would spread that cost over tasks",
        LANG_ZH: "调度派发层：直接驱动 BaseWaiter.execute_service()（线程池模式），"
        "不走 Master 的完整运行循环——后者的服务循环每秒一次，会把延迟量级从微秒抬到秒。"
        "**每提交一个任务走一次调度轮**，故该轮的全部簿记都计在这一个任务头上；"
        "Master 的用法是每轮服务多个 worker，那份开销本会摊到多个任务上",
    },
    "adapters.notes.zoo": {
        LANG_EN: "end-to-end stops at the worker completing and being deregistered from the "
        "in-flight table; the framework's later result delivery splits by generation: the "
        "previous generation's delivery path is broken (dispatch looks reactors up by "
        "reactor_name rather than by topic, so every call raises), and the completion signal can "
        "only come from the framework's internal callback; the current generation has that fixed, "
        "and the completion signal comes from the framework's own result reactor. **Caliber "
        "note**: the completion signal comes from this tier's Condition, and the cost of one "
        "notification per task counts toward its end-to-end; a bias in that direction makes the "
        "framework under test look slower, i.e. it works against it rather than for it",
        LANG_ZH: "端到端终点为「worker 完成并在飞表注销」；框架其后的结果投递按世代分流："
        "上一代的投递路径是坏的（dispatch 按 reactor_name 而非主题查响应器，每次调用必抛），"
        "完成信号只能取自框架内部回调；当前代该处已修好，完成信号取自框架自己的结果响应器。"
        "**口径说明**：完成信号由本档的 Condition 给出，每任务一次通知的开销计入其端到端；"
        "该方向的偏差使被测框架看起来更慢，即对被测框架不利而非有利",
    },
    "generations.label.previous": {
        LANG_EN: "previous-generation drive surface (workers is a writable attribute; the "
        "completion signal comes from the framework's internal callback)",
        LANG_ZH: "上一代驱动面（workers 为可写属性，完成信号取自框架内部回调）",
    },
    "generations.label.current": {
        LANG_EN: "current-generation drive surface (scheduling core + scheduling model; the "
        "completion signal comes from the result reactor)",
        LANG_ZH: "当前代驱动面（调度内核 core + 调度模型 model，完成信号取自结果响应器）",
    },
    "generations.capability.model_name_param": {
        LANG_EN: "constructor accepts model_name",
        LANG_ZH: "构造参数接受 model_name",
    },
    "generations.capability.workers_assignable": {
        LANG_EN: "workers is assignable",
        LANG_ZH: "workers 可赋值",
    },
    "generations.capability.core_set_workers": {
        LANG_EN: "scheduling core core.set_workers is callable",
        LANG_ZH: "调度内核 core.set_workers 可调用",
    },
    "generations.capability.core_is_inflight": {
        LANG_EN: "scheduling core core.is_inflight is callable",
        LANG_ZH: "调度内核 core.is_inflight 可调用",
    },
    "generations.capability.shutdown": {
        LANG_EN: "shutdown entry point is callable",
        LANG_ZH: "停机入口 shutdown 可调用",
    },
    "generations.capability.resource_pool": {
        LANG_EN: "resource_pool attribute is present",
        LANG_ZH: "资源池属性 resource_pool 存在",
    },
    "generations.capability.worker_running_callback": {
        LANG_EN: "completion callback worker_running_callback is callable",
        LANG_ZH: "完成回调 worker_running_callback 可调用",
    },
    "generations.observed.absent": {LANG_EN: "absent", LANG_ZH: "不存在"},
    "generations.observed.assignable": {
        LANG_EN: "present and accepts assignment",
        LANG_ZH: "存在且接受赋值",
    },
    "generations.observed.rejected": {
        LANG_EN: "present but rejects assignment ({exception})",
        LANG_ZH: "存在但拒绝赋值（{exception}）",
    },
    "generations.observed.accepted": {LANG_EN: "accepted", LANG_ZH: "接受"},
    "generations.observed.not_accepted": {LANG_EN: "not accepted", LANG_ZH: "不接受"},
    "generations.observed.callable": {LANG_EN: "callable", LANG_ZH: "可调用"},
    "generations.observed.not_callable": {LANG_EN: "not callable", LANG_ZH: "不可调用"},
    "generations.observed.present": {LANG_EN: "present", LANG_ZH: "存在"},
    "generations.mismatch": {
        LANG_EN: "the framework under test's drive surface matches none of the generations this "
        "harness knows, so it cannot be driven.\nprobed shapes:\n{detected}\n"
        "current-generation requirements: {current_required}\n"
        "previous-generation requirements: {previous_required}\n"
        "it will not fall back to any generation's default path - the numbers that would produce "
        "look normal while corresponding to a drive mechanism the framework does not actually have.",
        LANG_ZH: "被测框架的驱动面与本 harness 已知的任何一代都不匹配，无法驱动。\n"
        "探测到的形态：\n{detected}\n"
        "当前代要求：{current_required}\n"
        "上一代要求：{previous_required}\n"
        "不会退回某一代的默认路径去测——那样得到的数字看起来正常，"
        "却对应框架并不具备的驱动方式。",
    },
    "generations.mismatch.detected_line": {
        LANG_EN: "  - {capability}: {observed}",
        LANG_ZH: "  - {capability}：{observed}",
    },
    "runner.relative.comparison_note": {
        LANG_EN: "a ratio above 1 means the baseline is slower than the framework under test; "
        "valid only within a single run",
        LANG_ZH: "比值 >1 表示该对照方案比被测框架慢；仅在同一次运行内成立",
    },
    "runner.relative.note": {
        LANG_EN: "within-run relative ratios are the main body of selection evidence; they are "
        "insensitive to this run's overall drift",
        LANG_ZH: "同一次运行内的相对比，是选型证据的主体；对本次运行的整体漂移不敏感",
    },
    "runner.self_check.overhead_nonpositive": {
        LANG_EN: "the smallest tier's overhead is non-positive, so the ratio criterion does not "
        "apply; the raw value is recorded for human reading",
        LANG_ZH: "最短档开销非正，比值判据不适用，已记录原始值供人工判读",
    },
    "runner.self_check.overhead_polluted": {
        LANG_EN: "concurrency above the lowest tier: end-to-end includes queueing wait, so the "
        "per-task overhead obtained by dividing by concurrency rises - that is queueing, not a "
        "miscomputed cost, so this tier is excluded from the verdict (raw values kept faithfully)",
        LANG_ZH: "并发度高于最低档：端到端里含排队等待，除以并发度得到的每任务开销随之上升"
        "——那是排队不是成本错算，故不参与判定（原值已如实记录）",
    },
    "runner.self_check.body_oversubscribed": {
        LANG_EN: "concurrency above the lowest tier: wall time is affected by scheduling delay, "
        "and a deviation growing with concurrency means the environment is oversubscribed rather "
        "than mis-calibrated (raw values kept faithfully)",
        LANG_ZH: "并发度高于最低档：墙钟受调度推迟影响，偏差随并发度增长说明是环境超订而非"
        "校准错误（原值已如实记录）",
    },
    "runner.self_check.body_too_short": {
        LANG_EN: "tier too short: a window of tens of microseconds on a shared runner is "
        "dominated by scheduling granularity, which is not enough to decide (raw values kept "
        "faithfully)",
        LANG_ZH: "档位过短：几十微秒的窗口在共享 runner 上受调度颗粒度支配，不足以判定"
        "（原值已如实记录）",
    },
}

#: `<html lang>` 属性值（deck 与站点页共用）。zh 的 BCP-47 值带地区：报告是简体中文。
HTML_LANGS: dict[str, str] = {LANG_EN: "en", LANG_ZH: "zh-CN"}


def other_language(lang: str) -> str:
    """另一语言的代码——语言切换链接指向的目标。只有两种语言，故可互推。"""
    return LANG_ZH if lang == LANG_EN else LANG_EN


def template_fields(template: str) -> set[str]:
    """模板里用到的字段名（`str.format` 的具名占位符）。"""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def has_translation(key: str) -> bool:
    """键是否在目录里（渲染侧区分「翻译」与「旧留档散文原样透传」的依据）。"""
    return key in MESSAGES


def t(key: str, lang: str, **params: Any) -> str:
    """取一条文案。

    **字段集合与调用点必须严格一致**：少给参数会填出 `{foo}`、多给参数说明调用点还在用旧签名，
    两者都不报错地渲染出错误文字，故这里都抛。

    Args:
        key: 文案键。
        lang: 目标语言。
        **params: 模板参数。

    Returns:
        该语言下的文案。

    Raises:
        MissingTranslation: 键不存在、该语言没有译文、或模板参数与模板对不上。
    """
    entry = MESSAGES.get(key)
    if entry is None:
        raise MissingTranslation(f"没有这个文案键：{key}")
    text = entry.get(lang)
    if not text:
        raise MissingTranslation(f"文案键 {key} 缺 {lang} 的译文")
    expected = template_fields(text)
    if expected != set(params):
        raise MissingTranslation(
            f"文案键 {key}（{lang}）的参数对不上："
            f"缺 {sorted(expected - set(params))}、多 {sorted(set(params) - expected)}"
        )
    return text.format(**params)


def missing_translations() -> list[str]:
    """列出**两种语言没都填齐**的键（空值也算缺）。

    供用例调用——"漏了一条翻译"的表现是英文页里冒出一句中文，肉眼在长报告里极难发现。
    """
    gaps: list[str] = []
    for key, entry in MESSAGES.items():
        gaps.extend(f"{key}[{lang}]" for lang in LANGS if not entry.get(lang))
        stray = set(entry) - set(LANGS)
        if stray:
            gaps.append(f"{key} 有多余语言：{sorted(stray)}")
    return sorted(gaps)
