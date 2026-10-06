## Why

**现在这份报告是"长文档"，不是"能被一眼看懂的结论"。** 它有一份 96 单元的明细、四个维度、优/平/劣三节、口径与附录——适合核查，不适合**三十秒内知道结论**。而这份 harness 的读者（选型的人）要的恰恰是后者。

提供的新样式是一套 **1920×1080 的 deck**：深浅交替的页、一个大数字一页、条形图直接编码数值、固定画布 + 打印规则。它的视觉语言比现在的长文档强得多。

**但那份模板的内容不能照抄**，逐项比对最新留档后有四处冲突：

| 模板里写的 | 最新 CI 留档实测（并发 1 · 40 微秒档）|
|---|---|
| zoo 端到端 176.3 微秒，且「四个档位 zoo 的延迟最低」 | zoo 是 108.6 微秒，而 thread_pool 63.2 / asyncio_pool 89.9 / bare_thread 103.4 都更低 |
| zoo 开销占比 70.78% | 57.48% |
| zoo 吞吐 5,673/秒、process_pool 2,932/秒 | 9,212/秒 与 4,938/秒（模板漏了 thread_pool 的 15,820/秒）|
| 「对手的领先只存在于 40 微秒」 | 不成立：24 行劣势在长档位 |

而且模板的图表只含 apscheduler 与 process_pool——**恰好漏掉三个经常赢被测框架的方案**，也没有任何一页讲"我们在哪更慢"。这与本仓库的硬要求（报告 MUST 公开不利数据）冲突。

故本次做的是**样式**：deck 的视觉语言照搬，**逐页内容由报告模型生成**，并强制含一页「我们在哪些档位更慢」。

## What Changes

- **HTML 输出改为 deck**：一套由模型生成的 1920×1080 幻灯片（封面 / 方法与口径 / 核心结论 / 各维度条形图 / 相对转折点 / **我们在哪些档位更慢** / 选型建议 / 附录指引）。
- **deck 的框架块原样 vendor**（固定画布、缩放适配、键盘与点击导航、打印规则、postMessage 协议）——它们是样式的一部分，不重写。
- **失败与不利数据不因"版面装不下"而消失**：deck 里必有一页讲劣势（47 行按病因归类），逐单元明细改由 Markdown 版与留档承担，并在 deck 里给出指针。
- **Markdown 与 PDF 照旧**：Markdown 仍是可 diff 的全文（含附录明细），PDF 仍由 reportlab 生成（把视觉语言朝 deck 调，**不引入无头浏览器**——那是约 300MB 的新依赖加 CI 时长，换来的只是把同一份内容排成幻灯片页）。
- **原始数据出处改成仓库内相对路径**：三种格式原本印的是构建机的绝对路径（本机是 `F:\...`、CI 是 `/home/runner/...`），在公开站点上都指不到读者能打开的位置。
- **站点首页的链接不变**：deck 仍叫 `index.html`，`report.md` 与 `report.pdf` 仍在。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `comparison-report`：新增一条要求——HTML 输出 MUST 是"一页一个结论"的 deck 形式；且**必须含一页列出被测框架处于劣势的档位**，其数据与其余格式同源、不得因版面而省略。

## Impact

| 类别 | 文件 |
|---|---|
| deck 渲染（新） | `src/zoo_bench/render/deck.py`（模型 → 幻灯片） |
| deck 框架资产（新） | `src/zoo_bench/render/deck_framework.html`（原样 vendor 的 style/script） |
| 编排 | `src/zoo_bench/render/report.py`（HTML 走 deck） |
| 站点 | `src/zoo_bench/render/index.py`（链接口径确认） |
| 测试 | `tests/test_render_deck.py`（新）、`tests/test_render.py` |

**不涉及的边界**

- 不改 Markdown 与 PDF 的产出（PDF 只调视觉，不改结构）
- 不改跨版本对比页（那是另一份文档，本次不动）
- 不引入新的第三方依赖（不引入无头浏览器）
- 不改测量、留档与矩阵
