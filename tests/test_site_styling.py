"""站点样式与结构的验证。

四项美化各有守卫，但**断言的对象刻意不同**：结构项（锚点、目录、卡片事实）断言语义——有没有、
指到哪；观感项（粘性表头、斑马纹、打印规则）只能断言样式表里**存在那条规则**，因为观感本身
无法用断言表达。清楚这个区别，才不会把"规则在场"当成"看起来好看"。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from zoo_bench import storage
from zoo_bench.render import assets, report
from zoo_bench.render import blocks as blocks_module
from zoo_bench.render import html as html_renderer
from zoo_bench.render import index as index_renderer

FRAMEWORK = "zoo-framework==9.9.9"


def _model() -> dict[str, Any]:
    return {
        "source": {"path": "results/x.json"},
        "environment": {
            "hardware": {"cpu_model": "测试 CPU", "logical_cores": 8, "platform": "Test-AMD64"},
            "os": {"system": "TestOS", "release": "1", "version": "1.0"},
            "python": {"version": "3.13.0", "implementation": "CPython", "executable": "/py"},
            "subject": {"dist_version": "9.9.9", "module_version": "1.0", "note": "以发行元数据为准"},
            "harness": {"version": "0.0.1", "commit": "deadbeef"},
            "command": ["zoo-bench", "run"],
        },
        "run": {"absolute_note": "不可跨运行比较", "warmup_rounds": [3], "measured_rounds": [30]},
        "load": {"is_stand_in": True, "composition": "JSON 编解码", "caveat": "负载是替身"},
        "conclusion": {
            "overhead_threshold": 0.15,
            "overhead_crossings": [],
            "relative_turnings": [],
            "summary": ["结论一句。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {"title": "延迟", "note": "口径", "rows": []},
            "overhead": {"title": "开销", "note": "口径", "rows": []},
            "throughput": {"title": "吞吐", "note": "口径", "rows": []},
            "semantics": {
                "title": "调度语义",
                "status": "not_measured",
                "scope_note": "内部开关对照",
                "items": [],
                "reason": "不可测",
            },
        },
        "unfavorable": {"items": [], "found": True, "note": "无不利数据不合格"},
        "caveats": [],
        "absolute": {"note": "不可跨运行比较", "process_isolation": {}},
        "self_check": {"ok": True},
    }


# ------------------------------------------------------------------ 样式表是一份独立文件


def test_stylesheet_ships_and_is_written_next_to_the_page(tmp_path: Path) -> None:
    """样式表是独立文件、写在与页面同目录处，且页面**引用**它而不是内联。"""
    assert assets.style_text().strip(), "样式表不能是空的"

    outcome = report.render(_model(), tmp_path, threshold=0.15)
    written = tmp_path / assets.STYLE_FILENAME

    assert written.is_file(), "每个输出目录都应带一份 style.css（相对路径引用，不能跨目录）"
    assert written.read_text(encoding="utf-8") == assets.style_text()

    page = Path(outcome["html"]).read_text(encoding="utf-8")
    assert f'<link rel="stylesheet" href="{assets.STYLE_FILENAME}">' in page
    assert "<style>" not in page, "引用外部样式表后不该再内联"


def test_stylesheet_carries_the_requested_rules() -> None:
    """四项目美观里属于观感的三项：规则必须在场。

    **这是"规则在场"而非"看起来好看"**——后者断言不了。故这条守住的是"有人把规则删了"，
    不是视觉质量。
    """
    css = assets.style_text()

    assert "position: sticky" in css, "长表格的粘性表头"
    assert "nth-child(even)" in css, "行斑马纹"
    assert "@media print" in css, "打印样式"
    assert "prefers-color-scheme: dark" in css, "深浅色自适应"
    assert "columns: 2" in css, "目录分栏"


# ------------------------------------------------------------------ 锚点与目录


def test_headings_get_anchors_and_the_toc_links_them() -> None:
    """结构项：每个标题有锚点，目录链到二级标题，且链接目标真实存在。"""
    blocks = blocks_module.build_blocks(_model(), [])
    page = html_renderer.blocks_to_html(blocks)

    section = "结论摘要"
    assert 'id="' in page, "标题必须有锚点，否则目录无处可指"

    assert '<nav class="toc">' in page
    assert section in page
    # 目录里的每个锚点都必须在页面上找得到对应的 id——否则是死链
    for chunk in page.split('<nav class="toc">')[1].split("</nav>")[0].split("<li>"):
        if 'href="#' not in chunk:
            continue
        anchor = chunk.split('href="#')[1].split('"')[0]
        assert f'id="{anchor}"' in page, f"目录里的 {anchor} 在页面上没有对应锚点"


def test_duplicate_headings_get_distinct_anchors() -> None:
    """报告里有大量前缀相同的标题（"维度：…"），锚点不能互相覆盖。"""
    taken: set[str] = set()
    first = html_renderer.slug("维度：延迟", taken=taken)
    second = html_renderer.slug("维度：延迟", taken=taken)

    assert first != second
    assert second.startswith(first)


# ------------------------------------------------------------------ 首页卡片


def _archive(results: Path, run: dict[str, Any]) -> None:
    storage.save_run(
        {"run": run, "units": [], "self_check": {"ok": True}},
        framework=FRAMEWORK,
        root=results,
    )


def test_index_shows_a_card_per_version_with_run_facts(tmp_path: Path) -> None:
    """首页卡片上的事实取自**留档**，不是猜的——生成时间、单元数、自检状态都在那里。"""
    results = tmp_path / "results"
    site = tmp_path / "site"
    slug = storage.framework_slug(FRAMEWORK)

    _archive(
        results,
        {
            "started_at_epoch": 1767225600.0,
            "unit_count": 96,
            "failed_unit_count": 4,
            "absolute_note": "不可跨运行比较",
        },
    )
    (site / slug).mkdir(parents=True)
    (site / slug / "index.html").write_text("x", encoding="utf-8")
    (site / slug / "report.pdf").write_bytes(b"%PDF-1.4")

    page = index_renderer.render(results, site).read_text(encoding="utf-8")

    assert 'class="version-card"' in page
    assert slug in page
    assert "96" in page, "有效单元数应来自留档"
    assert "4" in page, "失败单元数应来自留档"
    assert "自检通过" in page
    assert f'href="{slug}/index.html"' in page
    assert f'href="{slug}/report.pdf"' in page
    assert "2026-01-01" in page, "生成时间应来自留档的 started_at_epoch（UTC）"


def test_index_omits_versions_whose_report_was_not_rendered(tmp_path: Path) -> None:
    """报告页不存在的版本不进列表——链接不能指向空处。"""
    results = tmp_path / "results"
    site = tmp_path / "site"
    _archive(results, {"started_at_epoch": 1767225600.0, "unit_count": 1})

    page = index_renderer.render(results, site).read_text(encoding="utf-8")

    assert "version-card" not in page
    assert "还没有任何已渲染的报告" in page


def test_index_lists_compare_pages_with_their_labels(tmp_path: Path) -> None:
    """对比页要能从首页进得去，且标题取自**对比自己的结果**而不是目录名。

    目录名是 slug 拼出来的（`compare-zoo-framework-0.6.0--zoo-framework-0.7.1b0`），而 slug 里
    本来就有连字符——照着目录名解析出来的标签迟早对不上。故标题读 `compare.json` 里两侧的标签。
    """
    site = tmp_path / "site"
    directory = site / "compare-zoo-framework-0.6.0--zoo-framework-0.7.1b0"
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("x", encoding="utf-8")
    (directory / "compare.json").write_text(
        json.dumps(
            {
                "before": {"label": "0.6.0", "self_check_ok": True},
                "after": {"label": "0.7.1b0", "self_check_ok": True},
                "shared_unit_count": 96,
            }
        ),
        encoding="utf-8",
    )

    page = index_renderer.render(tmp_path / "results", site).read_text(encoding="utf-8")

    assert "compare-card" in page
    assert "0.6.0 → 0.7.1b0" in page, "标题应取对比结果里的标签"
    assert "96" in page, "共有单元数应来自对比结果"
    assert "两侧自检均通过" in page
    assert 'href="compare-zoo-framework-0.6.0--zoo-framework-0.7.1b0/index.html"' in page


def test_index_omits_compare_directories_without_a_page(tmp_path: Path) -> None:
    """只有目录、没有对比页时不进列表——链接不能指向空处。"""
    (tmp_path / "site" / "compare-a--b").mkdir(parents=True)

    page = index_renderer.render(tmp_path / "results", tmp_path / "site").read_text(
        encoding="utf-8"
    )

    assert "compare-card" not in page


def test_compare_card_does_not_fabricate_a_trust_verdict(tmp_path: Path) -> None:
    """读不到机器可读结果时**不猜"自检通过"**——"不知道"与"通过了"是两回事，
    而这一栏正是读者判断这份对比可不可信的依据。"""
    site = tmp_path / "site"
    directory = site / "compare-a--b"
    directory.mkdir(parents=True)
    (directory / "index.html").write_text("x", encoding="utf-8")

    page = index_renderer.render(tmp_path / "results", site).read_text(encoding="utf-8")

    assert "compare-card" in page, "对比页存在就该能进得去"
    assert "两侧自检均通过" not in page
    assert "不作数" not in page, "读不到状态时既不能说通过、也不能说未通过"


def test_index_ships_the_stylesheet(tmp_path: Path) -> None:
    """首页也要能拿到样式——它是读者进入报告的唯一入口。"""
    site = tmp_path / "site"
    index_renderer.render(tmp_path / "results", site)

    assert (site / assets.STYLE_FILENAME).is_file()
    assert f'<link rel="stylesheet" href="{assets.STYLE_FILENAME}">' in (
        site / "index.html"
    ).read_text(encoding="utf-8")
