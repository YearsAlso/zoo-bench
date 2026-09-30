"""适配器契约。

harness 只依赖本模块定义的契约，不内建任何具体方案（design D2）。新增对照只需实现
``BaseAdapter`` 并登记，不必改动编排、度量或渲染代码。

被测框架自身（zoo）与全部对照方案实现同一个契约——这是 spec ``adapter-contract`` 中
"被测框架 MUST 通过与对照相同的契约接入"那条公平性断言的落点。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import ClassVar


class Tier(StrEnum):
    """适配器所属档位。"""

    UNDER_TEST = "under_test"
    BARE = "bare"
    STDLIB = "stdlib"
    ECOSYSTEM = "ecosystem"


#: ``drain`` 的内部兜底上限（秒）。它不是被测指标，只用于避免某个档位挂死时整轮无终止。
#: 超过该上限仍未排空即判该测量单元失败。
DRAIN_TIMEOUT_SECONDS = 600.0


class OptionalDependencyMissing(RuntimeError):
    """可选对照方案的依赖未安装。

    刻意不用裸 ``ImportError``：需要区分"该方案的可选依赖没装（可预期，装 extras 即可）"
    与"模块路径写错（真故障）"，并给出可直接照抄的安装命令。
    """


class BaseAdapter(ABC):
    """一个方案的执行环境。

    实现者只需回答"如何以 N 路并发跑一个同步函数、并把它自报的耗时收回来"；耗时口径、
    预热、重复采样与结果归组都由编排层负责。

    被测执行体 ``body`` 的约定：

    - 无参数调用，返回它在**自身内部**实测的耗时（秒）——这是 design D5 要求的同一次
      运行内埋点，编排层不做跨运行减法
    - **必须是可被 pickle 的模块级可调用对象**（不能是闭包），因为多进程档位要把它送到
      子进程。需要参数时用可 pickle 的对象包裹模块级函数
    - MUST NOT 吞掉异常：异常须向上传播，使该测量单元被标记为失败而不是产出可疑数字

    Attributes:
        name: 适配器标识，与 ``matrix.yaml`` 中 ``adapters`` 的条目对应。
        tier: 所属档位。
        comparable: 是否可与被测框架直接对标。为 False 时其数据不参与交叉点推导。
        notes: 口径说明，随数据出现在报告中。
        drive_level: 被测框架被驱动的层级说明；**仅被测框架的适配器需要**，用于满足
            spec 的"被测层级被声明"（避免把派发原语开销读成终端用户延迟）。
    """

    name: ClassVar[str] = ""
    tier: ClassVar[Tier]
    comparable: ClassVar[bool] = True
    notes: ClassVar[str] = ""
    drive_level: ClassVar[str] = ""

    @abstractmethod
    def setup(self, *, workers: int) -> None:
        """准备 ``workers`` 路并发的执行环境。

        本方法的耗时 MUST NOT 计入被测区间（design D4）：子进程派生、依赖导入、线程池
        创建都发生在这里。
        """

    @abstractmethod
    def submit(self, body: Callable[[], float]) -> None:
        """提交一个执行体。MUST NOT 阻塞至其完成。"""

    @abstractmethod
    def drain(self) -> list[float]:
        """阻塞至全部已提交执行体完成，返回各执行体自报的耗时（秒）。

        返回后本轮提交被清空，使同一实例可跑多轮（预热轮 + 正式采样轮），无需重新
        ``setup``。执行体抛出的异常 MUST 向上传播。返回顺序 MUST NOT 被假定为提交顺序。
        """

    @abstractmethod
    def teardown(self) -> None:
        """释放执行环境。幂等。"""
