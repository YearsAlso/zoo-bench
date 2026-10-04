"""`matrix.yaml` 的读取。

版本清单**显式维护**（design D8）：不自动跟踪 PyPI 最新版，也不跟踪框架的 dev 分支。本模块
只把声明译成测量单元清单，**不含任何"猜一个版本"的默认值**——猜出来的版本会让报告与
`matrix.yaml` 对不上，而那份对不上不会被任何人发现。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import yaml

from .runner import DEFAULT_MEASURED_ROUNDS, DEFAULT_WARMUP_ROUNDS, UnitSpec
from .targets import FrameworkTarget, MatrixError, parse_target

DEFAULT_MATRIX_PATH = "matrix.yaml"

__all__ = [
    "DEFAULT_MATRIX_PATH",
    "FrameworkTarget",
    "Matrix",
    "MatrixError",
    "load",
    "parse_target",
    "resolve_adapters",
    "select_framework",
    "unit_specs",
]


@dataclass(frozen=True)
class Matrix:
    """测量矩阵。

    Attributes:
        frameworks: 允许测量的框架规格（如 ``zoo-framework==0.6.0``）。
        concurrency: 并发度档位。
        body_tiers_us: 执行体目标耗时档位（微秒）。
        adapters: 参与测量的适配器；**为 None 表示"全部已登记适配器"**。
        extra_modules: 需要额外装载的外部适配器模块。
    """

    frameworks: tuple[str, ...]
    concurrency: tuple[int, ...]
    body_tiers_us: tuple[float, ...]
    adapters: tuple[str, ...] | None = None
    extra_modules: tuple[str, ...] = ()


def _require(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise MatrixError(f"{path} 缺少必需字段 {key!r}")
    return mapping[key]


def load(path: str | Path = DEFAULT_MATRIX_PATH) -> Matrix:
    """读取矩阵。

    Args:
        path: `matrix.yaml` 路径。

    Returns:
        解析后的矩阵。

    Raises:
        MatrixError: 文件不存在或字段缺失。
    """
    matrix_path = Path(path)
    if not matrix_path.is_file():
        raise MatrixError(f"找不到矩阵文件：{matrix_path}")

    raw = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
    adapters = raw.get("adapters")
    return Matrix(
        frameworks=tuple(str(item) for item in _require(raw, "frameworks", matrix_path)),
        concurrency=tuple(int(item) for item in _require(raw, "concurrency", matrix_path)),
        body_tiers_us=tuple(float(item) for item in _require(raw, "body_tiers_us", matrix_path)),
        adapters=None if adapters is None else tuple(str(item) for item in adapters),
        extra_modules=tuple(str(item) for item in raw.get("extra_modules") or ()),
    )


def resolve_adapters(matrix: Matrix) -> tuple[str, ...]:
    """把 ``adapters`` 解析成具体的适配器标识清单。

    为 None 时取**全部已登记适配器**。这不只是省事：手维护的那份清单曾经漏掉被测对象本身，
    因为它是在 zoo 适配器存在之前写的——**"忘了同步"这种错误靠不住人，只能靠机制**。

    Raises:
        MatrixError: 显式列出的适配器里没有测对象，或清单为空。
    """
    if matrix.adapters is not None:
        return matrix.adapters

    from .adapters import registry

    registry.load_builtins()
    return tuple(sorted(registry.registered()))


def select_framework(matrix: Matrix, specifier: str | None) -> str:
    """选出要测的框架规格。

    允许传完整规格、纯版本号或 git 引用；不传时取清单第一项——**但不是"随便挑一个"**：清单本身
    就是显式维护的，第一项即维护者指定的基准版本。

    Raises:
        MatrixError: 指定的目标不在清单里。
    """
    if specifier is None:
        return matrix.frameworks[0]

    if specifier in matrix.frameworks:
        return specifier

    matched = [
        item
        for item in matrix.frameworks
        if item.endswith(f"=={specifier}") or item.endswith(f"@{specifier}")
    ]
    if len(matched) == 1:
        return matched[0]

    raise MatrixError(f"{specifier!r} 不在 matrix.yaml 的 frameworks 清单里：{matrix.frameworks}")


def unit_specs(
    matrix: Matrix,
    *,
    framework: str,
    adapters: tuple[str, ...] | None = None,
    concurrency: tuple[int, ...] | None = None,
    body_tiers_us: tuple[float, ...] | None = None,
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS,
    measured_rounds: int = DEFAULT_MEASURED_ROUNDS,
) -> list[UnitSpec]:
    """把矩阵展开成测量单元清单。

    Args:
        matrix: 矩阵。
        framework: 被测框架规格。
        adapters: 覆盖适配器清单。
        concurrency: 覆盖并发度档位。
        body_tiers_us: 覆盖执行体档位。
        warmup_rounds: 预热轮数。
        measured_rounds: 正式采样轮数。

    Returns:
        单元清单，顺序为适配器 → 并发度 → 档位。
    """
    return [
        UnitSpec(
            adapter=adapter,
            framework=framework,
            concurrency=workers,
            body_tier_us=tier,
            warmup_rounds=warmup_rounds,
            measured_rounds=measured_rounds,
            extra_modules=matrix.extra_modules,
        )
        for adapter, workers, tier in product(
            adapters if adapters is not None else resolve_adapters(matrix),
            concurrency if concurrency is not None else matrix.concurrency,
            body_tiers_us if body_tiers_us is not None else matrix.body_tiers_us,
        )
    ]
