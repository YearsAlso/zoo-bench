## Context

现状见 proposal.md 的 Why。设计上要接的接缝：deck 的页面由 `render/deck.py` 拼装——它把三段 vendored 资产（`deck_framework.css` / `deck_chrome.html` / `deck_framework.js`）**原样**插入，并在其后追加我自己的一段样式（`_EXTRA_CSS`）。那三段资产标着 DO NOT EDIT 且被 sha256 钉在用例里，所以任何导航/动画都得走"追加"而不是"修改"。

框架脚本已经留了扩展口（实测）：它监听 `window` 上的 `message`，收 `{type:'od:slide', action: 'next'|'prev'|'go'|'first'|'last'}`；它自己也把 `{type:'od:slide-state', active, count}` 发到 `window.parent`（顶层文档时即自身）。它**没有** wheel 处理。

## Goals / Non-Goals

**Goals:**

- 滚轮翻页可用且不误跳；入场动画按序、克制、可关。
- vendored 三段资产字节不变（哈希用例继续绿）。

**Non-Goals:**

- 不做页间过渡、自动播放、进度条。
- 不引入动画库或任何新依赖。
- 不改打印布局与页面结构。

## Decisions

### D1 用框架自己的 postMessage 协议追加，不改框架脚本

我的脚本往 `window` 发 `od:slide` 消息，框架的既有监听负责翻页；我另收 `od:slide-state` 决定给哪页挂动画类。**备选**：复制一份框架脚本改掉（否决：那会让"框架块原样"的哈希用例失效，也丢掉了"框架块可独立替换"这个性质）。

顺带一个次序事实：框架在自身 IIFE 里 `paint()` 会广播一次 `od:slide-state`，而 `message` 是**异步**投递的——故我这段追加脚本已经跑完，首屏也能收到那次广播并给第一页挂上动画，不需要额外的初始化代码。

### D2 不引 anime.js，用 CSS 关键帧

入场动画 = `opacity` + `translateY(12px)`，错开 40ms 一档（标题 0 / 正文 40 / 表格 80 / 页脚 120，其后各档保持 280ms 上限）。

**否决 anime.js 的三条理由**：① deck 必须自足（会被单独转发、作为归档长期存在），CDN 引用等于给"可离线打开的文档"接外部失败点；② 内联进每页约 17 KB，而本轮是 5 个目标 × 2 语言；③ 库真正的价值在时间线编排、弹簧、滚动驱动、SVG 变形，这里一个都不需要。另有一条技术原因：框架的适配缩放靠 `.deck-stage` 上的 `transform: scale()`，在它上面叠库的 transform 动画会互相干扰；作用在页内子元素上则完全回避。

### D3 收敛参数与跨浏览器的 deltaMode

阈值 24（像素）、冷却 450ms、`deltaMode === 1`（行模式）时按约 16px/行换算——不换算的话 Firefox 的行模式 `deltaY` 只有几，会被阈值**全部挡掉**，表现为"Firefox 上滚轮彻底失灵"。监听用 `{ passive: true }`：我们从不 `preventDefault`，不抢框架对点击的处理。

### D4 动画的初始态挂在 JS 加的类上，而不是渲染时就写上

`opacity: 0` 只对 `.slide.active.deck-anim` 的子元素生效，而 `deck-anim` 由 JS 在翻页时加上。这条不只是洁癖——它同时满足三个约束：无 JS 的读者内容完整（类的名字永远不会出现）；打印时不可能抓到初始态（`@media print` 再兜一层 `animation: none; opacity: 1`）；`prefers-reduced-motion: reduce` 时 JS 干脆不加类（CSS 里再兜一层）。

### D5 两处收敛是硬要求；另有一处打印缺陷是实测才发现的

`@media print` 与 `@media (prefers-reduced-motion: reduce)` 都要显式把动画关掉。前者防的是"打印抓到初始态 → 空白页"（`animation-fill-mode: both` 在动画未开始时的表现正是初始态）；后者是无障碍要求。

**实测发现**：只关动画还不够——框架的打印规则**压根没生效**。它的 `.slide { display: flex !important }` 与屏幕规则 `.slide:not(.active) { display: none !important }` 都带 `!important`，但后者**选择器更具体**，于是打印时非当前页仍是 `display: none`：一份 38 页的 deck 打印出来只有当前那一页（先前的核验只断言过"框架块原样在场"，从没真的打过一次）。修法是补一条**同具体度**、**排在更后**的规则。这条不能靠"框架的打印规则已经写了"来省——两条 `!important` 相撞时决定胜负的是具体度而非书写顺序。

## Risks / Trade-offs

- **观感没有门禁**：能断言的只有"处理器与规则在场""框架块没被改""无 JS 时内容完整"。滚起来顺不顺手只能靠眼睛——如实记下，不假装用例覆盖了它。
- **滚轮劫持的争议**：deck 每页固定 1080px 且内容已按行分页，没有可滚的东西，故劫持是安全的；但如果将来某页出现可滚动区域，需要按"事件目标是否可滚"放行。已记入本文件作为已知边界。
- **无头浏览器里的 `prefers-reduced-motion` 默认值**：headless 环境可能默认声明 `reduce`（实测以 `matchMedia` 结果为准），故用浏览器做行为验证时要显式读出该声明，否则会把"没播动画"误判成实现有问题。
- **动画与绝对定位的页脚**：`.src` 是 `position: absolute`，`translateY` 对它的影响只是位移、不改变定位，无冲突。
- **无头核验的两个坑**（实测踩到，留给后来做同类核验的人）：① `postMessage` 是**异步**的，翻页状态**同步读永远是旧页**——必须延迟后再读，否则会把"实现没问题"判成"没翻页"；② `--virtual-time-budget` 会把定时器快进，CSS 动画的**时间进度**因此不可信，动画的证据要用 `getAnimations()`（结构性：`playState` 与 `currentTime`）而不是 `getComputedStyle().opacity`。同理，测"冷却"时每次手势之间必须等过冷却窗口，否则测到的是冷却而不是被测的那条规则。
