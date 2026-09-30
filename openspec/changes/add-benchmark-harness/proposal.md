## Why

zoo-framework 的定位是「Python 快速开发高性能任务调度库」，但**没有一个能回答「它到底快不快、相对谁快」的常设机制**。仅有的测量材料是框架仓库的 `bench/`，它是 `adopt-rust-core` 的一次性可行性论证：`README.md` 明确声明"不进入产品代码"，且其 Linux 数字因 WSL2 放大跨线程唤醒而**不可用**。结论归档后，这批脚本既不承载后续版本的性能信息，也不面向对外举证。

后果有三：

- **对外无从举证**。使用者问"为什么用 zoo 而不是裸 `ThreadPoolExecutor`"，仓库里没有一个可引用的数字。而 `bench/` 实测恰恰显示框架开销在最短档位上占端到端 **70.76%**——**在那个区间裸写法大概率更快**。没有公开的交叉点，说服力只能靠文字。
- **版本性能不可观测**。既有四项纯 Python 优化（事件管道去 `gevent` 800x、`ThreadSafeDict` 换 `RLock` 13x、`BaseFIFO` 换 `deque` 12.4x、默认启用资源池 16x）实测量级远超已被否决的 Rust 方案（1.67x），但它们至今没有任何量化验收手段。
- **`bench/` 的定位已被耗尽**。它是"测量与决策"，不是"测量与回归"；继续往里加会稀释其已归档结论的可信度。

因此新建独立仓库 `zoo-bench` 承载**长期维护的基准 harness**：可复跑、可比对、可对外发布，且不改变 `zoo_framework/` 的任何运行时行为。

## What Changes

**A 组 · 适配器契约与内置对照**

- 定义适配器契约（`setup` / `submit` / `drain` / `teardown`），harness 只依赖契约、不依赖具体方案
- 内置三档对照：不做框架的裸写法（`threading.Thread` 每任务一线程、`ThreadPoolExecutor`）、标准库其他并发模型（`asyncio`、`ProcessPoolExecutor`）、同生态调度库（`APScheduler`；`Celery` 走可选依赖，不进默认跑）
- 每个适配器跑在**独立子进程**，避免 asyncio 事件循环与 gevent monkey patch 互相污染
- 开放接口：使用者实现契约即可加入新对照

**B 组 · 测量协议**

- 四档执行体（cpu 型，按 `body_tiers_us` 调参）+ 并发度扫描（`concurrency`）
- 执行体耗时**在同一次运行内埋点**，不用另一次测量的结果做减法
- warmup + N 次重复 + 分位数，同时报告离散度而非仅中心值
- 报告区分**同一次运行内的相对比**（证据主体）与**绝对耗时的微秒数字**（标注不可跨运行比较）
- 适配器等价性冒烟测试与测量自检

**C 组 · 报告与发布**

- 环境自述块（CPU 型号与核数、OS 与内核、Python 版本、依赖版本、被测框架版本、commit sha、完整命令）
- `zoo-bench render` 由原始数据产出 Markdown + 静态 SVG 图表
- 报告结构包含**结论摘要（交叉点）**与**公开的不利数据**
- `results/` 按框架版本留档，`zoo-bench compare` 产出跨版本提升报告
- GitHub Actions 跑在 `ubuntu-latest`，产物发布到 zoo-bench 自建 Pages
- zoo-framework 侧仅加一条指向报告的链接

## Capabilities

### New Capabilities

- `adapter-contract`:对照实现的契约、内置档位的覆盖范围、进程隔离要求、以及新增对照的扩展方式
- `measurement-protocol`:执行体与并发度的负载定义、耗时采集口径、可比较性边界、以及测量结果自身的自检
- `comparison-report`:报告的环境自述、维度与结论结构、原始数据留档、跨版本对比、以及对外发布

### Modified Capabilities

无。zoo-bench 为新建仓库，`openspec/specs/` 本次首次建立基线。

## Impact

**新建仓库** `YearsAlso/zoo-bench`，结构：

| 路径 | 内容 |
|---|---|
| `src/zoo_bench/adapters/` | 适配器契约与内置三档实现 |
| `src/zoo_bench/workloads/` | 负载形状定义 |
| `src/zoo_bench/runner.py` | 编排：子进程隔离、warmup、重复、埋点 |
| `src/zoo_bench/metrics.py` | 分位数与离散度 |
| `src/zoo_bench/render/` | 原始 JSON → Markdown + SVG |
| `src/zoo_bench/cli.py` | `run` / `render` / `compare` |
| `matrix.yaml` | 要测的框架版本 × 适配器 × 并发度 × 执行体档位 |
| `results/` | 原始 JSON 留档 |
| `docs/` | 由 Pages 发布的报告 |
| `.github/workflows/bench.yml` | 跑矩阵并发布 Pages |

**zoo-framework 侧改动**：仅 `.gitignore` 增 `/zoo-bench/`、`README.md` 与 `docs/benchmark.md` 各增一条指向报告的链接，**不改 `zoo_framework/` 任何源码**。

**不涉及的边界**

- 不改 `zoo_framework/` 的运行时行为与公开 API
- 不复活也不改动 `bench/`，其 `adopt-rust-core` 结论与"不进入产品代码"的定位保持不变
- 不把 `Celery` 放进默认跑（broker 依赖与进程内口径不可直接对标，见 design D3）
- 不做跨仓库写回：不向 zoo-framework 提交报告、不需要 PAT、不产生 bot commit
- 不追求硬实时或抖动有界，只给出可观测的分位数与离散度
- 不与 `bench/` 的 Rust 对照做重复论证；Rust 方案已由 `adopt-rust-core` 判定 no-go
