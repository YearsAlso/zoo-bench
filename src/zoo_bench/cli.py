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

from packaging.version import InvalidVersion, Version

from . import compare, environment, storage
from . import matrix as matrix_module
from .render import compare as compare_renderer
from .render import index as site_index
from .render import report as report_renderer
from .render.assets import write_style
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


def _same_version(left: str | None, right: str | None) -> bool:
    """两个版本串是否指同一个版本（按 PEP 440 归一后比较）。

    **不能字符串相等**：实测框架发布的 ``0.7.1b0`` 在 PyPI 索引里叫 ``0.7.1b0``，而它写进
    轮子元数据的是 ``0.7.1-beta``（未经归一）——`importlib.metadata` 报的是后者。直接比字符串会
    把一个装对了的环境判成装错，是**假阳性拒绝**。
    """
    if left is None or right is None:
        return False
    try:
        return Version(left) == Version(right)
    except InvalidVersion:
        return left == right


def _verify_installed(target: matrix_module.FrameworkTarget) -> str | None:
    """确认当前装着的正是要测的那个目标；返回 None 表示通过。

    PyPI 目标比版本号（归一后）；**git 目标比 ``direct_url.json`` 里的 URL 与引用**——分支安装
    的版本号只是仓库里写着的声明值，可能滞后于开发、也可能与已发布版本撞号，**不能拿它当身份**。
    少了这道校验，就会出现"以为在测 dev 分支、其实装的是某个 release"的静默错配。
    """
    if target.kind == "pypi":
        installed = _installed_framework_version()
        if _same_version(installed, target.version):
            return None
        return (
            f"当前安装的 {target.name} 是 {installed!r}，而矩阵选定的是 {target.specifier!r}。\n"
            f"请先安装：pip install '{target.specifier}'"
        )

    source = environment.install_source() or {}
    vcs = source.get("vcs_info") or {}
    if not vcs:
        return (
            f"当前安装的 {target.name} 不是 git 安装，而矩阵选定的是 {target.specifier!r}。\n"
            f"请先安装：pip install '{target.specifier}'"
        )
    if vcs.get("requested_revision") != target.ref:
        return (
            f"当前安装的 {target.name} 来自 {vcs.get('requested_revision')!r}，"
            f"而矩阵选定的是 {target.ref!r}。\n请先安装：pip install '{target.specifier}'"
        )

    url = str(source.get("url", "")).rstrip("/")
    if target.url and target.url.rstrip("/") not in url:
        return (
            f"安装来源与矩阵不符：矩阵是 {target.url}，实际来自 {url}。\n"
            f"请先安装：pip install '{target.specifier}'"
        )
    return None


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

    problem = _verify_installed(matrix_module.parse_target(framework))
    if problem:
        print(problem, file=sys.stderr)
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

    outcome = report_renderer.render(model, args.out, threshold=OVERHEAD_THRESHOLD)
    print(f"报告：{outcome['html']}")
    print(f"Markdown：{outcome['markdown']}")
    print(f"PDF：{outcome['pdf']}")
    font = outcome.get("font") or {}
    if font.get("path"):
        print(f"PDF 字体：{font['path']}（face {font.get('face_index')}）")
        for skipped in font.get("skipped", []):
            print(f"  跳过的候选：{skipped}")
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


# ------------------------------------------------------------------ compare


def compare_command(args: argparse.Namespace) -> int:
    """由两份留档数据产出跨版本对比。

    缺失的版本**明确报出来**并列出已留档的版本，而不是产出以缺失数据充数的对比（spec 的硬要求）。
    """
    before_path = storage.latest_run(args.before, root=args.results)
    after_path = storage.latest_run(args.after, root=args.results)

    missing = [
        (label, spec)
        for label, spec, path in (
            ("旧版本", args.before, before_path),
            ("新版本", args.after, after_path),
        )
        if path is None
    ]
    if missing:
        print("以下版本没有留档数据，无法对比：", file=sys.stderr)
        for label, spec in missing:
            print(f"  - {label}：{spec}", file=sys.stderr)
        print(f"已留档的版本：{storage.available_frameworks(root=args.results) or '（无）'}", file=sys.stderr)
        return 7

    comparison = compare.compare_versions(
        storage.load_run(before_path),
        storage.load_run(after_path),
        before_label=args.before,
        after_label=args.after,
    )

    if not args.out:
        print(compare_renderer.render_markdown(comparison))
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_style(out)
    (out / "compare.md").write_text(compare_renderer.render_markdown(comparison), encoding="utf-8")
    (out / "index.html").write_text(compare_renderer.render_html(comparison), encoding="utf-8")
    print(f"对比：{out / 'index.html'}")
    return 0


# ------------------------------------------------------------------ index


def index_command(args: argparse.Namespace) -> int:
    """写站点首页：每个已留档版本一张卡片。

    **实现放在 `render/index.py` 而不是这里**——首页长什么样、卡片上放哪些事实，都该被用例盖到；
    CLI 只负责把留档目录与站点目录传进去。
    """
    path = site_index.render(args.results, args.site)
    print(f"首页：{path}")
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

    compare_parser = subparsers.add_parser("compare", help="由留档数据产出跨版本对比")
    compare_parser.add_argument("before", help="旧版本（完整规格或纯版本号）")
    compare_parser.add_argument("after", help="新版本")
    compare_parser.add_argument("--results", default=None, help="留档根目录")
    compare_parser.add_argument("--out", help="输出目录；不给则打到标准输出")
    compare_parser.set_defaults(handler=compare_command)

    index_parser = subparsers.add_parser("index", help="写站点首页")
    index_parser.add_argument("--results", default=None, help="留档根目录")
    index_parser.add_argument("--site", required=True, help="站点目录")
    index_parser.set_defaults(handler=index_command)
    return parser


def _make_output_never_fatal() -> None:
    """让"打印自己的输出"不会把进程杀掉。

    实测踩过这个坑：在本机 Windows 控制台（GBK）上渲染**成功**、产物全对，但打印被跳过的字体
    候选列表时撞上无法编码的 `µ`，`UnicodeEncodeError` 把一次成功变成了退出码 1。CI 上 stdout
    是 UTF-8，故这个坑只在特定环境出现——而"因为打日志而失败"是最没价值的一类失败。

    只把错误处理改成 ``replace``，不改编码：不改用户终端的观感，只是无法编码的字符变成 ``?``。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    """命令行主入口。

    Args:
        argv: 参数列表；None 时取 ``sys.argv[1:]``。

    Returns:
        进程退出码。
    """
    _make_output_never_fatal()
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
