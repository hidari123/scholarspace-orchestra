"""orchestra-runtime —— DAG 调度器与节点执行。

⚠️ 与 orchestra-core 的分工：
    core     → 领域模型 + 图校验（零外部依赖）
    runtime  → 调度器 + 执行器协议 + 节点注册表（依赖 core）
"""

from orchestra_runtime.builtin_nodes import (
    BranchExecutor,
    LLMExecutor,
    MergeExecutor,
    ToolExecutor,
    register_builtin_nodes,
)
from orchestra_runtime.executor import (
    ExecutionContext,
    NodeExecutionResult,
    NodeExecutor,
)
from orchestra_runtime.registry import (
    NodeCategory,
    NodeRegistry,
    NodeSpec,
    PortSpec,
)
from orchestra_runtime.scheduler import (
    NodeExecutorNotFoundError,
    RetryPolicy,
    Scheduler,
    SchedulerState,
)

__all__ = [
    "BranchExecutor",
    "ExecutionContext",
    "LLMExecutor",
    "MergeExecutor",
    "NodeCategory",
    "NodeExecutionResult",
    "NodeExecutor",
    "NodeExecutorNotFoundError",
    "NodeRegistry",
    "NodeSpec",
    "PortSpec",
    "RetryPolicy",
    "Scheduler",
    "SchedulerState",
    "ToolExecutor",
    "register_builtin_nodes",
]
