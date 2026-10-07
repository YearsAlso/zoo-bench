"""中日韩字体的解析。

本项目文档是中文，而 matplotlib 与 reportlab 的默认字体都渲染不出中文（得到方框或丢字），
CI 的 `ubuntu-latest` 默认也没有中文字体。故字体必须**显式解析**：按候选列表找到第一个可用
的；**找不到就明确失败并给出可执行的安装指引**——静默降级成方框比报错更坏，因为产出的文件
看起来是成功的（spec: comparison-report 的硬要求）。

解析结果由调用方记录进报告，使"中文为何能显示"可复核。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 指定字体文件的环境变量。本地与测试用它覆盖。
FONT_ENV = "ZOO_BENCH_CJK_FONT"

#: 候选字体，**顺序即优先级**。单体字体（TTF/OTF）排在字体集合（TTC）之前：实测
#: matplotlib 能直接读 Windows 的 `msyh.ttc`，但 reportlab 读 TTC 需要额外的 face 索引，
#: 把单体排前面能少一类问题。
CANDIDATES: tuple[str, ...] = (
    # Windows
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",
    # Debian / Ubuntu 的 fonts-noto-cjk（**CFF 轮廓**：matplotlib 能用，reportlab 不能）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    # TrueType 轮廓的 CJK 字体：**PDF 侧必须用这类**，见 find_pdf_font
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttf",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
)

#: TTC 是字体集合，逐个 face 试注册的上限。
MAX_TTC_FACES = 16

_INSTALL_HINT = (
    "找不到可用的中文字体，导出会得到方框或丢字。请安装以下任一并重试：\n"
    "  Debian/Ubuntu:  sudo apt-get install -y fonts-noto-cjk fonts-wqy-zenhei\n"
    "  Fedora/RHEL:    sudo dnf install -y google-noto-sans-cjk-fonts wqy-zenhei-fonts\n"
    "  macOS:          系统自带 PingFang，通常无需安装\n"
    f"  或设置环境变量 {FONT_ENV} 指向一个中文字体文件\n"
    "**PDF 另需 TrueType 轮廓的字体**：fonts-noto-cjk 是 CFF 轮廓，matplotlib 能用但\n"
    "reportlab（PDF）不能嵌——故上面一并装了 fonts-wqy-zenhei"
)


class CjkFontUnavailable(RuntimeError):
    """找不到任何可用的中文字体。

    刻意不用 ``FileNotFoundError``：调用方需要的是"该装什么"，不是"文件不存在"。
    """


def font_candidates() -> list[str]:
    """候选字体路径，环境变量指定的排在最前。

    Returns:
        路径字符串列表。
    """
    override = os.environ.get(FONT_ENV)
    return ([override] if override else []) + list(CANDIDATES)


def find_cjk_font(candidates: list[str] | None = None) -> Path:
    """解析出第一个存在的中文字体文件。

    Args:
        candidates: 覆盖候选列表（测试用）。

    Returns:
        字体文件路径。

    Raises:
        CjkFontUnavailable: 候选里一个都不存在。
    """
    tried = candidates if candidates is not None else font_candidates()
    for candidate in tried:
        path = Path(candidate)
        if path.is_file():
            return path

    raise CjkFontUnavailable(f"{_INSTALL_HINT}\n已尝试：{list(tried)}")


def apply_to_matplotlib(font: Path) -> str:
    """把字体设进 matplotlib，返回它注册的家族名。

    Args:
        font: 字体文件路径。

    Returns:
        家族名，供调用方记录进报告。
    """
    from matplotlib import font_manager, rcParams

    font_manager.fontManager.addfont(str(font))
    family = font_manager.FontProperties(fname=str(font)).get_name()

    rcParams["font.sans-serif"] = [family, *rcParams["font.sans-serif"]]
    # CJK 字体常缺真正的减号字形，不关掉会用方框代替负号
    rcParams["axes.unicode_minus"] = False
    return family


def font_covers(font: Path, text: str) -> tuple[bool, list[str]]:
    """字体是否覆盖 ``text`` 里的全部字符。

    报告里的文字会被**按轮廓嵌入**，缺字不会自动落到别的字体上——只会变成方框。故在选字体
    时就要能问"它认不认得我要写的字"。实测 SimHei 缺 ``µ``（MICRO SIGN），这类字符肉眼扫
    代码看不出来，只能机械检查。

    Args:
        font: 字体文件路径。
        text: 待检查的文本。

    Returns:
        ``(是否全覆盖, 缺失的字符列表)``。
    """
    from fontTools.ttLib import TTFont

    with TTFont(str(font), fontNumber=0, lazy=True) as handle:
        cmap: set[int] = set()
        for table in handle["cmap"].tables:
            cmap.update(table.cmap.keys())

    missing = sorted({character for character in text if ord(character) not in cmap})
    return not missing, missing


def register_for_reportlab(font: Path) -> dict[str, object]:
    """把字体注册进 reportlab，返回 ``{"name": 注册名, "face_index": ...}``。

    **TTC 要按 face 索引注册**（它是字体集合），逐个 face 试到成功为止。**CFF 轮廓的字体
    （如 fonts-noto-cjk 的 .ttc/.otf）注册会失败**——reportlab 只能嵌 TrueType 轮廓，这是它
    的限制而不是配置问题。故本函数把"能不能嵌"当作选字体的判据之一，见 :func:`find_pdf_font`。

    Args:
        font: 字体文件路径。

    Returns:
        ``{"name": str, "face_index": int | None, "path": str}``。

    Raises:
        CjkFontUnavailable: 该字体无法被 reportlab 注册（例如 CFF 轮廓、或集合里所有 face 都失败）。
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    attempts: list[str] = []
    indices: list[int | None] = (
        list(range(MAX_TTC_FACES)) if font.suffix.lower() == ".ttc" else [None]
    )

    for index in indices:
        name = f"zoo-bench-cjk-{index}" if index is not None else "zoo-bench-cjk"
        try:
            pdfmetrics.registerFont(TTFont(name, str(font), subfontIndex=index or 0))
        except Exception as exc:
            attempts.append(f"face {index}: {type(exc).__name__}: {exc}")
            continue

        # 把 bold / italic 全映射到同一张脸：否则 `<b>` 会去找拉丁粗体，中文就变成方框——
        # 代价是 PDF 里粗体不再有视觉区分，这是可接受的取舍（graph 里另有结构提示）
        pdfmetrics.registerFontFamily(name, normal=name, bold=name, italic=name, boldItalic=name)
        return {"name": name, "face_index": index, "path": str(font)}

    raise CjkFontUnavailable(
        f"字体 {font} 无法被 reportlab 注册（reportlab 只能嵌 TrueType 轮廓，CFF 轮廓的字体"
        f"如 fonts-noto-cjk 不行）。已尝试：{attempts[:3]}\n{_INSTALL_HINT}"
    )


