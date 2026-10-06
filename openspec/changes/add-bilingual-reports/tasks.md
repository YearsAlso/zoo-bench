## 1. 消息目录（新模块）

- [ ] 1.1 建 `src/zoo_bench/i18n.py`：语言常量（`zh` / `en`）、`DEFAULT_LANG = en`、`t(key, lang, **params)`、以及**术语与单位表**（微秒 → `microseconds`、任务/秒 → `tasks/s`、并发度 → `concurrency`、档位 → `body tier`、被测框架 → `the framework under test`），表放在文件顶部作为后续新增文案的基准
- [ ] 1.2 键 → 两语言文案的表：键用点分命名（`blocks.section.environment`、`charts.axis.body_tier`），值用 `str.format` 模板承载数字（如 `"并发度 {concurrency}"`）
- [ ] 1.3 用例：**键完整性**——每个键两种语言都有非空文案；`t()` 对缺失键**抛错**而不是回落或留空
- [ ] 1.4 用例：模板与调用点对不上（少参数 / 多参数 / 参数名写错）时抛错——否则模板会原样印出 `{concurrency}`

## 2. 语言沿链路（模型与块层）

- [ ] 2.1 `report.build_model(result, *, lang)`：维度名、摘要句、口径局限等文案全走目录；模型里记下 `lang`
- [ ] 2.2 `render/blocks.py` 的 96 处字面量入目录（章节标题、表头、口径注、指引句），`build_blocks(model, charts, *, lang)`
- [ ] 2.3 逐处分辨 `semantics.py` / `environment.py` / `attribution.py` / `runner.py` 里的中文：**进报告**的入目录；只在 CLI 上打印的**如实留着**（本次范围是站点，见 proposal 的 Non-Goals），并在代码注释里写明这条边界
- [ ] 2.4 用例：迁移过的模块里不再有"带中文的 f-string"残留——f 前缀没去掉就说明模板没生效（这类改动不会被任何功能断言发现）

## 3. 三个后端与图表

- [ ] 3.1 `render_deck` / `blocks_to_markdown` / `render_pdf` 接收 `lang`；页面 `<html lang>` 与文档标题随语言
- [ ] 3.2 `charts.render_all(..., lang)`：图表标题与轴标签入目录；**figure 文件名带语言**（`figures/en-overhead.svg` / `zh-overhead.svg`）
- [ ] 3.3 `render_pdf` 的字体解析按语言分流：中文产物缺中文字体仍失败（既有行为）；**英文产物不因缺中文字体而失败**
- [ ] 3.4 导出前的字符门禁按语言选规则：中文走既有白名单 + CJK 范围，英文走 `text.isascii()`；违反时**指出具体文本**，以便区分"漏了翻译"与"数据本身如此"
- [ ] 3.5 用例：英文产物正文不含 CJK 字符；英文产物在**无中文字体**的环境里仍能导出；两种语言的图表文件都在场且互不覆盖

## 4. 站点布局与互链

- [ ] 4.1 `render/report.py` 一次写出两份：根 = 英文、`zh/` = 中文（既有的 `index.html` / `report.md` / `report.pdf` 三件套 + 各自的 `figures/`）
- [ ] 4.2 链接构造集中到一处函数（语言切换、首页版本卡片、对比页入口都在两个语言层之间跳），避免各处手写相对路径
- [ ] 4.3 `render/index.py` 与 `render/compare.py` 各出两份；语言切换链接进页头
- [ ] 4.4 用例：**全站内部链接可达**——遍历站点里所有 `<a href>`（排除外链与 `#` 锚点），逐个断言目标文件存在
- [ ] 4.5 用例：同一次留档渲染出的中英两份里，同一个结论给出**同一个数字**（同源，不是各算各的）

## 5. 真实数据核验与收尾

- [ ] 5.1 用四个真实留档各渲染双语一遍：逐页核对无中文残留、无缺字方框、两种语言的图表都在
- [ ] 5.2 核对 `.github/workflows/bench.yml` **无需改动**：`render` / `index` / `compare` 的命令签名不变、一次调用出两份
- [ ] 5.3 未夹带范围外改动（测量、留档、矩阵、读数口径、`adapter-contract` 与 `measurement-protocol` 两份规格均未动）
- [ ] 5.4 断言有效性：注入违规（删一个英文键、英文页塞一处中文、图表文件名去掉语言前缀、链接少一层 `../`），对应断言必须变红，并按哈希逐字节还原
