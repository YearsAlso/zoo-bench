"""命令行入口。

`run` 产出并留档原始数据，`render` 由留档数据产出报告——**两者刻意分开**：spec 要求报告能
"不重新测量即产出"，而把测量与渲染合成一个动作会让"渲染失败"看起来像"测量失败"，也会让
重新渲染变成一次新的测量。

`render` 有两处会**以非零退出码拒绝**，因为 spec 用了 MUST：

- 缺环境自述：报告不得发布——读者无从判断数字的适用范围
- 缺"公开的不利数据"：一份只展示自己赢的报告不可信（可经 ``--allow-empty-unfavorable``
  显式豁免，使例外是**可审计的**，而不是靠忘记写）
"""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from . import environment, storage
from . import matrix as matrix_module
from .render import html as html_renderer
from .report import OVERHEAD_THRESHOLD, build_model
from .runner import (
    DEFAULT_MEASURED_ROUNDS,
    DEFAULT_WARMUP_ROUNDS,
    measure_matrix,
    summarize_self_check,
)


def _installed_framework_version() -> str | None:
    try:
        return version("zoo-framework")
    except PackageNotFoundError:
        return None


def _specified_version(specifier: str) -> str:
    return specifier.split("==", 1)[-1]


def _split(text: str | None, cast: Any = str) -> tuple[Any, ...] | None:
    if not text:
        return None
    return tuple(cast(part.strip()) for part in text.split(",") if part.strip())


def recorded_command(argv: list[str] | None = None) -> list[str]:
    """报告里记下的"复现命令"。

    以 ``zoo-bench`` 开头、后接本次进程收到的参数。**刻意不再插入子命令名**：参数里已经有
    它了（``run`` 或 ``render``），早先多插一次导致命令被记成 ``zoo-bench run run …``——
    一条照抄跑不通的复现命令，比不给更坏。

    Args:
        argv: 参数列表；None 时取 ``sys.argv[1:]``。

    Returns:
        可直接粘进终端的命令。
    """
    return ["zoo-bench", *(sys.argv[1:] if argv is None else argv)]


# ------------------------------------------------------------------ run


def run_command(args: argparse.Namespace) -> int:
    """测量一轮并留档原始数据。"""
    matrix = matrix_module.load(args.matrix)
    framework = matrix_module.select_framework(matrix, args.framework)

    installed = _installed_framework_version()
    expected = _specified_version(framework)
    if installed != expected:
        print(
            f"当前安装的 zoo-framework 是 {installed!r}，而矩阵选定的是 {framework!r}。\n"
            f"请先安装：pip install '{framework}'",
            file=sys.stderr,
        )
        return 2

    specs = matrix_module.unit_specs(
        matrix,
        framework=framework,
        adapters=_split(args.adapters),
        concurrency=_split(args.concurrency, int),
        body_tiers_us=_split(args.tiers, float),
        warmup_rounds=args.warmup,
        measured_rounds=args.rounds,
    )
    print(f"测量 {len(specs)} 个单元（{framework}）…")

    result = measure_matrix(specs)
    result["environment"] = environment.collect(command=recorded_command())

    path = storage.save_run(result, framework=framework, root=args.out)
    print(f"原始数据已留档：{path}")

    passed = result["self_check"]["ok"]
    print(f"自检：{'通过' if passed else '未通过'}")
    for problem in summarize_self_check(result["self_check"]):
        print(f"  - {problem}")
    return 0


# ------------------------------------------------------------------ render


def _resolve_data_path(args: argparse.Namespace) -> Path | None:
    if args.data:
        return Path(args.data)
    if not args.framework:
        available = storage.available_frameworks(root=args.results)
        print(
            "需要 --data 指定留档文件，或用 --framework 指定要渲染的版本。"
            f"\n已留档的版本：{available or '（无）'}",
            file=sys.stderr,
        )
        return None
    return storage.latest_run(args.framework, root=args.results)


def render_command(args: argparse.Namespace) -> int:
    """由留档数据产出报告。"""
    data_path = _resolve_data_path(args)
    if data_path is None:
        return 2
    if not data_path.is_file():
        print(f"找不到留档数据：{data_path}", file=sys.stderr)
        return 2

    result = storage.load_run(data_path)
    model = build_model(result)
    model["source"] = {"path": str(data_path)}

    if model["environment"] is None:
        print(
            "留档数据里没有环境自述，拒绝出报告——读者无从判断数字的适用范围。"
            "\n请用 zoo-bench run 重新采集（它会一并存下环境自述）。",
            file=sys.stderr,
        )
        return 3

    if not result.get("self_check", {}).get("ok"):
        print(
            "本轮测量的自检未通过，拒绝出报告。"
            "\n自检覆盖执行体档位偏差、跨档位开销一致性与适配器等价性——它失败意味着"
            "数字本身不可信，此时发布比不发布更坏。未通过的项：",
            file=sys.stderr,
        )
        for problem in summarize_self_check(result.get("self_check", {})):
            print(f"  - {problem}", file=sys.stderr)
        return 5

    if model["subject"] is None:
        print(
            "留档数据里没有被测框架的单元，拒绝出报告。"
            "\n这是一份配置错误而不是测量结论：报告里必须有被测对象，否则无从对照。"
            "\n检查 matrix.yaml 的 adapters 是否漏了被测框架（留空表示全部已登记适配器）。",
            file=sys.stderr,
        )
        return 6

    if not model["unfavorable"]["found"]:
        if not args.allow_empty_unfavorable:
            print(
                "本轮测量里没有出现被测框架处于劣势的档位，拒绝出报告。"
                "\n框架开销是纯增量，短任务档位上出现劣势是预期结果；没有它通常意味着"
                "档位选得不合适或测量有问题。确需发布请加 --allow-empty-unfavorable——"
                "该豁免会被写进报告，使例外是审计线索而不是被人忘掉的一步。",
                file=sys.stderr,
            )
            return 4
        model["caveats"].append(
            {
                "kind": "发布豁免",
                "text": "本轮测量不含被测框架处于劣势的档位，经 --allow-empty-unfavorable "
                "显式豁免发布。该豁免是审计线索：再次出现同样情形时应先检查档位选择与"
                "测量是否正常，而不是习惯性地豁免。",
            }
        )

    outcome = html_renderer.render(model, args.out, threshold=OVERHEAD_THRESHOLD)
    print(f"报告：{outcome['html']}")
    print(f"Markdown：{outcome['markdown']}")
    if outcome["font"]:
        print(f"图表字体：{outcome['font']}")
    return 0


