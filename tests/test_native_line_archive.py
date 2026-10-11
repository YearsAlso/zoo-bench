"""该线如何进留档、失败如何示人（spec: native-task-line 的留档与失败语义）。

按 task 2.3 与 4.1–4.4 的验收点组织，与 `tests/test_native_line_measure.py`（测量口径）
和 `tests/test_native_line_equivalence.py`（门禁本身的鉴别力）互补：本文件问的是**结论落到哪、
失败长什么样**，而不是数字怎么来。

**替身注入是本文件的主要手法**：要验的那几态（扩展装不上、握手版本不匹配、扩展取自另一个 tag、
两臂不等价）真环境里一个都造不出来，而它们恰恰是"失败必须示人、不许静默降级"这条要求的全部
内容。替身经 :func:`zoo_bench.runner.run_in_child` 的 ``extra_search_path`` 注入——那只影响被测
子进程，父进程的 ``sys.path`` 与已经 import 好的 ``zoo_framework`` 不受污染（见该函数 docstring：
把替身塞进父进程的路径是比被验证的行为更坏的代价）。

替身的**保真由构造保证**：成功路径与失败路径都直接委托给本仓的参照实现
（:mod:`zoo_bench.native_line.reference`），异常族按 ``equivalence.ERROR_FAMILY_BY_REFERENCE``
的同一条对应归一化——与 ``tests/test_native_line_equivalence.py`` 的 ``FAITHFUL`` 假臂是同一件事，
只是搬进了子进程。故"门禁对这些用例判过/判否"与"门禁对真扩展判过/判否"同源；该线本身对真扩展
的核验由 `tests/test_native_line_measure.py` 与 `tests/test_native_line_equivalence.py` 守着。
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import pytest
from zoo_framework.native.contract import CONTRACT_VERSION

from zoo_bench import environment, storage
from zoo_bench import runner as runner_module
from zoo_bench.i18n import LANG_EN, LANG_ZH, resolve_reason, t
from zoo_bench.native_line import reference
from zoo_bench.native_line.inputs import GATE_INPUTS, REQUIRED_GROUPS, groups_present
from zoo_bench.native_line.line import CHILD_MODULE, NativeLinePlan, run_line
from zoo_bench.native_line.measure import REQUIRED_SYMBOLS
from zoo_bench.render import markdown as markdown_renderer
from zoo_bench.report import build_model
from zoo_bench.runner import UnitSpec, measure_matrix, run_in_child, summarize_native_line

#: 被测框架规格。该线拿它与扩展的安装来源比对（design D9），故它同时决定了"扩展应取自哪个 tag"。
FRAMEWORK = "zoo-framework==0.11.0"

#: 上面那条规格对应的扩展**应**来自的版本标识（约定：PyPI 目标 → ``v<版本>``）。刻意写死而不是调
#: :func:`~zoo_bench.native_line.measure.expected_extension_revision`：那是被测代码，用它算期望值
#: 等于自证——它把约定改错时断言照样绿。
EXPECTED_REVISION = "v0.11.0"

#: 子进程上限（秒）。用例只量最小规模，上限只用于避免挂死时整套卡住。
CHILD_TIMEOUT_SECONDS = 300.0

#: 替身的 execute：**委托参照实现**并把异常族归一化（同族同名同文案，见模块 docstring）。
_FAITHFUL_EXECUTE = """\
    from zoo_bench.native_line.reference import (
        ReferenceInvalidInput,
        ReferenceTaskFailed,
        parse_response,
        to_json_bytes,
    )

    try:
        return to_json_bytes(parse_response(bytes(payload)))
    except ReferenceInvalidInput as exc:
        raise NativeInvalidInput(str(exc)) from exc
    except ReferenceTaskFailed as exc:
        raise NativeTaskFailed(str(exc)) from exc
"""

#: 刻意不等价的 execute：对**任何**输入都回同一串合法 JSON。它同时踩到两条：成功路径的输出不对，
#: 失败路径该抛的不抛——正是门禁两条比对口径（逐字节 / 错误族与文案）各自的靶子。
_UNEQUAL_EXECUTE = """\
    return b'{"slave": 1, "function": 3, "byte_count": 0, "registers": []}'
