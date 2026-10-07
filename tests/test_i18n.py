"""消息目录的完整性与判据（spec: comparison-report 的双语要求）。

两条判据都是**结构性**的：漏一条译文、或模板参数与调用点对不上，都**不会让报告生成失败**——
前者表现为英文页里冒出一句中文，后者会把 `{tier}` 原样印出来。两种都只能在用例里钉住。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from zoo_bench import i18n


def test_every_key_has_both_languages() -> None:
    """漏译必须在测试里就暴露，而不是等读者在英文页里读到一句中文。"""
    assert i18n.MESSAGES, "目录不能是空的"
    assert i18n.missing_translations() == []


def test_missing_key_raises_instead_of_falling_back() -> None:
    """缺键必须抛：回落或留空的表现都**看不出是错**（报告照样生成）。"""
    with pytest.raises(i18n.MissingTranslation):
        i18n.t("no.such.key", i18n.LANG_EN)


def test_template_parameter_mismatch_raises_both_ways() -> None:
    """少给参数会把 `{tier}` 印出来；多给参数说明调用点还在用旧签名。两者都必须抛。"""
    with pytest.raises(i18n.MissingTranslation):
        i18n.t("report.summary.crossing_found", i18n.LANG_EN, tier=1)
    with pytest.raises(i18n.MissingTranslation):
        i18n.t("report.dimension.latency.title", i18n.LANG_EN, extra=1)


def test_language_dirs_put_the_primary_language_at_the_root() -> None:
    """主语言占根路径、其余语言各占一个子目录（design D3）——都放根路径会互相覆盖。"""
    assert i18n.DEFAULT_LANG == i18n.LANG_EN
    assert i18n.LANGUAGE_DIRS[i18n.DEFAULT_LANG] == ""
    for lang in i18n.LANGS:
        if lang != i18n.DEFAULT_LANG:
            assert i18n.LANGUAGE_DIRS[lang], f"{lang} 需要一个子目录，否则两种语言会互相覆盖"


def test_english_messages_are_ascii() -> None:
    """英文文案必须**全 ASCII**（design D6）。

    导出前的字符白名单本来是按**中文字体覆盖**逐字试出来的（实测 SimHei 缺 `µ`），把 ASCII
    之外的字交给它等于把方框风险重新引进英文报告。故在目录这一层就把住：英文文案里出现非 ASCII
    即失败——顺带把"顺手打了中文标点"这类也拦下。

    **语言切换的标签豁免**：它是目标语言的本名（英文页上写「中文」）——导航惯例，找得到回家
    的路的恰恰是不认识当前页文字的那个人。它被 ``<a class="lang-switch">`` 标记、双语用例在
    可见文本检查里将其剥离；字体白名单管的是 PDF 正文，管不到一个链接标签。
    """
    exempt = {key for key in i18n.MESSAGES if key.startswith("lang_name.")}
    offenders = {
        key: [ch for ch in entry[i18n.LANG_EN] if ord(ch) > 127]
        for key, entry in i18n.MESSAGES.items()
        if key not in exempt and not entry[i18n.LANG_EN].isascii()
    }
    assert not offenders, f"这些英文文案含非 ASCII 字符：{offenders}"


TRANSLATED_MODULES = (
    "report.py",
    "render/blocks.py",
    "semantics.py",
    "environment.py",
    "attribution.py",
)


def _cjk_f_strings(path: str) -> dict[int, str]:
    """一个模块里**带中文的 f-string**，按行号列出（``raise`` 携带的报错串豁免）。"""
    src = Path(__file__).parents[1].joinpath("src", "zoo_bench", path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 报错串只进 stderr/CLI，不进报告：raise 现场豁免；为报错先拼 detail 的函数
    # （blocks 的字符白名单守卫）同样豁免
    exempt: set[int] = set()
    for func in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(item, ast.Raise)
            and item.exc is not None
            and "UnsafeReportText" in ast.dump(item.exc)
            for item in ast.walk(func)
        ):
            exempt |= {id(item) for item in ast.walk(func) if isinstance(item, ast.JoinedStr)}
    offenders: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and id(node) not in exempt:
            for value in node.values:
                if (
                    isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and any("一" <= ch <= "鿿" for ch in value.value)
                ):
                    offenders[node.lineno] = value.value
    return offenders


def test_no_chinese_f_strings_remain_in_the_translated_modules() -> None:
    """文案已入目录的模块里不许再残留**带中文的 f-string**（task 2.4 / design D8）。

    f 前缀没去掉就说明模板没接上，而这类残留**不会被任何功能断言发现**——两种语言的报告
    照样生成，英文页里会混进一句写死的中文。故按 AST 扫源码：

    - 扫描对象是文案已入目录的模块（report / blocks / semantics / environment /
      attribution）：它们的产品就是报告文字或进报告的键值数据；
    - ``raise`` 携带的报错串豁免——它只进 stderr，不进报告（blocks 的字符白名单报错即此类）；
    - ``runner.py`` 不在扫描之列：它的中文 f-string 全部落在**有意保留**的 CLI/自检区
      （见 runner.py 顶部的 D8 边界注释），而它进报告的值由 test_attribution /
      test_report_model 以"必须是目录键"正面钉住——那种钉法才抓得住漏网。
    """
    offenders = {path: hits for path in TRANSLATED_MODULES if (hits := _cjk_f_strings(path))}
    assert not offenders, f"这些模块仍有带中文的 f-string（模板没接上）：{offenders}"
