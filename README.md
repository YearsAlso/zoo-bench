# zoo-bench

[English](#english) | [中文](#中文)

[![bench](https://github.com/YearsAlso/zoo-bench/actions/workflows/bench.yml/badge.svg)](https://github.com/YearsAlso/zoo-bench/actions/workflows/bench.yml)

> **The maintained performance evidence for [Zoo Framework](https://github.com/YearsAlso/zoo-framework).**
> Zoo Framework 的**维护中**性能证据 —— 每个版本重新测量、公开原始数据、并如实列出本框架输掉的档位。

**📊 Live report / 线上报告：<https://yearsalso.github.io/zoo-bench/> (English) · [中文](https://yearsalso.github.io/zoo-bench/zh/)**

---

<a name="english"></a>
## 🇬🇧 English

### What this is

A long-lived benchmark harness that measures Zoo Framework against hand-written baselines, the
standard-library concurrency models, and a same-ecosystem scheduler — then publishes the result as a
public report in **HTML / Markdown / PDF**.

### What it answers — and what it does not

| Answers | Does not answer |
|---|---|
| How does the framework compare to bare threads, `ThreadPoolExecutor`, `asyncio`, `ProcessPoolExecutor` and `APScheduler`, and **at which task durations**? | Whether a given change regressed performance. That would be a CI gate; this publishes a report. |
| **Where does the framework lose?** — published explicitly, not buried. | How it compares to Rust. That was settled as *no-go* in the framework repo's [`bench/`](https://github.com/YearsAlso/zoo-framework/tree/dev/bench). |
| What does the framework's own overhead cost per task, and at what task duration does it stop dominating? | What the framework's *scheduling semantics* cost — see below. |
| **Where does that overhead go?** — decomposed into submit path / handoff / body / completion, **segment by segment against each baseline** | What to change. The report names the segment holding the excess and the framework seams it wrapped; the fix itself is a framework-side decision. |

**The scheduling-semantics dimension is measured per framework generation, and its conclusion is
scoped to the generation it was derived on.** On the older generation (`zoo-framework==0.6.0`) it
reads *not measurable*, with per-item evidence: timeout enforcement exists but its action is
commented out, and the event layer cannot execute a reactor at all. On the current generation
(`0.7.1b0`) the dispatch-layer timeout **is** enforced — the worker is reaped near the configured
`runTimeout` instead of at the body's natural end — so the dimension yields a usable item. Where a
per-item claim and its `file:line` coordinates come from the other generation, the report says
*not re-checked on this one* rather than copying them over. That is a **conclusion reached by
probing**, not an omission — the report distinguishes "measured and found unmeasurable" from
"never measured".

**Which generations are supported is decided by capability probing, not by version strings.** The
installed framework's drive surface is inspected (is `workers` assignable? does `core.set_workers`
exist? is `shutdown` callable?) and the harness picks the matching driving path. Version strings
are deliberately not consulted: the published `0.7.1b0` writes `0.7.1-beta` into its own metadata,
and the numbers of future generations cannot be known in advance. If the drive surface matches no
known generation, the harness **fails loudly** and lists what it probed — it never quietly falls
back to one generation's path and measures something the framework does not actually do.

### How to read the report

- **Relative numbers inside a single run are the evidence.** Absolute durations are labelled
  *not comparable across runs*, because CI runners are shared VMs and the drift is large enough to
  swamp a version difference.
- Three formats, **one source**: the HTML page is a **deck** (1920x1080, one conclusion per slide,
  values encoded as bars), Markdown is the diffable full text, PDF is the distributable document.
  The deck's prose comes from the same block list as the other two and its bars from the same report
  model, so the three cannot disagree — and the deck **keeps an explicit slide for the tiers where
  this framework loses**, paginated in full rather than truncated to fit.
- **The site is bilingual**: English at the root, Chinese under `zh/`, each page linking to the
  other in its header. Report, index and comparison pages all exist in both, with figures and PDF
  rendered per language — the two come from the same run, so the numbers cannot disagree either.
- **The raw data is public**: `results/<framework-version>/<timestamp>.json` in this repository,
  containing **per-round samples** (not just aggregates), so the aggregation itself can be audited.
- **Where the overhead goes** is itself a report dimension: per-task end-to-end split into four
  segments, compared segment-by-segment against each baseline. It samples a small fixed set of
  tiers and concurrencies (it is a diagnosis, not the headline evidence) and publishes the cost of
  its own instrumentation so readers can discount it.
- Version over version: `zoo-bench compare <v1> <v2>`. The site also publishes a **comparison page
  for each adjacent pair in `matrix.yaml`**, so "what changed since the previous version" is
  readable online rather than only from the CLI.

### Run it locally

```bash
uv sync                                          # or: pip install -e ".[dev]"
zoo-bench run                                    # measure every unit in matrix.yaml, archive raw data
zoo-bench render --framework 0.6.0 --out site/0.6.0
zoo-bench index --site site
zoo-bench compare 0.6.0 0.7.1b0 --out site/compare
```

- **`matrix.yaml` is the single source of truth** for which framework versions, adapters,
  concurrency levels and body tiers are measured. Omitting the `adapters` key means *every
  registered adapter* — so the framework under test cannot be forgotten.
- **CJK fonts are required for the Chinese products only.** Charts need any CJK font; **the PDF
  needs a TrueType-outline one**. `fonts-noto-cjk` is CFF-outline and **reportlab cannot embed it** —
  install `fonts-wqy-zenhei` too (CI does). A missing font fails loudly rather than producing a
  report full of boxes; the English products use matplotlib's built-ins and never look for one.

### Add your own comparison

Implement `zoo_bench.adapters.BaseAdapter` (`setup` / `submit` / `drain` / `teardown`), register it,
or list your module path under `extra_modules` in `matrix.yaml` — **no file in this repository needs
to change**. Your implementation gets the same equivalence check as the built-ins (submit N bodies,
assert every one ran exactly once); anything that fails it is not trusted.

That openness is deliberate. A report written *and measured* by the framework's own maintainer
deserves suspicion; being able to submit your own baseline is what makes it falsifiable.

### Measurement calibers

These three are the entire basis for trusting the numbers:

1. **Body duration is instrumented inside the same run** — framework overhead = end-to-end − that,
   never a subtraction against a separately measured run.
2. **Warm-up rounds are discarded** before statistics; sample count = measured rounds × concurrency.
3. **Absolute durations are not comparable across runs** — only same-run relative quantities are.

On top of that: every measurement unit runs in **its own process**; each adapter is verified
**equivalent** before its data is used; and two self-check conditions (concurrency above the
machine's real capacity, and very short body tiers) are **reported but not gated** — there they
measure queueing and scheduling granularity, not the framework.

### Layout

```
src/zoo_bench/     harness: adapters/ runner/ workloads/ render/ metrics/ semantics/ attribution/ cli
matrix.yaml        what to measure
results/           archived raw data (committed — public and version-addressed)
openspec/specs/    authoritative specs (three capabilities)
openspec/changes/archive/   the change that built this: proposal / design D1–D12 / deltas / tasks
```

### Known limits and costs

- At concurrency above the machine's capacity, overhead figures **include queueing**; the report
  names those groups and withholds their crossing points rather than letting a queueing number
  drive the headline.
- **Framework overhead is not published above the machine's parallelism.** At those levels each
  body's self-reported duration carries the scheduler's descheduling wait, so subtracting it from
  "wall clock ÷ concurrency" is meaningless — measured at −913.8% for 64-way work on a 4-core
  runner. End-to-end, throughput and the bodies' own numbers stay in the report; the withheld
  groups are named, with the reason.
- On CI this runs on a shared `ubuntu-latest` runner: relative comparisons hold, absolute numbers
  drift between runs.
- Every push to `main` commits roughly **2.6 MB per measured target** (the files are JSON, so git
  stores them compressed to roughly a quarter of that) — four targets is ~10 MB.
- Any push to `main` — even docs-only — triggers a full measurement run whose length grows with the
  number of targets (measured: 12.7 minutes for three).
- **Git LFS is deliberately not used for `results/`.** The largest file is ~2.8 MB (GitHub's warning
  threshold is 50 MB), a push adds ~1.9 MB of compressed history, and LFS would make the raw data
  require tooling to read — the opposite of "publicly auditable". Revisit if the repo passes
  roughly 1 GB or the archives outgrow a single commit.
- Comparisons against Rust are out of scope here; see the framework repo's `bench/DECISION.md`.

---

<a name="中文"></a>
## 🇨🇳 中文

### 这是什么

一份**长期维护**的基准 harness：把 Zoo Framework 与手写基线、标准库的并发模型、以及同生态的
调度库放在一起测量，并把结果发布成公开报告（**HTML / Markdown / PDF** 三种格式）。

### 它回答什么，不回答什么

| 回答 | 不回答 |
|---|---|
| 相对裸线程、`ThreadPoolExecutor`、`asyncio`、`ProcessPoolExecutor`、`APScheduler`，本框架快不快，**以及在哪些任务时长上快** | 某次改动有没有造成性能回退。那是 CI 门禁该做的事；这份东西出的是报告 |
| **本框架在哪里输** —— 明确列出，不藏在脚注里 | 相对 Rust 如何。那个问题已在框架仓库的 [`bench/`](https://github.com/YearsAlso/zoo-framework/tree/dev/bench) 里判定为 *no-go* |
| 框架自身开销每任务多少钱，以及任务长到多少时它不再占主导 | 框架的**调度语义**值多少钱 —— 见下 |
| **这些开销花在哪** —— 拆成提交侧 / 手交 / 执行体 / 回程，并**与各对照方案逐段对照** | 该改成什么。报告指出超出落在哪一段、以及为此包装了框架的哪些入口；怎么改是框架侧的决定 |

**调度语义这一维按框架世代分别测量，结论只对它得出的那一代成立。** 在上一代
（`zoo-framework==0.6.0`）上它读作*不可测*，并逐项给出证据：超时的执行动作是注释掉的、
事件层根本执行不了反应器。在当前代（`0.7.1b0`）上，派发层的超时**是生效的**——worker 会在
配置的 `runTimeout` 附近被摘除，而不是等到执行体自然结束——故该维产出一个可用项。若某一项的
结论与它的 `file:line` 坐标来自另一代，报告会写明*在当前代未复核*，而不是照抄过来。这是
**探查得出的结论而非遗漏**——报告区分"测了发现不可测"与"从未测过"两种状态。

**支持哪些世代由能力探测决定，不靠版本号字符串。** 装上框架后先看它的驱动面（`workers` 可
赋值吗？`core.set_workers` 在吗？`shutdown` 可调用吗？），再按匹配到的那一代选驱动路径。刻意
不看版本号：已发布的 `0.7.1b0` 在自己的元数据里写的是 `0.7.1-beta`，而未来世代的版本号无从
预知。若驱动面与已知的任何一代都不匹配，harness 会**明确失败**并列出探测到了什么——不会悄悄
退回某一代的路径，去测一件框架实际上并没有做的事。

### 怎么读这份报告

- **同一次运行内的相对量才是证据。** 绝对耗时标注为*不可跨运行比较*——CI runner 是共享
  虚拟机，其漂移足以淹没版本差异。
- 三种格式、**同一份来源**：HTML 页是 **deck**（1920x1080，一页一个结论，数值以条形编码），
  Markdown 是可 diff 的全文，PDF 是可分发的文档。deck 的文字与另两份出自同一份块列表、条形出自
  同一份报告模型，故三者不可能互相矛盾——且 deck **必有一页列出本框架输掉的档位**，分页续排到
  底而不是为版面截断。
- **站点是双语的**：英文在根路径、中文在 `zh/` 子目录，每页页头互链。报告页、首页、对比页两种
  语言各一份，图表与 PDF 也按语言各出一份——两份出自同一次运行，数字同样不可能对不上。
- **原始数据是公开的**：本仓库的 `results/<框架版本>/<时间戳>.json`，含**逐轮原始样本**
  （不只是聚合值），所以聚合本身也可以被审计。
- **开销花在哪**本身就是报告里的一个维度：每任务端到端拆成四段，并与各对照方案**逐段对照**。
  它只抽样固定的一小批档位与并发度（它是诊断，不是头条证据），并把自己插桩的成本一并发布，
  让读者可以如实折价。
- 跨版本：`zoo-bench compare <v1> <v2>`。站点上还会发布 **`matrix.yaml` 里每一对相邻目标的
  对比页**，所以"比上一版变了什么"在网上就能读到，不必只靠命令行。

### 在本地跑

```bash
uv sync                                          # 或：pip install -e ".[dev]"
zoo-bench run                                    # 测量 matrix.yaml 里的全部单元并留档原始数据
zoo-bench render --framework 0.6.0 --out site/0.6.0
zoo-bench index --site site
zoo-bench compare 0.6.0 0.7.1b0 --out site/compare
```

- **`matrix.yaml` 是唯一真源**：测哪些框架版本、哪些适配器、哪些并发度与执行体档位，都写在
  那里。**省略 `adapters` 键即"全部已登记适配器"**——被测对象因此不可能被漏掉。
- **中文产物需要中文字体。** 图表用任何中文字体即可；**PDF 另需 TrueType 轮廓的字体**。
  `fonts-noto-cjk` 是 CFF 轮廓、**reportlab 不能嵌**——要一并装 `fonts-wqy-zenhei`（CI 就是
  这么做的）。缺字体时会明确失败，而不是产出一份满是方框的报告；英文产物用 matplotlib 内置
  字体，不会去找中文字体。

### 加入你自己的对照

实现 `zoo_bench.adapters.BaseAdapter` 的四个方法（`setup` / `submit` / `drain` / `teardown`），
登记它，或在 `matrix.yaml` 的 `extra_modules` 里列出你的模块路径——**无需改动本仓库任何文件**。
你的实现会与内置档位受到同一套等价性验证（提交 N 个执行体、断言全部执行且各执行一次），
不通过的不会被采信。

这条路径是**刻意开放**的：一份由被测框架维护者撰写、且测量被测框架的报告，读者有理由怀疑
对照被写慢或口径被挑选；让任何人能提交自己的对照，是它可被质疑、也可被证伪的前提。

### 测量口径

以下三条是这些数字可信度的**全部依据**：

1. **执行体耗时在同一次运行内埋点** —— 框架开销 = 端到端 − 它，**不做跨运行减法**。
2. **预热轮不入统计**，采样数 = 正式轮数 × 并发度。
3. **绝对耗时不可跨运行比较**，只有同一次运行内的相对量可以。

此外：每个测量单元跑在**独立进程**里；每个适配器在被采信前都通过**等价性验证**；两条自检
条件（并发度超出机器实际容量、执行体档位过短）**只报告不判否**——在那两处量到的是排队与
调度颗粒度，不是框架。

### 目录结构

```
src/zoo_bench/     harness：adapters/ runner/ workloads/ render/ metrics/ semantics/ cli
matrix.yaml        测什么
results/           留档的原始数据（进仓库 —— 公开且按版本可寻址）
openspec/specs/    权威规格（三份能力）
openspec/changes/archive/   建立本仓库的那个变更：proposal / design D1–D12 / delta spec / tasks
```

### 已知边界与代价

- 并发度超出机器容量时，开销数字**含排队等待**；报告会点名这些组并**不给它们交叉点**，
  而不是让一个排队数字去左右结论。
- **并发度超过机器并行能力时，框架开销根本不给。** 那些档位下执行体自报的耗时里含被调度
  推迟的等待，拿它去减"墙钟 ÷ 并发度"没有意义——实测在 4 核 runner 上并发 64 时相减得
  −913.8%。端到端、吞吐与执行体自报值照旧留在报告里，被撤下的组会点出来并说明原因。
- CI 跑在共享的 `ubuntu-latest` 上：相对比较成立，绝对数字逐轮漂移。
- 每次推送到 `main` 会按**每个被测目标约 2.6 MB** 提交原始数据（四个目标约 10 MB；内容是
  JSON，git 存进历史时约压到四分之一）。
- 任何推送到 `main`（哪怕只改文档）都会触发一轮完整测量，**时长随目标数增长**（三个目标时
  实测 12.7 分钟）。
- **`results/` 刻意不用 Git LFS。** 最大的文件约 2.8 MB（GitHub 的告警线是 50 MB），
  一次推送只给历史加约 1.9 MB 压缩后体积；而 LFS 会让原始数据变成**需要装工具才能读**——
  恰好与"公开可审计"相反。等仓库超过约 1 GB、或单份归档大到塞不进一次提交时再回来看。
- 与 Rust 的对照不在本仓库范围内，见框架仓库的 `bench/DECISION.md`。