"""


def _tasks_entry() -> str:
    """替身注册的任务契约（字段与真扩展逐项一致，取自真扩展的快照）。"""
    return f"""\
{{
    TASK_NAME: NativeTaskContract(
        name=TASK_NAME,
        contract_version=1,
        input_format="bytes",
        max_input_bytes={reference.MAX_INPUT_BYTES},
        output_format="json",
        error_classes=("NativeInvalidInput", "NativeTaskFailed"),
    )
}}"""


_SHIM_TEMPLATE = '''"""用例替身：与真扩展同名同接口，用来造出真环境里造不出的失败形态（见 tests/test_native_line_archive.py）."""
{head}\
from zoo_framework.native.contract import (
    NativeInvalidInput,
    NativeTaskContract,
    NativeTaskFailed,
)

TASK_NAME = {task_name!r}
CONTRACT_VERSION = {contract_version}
TASKS = {tasks}


def contract_version():
    """扩展上报的契约版本."""
    return CONTRACT_VERSION


def capabilities():
    """扩展声明支持的能力名."""
    return ()


def tasks():
    """扩展注册的任务名 → 契约描述."""
    return TASKS


def execute(task_name, payload):
    """执行一个原生任务；行为由用例给定."""
{execute}'''


def _extension_shim(
    root: Path,
    *,
    execute: str = _FAITHFUL_EXECUTE,
    contract_version: int = 1,
    revision: str = EXPECTED_REVISION,
    head: str = "",
) -> Path:
    """写一份与真扩展同名的替身到 ``root``（模块 + 带来源记录的 dist-info），返回 ``root``.

    Args:
        root: 替身目录（会被放在子进程 import 路径的**最前**）。
        execute: ``execute`` 的函数体（已缩进；见两个 ``_*_EXECUTE`` 常量）。
        contract_version: 替身上报的契约版本（握手核验的靶子）。
        revision: dist-info 里记的安装来源版本（同源核验的靶子）。
        head: 模块体开头插入的语句（造"装不上"的形态时用它抛 ImportError）。

    Returns:
        ``root``。
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "zoo_framework_native.py").write_text(
        _SHIM_TEMPLATE.format(
            head=head,
            task_name=reference.TASK_NAME,
            contract_version=contract_version,
            tasks=_tasks_entry(),
            execute=execute,
        ),
        encoding="utf-8",
    )
    metadata = root / "zoo_framework_native-0.1.0.dist-info"
    metadata.mkdir(exist_ok=True)
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: zoo-framework-native\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (metadata / "direct_url.json").write_text(
        json.dumps(
            {
                "url": "https://github.com/YearsAlso/zoo-framework.git",
                "vcs_info": {
                    "vcs": "git",
                    "commit_id": "0" * 40,
                    "requested_revision": revision,
                },
                "subdirectory": "native",
            }
        ),
        encoding="utf-8",
    )
    return root


def _old_release_shim(root: Path) -> Path:
    """写一份"能装上、但没有这项能力"的已发布版本替身：有 ``zoo_framework`` 包，符号一个没有.

    它模拟的是 task 4.2 的第一态——某个在该能力存在**之前**发布的版本（旧版本持续报假红是这条
    探查顺序要防的事，见 design D11）。四个能力符号一个都不在，故连扩展都不会去碰。
    """
    package = root / "zoo_framework"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(
        "# 用例替身：不具备该线所依赖能力的已发布版本。\n", encoding="utf-8"
    )
    return root


def _tiny_plan() -> NativeLinePlan:
    """最小的一份计划：一格、一个并发度。轮数取与既有用例同一量级——判据与全量跑的是同一段代码。"""
    return NativeLinePlan(
        register_counts=(1,), concurrencies=(1,), warmup_rounds=3, measured_rounds=6
    )


def _archive_spec(framework: str = FRAMEWORK) -> UnitSpec:
    """一份最小的主矩阵单元。线程池适配器不依赖被测框架，故主矩阵的成败与扩展无关。"""
    return UnitSpec(
        adapter="thread_pool",
        framework=framework,
        concurrency=1,
        body_tier_us=300.0,
        warmup_rounds=1,
        measured_rounds=2,
    )


