"""块层、HTML 序列化器与站点首页的验证（spec: comparison-report 的同源要求）。

**"同源"在这里第一次能真正验**：Markdown 与 HTML 是两个独立后端，若它们各自从模型里拼章节，
数字就可能不一致。所以断言写成"扰动模型里一个数字，两个后端都得跟着变"——若某个后端自留一份
数据，它就不跟着变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from zoo_bench import cli, storage
from zoo_bench.render import blocks as blocks_module
from zoo_bench.render import html as html_renderer
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.runner import ABSOLUTE_NOTE

FRAMEWORK = "zoo-framework==9.9.9"


def _model(*, ratio: float = 0.26, command: str = "zoo-bench run") -> dict[str, Any]:
    return {
        "source": {"path": "results/x.json"},
        "environment": {
            "hardware": {"cpu_model": "测试 CPU", "logical_cores": 8, "platform": "Test-AMD64"},
            "os": {"system": "TestOS", "release": "1", "version": "1.0"},
            "python": {"version": "3.13.0", "implementation": "CPython", "executable": "/py"},
            "subject": {"dist_version": "9.9.9", "module_version": "1.0", "note": "以发行元数据为准"},
            "harness": {"version": "0.0.1", "commit": "deadbeef"},
            "command": command.split(),
        },
        "load": {"is_stand_in": True, "composition": "JSON 编解码", "caveat": "负载是替身"},
        "conclusion": {
            "overhead_threshold": 0.15,
            "overhead_crossings": [],
            "relative_turnings": [],
            "summary": ["执行体时长达到约 2700 µs 时开销占比降到 15% 以下。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {
                "title": "延迟分位数与抖动",
                "note": "端到端 ÷ 并发度",
                "rows": [
                    {
                        "adapter": "zoo",
                        "concurrency": 4,
                        "body_tier_us": 300.0,
                        "end_to_end_per_task": {
                            "median": 0.00035,
                            "p95": 0.0004,
                            "p99": 0.00045,
                            "relative_spread": 0.05,
                        },
                    }
                ],
            },
            "overhead": {
                "title": "框架自身开销占比",
                "note": "同一次运行内埋点",
                "rows": [
                    {
                        "adapter": "zoo",
                        "concurrency": 4,
                        "body_tier_us": 300.0,
                        "framework_overhead_seconds": 0.0001,
                        "framework_overhead_ratio": ratio,
                        "body_seconds": 0.0003,
                    }
                ],
            },
            "throughput": {
                "title": "吞吐与并发伸缩",
                "note": "并发度 ÷ 端到端中位数",
                "rows": [
                    {
                        "adapter": "zoo",
                        "concurrency": 4,
                        "body_tier_us": 300.0,
                        "throughput_per_second": 7000.0,
                    }
                ],
            },
            "semantics": {
                "title": "调度语义的代价",
                "status": "not_measured",
                "scope_note": "内部开关对照，不与对照方案横向比较",
                "items": [],
                "reason": "0.6.0 上没有一项可测",
            },
        },
        "unfavorable": {
            "items": [
                {
                    "baseline": "bare_thread",
                    "concurrency": 4,
                    "body_tier_us": 300.0,
                    "subject_median_seconds": 0.00035,
                    "gap": "bare_thread 比 zoo 快 1.25x",
                }
            ],
            "found": True,
            "note": "不利数据缺失的报告不合格",
        },
        "caveats": [{"kind": "被测层级", "text": "调度派发层"}],
        "absolute": {"note": ABSOLUTE_NOTE, "process_isolation": {"all_distinct": True}},
        "self_check": {"ok": True},
    }


# ------------------------------------------------------------------ 块层


def test_blocks_cover_every_required_section() -> None:
    headings = [
        block.text for block in blocks_module.build_blocks(_model(), []) if block.kind == blocks_module.HEADING
    ]
    for expected in (
        "zoo-bench 性能报告",
        "运行环境",
        "负载",
        "结论摘要",
        "维度：延迟分位数与抖动",
        "维度：框架自身开销占比",
        "维度：吞吐与并发伸缩",
        "维度：调度语义的代价",
        "公开的不利数据",
        "口径局限与偏差来源",
    ):
        assert expected in headings, f"块层缺少章节：{expected}"


def test_blocks_are_output_agnostic() -> None:
    """块本身不该带任何输出格式的语法。"""
    for block in blocks_module.build_blocks(_model(), []):
        assert not block.text.startswith("#")
        assert "<" not in block.src


# ------------------------------------------------------------------ HTML


def test_html_contains_the_same_sections_as_markdown() -> None:
    model = _model()
    html = html_renderer.render_html(model, [])
    for heading in ("运行环境", "负载", "结论摘要", "公开的不利数据", "口径局限与偏差来源"):
        assert heading in html


def test_html_escapes_model_values() -> None:
    """报告里会出现用户可控的字符串（命令、包版本），必须转义——这是注入面。"""
    html = html_renderer.render_html(_model(command="<script>alert(1)</script>"), [])

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_inline_escapes_before_applying_markup() -> None:
    """顺序必须是「先转义再替换」——反了就会把数据当标记执行。"""
    rendered = html_renderer.inline("**<b>粗</b>**")

    assert "<b>" not in rendered
    assert "&lt;b&gt;" in rendered
    assert rendered.startswith("<strong>")


def test_html_renders_tables_images_and_bullets() -> None:
    html = html_renderer.render_html(
        _model(),
        [{"figure": "f", "paths": {"svg": "/tmp/f.svg", "png": "/tmp/f.png"}, "scope": "范围"}],
    )
    assert html.count("<table>") >= 4
    assert '<img src="figures/f.svg"' in html
    assert "<ul>" in html
    assert "<blockquote>" in html


def test_html_is_a_complete_document() -> None:
    html = html_renderer.render_html(_model(), [])
    assert html.startswith("<!doctype html>")
    assert '<html lang="zh-CN">' in html
    assert '<meta charset="utf-8">' in html
    assert html.rstrip().endswith("</html>")


# ------------------------------------------------------------------ 两个后端同源


def test_both_backends_read_the_same_model() -> None:
    """扰动模型里的数字，两个后端都必须跟着变。

    各自解析数据、或某一端缓存了一份，这个断言就会发现它不跟着变——"同源"不能只是口号。
    """
    low = _model(ratio=0.26)
    high = _model(ratio=0.99)

    markdown_low = markdown_renderer.render_markdown(low, [])
    markdown_high = markdown_renderer.render_markdown(high, [])
    html_low = html_renderer.render_html(low, [])
    html_high = html_renderer.render_html(high, [])

    assert "26.00%" in markdown_low and "99.00%" in markdown_high
    assert "26.00%" in html_low and "99.00%" in html_high
    assert markdown_low != markdown_high
    assert html_low != html_high


def test_render_writes_both_formats(tmp_path: Path) -> None:
    """站点要 HTML，diff 与复用要 Markdown——一次渲染都写出来。"""
    outcome = html_renderer.render(_model(), tmp_path, threshold=0.15)

    assert Path(outcome["html"]).is_file()
    assert Path(outcome["markdown"]).is_file()
    assert Path(outcome["html"]).name == "index.html", "站点入口必须是 index.html"


# ------------------------------------------------------------------ 站点首页


def _archive(root: Path, specifier: str) -> None:
    storage.save_run({"run": {}, "units": [], "self_check": {"ok": True}}, framework=specifier, root=root)


def test_index_links_every_rendered_version(tmp_path: Path) -> None:
    results = tmp_path / "results"
    site = tmp_path / "site"
    _archive(results, FRAMEWORK)
    slug = storage.framework_slug(FRAMEWORK)
    (site / slug).mkdir(parents=True)
    (site / slug / "index.html").write_text("x", encoding="utf-8")

    assert cli.main(["index", "--results", str(results), "--site", str(site)]) == 0

    index = (site / "index.html").read_text(encoding="utf-8")
    assert f'href="{slug}/index.html"' in index
    assert slug in index


def test_index_says_so_when_nothing_has_been_rendered_yet(tmp_path: Path) -> None:
    results = tmp_path / "results"
    site = tmp_path / "site"
    _archive(results, FRAMEWORK)

    assert cli.main(["index", "--results", str(results), "--site", str(site)]) == 0

    index = (site / "index.html").read_text(encoding="utf-8")
    assert "还没有任何已渲染的报告" in index


# ------------------------------------------------------------------ 自检门禁


def test_render_refuses_when_self_check_failed(tmp_path: Path) -> None:
    """自检失败时发布比不发布更坏——数字本身不可信。"""
    results = tmp_path / "results"
    storage.save_run(
        {
            "run": {},
            "units": [],
            "relative": {"comparisons": []},
            "environment": {"hardware": {}, "os": {}, "python": {}, "harness": {}, "subject": {}},
            "self_check": {"ok": False},
        },
        framework=FRAMEWORK,
        root=results,
    )

    code = cli.main(
        ["render", "--framework", "9.9.9", "--results", str(results), "--out", str(tmp_path / "site")]
    )
    assert code == 5
    assert not (tmp_path / "site" / "index.html").exists()


def test_frameworks_command_prints_the_matrix_verbatim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CI 从这条输出取版本清单——所以它必须与 matrix.yaml 完全一致，不含任何加工。"""
    matrix_path = tmp_path / "matrix.yaml"
    matrix_path.write_text(
        "frameworks:\n  - 'zoo-framework==1.2.3'\n  - 'zoo-framework==1.2.4'\n"
        "concurrency: [1]\nbody_tiers_us: [100]\nadapters: [zoo]\n",
        encoding="utf-8",
    )

    assert cli.main(["frameworks", "--matrix", str(matrix_path)]) == 0

    assert capsys.readouterr().out.split() == ["zoo-framework==1.2.3", "zoo-framework==1.2.4"]
