"""被测目标的解析。

矩阵里的一个条目有两种写法，这里给出唯一的解释：

- ``包名==版本`` —— PyPI 上某个已发布的版本
- ``包名 @ git+<仓库地址>@<分支/标签/commit>`` —— 某个 git 引用的 HEAD（``包名`` 可省略）

**独立成模块是为了只有一个解释器**：目录命名（``storage``）与安装校验（``cli``）都要用到
"包名/版本/引用"这三样东西，各自解析一遍必然漂移——实测就出现过同一份 spec 在被测目标的
文件名里变成 `zoo-framework-dev`、而在 slug 里变成整条 URL 的情形。
"""

from __future__ import annotations

from dataclasses import dataclass


class MatrixError(RuntimeError):
    """矩阵文件缺失、字段不合法，或条目无法识别。"""


@dataclass(frozen=True)
class FrameworkTarget:
    """一个被测目标的解释结果。

    Attributes:
        specifier: 原样交给 pip 的规格串。
        name: 分发包名。
        kind: ``"pypi"`` 或 ``"git"``。
        version: PyPI 目标的版本号；git 目标为 None。
        url: git 目标的仓库地址；PyPI 目标为 None。
        ref: git 目标的分支/标签/commit；PyPI 目标为 None。
    """

    specifier: str
    name: str
    kind: str
    version: str | None = None
    url: str | None = None
    ref: str | None = None

    @property
    def label(self) -> str:
        """报告与界面上用的短标签。"""
        if self.kind == "pypi":
            return f"{self.name} {self.version}"
        return f"{self.name} {self.ref}"


def _name_from_url(url: str) -> str:
    """从仓库地址推断包名（``.../zoo-framework.git`` -> ``zoo-framework``）。"""
    return url.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


def parse_target(specifier: str) -> FrameworkTarget:
    """把矩阵里的一个条目解释成被测目标。

    两种写法都要支持：已发布版本是读者能装到的东西，仓库分支是"还没发但已经改了"的状态；
    只测前者会让报告落后于开发，只测后者则报告里没有可安装的对应物。

    Args:
        specifier: 矩阵条目原文。

    Returns:
        解释结果。

    Raises:
        MatrixError: 写法无法识别（含 git 目标缺 ``@<引用>``）。
    """
    text = specifier.strip()

    if "git+" in text:
        url_part = text.split("git+", 1)[1]
        # 引用在最后一个 @ 之后（正则里 URL 可能自带 @，如带凭据的地址）
        url, _, ref = url_part.rpartition("@")
        if not url or not ref:
            raise MatrixError(
                f"{specifier!r} 看起来是 git 目标，但缺少 @<分支/标签/commit>——"
                "没有它报告无法说明测的是哪个引用"
            )

        # `包名 @ git+URL@ref` 与 `git+URL@ref` 两种写法都要认：
        # 前者的 @ 之前是包名，后者那里是 URL 本身（靠 :// 区分）
        prefix = text.split("@", 1)[0].strip()
        named = bool(prefix) and "://" not in prefix and not prefix.startswith("git+")
        return FrameworkTarget(
            specifier=text,
            name=prefix if named else _name_from_url(url),
            kind="git",
            url=url,
            ref=ref,
        )

    if "==" in text:
        name, _, version = text.partition("==")
        if not name.strip() or not version.strip():
            raise MatrixError(f"{specifier!r} 缺少包名或版本号")
        return FrameworkTarget(
            specifier=text, name=name.strip(), kind="pypi", version=version.strip()
        )

    raise MatrixError(
        f"{specifier!r} 无法识别：要么写成 `包名==版本`，要么写成 "
        "`包名 @ git+<仓库地址>@<分支/标签/commit>`"
    )
