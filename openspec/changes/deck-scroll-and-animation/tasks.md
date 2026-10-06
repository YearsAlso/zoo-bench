## 1. 追加层（不改框架块）

- [x] 1.1 `_EXTRA_CSS` 加入场关键帧：`opacity` 0→1 + `translateY(12px)`→0，420ms；错开 40ms 一档（0 / 40 / 80 / 120，其后 280ms）
- [x] 1.2 初始态只挂在 **JS 加的类**上（`.slide.active.deck-anim > *`），渲染时不给任何元素写 `opacity: 0`
- [x] 1.3 两处收敛写进同一段样式：`@media print`（`animation: none` + `opacity: 1` + `transform: none`）与 `@media (prefers-reduced-motion: reduce)`
- [x] 1.4 新增 `_EXTRA_JS` 拼在 vendored 脚本**之后**：滚轮 → 往 `window` 发 `{type:'od:slide', action}`；收 `od:slide-state` → 先移除类、强制重排、再加，以重启动画
- [x] 1.5 滚轮参数：阈值 24px、冷却 450ms、`deltaMode === 1` 按 16px/行换算、`{ passive: true }` 且从不 `preventDefault`
- [x] 1.6 `prefers-reduced-motion: reduce` 时 JS 干脆不加动画类（CSS 那层之外再加一道）
- [x] 1.7 **实测发现的缺陷（原计划外）**：框架的打印规则被屏幕规则的选择器具体度压掉，实测 38 页的 deck 打印只出 1 页。补一条同具体度、排在其后的 `.slide:not(.active) { display: flex !important }`

## 2. 用例

- [x] 2.1 页面里有 `wheel` 处理器，而 **vendored 框架脚本里没有**（`wheel` 不出现在那份资产里）——证明是追加
- [x] 2.2 vendored 三份资产的哈希用例仍然通过（既有判据未改）
- [x] 2.3 `@media (prefers-reduced-motion: reduce)` 与 `@media print` 的收敛规则都在场，打印那条含 `opacity: 1`
- [x] 2.4 动画初始态只在 JS 类之下——**判据改了三版**：查子串会漏判（`nth-child` 延时段也含那个前缀）、逐行查会误判（选择器与声明不同行），最终按**规则**（选择器 + 声明块、排除关键帧的 `from`/`to`）判定
- [x] 2.5 滚轮参数（阈值 / 冷却 / `deltaMode` 换算）钉住
- [x] 2.6 打印放开非当前页那条规则钉住（因 1.7 新增）

## 3. 真实浏览器的行为核验

- [x] 3.1 **8 个同步 wheel 事件只翻一页**（01→02）；等过冷却后第二次手势再翻一页（→03）
- [x] 3.2 动画确实在跑：`getAnimations()` 报 `running@0ms`；并**显式读出**该环境的 `prefers-reduced-motion`（实测 `false`）——无头环境默认值不确定，不读会把"照设置不播"误判成实现有问题
- [x] 3.3 打印核验：浏览器导出 PDF **39 页、无空白页**（修 1.7 之前只有 1 页）；首、次、末页都能抽出文字
- [x] 3.4 行模式（`deltaMode=1, deltaY=3`）也能翻页（→04）；逐页核对无版式回归

## 4. 收尾

- [x] 4.1 未夹带范围外改动：只动 `render/deck.py` 与 `tests/test_render_deck.py` 及本变更工件；测量、留档、矩阵、口径、双语变更均未动
- [x] 4.2 断言有效性：注入五处违规（去掉冷却、把初始态写死在普通选择器、删掉打印收敛、**把 wheel 塞进 vendored 框架脚本**、删掉打印放开非当前页那条），对应断言全部变红，md5 逐字节还原