def _line(shim: Path) -> dict[str, Any]:
    """用替身跑一遍该线（替身装不上 / 握手不过时它当场收结论，不派生任何测量子进程）."""
    return run_line(
        FRAMEWORK,
        _tiny_plan(),
        run=functools.partial(run_in_child, extra_search_path=[str(shim)]),
        timeout=CHILD_TIMEOUT_SECONDS,
    )


def _inject_into_the_line(monkeypatch: pytest.MonkeyPatch, shim: Path) -> None:
    """把替身**只**注入该线的子进程（主矩阵、语义、归因照常走真环境）。

    该线的子进程入口是 ``module=zoo_bench.native_line.child``，与主矩阵的
    ``zoo_bench.worker`` 分属两个模块——这正是不必改父进程 import 路径就能分别注入的原因。
    """
    original = runner_module.run_in_child

    def patched(kind: str, spec: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("module", "zoo_bench.worker") == CHILD_MODULE:
            kwargs["extra_search_path"] = [str(shim)]
        return original(kind, spec, **kwargs)

    monkeypatch.setattr(runner_module, "run_in_child", patched)


@pytest.fixture(scope="module")
def faithful_line(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """保真替身下一次真实的该线测量（模块内共用，省掉重复的真跑）.

    它量不出结论时**在这里就红**：那样下面每个用例报的都是同一条根因。
    """
    payload = _line(_extension_shim(tmp_path_factory.mktemp("faithful")))
    assert payload["status"] == "ok", f"保真替身下该线应为 ok，实得 {payload['reason']}"
    return payload


# ------------------------------------------------------------------ 2.3 门禁结论进留档


def test_the_gate_verdict_lands_in_the_archive_with_its_caliber(
    faithful_line: dict[str, Any],
) -> None:
    """门禁**通过**的结论连同口径一起进留档：比对方式、判据条数、分组计数与输入上限都在场.

    只留一句 ``ok: true`` 的话，读者无法回答"它是按什么尺子量出来的"——而这把尺子（逐字节、
    覆盖哪几类输入、输入上限多少字节）正是"等价"这个词的全部内容。
    """
    gate = faithful_line["equivalence"]

    assert gate["ok"] is True
    assert gate["comparison"] == "byte_identical"
    assert gate["checked"] == len(GATE_INPUTS)
    assert gate["mismatches"] == []
    assert gate["input_envelope_bytes"] == reference.MAX_INPUT_BYTES
    # 三类判据输入都要在场：只比"都解析成功"的输入，失败路径的语义分歧就没人看着（门禁 docstring）
    for group in REQUIRED_GROUPS:
        assert gate["groups"].get(group), f"判据输入集的分组 {group} 一条都没有"
    assert sum(gate["groups"].values()) == gate["checked"], "分组计数与判据条数不一致"
    assert gate["groups"] == groups_present(GATE_INPUTS)


def test_a_deliberately_unequal_extension_fails_the_gate_and_names_the_inputs(
    tmp_path: Path,
) -> None:
    """刻意造出的不等价：留档里该结论为**失败**，且带上是**哪条输入的哪些字节**不等价.

    这是 2.3 的否侧。没有否侧的话，"门禁结论进留档"在实现把结论写死成"通过"时照样绿——而那正是
    这条要求要拦的故障：数字好看、前提不成立。
    """
    payload = _line(_extension_shim(tmp_path / "unequal", execute=_UNEQUAL_EXECUTE))

    assert payload["status"] == "failed"
    assert payload["reason"] == "native_line.reason.equivalence_failed"
    assert payload["derived"]["suppressed"] is True, "不等价时 MUST NOT 产出任何数字"
    assert payload["groups"] == [] and payload["pairwise"] == []
    assert "by_tier" not in payload, "前置核验没过时不该存在逐档数字"

    gate = payload["equivalence"]
    assert gate["ok"] is False
    assert payload["reason_params"] == {
        "count": len(gate["mismatches"]),
        "keys": ", ".join(str(item["key"]) for item in gate["mismatches"]),
    }
    # 替身对**任何**输入都回同一串 JSON：成功路径比不出输出、失败路径该抛的不抛，故 15 条全不等价
    assert len(gate["mismatches"]) == len(GATE_INPUTS)
    mismatch = gate["mismatches"][0]
    assert set(mismatch) == {"key", "group", "frame_hex", "frame_bytes", "reference", "native"}
    assert mismatch["reference"] != mismatch["native"]
    frame = bytes.fromhex(mismatch["frame_hex"])
    assert len(frame) == mismatch["frame_bytes"]
    # 留档里的"具体输入"就是真跑的那条输入，不是一句话
    assert frame == next(item.frame for item in GATE_INPUTS if item.key == mismatch["key"])


# ------------------------------------------------------------------ 4.1 同一份按版本留档


def test_the_line_lands_in_the_same_per_version_archive_and_survives_a_rerender(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """该线经独立子进程跑，结论作为**同一份按版本留档**里的新键；用留档重渲染仍含该维度.

    与主矩阵、语义、归因**同一份**留档（而不是另开一份）是规格的要求：读者要能拿一份数据回答
    "这个版本上各维度分别是什么"。故这里断言的是同一份文件的键集合，以及"重渲染不需要重测量"。
    """
    shim = _extension_shim(tmp_path / "faithful")
    _inject_into_the_line(monkeypatch, shim)

    result = measure_matrix([_archive_spec()], verify_adapters=False, native_line=_tiny_plan())
    # 真 `zoo-bench run` 会并存环境自述；缺它时渲染层拒绝发布，故这里照同一条路走
    result["environment"] = environment.collect(command=["zoo-bench", "run"])

    assert result["units"][0]["status"] == "ok"
    assert result["semantics"] is not None
    assert result["attribution"] and result["attribution"]["groups"]

    line = result["native_line"]
    assert line["status"] == "ok"
    assert line["groups"], "该线要有逐格留档"
    parent_pid = os.getpid()
    assert all(item["child"]["pid"] != parent_pid for item in line["groups"]), (
        "该线 MUST 经独立子进程运行：格子里的 pid 不该是父进程的"
    )
    assert line["plan"]["sampling"]["granularity"] == "concurrency"
    # 负载是**真实任务**这件事要可核（spec: native-task-line「负载是真实任务这一事实可核」）：任务名
    # 进留档，且与逐格留档里那个任务名一致——报告说的是这一轮**实际跑**的负载，不是另抄的声明
    assert line["plan"].get("task") == reference.TASK_NAME
    assert line["groups"] and all(item.get("measurement") for item in line["groups"])
    assert {item["measurement"]["task"] for item in line["groups"]} == {line["plan"]["task"]}

    path = storage.save_run(result, framework=FRAMEWORK, root=tmp_path / "results")
    loaded = storage.load_run(path)
    # 同一份留档里四个维度都在：主矩阵、语义、归因、该线
    assert {"units", "semantics", "attribution", "native_line"} <= set(loaded)

    for lang in (LANG_EN, LANG_ZH):
        model = build_model(loaded, lang=lang)
        dimension = model["dimensions"]["native_line"]
        assert dimension["status"] == "ok", lang
        assert dimension["rows"], "重渲染的报告里该维度要有逐档的行"
        assert dimension["rows"][0]["tier"] == "R1"
        text = markdown_renderer.render_markdown(model, [])
        assert t("report.native_line.title", lang) in text, "报告里没有该维度的标题"
        assert dimension["rows"][0]["tier"] in text, "逐档的行没渲染出来"


# ------------------------------------------------------------------ 4.2 三态落档


def test_an_old_release_without_the_capability_is_not_applicable_and_gives_no_numbers(
    tmp_path: Path,
) -> None:
    """没有该能力的已发布版本 → ``not_applicable``（附缺席的符号），且一个数字都不产出.

    "不适用"与"失败"必须分开：旧版本常年报红的话，这条线在 CI 里会变成必须忽略的噪声，而噪声
    一被忽略，真失败也一起被忽略了。
    """
    payload = _line(_old_release_shim(tmp_path / "old-release"))

    assert payload["status"] == "not_applicable"
    assert payload["reason"] == "native_line.reason.capability_missing"
    assert set(payload["reason_params"]["missing"].split(", ")) == set(REQUIRED_SYMBOLS)
    # 不产出任何数字：逐格、配对、派生、口径都没有，且没跑过测量子进程
    assert payload["groups"] == [] and payload["pairwise"] == []
    assert payload["closure"] == [], "不适用时 MUST NOT 派生测量子进程"
    assert "by_tier" not in payload
    assert payload["caliber"] is None
    assert payload["derived"]["suppressed"] is True


def test_a_faithful_extension_gives_the_line_numbers(faithful_line: dict[str, Any]) -> None:
    """``ok`` 态：门禁过得去时该线真的给出逐档数字.

    它是上面那条"没有数字"的对照侧——没有它，"不产出数字"在实现从不产数字时也照样成立。
    """
    assert faithful_line["status"] == "ok"
    assert faithful_line["by_tier"], "有数字的一态要有逐档的行"
    assert faithful_line["caliber"], "有数字就要有口径声明"
    # 抑制块**只在数字被抑制时**才出现（`payload_skeleton` 是"还没跑"与"失败"共用的空壳，
    # 正常结论在这之上多长出来的只有派生数字）：故"没被抑制"在这里的可核形态是它缺席
    assert "derived" not in faithful_line, "给了数字就不该带抑制块"


def test_an_unloadable_extension_is_a_failure_that_names_the_reason(tmp_path: Path) -> None:
    """扩展装不上 → ``failed``（附原因），与"不适用"分属两态.

    与上一条的差别只在**能力在不在场**：框架的四个符号都在，坏的是扩展这一层。两态的形状相同
    （都不产数字）、含义相反，故这里逐项对照。
    """
    unloadable = _extension_shim(
        tmp_path / "broken", head='raise ImportError("替身：故意装不上（用例造的故障形态）")\n'
    )
    payload = _line(unloadable)

    assert payload["status"] == "failed"
    assert payload["reason"] == "native_line.reason.extension_unavailable"
    assert "zoo_framework_native" in payload["reason_params"]["detail"]
    assert payload["capability"]["present"] is True, "能力在场，坏的只是扩展"
    assert payload["groups"] == [] and payload["pairwise"] == []
    assert payload["derived"]["suppressed"] is True


# ------------------------------------------------------------------ 4.3 失败不扩散


def test_a_failing_line_does_not_spread_and_is_reported_in_both_outlets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """该线失败后其余维度与其余版本照常测量、归档、重渲染，且失败在两个出口都出得来（不得静默）.

    两个出口都要：只看报告的人与只看 CI 日志的人都不该漏掉它。该线的失败**不进自检**——自检不过
    会拦住渲染与发布，而一条线的失败不该让其余版本已经测出来的数据发不出去（design D12）。

    版本按 CI 的形状**顺序**跑（一次运行一个版本，各留一份留档）：一条线的失败若把整轮带下去，
    下一个版本就连测都测不到——故这里逐个版本断言它自己的主矩阵与归档都完成了。
    """
    broken = _extension_shim(
        tmp_path / "broken", head='raise ImportError("替身：故意装不上（用例造的故障形态）")\n'
    )
    _inject_into_the_line(monkeypatch, broken)
    results_root = tmp_path / "results"

    for framework in (FRAMEWORK, "zoo-framework==0.10.6"):
        result = measure_matrix(
            [_archive_spec(framework)], verify_adapters=False, native_line=_tiny_plan()
        )
        result["environment"] = environment.collect(command=["zoo-bench", "run"])

        # 不扩散：主矩阵、自检、语义、归因照常
        assert result["units"][0]["status"] == "ok", framework
        assert result["self_check"]["ok"] is True, "该线失败不该进自检（那会拦住其余数据的发布）"
        assert result["semantics"] is not None and result["semantics"]["status"] != "probe_failed"
        assert result["attribution"] and result["attribution"]["groups"]

        line = result["native_line"]
        assert line["status"] == "failed", framework
        assert line["reason"] == "native_line.reason.extension_unavailable"

        # 出口一：CI 摘要（cli.run_command 打印的就是它）
        warnings = summarize_native_line(result)
        assert len(warnings) == 1, framework
        assert "failed" in warnings[0] and "原生扩展不可用" in warnings[0]

        # 出口二：报告告警——留档 → 重渲染仍然带它（发布没有被拦下）
        path = storage.save_run(result, framework=framework, root=results_root)
        loaded = storage.load_run(path)
        for lang in (LANG_EN, LANG_ZH):
            model = build_model(loaded, lang=lang)
            kind = t("report.caveat.native_line.kind", lang)
            caveats = [item for item in model["caveats"] if item["kind"] == kind]
            assert len(caveats) == 1, f"{framework} {lang}: 报告里没有该线的告警"
            assert caveats[0]["status"] == "failed"
            text = markdown_renderer.render_markdown(model, [])
            assert kind in text, f"{framework} {lang}: 告警没进渲染产物"
            # 该维度本身也在报告里，且如实标为失败
            assert model["dimensions"]["native_line"]["status"] == "failed"
            assert model["dimensions"]["native_line"]["rows"] == [], "失败时不该有数字"

    # 两个版本各自留了档：失败的该线没有把任何一轮测量从归档里带走
    assert set(storage.available_frameworks(root=results_root)) == {
        storage.framework_slug(FRAMEWORK),
        storage.framework_slug("zoo-framework==0.10.6"),
    }


# ------------------------------------------------------------------ 4.4 不同源与契约不匹配


def test_an_extension_from_another_tag_fails_and_names_both_sides(tmp_path: Path) -> None:
    """扩展取自另一个 tag → ``failed`` 并指出双方版本，MUST NOT 静默降级为纯解释形态.

    降级是这里最坏的形态：扩展换了版本后，纯解释形态照跑、报告照出、数字照有——只是"原生更快"
    这句话指向了另一个版本的原生实现。故判据是"状态为 failed 且一个数字都没有"。
    """
    payload = _line(_extension_shim(tmp_path / "other-tag", revision="v0.10.0"))

    assert payload["status"] == "failed"
    assert payload["reason"] == "native_line.reason.source_mismatch"
    assert payload["reason_params"] == {"expected": EXPECTED_REVISION, "actual": "v0.10.0"}
    # 双方版本在留档里各自可查（核验用它俩比对，读者用它俩复核）
    assert payload["extension"]["expected_revision"] == EXPECTED_REVISION
    assert payload["extension"]["source"]["requested_revision"] == "v0.10.0"
    assert payload["derived"]["suppressed"] is True
    assert payload["groups"] == [] and "by_tier" not in payload

    for lang in (LANG_EN, LANG_ZH):
        text = resolve_reason(payload["reason"], lang, payload["reason_params"])
        assert EXPECTED_REVISION in text and "v0.10.0" in text, f"{lang}: 没把双方版本都写出来"


def test_a_contract_version_mismatch_fails_and_names_both_versions(tmp_path: Path) -> None:
    """契约版本不匹配 → ``failed`` 并指出双方版本，同样不许降级.

    这一态由**扩展**上报的版本与框架支持的版本之差触发，故留档里 MUST NOT 出现契约描述——握手
    没过，就没读到任何描述，写一份空的出来会让读者以为握手成功过。
    """
    payload = _line(_extension_shim(tmp_path / "wrong-contract", contract_version=2))

    assert payload["status"] == "failed"
    assert payload["reason"] == "native_line.reason.extension_unavailable"
    detail = payload["reason_params"]["detail"]
    assert "2" in detail and str(CONTRACT_VERSION) in detail, "没把双方版本都写出来"
    assert payload["extension"] == {}, "握手没过就不该有契约描述"
    assert payload["equivalence"] == {}, "握手没过就不该有门禁结论"
    assert payload["derived"]["suppressed"] is True
    assert payload["groups"] == [] and "by_tier" not in payload
