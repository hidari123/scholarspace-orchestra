"""运行状态机测试。"""

from __future__ import annotations

from orchestra_core.domain.run_status import RunStatus, can_transition


class TestRunStatus:
    """状态属性。"""

    def test_terminal_states(self) -> None:
        """终态。"""
        assert RunStatus.SUCCEEDED.is_terminal
        assert RunStatus.FAILED.is_terminal
        assert RunStatus.CANCELLED.is_terminal
        assert RunStatus.SKIPPED.is_terminal

    def test_non_terminal_states(self) -> None:
        """非终态。"""
        assert not RunStatus.PENDING.is_terminal
        assert not RunStatus.RUNNING.is_terminal

    def test_active_states(self) -> None:
        """进行中。"""
        assert RunStatus.PENDING.is_active
        assert RunStatus.RUNNING.is_active
        assert not RunStatus.SUCCEEDED.is_active


class TestTransitions:
    """状态迁移。"""

    def test_pending_to_running(self) -> None:
        """PENDING → RUNNING。"""
        assert can_transition(RunStatus.PENDING, RunStatus.RUNNING)

    def test_running_to_succeeded(self) -> None:
        """RUNNING → SUCCEEDED。"""
        assert can_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)

    def test_running_to_failed(self) -> None:
        """RUNNING → FAILED。"""
        assert can_transition(RunStatus.RUNNING, RunStatus.FAILED)

    def test_running_to_cancelled(self) -> None:
        """RUNNING → CANCELLED。"""
        assert can_transition(RunStatus.RUNNING, RunStatus.CANCELLED)

    def test_pending_to_cancelled(self) -> None:
        """PENDING → CANCELLED（还没开始就取消）。"""
        assert can_transition(RunStatus.PENDING, RunStatus.CANCELLED)

    def test_pending_to_skipped(self) -> None:
        """PENDING → SKIPPED（依赖检查发现不可达）。"""
        assert can_transition(RunStatus.PENDING, RunStatus.SKIPPED)

    def test_terminal_cannot_transition(self) -> None:
        """⚠️ 终态不能再迁移。"""
        assert not can_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)
        assert not can_transition(RunStatus.FAILED, RunStatus.RUNNING)

    def test_cannot_skip_running(self) -> None:
        """⚠️ PENDING 不能直接到 SUCCEEDED。

        没执行就成功 —— 状态乱了。
        """
        assert not can_transition(RunStatus.PENDING, RunStatus.SUCCEEDED)

    def test_cannot_go_backwards(self) -> None:
        """不能回退。"""
        assert not can_transition(RunStatus.RUNNING, RunStatus.PENDING)
        assert not can_transition(RunStatus.SUCCEEDED, RunStatus.RUNNING)

    def test_cancel_is_idempotent(self) -> None:
        """⚠️ 取消是幂等的。

        已经在终态的运行再取消 —— 不报错也不改状态。
        """
        assert can_transition(RunStatus.CANCELLED, RunStatus.CANCELLED)
        assert can_transition(RunStatus.RUNNING, RunStatus.CANCELLED)

    def test_same_state_transition_only_allowed_for_cancelled(self) -> None:
        """⚠️ 同状态迁移只有 CANCELLED 允许。

        RUNNING → RUNNING 应该是「重复开始执行」，那是 bug。
        """
        assert not can_transition(RunStatus.RUNNING, RunStatus.RUNNING)
        assert not can_transition(RunStatus.PENDING, RunStatus.PENDING)
