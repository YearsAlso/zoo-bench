"""该线的子进程入口：**先摆配置目录、再 import 框架**（design D13 的落点）.

为什么它不能复用 :mod:`zoo_bench.worker` 的 kind 分发：那个模块在**模块级**就 import 了
`zoo_bench.generations`（它模块级 import 被测框架的派发面）与 `zoo_bench.adapters`，解释器一启动
框架就已经被 import、参数就冻结在默认值——`chdir` 再摆配置也晚了。故该线自成入口模块。

本模块的**模块级依赖只有标准库与 :mod:`.config`**：顺序纪律因此是结构性保证的，而不是"记得别
在那里 import 东西"。用例按 AST 扫描本模块的顶层 import 断言这一点（改坏了会红）。

用法（由 :func:`zoo_bench.native_line.line.run_line` 调用，不面向人）::

    python -m zoo_bench.native_line.child <probe|group> <spec-json> <out-path>

``group`` 这个 kind 量的是**一个并发度下的全部 (形态, 规模) 组合**（三形态逐轮交替，故同进程内
的形态之间是配对样本）——粒度是并发度而不是单格，理由见 :mod:`zoo_bench.native_line.measure`。

退出码 0 表示结论已写入 ``out-path``；非 0 表示**本仓自身**出错（配置未生效、驱动面不与被测
版本匹配、单元执行失败……），原因在 stderr。注意区分两种"status"：进程的返回码说的是本仓有没有
跑通，而写进 ``out-path`` 的 ``status`` 是该线对**被测版本**的结论（``ok`` / ``not_applicable`` /
``failed``）——"被测版本不具备该能力"是结论，不是本仓的故障，故它照样以 0 退出。
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import config_of, stage_config

#: 该入口支持的 kind。
KINDS: tuple[str, ...] = ("probe", "group")

_USAGE = "用法: python -m zoo_bench.native_line.child <probe|group> <spec-json> <out-path>"


def _envelope(
    outcome: Mapping[str, Any], *, config_dir: str, measurement: Mapping[str, Any] | None
) -> dict[str, Any]:
    """把一次探查/测量的结论装进统一外层.

    统一外层是为了让父进程**只读一个形状**：无论该线是 `ok`、`not_applicable` 还是 `failed`，
    留档里都有同样的键（``reason`` / ``reason_params`` 是目录键与参数，报告按语言渲染）。

    ``evidence`` 是中文散文证据（如扩展报出的原文），**只作排查**：报告 MUST NOT 渲染它——
    它随被测版本变，进不了两语言目录。故它与 ``reason`` 并存而不是替代 ``reason``。
    """
    return {
        "status": str(outcome["status"]),
        "reason": str(outcome.get("reason", "")),
        "reason_params": dict(outcome.get("reason_params") or {}),
        "evidence": str(outcome.get("evidence", "")),
        "capability": dict(outcome.get("capability") or {}),
        "extension": dict(outcome.get("extension") or {}),
        "equivalence": dict(outcome.get("equivalence") or {}),
        "switches": dict(outcome.get("switches") or {}),
        "config_dir": config_dir,
        "measurement": dict(measurement) if measurement is not None else None,
    }


def collect(kind: str, spec: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """按 kind 取结论.

    ``group`` 这个 kind **在自己的进程里先跑一遍前置核验**才允许测量：等价性门禁若只由父进程的
    探查子进程跑过一次，那么"给出数字"这件事就不再由同一进程内的证据守着。代价是每组的判据
    输入比对（15 条输入 × 两臂，毫秒级），换来的是"没有一组数字能在门禁未过的进程里产生"。

    Args:
        kind: ``"probe"`` 或 ``"group"``。
        spec: 规格（含 ``config`` / ``config_dir`` / ``framework``；``group`` 另含
            ``forms`` / ``registers`` / ``concurrency`` / 轮数）。

    Returns:
        ``(结论, 测量数据或 None)``。
    """
    from .measure import measure_cycles, probe_capability

    outcome = probe_capability(spec)
    if kind == "probe" or outcome["status"] != "ok":
        return outcome, None
    return outcome, measure_cycles(spec)


def main(argv: list[str] | None = None) -> int:
    """子进程主入口.

    Args:
        argv: 命令行参数；None 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码：0 表示结论已落盘，2 表示用法错误，1 表示本仓自身出错。
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 3:
        print(_USAGE, file=sys.stderr)
        return 2

    kind, spec_json, out_path = arguments
    if kind not in KINDS:
        print(f"未知的 kind {kind!r}；可用：{list(KINDS)}", file=sys.stderr)
        return 2

    # chdir 之前先把输出路径定死：摆配置目录会切换工作目录，相对路径会写到别处去
    out = Path(out_path).resolve()

    try:
        spec: dict[str, Any] = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        print(f"spec 不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    config = dict(config_of(spec))
    # ↓↓↓ 这一行 MUST 早于任何框架 import（本模块模块级不许 import 框架，见模块 docstring）
    config_dir = stage_config(config)
    spec["config"] = config
    spec["config_dir"] = config_dir

    try:
        outcome, measurement = collect(kind, spec)
    except BaseException:
        # 真实堆栈进 stderr，供父进程收进失败原因
        traceback.print_exc()
        return 1

    out.write_text(
        json.dumps(
            _envelope(outcome, config_dir=config_dir, measurement=measurement),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
