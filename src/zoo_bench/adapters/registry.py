"""适配器登记。

内置适配器由 ``load_builtins`` 装载；使用者可在 ``src/zoo_bench/`` 之外实现契约并登记
（spec: adapter-contract 的"新增对照 MUST 无需修改既有代码"）。

刻意不做成"导入本包即自动登记全部内置档位"：那会让 ``import zoo_bench.adapters`` 连带
导入 apscheduler 等依赖，把一次纯读取变成一次重量级导入。装载是显式的。
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable

from .base import BaseAdapter, OptionalDependencyMissing

_REGISTRY: dict[str, type[BaseAdapter]] = {}

# 内置档位的模块名（相对本包）。celery 在最后：它没有可选依赖时会在导入期抛出
# OptionalDependencyMissing，被 load_external 跳过而非使整轮失败。
_BUILTIN_MODULES: tuple[str, ...] = (
    "bare_thread",
    "thread_pool",
    "asyncio_pool",
    "process_pool",
    "apscheduler_pool",
    "zoo",
    "celery_pool",
)


def register[T: BaseAdapter](cls: type[T]) -> type[T]:
    """把一个适配器类登记进注册表，可作为类装饰器使用。

    Raises:
        TypeError: ``cls`` 不是 ``BaseAdapter`` 的子类。
        ValueError: ``cls`` 未声明 ``name``，或该标识已被另一个类占用。
    """
    if not (isinstance(cls, type) and issubclass(cls, BaseAdapter)):
        raise TypeError(f"{cls!r} 不是 BaseAdapter 的子类")

    if not cls.name:
        raise ValueError(f"{cls.__name__} 未声明 name")

    existing = _REGISTRY.get(cls.name)
    if existing is not None and existing is not cls:
        raise ValueError(
            f"适配器标识 {cls.name!r} 已被 {existing.__module__}.{existing.__qualname__} 占用"
        )

    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type[BaseAdapter]:
    """按标识取出适配器类。

    Raises:
        KeyError: 该标识未登记。
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"未登记的适配器 {name!r}；已登记：{sorted(_REGISTRY)}") from None


def registered() -> dict[str, type[BaseAdapter]]:
    """返回当前注册表的副本。"""
    return dict(_REGISTRY)


def load_external(modules: Iterable[str]) -> tuple[list[str], list[str]]:
    """导入给定模块以触发其中的登记。

    Args:
        modules: 点分模块路径。

    Returns:
        ``(已装载, 已跳过)``。因可选依赖缺失而跳过的模块记入后者——这是可预期情形
        （例：没装 celery extras），不应使整轮测量失败；模块路径错误等真故障照常抛出。
    """
    loaded: list[str] = []
    skipped: list[str] = []
    for path in modules:
        try:
            importlib.import_module(path)
        except OptionalDependencyMissing:
            skipped.append(path)
            continue
        loaded.append(path)
    return loaded, skipped


def load_builtins() -> tuple[list[str], list[str]]:
    """装载全部内置档位。返回 ``(已装载, 已跳过)``。"""
    return load_external(f"{__name__.rsplit('.', 1)[0]}.{name}" for name in _BUILTIN_MODULES)
