"""对照方案的适配器契约与内置实现。

契约见 :mod:`zoo_bench.adapters.base`；登记与装载见 :mod:`zoo_bench.adapters.registry`。
harness 只依赖契约，不内建任何具体方案（design D2）。

内置档位由 ``load_builtins()`` **显式**装载，不随本包的导入而自动装载——否则
``import zoo_bench.adapters`` 会连带导入 apscheduler 等依赖。
"""

from .base import DRAIN_TIMEOUT_SECONDS, BaseAdapter, OptionalDependencyMissing, Tier
from .registry import get, load_builtins, load_external, register, registered

__all__ = [
    "DRAIN_TIMEOUT_SECONDS",
    "BaseAdapter",
    "OptionalDependencyMissing",
    "Tier",
    "get",
    "load_builtins",
    "load_external",
    "register",
    "registered",
]