def find_cjk_font_covering(text: str, candidates: list[str] | None = None) -> Path:
    """解析出**能覆盖 ``text`` 全部字符**的中文字体（供 matplotlib 出图用）。

    matplotlib 能读 CFF 轮廓的字体，故这里只管"认不认得这些字"。

    Args:
        text: 报告实际会渲染的文本。
        candidates: 覆盖候选列表（测试用）。

    Returns:
        字体文件路径。

    Raises:
        CjkFontUnavailable: 没有候选文件存在，或没有一个能覆盖 ``text``。
    """
    tried = candidates if candidates is not None else font_candidates()
    existing = [path for path in tried if Path(path).is_file()]
    if not existing:
        raise CjkFontUnavailable(f"{_INSTALL_HINT}\n已尝试：{list(tried)}")

    gaps: dict[str, list[str]] = {}
    for path in existing:
        covered, missing = font_covers(Path(path), text)
        if covered:
            return Path(path)
        gaps[path] = missing

    raise CjkFontUnavailable(
        f"候选字体都存在，但没有一个覆盖报告要渲染的全部字符。缺失情况：{gaps}\n{_INSTALL_HINT}"
    )


def find_pdf_font(text: str, candidates: list[str] | None = None) -> dict[str, object]:
    """解析出**既能覆盖 ``text``、又能被 reportlab 嵌入**的中文字体。

    两个条件都必要：覆盖不够会出方框，嵌不进去则 PDF 根本出不来。CFF 轮廓的字体在这里会被
    跳过并记下原因，而不是抛出去让调用方猜。

    Args:
        text: PDF 实际会渲染的文本。
        candidates: 覆盖候选列表（测试用）。

    Returns:
        :func:`register_for_reportlab` 的返回值，另加 ``"skipped"`` 记录被跳过的候选与原因。

    Raises:
        CjkFontUnavailable: 没有候选文件存在，或没有一个同时满足两个条件。
    """
    tried = candidates if candidates is not None else font_candidates()
    existing = [path for path in tried if Path(path).is_file()]
    if not existing:
        raise CjkFontUnavailable(f"{_INSTALL_HINT}\n已尝试：{list(tried)}")

    skipped: list[str] = []
    for path in existing:
        covered, missing = font_covers(Path(path), text)
        if not covered:
            skipped.append(f"{path}：缺字符 {missing[:8]}")
            continue
        try:
            registered = register_for_reportlab(Path(path))
        except CjkFontUnavailable as exc:
            skipped.append(f"{path}：{exc}".splitlines()[0])
            continue
        return {**registered, "skipped": skipped}

    raise CjkFontUnavailable(
        "候选字体都存在，但没有一个同时满足「覆盖报告文本」与「能被 reportlab 嵌入」。\n"
        "逐条原因：\n  " + "\n  ".join(skipped) + f"\n{_INSTALL_HINT}"
    )
