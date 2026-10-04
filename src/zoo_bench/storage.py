"""原始数据的留档与寻址。

spec 要求原始数据**按被测框架版本留档**，且给一个版本就能定位到它的数据。理由不是"整洁"：
报告要能不打分毫重测就重新渲染，跨版本对比要有可比的历史数据——两者都只从留档出发。

目录形如 ``results/<框架-slug>/<UTC 时间戳>.json``。按版本分目录、按时间戳分文件，使
"同一版本的多次测量"天然共存，跨版本比较则取各自的某一次。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .targets import MatrixError, parse_target

DEFAULT_RESULTS_DIRNAME = "results"

#: UTC 时间戳格式。用 UTC 而非本地时间，避免同一份留档在不同时区的机器上排序错乱。
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def results_root(base: str | Path | None = None) -> Path:
    """留档根目录。

    **默认是当前工作目录下的 ``results/``，不是包所在目录下的**。这一处曾经写错并造成真实故障：
    取包所在目录时，非 editable 安装（CI 就是）会把留档写进 ``site-packages/results/``——数据
    既不在仓库里、也进不了构建产物，而"留档"的全部意义就是这些数据能被找回来。

    Args:
        base: 显式指定；None 时取 ``./results``。

    Returns:
        根目录路径（不保证存在）。
    """
    return Path(base) if base is not None else Path.cwd() / DEFAULT_RESULTS_DIRNAME


def framework_slug(framework: str) -> str:
    """把框架规格转成可作目录名的 slug。

    两种规格都要处理：

    - ``zoo-framework==0.7.1b0`` -> ``zoo-framework-0.7.1b0``
    - ``zoo-framework @ git+https://github.com/o/r.git@dev`` -> ``zoo-framework-dev``

    git 引用只取**分支/标签名**而不是整条 URL：URL 进目录名既长又难读，而"哪个分支"已经足够
    定位，**具体 commit 由报告的环境自述记录**（``direct_url.json`` 里带）。

    **解析走 :func:`zoo_bench.targets.parse_target`，不在这里重写一遍**——两处各解析一次必然
    漂移，实测就出现过同一个 spec 在文件名里是 ``zoo-framework-dev``、在这条 slug 里变成整条 URL。

    **解析失败不抛异常**：这条函数同时在**查档**路径上被调用，而查档允许传纯版本号（``0.7.1b0``）
    甚至已经算好的 slug（``zoo-framework-dev``）——那些都不是合法的矩阵规格。写档路径上的非法
    规格由 ``targets.parse_target`` 在更早处拦下，不靠这里兜底。
    """
    try:
        target = parse_target(framework)
    except MatrixError:
        return _SLUG_UNSAFE.sub("-", framework.strip()).strip("-")

    suffix = target.version if target.kind == "pypi" else target.ref
    return _SLUG_UNSAFE.sub("-", f"{target.name}-{suffix}").strip("-")


def save_run(
    result: dict[str, Any],
    *,
    framework: str,
    root: str | Path | None = None,
    timestamp: str | None = None,
) -> Path:
    """把一次运行结果写入留档。

    Args:
        result: 运行结果（须可 JSON 序列化）。
        framework: 被测框架规格，如 ``zoo-framework==0.6.0``。
        root: 留档根目录；None 时用 :func:`results_root`。
        timestamp: 覆盖时间戳（测试用）。

    Returns:
        写入的文件路径。
    """
    directory = results_root(root) / framework_slug(framework)
    directory.mkdir(parents=True, exist_ok=True)

    stamp = timestamp or time.strftime(TIMESTAMP_FORMAT, time.gmtime())
    path = directory / f"{stamp}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_run(path: str | Path) -> dict[str, Any]:
    """读回一份留档。"""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _version_directories(specifier: str, root: str | Path | None) -> list[Path]:
    """按框架规格或纯版本号匹配到目录。

    允许传 ``zoo-framework==0.6.0``（完整规格）或 ``0.6.0``（纯版本）——后者更常被手敲。
    """
    base = results_root(root)
    if not base.is_dir():
        return []

    exact = base / framework_slug(specifier)
    if exact.is_dir():
        return [exact]

    suffix = f"-{specifier}"
    return sorted(
        entry
        for entry in base.iterdir()
        if entry.is_dir() and (entry.name == specifier or entry.name.endswith(suffix))
    )


def run_files(specifier: str, *, root: str | Path | None = None) -> list[Path]:
    """某框架版本的留档文件，按时间戳升序。

    Args:
        specifier: 框架规格或纯版本号。
        root: 留档根目录。

    Returns:
        文件路径列表；没有留档时为空列表。
    """
    files: list[Path] = []
    for directory in _version_directories(specifier, root):
        files.extend(sorted(directory.glob("*.json")))
    return files


def latest_run(specifier: str, *, root: str | Path | None = None) -> Path | None:
    """某框架版本最近一次留档；没有则返回 None。"""
    files = run_files(specifier, root=root)
    return files[-1] if files else None


def available_frameworks(*, root: str | Path | None = None) -> list[str]:
    """已留档的框架 slug 列表，按名称排序。"""
    base = results_root(root)
    if not base.is_dir():
        return []
    return sorted(entry.name for entry in base.iterdir() if entry.is_dir())
