"""运行状态与状态机。

⚠️ **状态迁移规则集中在这里** —— 不是散落在各个 `if` 里。

    Run:      PENDING ──► RUNNING ──┬──► SUCCEEDED
                          │           └──► FAILED
                          └──────► CANCELLED

    NodeRun:  PENDING ──► RUNNING ──┬──► SUCCEEDED
                          │           ├──► FAILED
                          │           └──► SKIPPED   (条件分支未命中)

⚠️ **取消是幂等的** —— 已经在终态的运行再取消，不会报错也不会改状态。
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "InvalidTransitionError",
    "RunStatus",
    "can_transition",
]


class InvalidTransitionError(ValueError):
    """非法状态迁移。"""


class RunStatus(StrEnum):
    """运行状态。"""

    #: 排队中
    PENDING = "pending"
    #: 执行中
    RUNNING = "running"
    #: 成功
    SUCCEEDED = "succeeded"
    #: 失败
    FAILED = "failed"
    #: 已取消
    CANCELLED = "cancelled"
    #: 已跳过（仅节点用 —— 条件分支未命中）
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        """是否是终态。

        ⚠️ 终态的运行不会再变化 —— 调度器据此停止跟踪。
        """
        return self in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.SKIPPED,
        }

    @property
    def is_active(self) -> bool:
        """是否在进行中。"""
        return self in {RunStatus.PENDING, RunStatus.RUNNING}


#: 合法的状态迁移表。
#:
#: ⚠️ **用表驱动而不是 if-else** ——
#:    加一个状态时只改这张表，不到处找 if。
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLED,
            # ⚠️ PENDING 直接跳过 —— 依赖检查发现不可达时
            RunStatus.SKIPPED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    # ⚠️ 终态没有后继 —— 但取消是幂等的，
    #    所以 CANCELLED 允许再 CANCELLED。
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset({RunStatus.CANCELLED}),
    RunStatus.SKIPPED: frozenset(),
}


def can_transition(source: RunStatus, target: RunStatus) -> bool:
    """判断状态迁移是否合法。

    :param source: 当前状态
    :param target: 目标状态
    """
    if source == target:
        # ⚠️ 同状态迁移 —— 只有 CANCELLED 允许（幂等取消）
        return source is RunStatus.CANCELLED

    return target in _TRANSITIONS.get(source, frozenset())
