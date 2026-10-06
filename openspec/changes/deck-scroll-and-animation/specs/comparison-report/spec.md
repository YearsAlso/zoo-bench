## ADDED Requirements

### Requirement: deck MUST 支持滚轮翻页，且入场动画 MUST 可被读者与打印绕开

报告的 deck SHALL 支持用**滚轮**翻页（与既有的点击、方向键并列），并 MUST 对连续的同向滚动做收敛（阈值 + 冷却），使触控板的一次惯性手势不会跳过多页。deck 的每页元素 MAY 按序入场（淡入），但动画 MUST 满足三条：读者声明 `prefers-reduced-motion: reduce` 时 MUST NOT 播放；打印与 PDF 导出时 MUST NOT 留下动画的初始态（否则那一页是空白）；**动画 MUST NOT 成为内容的前提**——未启用 JavaScript 的读者看到的必须是完整内容。打印与 PDF 导出 MUST 输出**全部页**，MUST NOT 只输出当前所在的那一页。

#### Scenario: 滚轮翻页
- **WHEN** 在报告页上滚动滚轮
- **THEN** 翻到下一页或上一页；到首页之后再往上、到末页之后再往下都停住，不循环

#### Scenario: 一次惯性手势只翻一页
- **WHEN** 触控板一次手势连续发出多个滚轮事件
- **THEN** 只翻一页（阈值与冷却生效），不出现"一划跳五页"

#### Scenario: 声明减少动态效果的读者不看到动画
- **WHEN** 读者的系统设置为 `prefers-reduced-motion: reduce`
- **THEN** 每页内容直接呈现，不播放入场动画

#### Scenario: 打印不留空白页
- **WHEN** 打印报告，或由浏览器导出为 PDF
- **THEN** 每一页的内容都在场，不因动画的初始态而缺失

#### Scenario: 打印输出全部页
- **WHEN** 打印一份多页 deck
- **THEN** 每一页都在输出里，而不是只有当前所在的那一页

#### Scenario: 无 JavaScript 时内容仍完整
- **WHEN** 读者禁用了 JavaScript
- **THEN** 全部内容可见——动画只是增强，不是内容的前提

#### Scenario: 框架块未被改动
- **WHEN** 检查 vendored 的框架样式与脚本
- **THEN** 它们与 vendor 时逐字节一致；滚轮与动画都落在**追加**的那一层里
