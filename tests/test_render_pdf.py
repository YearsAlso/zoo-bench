"""PDF 导出与中文字体解析的验证（spec: comparison-report 的 PDF 要求 + design D11/D12）。

两处刻意安排：

1. **"中文没变方框"用从 PDF 里抽文字来验**，而不是断言文件存在——缺字照样能生成文件，只是
   里面是方框。这个检查能机械地证伪"字体没起作用"。
2. **字体解析要同时满足"覆盖文本"与"能被 reportlab 嵌入"**：实测发现 fonts-noto-cjk 是 CFF
   轮廓，matplotlib 能用而 reportlab 不能嵌——只按"覆盖"选字体，PDF 会直接出不来。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from zoo_bench.render import report as report_renderer
from zoo_bench.render.fonts import CjkFontUnavailable, find_pdf_font
from zoo_bench.runner import ABSOLUTE_NOTE


def _model() -> dict[str, Any]:
    """够渲染的小模型：含中文、表格与两个维度。"""
    summary = {"median": 0.00035, "p95": 0.0004, "p99": 0.00045, "relative_spread": 0.05}
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
        "run": {"absolute_note": ABSOLUTE_NOTE},
        "load": {"is_stand_in": True, "composition": "JSON 编解码", "caveat": "负载是替身"},
        "conclusion": {
            "overhead_threshold": 0.15,
            "overhead_crossings": [],
            "relative_turnings": [],
            "summary": ["执行体时长达到约 2700 微秒时开销占比降到 15% 以下。"],
            "note": "单点加速比没有选型含义",
        },
        "dimensions": {
            "latency": {
                "title": "延迟分位数与抖动",
                "note": "端到端 / 并发度",
                "rows": [
                    {"adapter": "zoo", "concurrency": 4, "body_tier_us": 300.0,
                     "end_to_end_per_task": dict(summary)}
                ],
            },
            "overhead": {
                "title": "框架自身开销占比",
                "note": "同一次运行内埋点",
                "rows": [
                    {"adapter": "zoo", "concurrency": 4, "body_tier_us": 300.0,
                     "framework_overhead_seconds": 0.0001, "framework_overhead_ratio": 0.26,
                     "body_seconds": 0.0003}
                ],
            },
            "throughput": {
                "title": "吞吐与并发伸缩",
                "note": "并发度 / 端到端中位数",
                "rows": [
                    {"adapter": "zoo", "concurrency": 4, "body_tier_us": 300.0,
                     "throughput_per_second": 7000.0}
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
                {"baseline": "bare_thread", "concurrency": 4, "body_tier_us": 300.0,
                 "subject_median_seconds": 0.00035, "gap": "bare_thread 比 zoo 快 1.25x"}
            ],
            "found": True,
            "note": "不利数据缺失的报告不合格",
        },
        "caveats": [{"kind": "被测层级", "text": "调度派发层"}],
        "absolute": {"note": ABSOLUTE_NOTE, "process_isolation": {"all_distinct": True}},
        "self_check": {"ok": True},
    }


def _pdf_text(path: str) -> str:
    return "".join(page.extract_text() or "" for page in PdfReader(path).pages)


# ------------------------------------------------------------------ PDF 产出


def test_pdf_is_produced_alongside_the_other_formats(tmp_path: Path) -> None:
    outcome = report_renderer.render(_model(), tmp_path, threshold=0.15)

    assert Path(outcome["pdf"]).is_file()
    assert Path(outcome["pdf"]).stat().st_size > 0


def test_pdf_carries_the_chinese_report_text(tmp_path: Path) -> None:
    """**"中文没变方框"的机械判据**：从 PDF 里把文字抽出来看。

    缺字照样能生成文件，只是里面是方框——所以"文件存在"证明不了任何事。
    """
    outcome = report_renderer.render(_model(), tmp_path, threshold=0.15)
    text = _pdf_text(outcome["pdf"])

    assert "运行环境" in text
    assert "结论摘要" in text
    assert "公开的不利数据" in text


def test_pdf_renders_the_same_numbers_as_the_other_backends(tmp_path: Path) -> None:
    """同源：三种格式都从同一份块列表来，故数字必须一致。"""
    outcome = report_renderer.render(_model(), tmp_path, threshold=0.15)

    markdown = Path(outcome["markdown"]).read_text(encoding="utf-8")
    html = Path(outcome["html"]).read_text(encoding="utf-8")
    pdf = _pdf_text(outcome["pdf"])

    for backend_text in (markdown, html, pdf):
        assert "26.00%" in backend_text, "三种格式都必须出现同一个开销占比"


def test_a_diverging_backend_would_be_caught(tmp_path: Path) -> None:
    """4.11 要求的**鉴别力**：扰动模型里的一个数字，三个后端都必须跟着变。

    若某个后端自留一份数据抽取路径（或缓存了一份），它就不会跟着变——"同源"不能只是口号。
    这条用例是它唯一的证明方式：先证明"会跟着变"，才能说"一致"不是巧合。
    """
    low = _model()
    high = _model()
    high["dimensions"]["overhead"]["rows"][0]["framework_overhead_ratio"] = 0.99

    low_out = report_renderer.render(low, tmp_path / "low", threshold=0.15)
    high_out = report_renderer.render(high, tmp_path / "high", threshold=0.15)

    for key in ("markdown", "html"):
        assert Path(low_out[key]).read_text(encoding="utf-8") != Path(high_out[key]).read_text(
            encoding="utf-8"
        ), f"{key} 没跟着模型变——它自留了一份数据"

    assert _pdf_text(low_out["pdf"]) != _pdf_text(high_out["pdf"]), "PDF 没跟着模型变"
    assert "99.00%" in _pdf_text(high_out["pdf"])


def test_pdf_embeds_the_chart_images(tmp_path: Path) -> None:
    """图表要真嵌进去。SVG 不能直接嵌 PDF，故用的是同一份 figure 的 PNG。"""
    outcome = report_renderer.render(_model(), tmp_path, threshold=0.15)
    reader = PdfReader(outcome["pdf"])

    has_image = any("/Image" in str(page.get("/Resources", {})) for page in reader.pages)
    assert has_image, "PDF 里没有嵌入图片——图表大概率没进报告"


def test_pdf_records_the_font_it_embedded(tmp_path: Path) -> None:
    """字体必须可复核：报告里要能回答"中文为何能显示"。

    同时记录被跳过的候选与原因——只按"覆盖"选字体的话，reportlab 遇到 CFF 轮廓（如
    fonts-noto-cjk）会直接出不来，而跳过原因正是排查这类问题的入口。
    """
    outcome = report_renderer.render(_model(), tmp_path, threshold=0.15)
    font = outcome["font"]

    assert Path(font["path"]).is_file()
    assert font["name"]
    assert isinstance(font.get("skipped"), list)


# ------------------------------------------------------------------ 字体解析


# ------------------------------------------------------------------ 报告正文字符集


def test_report_text_uses_only_renderable_characters() -> None:
    """报告正文只使用中文字体可靠具备的字符。

    实测被 U+2212（减号）卡住过一轮 CI：`wqy-zenhei` 有 `µ`/`—`/`/`/`×`/`→` 却**唯独没有
    减号**——字体覆盖是逐字符的，猜不得。故运算符一律 ASCII、单位写中文，并由导出前检查兜住。
    """
    from zoo_bench.render.blocks import build_blocks, unsafe_characters
    from zoo_bench.render.pdf import blocks_text

    text = blocks_text(build_blocks(_model(), []))
    unsafe = unsafe_characters(text)

    assert not unsafe, f"报告正文里出现了中文字体不一定有的字符：{[f'U+{ord(c):04X}' for c in unsafe]}"


def test_unsafe_characters_are_named_with_a_replacement() -> None:
    """违例要指名道姓并给出改法，而不是只说"有非法字符"。"""
    from zoo_bench.render.blocks import UnsafeReportText, assert_report_text_is_renderable

    with pytest.raises(UnsafeReportText) as excinfo:
        assert_report_text_is_renderable("开销 = 端到端 − 执行体")

    message = str(excinfo.value)
    assert "U+2212" in message
    assert "-" in message, "要给出可替换的写法"


def test_pdf_export_refuses_unsafe_report_text(tmp_path: Path) -> None:
    """模型里混进非法符号时，PDF 导出必须失败并指名——而不是产出一份满屏方框的 PDF。"""
    from zoo_bench.render.blocks import UnsafeReportText

    model = _model()
    model["caveats"] = [{"kind": "测试", "text": "开销 = 端到端 − 执行体"}]

    with pytest.raises(UnsafeReportText, match="U\\+2212"):
        report_renderer.render(model, tmp_path, threshold=0.15)


def test_font_resolution_fails_clearly_when_no_candidate_exists(tmp_path: Path) -> None:
    with pytest.raises(CjkFontUnavailable) as excinfo:
        find_pdf_font("中文", candidates=[str(tmp_path / "nope.ttf")])

    assert "fonts-wqy-zenhei" in str(excinfo.value), "必须给出可执行的安装指引"
    assert "nope.ttf" in str(excinfo.value), "必须列出尝试过哪些候选"


def test_font_resolution_reports_why_each_candidate_was_skipped() -> None:
    """覆盖不够的候选要被**跳过并记原因**，而不是抛出去让调用方猜。

    用私用区码位（常规字体不该有映射）构造"所有候选都覆盖不足"。
    """
    with pytest.raises(CjkFontUnavailable) as excinfo:
        find_pdf_font("")

    assert "缺字符" in str(excinfo.value)


def test_svg_paths_are_resolved_to_absolute_for_pdf(tmp_path: Path) -> None:
    """块里的图路径是**相对**的（站点必须如此），而 PDF 要读本地文件——故导出前要解析。"""
    from zoo_bench.render.pdf import _absolute_charts

    charts = [{"figure": "f", "paths": {"svg": "/tmp/f.svg", "png": "/tmp/f.png"}}]
    resolved = _absolute_charts(charts, tmp_path / "figures")

    assert Path(resolved[0]["paths"]["png"]).is_absolute()
    assert Path(resolved[0]["paths"]["png"]).parent == tmp_path / "figures"
