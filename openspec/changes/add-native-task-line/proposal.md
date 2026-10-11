## Why

zoo-framework 0.11.0 引入两项新能力——自适应调度（`adaptive:enabled`）与原生任务执行
（`native:enabled`），**两者默认都是 false**。而本仓现有测量形态的驱动面是
`BaseWaiter` + 一个普通 `BaseWorker` 子类（`adapters/zoo.py` 的 `_BodyWorker`）：
这两个开关的唯一读取点在 `DualArmWorker`（`adaptive:enabled` 见 `dual_arm_worker.py:131`，
`native:enabled` 见 `dual_arm_worker.py:88`）。

后果是主矩阵的 96 个单元对这两项能力**零覆盖**：把开关打开再跑一遍，得到的运行与关着时
逐字节相同。对外因此无法回答「开了有多少收益」，而这正是选型问题；该收益目前只存在于框架仓
自身的测量记录里（Modbus RTU 帧解析 ≥21 字节帧 2.83x–9.71x），那套口径与本仓数字不同源、
按 D6 不可并列。0.11.0 已发布、默认口径已量（无变化），能力本身已定型（扩展只注册
`modbus_rtu.parse_response` 一个任务），此时把它纳入本站可信口径的条件才齐备。

## What Changes

- 新增**原生任务线**：以真实任务（Modbus RTU 响应帧解析）驱动被测框架的三种形态——
  纯 Python 臂、原生臂、双臂自适应（`adaptive:enabled=true` 且声明原生任务名），
  作为主矩阵之外的独立测量线。
- 新增**语义等价性门禁**：两臂对同一批帧的输出 MUST 逐字节相同；不等则该线不出数字。
  没有这道门禁，「倍数」可能来自两件不同的事。
- **帧长度成为该线的档位**，覆盖框架自身工作包络的两端（1 寄存器 7 字节小帧与
  ≥8 寄存器 21 字节帧、最大帧），用于检查自适应是否收敛到「小帧走 Python、大帧走原生」。
- 新增**扩展的获取与版本对齐**：CI 从框架仓**同一 tag** 的 `native/` 子目录构建并安装
  `zoo-framework-native`（maturin 构建后端；ubuntu runner 自带 Rust），扩展源码版本与
  被测框架版本一并写入留档。
- **能力缺失 / 开关关闭 / 扩展缺失 MUST 明确示人**：MUST NOT 静默省略该线、MUST NOT
  以替代数字顶替（对 <0.11.0 的已发布版本、free-threaded 列等）。
- 报告侧：新增该线的维度与结论，deck / Markdown / PDF 三种格式与中英双语均在場；
  该线上的**不利数据**（小帧上原生臂并不占优）按既有对称要求公开；版本间对比可查询。
- 该线 MUST NOT 与主矩阵的 96 单元混读，报告 MUST 声明两条线的口径不同。

**BREAKING**: 无。主矩阵 96 单元的编排、口径与既有留档不变。

## Capabilities

### New Capabilities

- `native-task-line`: 真实任务线的测量形态——三条被对照形态的构成、帧长度档位、
  语义等价性门禁、开关与能力缺失时的明确示人、扩展的获取与版本对齐、
  以及与主矩阵口径的边界。

### Modified Capabilities

- `comparison-report`: 报告 MUST 含原生任务线这一维度与结论（三格式、两语言、
  版本间对比可查、不利数据对称公开、自检失败阻止发布）。

## Impact

- **本仓代码**：`src/zoo_bench/`（新线模块与其驱动、留档 schema 扩展、report 与
  `render/` 的该维度、双语 i18n 键）、`matrix.yaml`（该线的**档位**声明——三种被对照形态
  由规格的 MUST 固定，单点声明在 `src/zoo_bench/native_line/forms.py`，不进矩阵）、
  `.github/workflows/bench.yml`（Rust 构建与扩展安装步骤）。
- **与框架仓的耦合**：扩展源码取自框架仓 tag 的 `native/` 子目录；Python 参照实现由本仓
  自带，并受等价性门禁约束。两条依赖都在留档里可追溯。
- **不触碰**：主矩阵 96 单元的编排与既有报告数值；框架仓自身（本变更不含框架代码改动）。
