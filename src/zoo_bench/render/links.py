"""站点内部链接与文件位置的唯一构造处（design D4）。

两种语言各占"一棵树"的一层：主语言（英文）在树的根、其余语言在自己的子目录（``zh/``，
design D3）。站点里有**三棵同形的树**，各由一条命令写出——站点首页（``index --site site``）、
每个版本目录（``render --out site/<slug>``）、每个对比目录（``compare --out site/compare-X``）；
每棵树里英文页面在根、中文页面在 ``<自己>`` 下的 ``zh/``。于是"另一语言的同一页"永远是同目录下
的 ``zh/`` 或上一层，链接与页面在哪棵树里无关。

所有跨页链接都从这里构造，任何地方都不手写 ``../``：``assets.py`` 的注释记过，相对路径的层数
差一层就指到空处，而"哪条链接指空"只有全站遍历的用例兜得住（tasks 4.4）。
"""

from __future__ import annotations

from ..i18n import LANG_EN, LANG_ZH, LANGUAGE_DIRS, other_language, t
from .assets import STYLE_FILENAME

INDEX_FILENAME = "index.html"
MARKDOWN_FILENAME = "report.md"
PDF_FILENAME = "report.pdf"


def subtree_path(lang: str, *segments: str) -> str:
    """语言树根下某文件的站点根相对路径：主语言直接取 segments，其余语言先过 ``LANGUAGE_DIRS``。

    **只用于站点首页**（``index.html`` 与 ``zh/index.html``）——它在语言树根。版本页与对比页
    另有自己的语言摆放（见 :func:`directory_path`）。

    Args:
        lang: 目标语言。
        *segments: 语言树内该文件的路径段。

    Returns:
        形如 ``zh/index.html`` 的站点根相对路径。
    """
    prefix = (LANGUAGE_DIRS[lang],) if LANGUAGE_DIRS[lang] else ()
    return "/".join((*prefix, *segments))


def directory_path(lang: str, directory: str, *segments: str) -> str:
    """**内容目录**（版本目录 / 对比目录）里该语言页面的站点根相对路径。

    语言树在内容目录这一层**不是简单镜像**：英文页面在目录自身（``<dir>/index.html``），中文
    页面在该目录下的 ``zh/``（``<dir>/zh/index.html``）。这样每一页的另一语言版本都恰好是它的
    邻居——同目录下的 ``zh/`` 或上一层——语言切换链接因此与页面在站点里的深度无关。

    Args:
        lang: 目标语言。
        directory: 内容目录名（如 ``zoo-framework-0.9.0``、``compare-a--b``）。
        *segments: 该目录内的路径段。

    Returns:
        形如 ``zoo-framework-0.9.0/zh/report.md`` 的站点根相对路径。
    """
    own = (LANGUAGE_DIRS[lang],) if LANGUAGE_DIRS[lang] else ()
    return "/".join((directory, *own, *segments))


def language_switch_href(lang: str) -> str:
    """同一页面另一语言版本的链接。

    两层语言树互为镜像：每棵树都是"多包一层自己的目录"，故英文页上的中文版永远在它自己的
    ``zh/`` 子目录里、中文页上的英文版永远在其上一层的同名文件——链接与页面在树里的深度无关。
    """
    if lang == LANG_EN:
        return subtree_path(LANG_ZH, INDEX_FILENAME)
    return "../" + INDEX_FILENAME


def language_switch_label(lang: str) -> str:
    """语言切换链接的标签：目标语言的本名（英文页上写「中文」，中文页上写 English）。

    本名是导航惯例——找得到回家的路的人恰恰是不认识当前页文字的那个人。它豁免于英文页的
    ASCII 门禁（导航不是正文）。
    """
    return t(f"lang_name.{other_language(lang)}", lang)


def page_depth(lang: str) -> int:
    """页面在**自己那棵树**里的层数：主语言在根（0 层），其余语言在自己的子目录里（1 层）。

    每条命令只写自己那棵树——`render --out site/<slug>`、`compare --out site/compare-X`、
    `index --site site`——故渲染器算相对路径时只知道自己树的根（``out``），不知道整站在它上面
    套了几层。页面对"另一个语言"与"站点内的兄弟文件"的链接因此都从这里出发计算。
    """
    return 0 if lang == LANG_EN else 1


def href(depth: int, target: str) -> str:
    """从自己那棵树的第 ``depth`` 层的页面指向树根下 ``target`` 的相对链接。"""
    return "../" * depth + target


def stylesheet_href(lang: str) -> str:
    """页面该引用的样式表相对路径——它也是一条跨目录链接，同样集中在这里算。

    样式表与引用它的页面**同树**（各命令自己写一份到自己的输出根），故中文页要回上一层。
    """
    return href(page_depth(lang), STYLE_FILENAME)
