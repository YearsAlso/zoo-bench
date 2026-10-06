## Why

这份报告的定位是**对外选型举证**（见 README 与 issue #1），但它目前整站都是中文：站点首页、每版本的三份产物（deck / Markdown / PDF）、跨版本对比页，连图表的轴标签也是中文。英文读者拿到 PDF 只能看图，读不了口径与「公开的不利数据」——而那恰恰是这份报告最该被读懂的部分。

## What Changes

- **站点全面双语**：每版本报告页（deck / Markdown / PDF）、站点首页、跨版本对比页，各出中英两份，各自完整。
- **BREAKING**：语言布局改为**英文为主**——根路径（`site/index.html`、`site/<slug>/index.html`、`site/compare-*/index.html`）改为英文，中文移入同级的 `zh/` 子目录。已被外部引用的旧链接仍可访问，但内容会变成英文。
- **全量翻译**：进报告的每个字都有英文，含口径局限、负载是替身声明、「不可跨运行比较」这类长段落；英文产物里不留中文原文。
- **纯 Python 消息目录**承载译文，`lang` 沿渲染链路显式传递；**缺键即渲染失败**（不引 gettext/babel——那是新依赖，而这里只有几百条文案）。
- **英文字符集另有规则**：导出前的字符白名单门禁原是按**中文字体覆盖**逐字试出来的（实测 SimHei 缺 `µ`），英文产物改用更严的 ASCII 规则，把方框风险从一个语言里彻底移走。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `comparison-report`：新增「站点 MUST 中英双语、英文为主」；并修改既有的 PDF 字体要求——它原写「正确渲染项目文档所用语言（中文）」，现在是双语的，故改为按**该份产物所用语言**判定（英文产物不要求中文字体，而是要求 ASCII 字符集）。

## Impact

| 类别 | 文件 |
|---|---|
| 消息目录（新） | `src/zoo_bench/i18n.py`（或同义模块）：语言常量、`t()`、术语与单位表 |
| 链路（改） | `report.py`（模型文案）、`render/blocks.py`（96 处）、`render/deck.py`（64）、`render/charts.py`（标题与轴标签）、`render/markdown.py`、`render/pdf.py`（按语言解字体） |
| 站点（改） | `render/index.py`（首页双语 + `zh/`）、`render/compare.py`（对比页双语） |
| 编排（改） | `render/report.py`（一次出两种语言）、`cli.py`（`render` / `index` / `compare` 写两份） |
| 测试 | 新增：键完整性、英文无 CJK、两种语言数值一致、站点内部链接全部可达、图表文件不互相覆盖 |
| CI | **不改** `bench.yml`：命令签名不变，一次调用出两种语言 |

**不涉及的边界**

- 不改测量、留档、矩阵与读数口径
- 不改 `adapter-contract` / `measurement-protocol` 两份规格
- 不引第三方依赖（不用 gettext / babel / 任何 i18n 库）
- 不做「页内切换」：图表是图片、PDF 与 deck 会被单独转发，单页双语在这些形态上做不到
- 不保留中文版在根路径（旧链接会变成英文，已在上面标为 BREAKING）
