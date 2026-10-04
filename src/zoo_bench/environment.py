"""运行环境自述。

spec 要求报告自述"足以让第三方判断数字适用范围"的环境：硬件与软件标识、被测对象标识、
复现命令。三项各有其用途——前两项判断数字能不能套到自己的场景，后一项让人能自己跑一遍。

**采集不到的字段如实记为不可用，不编造也不拿近似值顶替**。一份自述的价值全在它是否可信：
用 `platform.processor()` 在 Windows 上得到的 `AMD64 Family 25 Model 97 Stepping 2` 看着
像型号其实不含型号，把它当"CPU 型号"写进报告比写 `null` 更坏。
"""

from __future__ import annotations

import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

#: 记录版本用于核对"报告里的依赖与实测时是否一致"的包。只列本 harness 与被测框架的
#: 直接依赖——把整个传递闭包都写进去会让自述退化成噪音。
TRACKED_PACKAGES: tuple[str, ...] = (
    "zoo-framework",
    "apscheduler",
    "matplotlib",
    "reportlab",
    "gevent",
    "tzlocal",
)

#: 项目仓库根：``src/zoo_bench/environment.py`` 往上三层。
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _distribution_version(name: str) -> str | None:
    """发行元数据里的版本；未安装时返回 None。"""
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def cpu_model() -> tuple[str | None, str]:
    """CPU 型号及其来源。

    Returns:
        ``(型号, 来源说明)``。型号取不到时为 ``(None, 原因)``——**不返回近似值**。
    """
    system = platform.system()

    if system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.lower().startswith("model name"):
                        return line.split(":", 1)[1].strip(), "/proc/cpuinfo"
        except OSError as exc:
            return None, f"读取 /proc/cpuinfo 失败：{exc}"
        return None, "/proc/cpuinfo 中没有 model name 字段"

    if system == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            finally:
                winreg.CloseKey(key)
            return str(value).strip(), r"注册表 ProcessorNameString"
        except OSError as exc:
            return None, f"读取注册表失败：{exc}"

    if system == "Darwin":
        sysctl = shutil.which("sysctl")
        if sysctl is None:
            return None, "找不到 sysctl"
        completed = subprocess.run(
            [sysctl, "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip(), "sysctl machdep.cpu.brand_string"
        return None, f"sysctl 返回 {completed.returncode}"

    # 刻意不退回 platform.processor()：它在多个平台上返回架构串而非型号，冒充型号会误导读者
    return None, f"暂不支持从 {system} 取 CPU 型号"


def harness_commit() -> tuple[str | None, str]:
    """zoo-bench 自身的 commit 标识及其来源。

    **依次尝试多个根**：当前工作目录优先，其次包所在目录。实测踩过这个坑——只取包所在目录时，
    非 editable 安装（CI 就是）那里是 site-packages、不是 git 仓库，报告里于是写着
    `harness commit: None`，而"这份报告由哪个 commit 产出"正是它要回答的问题。
    """
    git = shutil.which("git")
    if git is None:
        return None, "找不到 git"

    attempts: list[str] = []
    for root in (Path.cwd(), Path(_PACKAGE_ROOT)):
        completed = subprocess.run(
            [git, "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip(), f"git -C {root} rev-parse HEAD"
        attempts.append(str(root))

    return None, f"以下路径都不是 git 仓库：{attempts}"


def collect(command: list[str] | None = None) -> dict[str, Any]:
    """采集环境自述。

    Args:
        command: 产生该报告的完整命令；None 时用 ``sys.argv``。

    Returns:
        可直接 JSON 序列化的自述。取不到的字段为 ``null``，并在同级的 ``*_unavailable``
        或 ``cpu_model_source`` 里说明原因。
    """
    from . import __version__ as harness_version

    model, model_source = cpu_model()
    commit, commit_source = harness_commit()
    uname = platform.uname()

    return {
        "hardware": {
            "cpu_model": model,
            "cpu_model_source": model_source,
            "logical_cores": os.cpu_count(),
            "platform": f"{uname.system}-{uname.machine}",
        },
        "os": {
            "system": uname.system,
            "release": uname.release,
            "version": uname.version,
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "packages": {name: _distribution_version(name) for name in TRACKED_PACKAGES},
        "subject": _subject_identity(),
        "harness": {
            "version": harness_version,
            "commit": commit,
            "commit_source": commit_source,
        },
        "command": list(command if command is not None else sys.argv),
    }


def install_source(distribution: str = "zoo-framework") -> dict[str, Any] | None:
    """被测框架的安装来源（``dist-info/direct_url.json``）。

    **git 安装时这是识别"到底测的是哪个 commit"的唯一凭据**：发行元数据的版本号对分支安装只是
    仓库里写着的声明值（可能滞后于开发、也可能与已发布版本撞号），而 `direct_url.json` 里带着
    真实 commit。报告要能回答"这份数据出自哪个提交"，靠的就是它。
    """
    try:
        raw = metadata.distribution(distribution).read_text("direct_url.json")
    except (metadata.PackageNotFoundError, FileNotFoundError, OSError):
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def drive_generation() -> dict[str, Any]:
    """当前装着的被测框架提供哪一代的驱动面。

    **判定由 :mod:`zoo_bench.generations` 负责**，本模块只负责把它记进自述：哪一代能驱动是
    共用知识，而"这次测量用的是哪个世代的驱动面"是读者判断数字适用范围所需的事实——不记
    下来，同一份报告里看不出被测对象是按哪套派发面驱动的。

    **任何失败都如实记为不可用**：自述采集不该让整轮测量失败（判定失败会在测量单元里以更
    完整的形式报出来），故这里把异常转成 ``error`` 字段。
    """
    try:
        from .generations import probe_drive_generation

        return probe_drive_generation()
    except Exception as exc:
        return {"generation": None, "label": None, "error": f"{type(exc).__name__}: {exc}"}


def _subject_identity() -> dict[str, Any]:
    """被测框架的标识。

    **发行元数据是版本的真源**，模块的 ``__version__`` 只作附注——实测
    ``zoo-framework==0.6.0`` 的 ``zoo_framework.__version__`` 是 ``0.1.1-beta``，与发行
    元数据不一致；若以它为准，报告与 ``matrix.yaml`` 就对不上（design 的版本偏斜风险）。
    """
    identity: dict[str, Any] = {
        "dist_version": _distribution_version("zoo-framework"),
        "module_version": None,
        "module_path": None,
        "install_source": install_source(),
        "drive_generation": drive_generation(),
        "note": "以 dist_version（发行元数据）为版本真源；module_version 仅作附注",
    }

    try:
        import zoo_framework
    except ImportError as exc:
        identity["import_error"] = str(exc)
        return identity

    identity["module_version"] = getattr(zoo_framework, "__version__", None)
    identity["module_path"] = getattr(zoo_framework, "__file__", None)
    return identity
