"""同生态调度库之二：``Celery``。

三处刻意的设计（design D3）：

- **不在默认矩阵里，且 ``comparable = False``**。Celery 是分布式方案：需要 broker 与独立
  worker 进程，投递与结果回传都要跨进程。这与"进程内派发一段执行体、收回完成信号"不是
  同一件事，它的数字不参与交叉点推导。
- **依赖在模块导入期即检查**，没装 extras 时抛 ``OptionalDependencyMissing``（带可直接
  照抄的安装命令），而不是让 ``ImportError`` 冒到调用方。
- **刻意不做 ``task_always_eager`` 之类的兜底**。那会让执行体在本进程内联执行，测出来的
  数字与"分布式调度"毫无关系——用假数据冒充对照，比不测更坏。
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .base import DRAIN_TIMEOUT_SECONDS, BaseAdapter, OptionalDependencyMissing, Tier
from .registry import register

try:
    from celery import Celery
except ImportError as exc:
    raise OptionalDependencyMissing(
        "celery 适配器需要可选依赖：pip install 'zoo-bench[celery]'"
    ) from exc

BROKER_ENV = "ZOO_BENCH_CELERY_BROKER"
BACKEND_ENV = "ZOO_BENCH_CELERY_BACKEND"

app = Celery("zoo_bench")


@app.task(name="zoo_bench.run_body")  # type: ignore[untyped-decorator]
def run_body(body: Callable[[], float]) -> float:
    """worker 侧执行执行体并回传其自报耗时。"""
    return body()


@register
class CeleryAdapter(BaseAdapter):
    """把执行体投给 Celery，由独立 worker 进程执行。"""

    name = "celery"
    tier = Tier.ECOSYSTEM
    comparable = False
    notes = "adapters.notes.celery_pool"

    def setup(self, *, workers: int) -> None:
        broker = os.environ.get(BROKER_ENV)
        backend = os.environ.get(BACKEND_ENV)
        if not broker or not backend:
            raise RuntimeError(
                f"celery 对照需要基础设施：请设置 {BROKER_ENV} 与 {BACKEND_ENV}"
                f"（例如 redis://localhost:6379/0），并另起一个 worker："
                f"celery -A zoo_bench.adapters.celery_pool.app worker"
                f" --concurrency={workers} --pool=threads"
            )
        app.conf.broker_url = broker
        app.conf.result_backend = backend
        self._results: list = []

    def submit(self, body: Callable[[], float]) -> None:
        self._results.append(run_body.delay(body))

    def drain(self) -> list[float]:
        results, self._results = self._results, []
        return [result.get(timeout=DRAIN_TIMEOUT_SECONDS) for result in results]

    def teardown(self) -> None:
        self._results.clear()
