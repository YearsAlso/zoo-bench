"""该线的配置：开关组合、配置目录的就位，以及"声明 vs 解析"的核验（design D6 / D13）.

**本模块 MUST 只依赖标准库**（模块级）。这不是风格问题：它是子进程里最先被 import 的一环，
而"配置目录先就位、再 import 框架"是 design D13 的硬时序——一旦这里（或它的 import 链上）
碰了框架，`chdir` 就已经晚了，开关会静默冻结在默认值（实测复现过：路由形态退化成纯解释形态，
而报告上看起来一切正常）。用例 `tests/test_native_line_measure.py`（子进程入口的模块级 import
只有标准库与 ``.config``）对这条依赖纪律有断言，使"以后有人顺手在这里 import 一个兄弟模块"
不会变成静默失效。

**为什么开关的组合要进留档**：读者要能回答"这一组是在哪套开关下测的"。声明（本模块的
:data:`SWITCHES`）与**解析后的实际值**（:func:`verify_switches` 的返回）都要在留档里——
只留声明的话，声明与实际漂移时没人看得出。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: 摆给框架的配置（**开关组合**，逐组进留档）。
#:
#: 刻意**不声明** ``adaptive:exploration``：探索率取框架的默认值，并把**解析后**的实际值记进
#: 留档——写一份声明反而会与实际值漂移，而"这一组用的是哪个探索率"由解析值回答得更可靠。
#:
#: ``adaptive:statsPath`` 显式留空：该线 MUST NOT 配置统计落盘路径（design D6）。配了它并**不会**
#: 落盘（框架内没有 `flush_stats` 的调用点），但首次取决策器时会**读盘**——那一刻落在计时区间内，
#: 且读回来的统计会成为本轮的先验，"这一轮学到什么"不再只由这一轮决定。
SWITCHES: dict[str, Any] = {
    "adaptive": {"enabled": True, "statsPath": ""},
    "native": {"enabled": True},
}

#: 摆给框架的配置文件名（框架按 ``./config.json`` 解析）.
CONFIG_FILENAME = "config.json"

#: 配置目录的临时目录名前缀（进留档，使"这份配置从哪来"可追）.
CONFIG_DIR_PREFIX = "zoo-bench-native-line-"


def stage_config(config: Mapping[str, Any]) -> str:
    """把开关组合写进一个新的临时目录，并**切换工作目录到那里**.

    这是 design D13 的落点：框架的参数类在**首次 import 时**以 ``./config.json`` 解析，故配置
    MUST 在那之前就位于当前工作目录。子进程因此先摆目录、再 import 框架。

    目录**不自动清理**：失败时人要看的就是这份文件（"开关到底摆进去了没有"是 D13 类故障的第一
    个疑点）。路径进留档，读者可据此复现同一套开关。

    Args:
        config: 开关组合（嵌套 dict，键即框架的配置路径）。

    Returns:
        摆好的配置目录的绝对路径。
    """
    directory = tempfile.mkdtemp(prefix=CONFIG_DIR_PREFIX)
    Path(directory, CONFIG_FILENAME).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chdir(directory)
    return directory


def flatten(config: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """把嵌套配置摊成框架的路径语法（``{"adaptive": {"enabled": true}}`` → ``adaptive:enabled``）."""
    flat: dict[str, Any] = {}
    for key, value in config.items():
        path = f"{prefix}:{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            flat.update(flatten(value, path))
        else:
            flat[path] = value
    return flat


def config_of(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    """规格里声明的开关组合.

    **缺席**（``None`` / 不含该键）即取 :data:`SWITCHES`；**显式给的空组合**就是空组合。两种情形
    必须分开：``{"config": {}}``（不含开关的配置目录）是该线"开关只由这份配置决定"这一验证所
    依赖的一态，若按"假值即取默认"处理，它就退化成默认组合、那条验证恒绿。
    """
    raw = spec.get("config")
    return SWITCHES if raw is None else raw


def resolved_switches() -> dict[str, Any]:
    """框架侧**解析后**的开关与决策参数值（证据，不是声明）.

    Raises:
        ImportError: 被测版本没有这套参数类（该版本不具备该能力，由能力探查先行拦下）。
    """
    from zoo_framework.params import AdaptiveParams, NativeParams

    return {
        "adaptive:enabled": bool(AdaptiveParams.ADAPTIVE_ENABLED),
        "adaptive:exploration": float(AdaptiveParams.EXPLORATION),
        "adaptive:statsPath": str(AdaptiveParams.STATS_PATH),
        "native:enabled": bool(NativeParams.NATIVE_ENABLED),
    }


def verify_switches(config: Mapping[str, Any]) -> dict[str, Any]:
    """核验声明的开关组合真的生效了（design D13 的可证伪判据）.

    只断言"配置没生效"这一种失败：框架的参数在首次 import 时解析，配置目录若在那之后才就位，
    参数**冻结在默认值**——路由形态会静默退化成纯解释形态，而报告上看起来一切正常。故这里逐键
    比对声明值与解析值，不等即报错。

    声明里**没写的键不参与比对**：不声明即"取框架默认值"，那是合法用法（规格里"两个开关都是
    默认关闭值"那一态正是如此），此时解析值是证据而非判据。

    Args:
        config: 声明的开关组合。

    Returns:
        解析后的实际值（进留档）。

    Raises:
        RuntimeError: 某个声明的键没有生效（附"声明 vs 实际"）。
        ValueError: 声明了统计落盘路径——该线 MUST NOT 配置它（design D6）。
    """
    resolved = resolved_switches()
    stats_path = str(resolved["adaptive:statsPath"])
    if stats_path:
        raise ValueError(
            f"该线 MUST NOT 配置统计落盘路径，实际解析出 adaptive:statsPath={stats_path!r}；"
            "配了它会在首次取决策器时读盘（那一刻落在计时区间内），"
            "并让读回来的统计成为本轮先验"
        )

    mismatched = {
        key: {"declared": value, "resolved": resolved.get(key)}
        for key, value in flatten(config).items()
        if resolved.get(key) != value
    }
    if mismatched:
        raise RuntimeError(
            f"配置未生效：{mismatched}。框架的参数类在**首次 import 时**解析 ./config.json，"
            "故配置目录 MUST 在任何框架 import 之前就位（design D13）——"
            "否则路由形态会静默退化成纯解释形态"
        )
    return resolved


__all__ = [
    "CONFIG_DIR_PREFIX",
    "CONFIG_FILENAME",
    "SWITCHES",
    "config_of",
    "flatten",
    "resolved_switches",
    "stage_config",
    "verify_switches",
]