# ------------------------------------------------------------------ frameworks


def frameworks_command(args: argparse.Namespace) -> int:
    """打印矩阵里的框架清单，一行一个。

    给 CI 用：让工作流从 ``matrix.yaml`` 取版本清单，而不是把版本号抄进 YAML——抄一份就会有
    两处真源，而它们迟早不一致（design D8）。
    """
    for framework in matrix_module.load(args.matrix).frameworks:
        print(framework)
    return 0


# ------------------------------------------------------------------ index


def index_command(args: argparse.Namespace) -> int:
    """写站点首页：列出已出报告的版本并链过去。

    **放在代码里而不是 YAML 里**——把"首页长什么样"写在 workflow 里就没法被用例盖到，而它是
    读者进入报告的唯一入口。
    """
    site = Path(args.site)
    site.mkdir(parents=True, exist_ok=True)

    entries = [
        (slug, f"{slug}/index.html")
        for slug in storage.available_frameworks(root=args.results)
        if (site / slug / "index.html").is_file()
    ]

    if entries:
        items = "\n".join(f'<li><a href="{href}">{slug}</a></li>' for slug, href in entries)
        body = f"<ul>{items}</ul>"
    else:
        body = "<p>还没有任何已渲染的报告。</p>"

    (site / "index.html").write_text(
        "\n".join(
            [
                "<!doctype html>",
                '<html lang="zh-CN">',
                "<head>",
                '<meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width, initial-scale=1">',
                "<title>zoo-bench 性能报告</title>",
                "<style>body{font-family:-apple-system,'Segoe UI','Noto Sans CJK SC',sans-serif;"
                "max-width:40rem;margin:3rem auto;padding:0 1rem;line-height:1.7}"
                "</style>",
                "</head>",
                "<body>",
                "<h1>zoo-bench 性能报告</h1>",
                body,
                "</body>",
                "</html>",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"首页：{site / 'index.html'}")
    return 0


# ------------------------------------------------------------------ 入口


def build_parser() -> argparse.ArgumentParser:
    """构造参数解析器。"""
    parser = argparse.ArgumentParser(prog="zoo-bench", description="Zoo Framework 的基准 harness")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="测量一轮并留档原始数据")
    run_parser.add_argument("--matrix", default=matrix_module.DEFAULT_MATRIX_PATH, help="矩阵文件")
    run_parser.add_argument("--framework", help="要测的框架规格或纯版本号；默认取矩阵第一项")
    run_parser.add_argument("--out", default=None, help="留档根目录；默认为仓库根下的 results/")
    run_parser.add_argument("--adapters", help="逗号分隔，覆盖矩阵的适配器清单")
    run_parser.add_argument("--concurrency", help="逗号分隔，覆盖并发度档位")
    run_parser.add_argument("--tiers", help="逗号分隔，覆盖执行体档位（微秒）")
    run_parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP_ROUNDS, help="预热轮数")
    run_parser.add_argument("--rounds", type=int, default=DEFAULT_MEASURED_ROUNDS, help="正式采样轮数")
    run_parser.set_defaults(handler=run_command)

    render_parser = subparsers.add_parser("render", help="由留档数据产出报告")
    render_parser.add_argument("--data", help="留档文件路径")
    render_parser.add_argument("--framework", help="渲染哪个已留档的版本（取最近一次）")
    render_parser.add_argument("--results", default=None, help="留档根目录")
    render_parser.add_argument("--out", required=True, help="报告输出目录")
    render_parser.add_argument(
        "--allow-empty-unfavorable",
        action="store_true",
        help="显式豁免「缺不利数据」这一拒绝条件（例外会被写进报告，使豁免可审计）",
    )
    render_parser.set_defaults(handler=render_command)

    frameworks_parser = subparsers.add_parser("frameworks", help="打印矩阵里的框架清单")
    frameworks_parser.add_argument("--matrix", default=matrix_module.DEFAULT_MATRIX_PATH)
    frameworks_parser.set_defaults(handler=frameworks_command)

    index_parser = subparsers.add_parser("index", help="写站点首页")
    index_parser.add_argument("--results", default=None, help="留档根目录")
    index_parser.add_argument("--site", required=True, help="站点目录")
    index_parser.set_defaults(handler=index_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。

    Args:
        argv: 参数列表；None 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码。
    """
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
