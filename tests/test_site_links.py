"""整站内部链接与双语数字的一致性（tasks 4.4 / 4.5）。

两条判据都只能在**整站**上验：

1. **内部链接可达**——相对路径的层数差一层就指到空处，而单页用例只知道自己那一页：首页链到版本页、
   版本页链到另一语言的自己，任何一条算错都能让读者走到 404。故遍历站点所有 ``<a href>``（以及
   样式表 ``<link href>``），逐个断言目标文件存在。
2. **两种语言里同一个结论给出同一个数字**——散文随语言变，数字不随语言变。

站点由三条命令真实产出（``render`` / ``compare`` / ``index``），而不是手搭文件树：链接的层数约定
正是这三条命令之间的约定，手搭就测不到它们是否还对得上。
"""

from __future__ import annotations

import re
from pathlib import Path

# 复用双语产物用例的 fixture：它由真实的 build_model 走通，且含逐轮样本（分类要算带宽）
from test_bilingual_outputs import _ENVIRONMENT, _result

from zoo_bench import cli, storage

VERSIONS = ("1.0.0", "2.0.0")

#: 对照方案比被测框架慢/快的比例；1.0 以下 = 被测框架处于劣势（发布门禁据此放行）
RATIOS = (0.8, 0.9)

_HREF = re.compile(r'href="([^"]+)"')
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _archive(results: Path, version: str, *, ratio: float) -> None:
    """留档一份桩数据：环境自述齐备、比例保证"有不利数据"（否则渲染被门禁拒发）。"""
    result = _result()
    result["environment"] = _ENVIRONMENT
    for comparison in result["relative"]["comparisons"]:
        comparison["ratios_vs_subject"] = {"baseline-a": ratio}
    storage.save_run(result, framework=f"zoo-framework=={version}", root=results)


def _build_site(tmp_path: Path) -> Path:
    """用三条真实命令搭一个完整站点，返回站点根。

    命令的顺序与 bench.yml 一致（先逐版本 render、再 compare、最后 index）——站点首页最后写，
    故它能收录全部版本页与对比页。
    """
    results = tmp_path / "results"
    site = tmp_path / "site"
    slugs: list[str] = []

    for version, ratio in zip(VERSIONS, RATIOS, strict=True):
        _archive(results, version, ratio=ratio)
        slug = storage.framework_slug(f"zoo-framework=={version}")
        slugs.append(slug)
        assert (
            cli.main(
                [
                    "render",
                    "--framework",
                    version,
                    "--results",
                    str(results),
                    "--out",
                    str(site / slug),
                ]
            )
            == 0
        ), f"{version} 的报告没渲染成"

    assert (
        cli.main(
            [
                "compare",
                VERSIONS[0],
                VERSIONS[1],
                "--results",
                str(results),
                "--out",
                str(site / f"compare-{slugs[0]}--{slugs[1]}"),
            ]
        )
        == 0
    ), "对比页没生成"

    assert cli.main(["index", "--results", str(results), "--site", str(site)]) == 0
    return site


def _internal_hrefs(page: Path) -> list[str]:
    """页面上的内部链接：排除外链与 ``#`` 锚点（锚点指页内，站点遍历管不到）。"""
    return [
        href
        for href in _HREF.findall(page.read_text(encoding="utf-8"))
        if not href.startswith(("http://", "https://", "mailto:", "#"))
    ]


def test_every_internal_link_resolves_to_a_file(tmp_path: Path) -> None:
    """站点里每一条内部链接都指得到文件——**这正是相对路径算错时唯一守得住的地方**。"""
    site = _build_site(tmp_path)
    pages = sorted(site.rglob("*.html"))

    # 站点首页 ×2、两个版本页 ×2、对比页 ×2；页数不对说明有产物没写出来（下面就成了空断言）
    assert len(pages) == 8, f"站点页数不对：{[p.relative_to(site).as_posix() for p in pages]}"

    checked = 0
    missing: list[str] = []
    for page in pages:
        hrefs = _internal_hrefs(page)
        assert hrefs, f"{page.relative_to(site).as_posix()} 一条内部链接都没有"
        checked += len(hrefs)
        missing.extend(
            f"{page.relative_to(site).as_posix()} -> {href}"
            for href in hrefs
            if not (page.parent / href).resolve().is_file()
        )

    assert checked >= 8, "遍历到的链接太少，判据的覆盖面不够"
    assert not missing, f"站点里有指不到文件的内部链接：{missing}"


def test_language_switch_links_are_on_every_page(tmp_path: Path) -> None:
    """语言互链必须真的在页面上——否则上面的遍历恰好漏掉最该守的那类链接（spec 场景「语言互链可达」）。

    每页的"另一语言"都是它的邻居：同目录下的 ``zh/``（英文页）或上一层（中文页）。站点首页、
    版本页、对比页三种页面都查。
    """
    site = _build_site(tmp_path)
    slug = storage.framework_slug(f"zoo-framework=={VERSIONS[0]}")
    compare = f"compare-{slug}--{storage.framework_slug(f'zoo-framework=={VERSIONS[1]}')}"

    expected = {
        "index.html": "zh/index.html",
        f"{slug}/index.html": "zh/index.html",
        f"{slug}/zh/index.html": "../index.html",
        f"{compare}/index.html": "zh/index.html",
        f"{compare}/zh/index.html": "../index.html",
        "zh/index.html": "../index.html",
    }
    for relative, href in expected.items():
        page = site / relative
        assert page.is_file(), f"缺页面：{relative}"
        assert href in _internal_hrefs(page), f"{relative} 的语言切换链接不是 {href}"


def test_both_languages_report_the_same_numbers(tmp_path: Path) -> None:
    """同一次留档渲染两遍：**数字必须逐字一致**（散文随语言变，数字不随语言变）。

    判据是"数字多重集相等"而不是"某几个数看着对"：它同时守住"少印了一个数"与"印成了另一个数"。
    """
    site = _build_site(tmp_path)
    slug = storage.framework_slug(f"zoo-framework=={VERSIONS[0]}")
    compare = f"compare-{slug}--{storage.framework_slug(f'zoo-framework=={VERSIONS[1]}')}"

    for english_path, chinese_path in (
        (site / slug / "report.md", site / slug / "zh" / "report.md"),
        (site / compare / "compare.md", site / compare / "zh" / "compare.md"),
    ):
        english = sorted(_NUMBER.findall(english_path.read_text(encoding="utf-8")))
        chinese = sorted(_NUMBER.findall(chinese_path.read_text(encoding="utf-8")))

        assert english, f"{english_path.name} 里一个数字都没有，比较是空断言"
        assert english == chinese, (
            f"{english_path.parent.relative_to(site)} 的两种语言数字不一致："
            f"英文 {english} / 中文 {chinese}"
        )
