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

DEFAULT_MATRIX_PATH = "matrix.yaml"


class MatrixError(RuntimeError):
    """`matrix.yaml` 缺失或字段不合法。"""


@dataclass(frozen=True)
class Matrix:
    """测量矩阵。

    Attributes:
        frameworks: 允许测量的框架规格（如 ``zoo-framework==0.6.0``）。
        concurrency: 并发度档位。
        body_tiers_us: 执行体目标耗时档位（微秒）。
        adapters: 默认参与的适配器。
        extra_modules: 需要额外装载的外部适配器模块。
    """

    frameworks: tuple[str, ...]
    concurrency: tuple[int, ...]
    body_tiers_us: tuple[float, ...]
    adapters: tuple[str, ...]
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
    return Matrix(
        frameworks=tuple(str(item) for item in _require(raw, "frameworks", matrix_path)),
        concurrency=tuple(int(item) for item in _require(raw, "concurrency", matrix_path)),
        body_tiers_us=tuple(float(item) for item in _require(raw, "body_tiers_us", matrix_path)),
        adapters=tuple(str(item) for item in _require(raw, "adapters", matrix_path)),
        extra_modules=tuple(str(item) for item in raw.get("extra_modules") or ()),
    )


def select_framework(matrix: Matrix, specifier: str | None) -> str:
    """选出要测的框架规格。

    允许传完整规格或纯版本号；不传时取清单第一项——**但不是"随便挑一个"**：清单本身就是
    显式维护的，第一项即维护者指定的基准版本。

    Raises:
        MatrixError: 指定的版本不在清单里。
    """
    if specifier is None:
        return matrix.frameworks[0]

    if specifier in matrix.frameworks:
        return specifier

    matched = [item for item in matrix.frameworks if item.endswith(f"=={specifier}")]
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
            adapters if adapters is not None else matrix.adapters,
            concurrency if concurrency is not None else matrix.concurrency,
            body_tiers_us if body_tiers_us is not None else matrix.body_tiers_us,
        )
    ]
