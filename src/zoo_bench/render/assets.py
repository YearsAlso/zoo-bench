"""站点静态资源（目前只有一份样式表）。

样式**不写成 Python 字符串**，而从 ``style.css`` 读入——这样它有语法高亮、可格式化、可 lint，
也能被单独 review。页面用 ``<link>`` 引用而不是内联 ``<style>``，故浏览器能缓存、且一处改、
全站生效。

**每个输出目录各自带一份** ``style.css``，不用跨目录的相对路径：报告的版本页、对比页、站点
首页分别可能输出到互不相邻的目录（``site/`` 与 ``site/<slug>/``），一个 ``../style.css``
在它们之间必然对不上。
"""

from __future__ import annotations

from pathlib import Path

STYLE_FILENAME = "style.css"

_SOURCE = Path(__file__).resolve().parent / STYLE_FILENAME


def style_text() -> str:
    """样式表的文本。"""
    return _SOURCE.read_text(encoding="utf-8")


def write_style(directory: str | Path) -> Path:
    """把样式表写进给定目录，返回写入路径。

    Args:
        directory: 目标目录（会按需创建）。

    Returns:
        写入的 ``style.css`` 路径。
    """
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / STYLE_FILENAME
    path.write_text(style_text(), encoding="utf-8")
    return path
