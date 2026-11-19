"""orchestra-core —— 编排引擎的领域与应用层。

⚠️ **零外部依赖**（领域层）—— 编排的核心概念可以独立复用。
"""

from orchestra_core.application.ports import (
    FlowRepositoryPort,
    RunRepositoryPort,
)
from orchestra_core.application.use_cases import (
    FlowContext,
    SessionContext,
    append_message,
    create_flow,
    create_session,
    delete_flow,
    get_flow,
    list_flows,
    update_flow,
    validate_flow,
)
from orchestra_core.domain.entities import (
    Edge,
    ErrorInfo,
    Flow,
    FlowGraph,
    Node,
    NodeRun,
    PortBinding,
    PortSpec,
    Run,
    Usage,
)
from orchestra_core.domain.errors import (
    CycleDetectedError,
    FlowValidationError,
    NodeNotFoundError,
    PortTypeMismatchError,
)
from orchestra_core.domain.graph import build_graph, find_cycle, topological_sort
from orchestra_core.domain.run_status import (
    InvalidTransitionError,
    RunStatus,
    can_transition,
)
from orchestra_core.domain.session import (
    ContentBlock,
    DelegationRecord,
    Message,
    MessageRole,
    Session,
)
from orchestra_core.domain.value_objects import (
    AccessLevel,
    EdgeId,
    FlowId,
    FlowVersion,
    NodeId,
    NodeRunId,
    Position,
    RunId,
    SessionId,
)
from orchestra_core.infrastructure.memory import (
    InMemoryFlowRepository,
    InMemoryRunRepository,
)

__all__ = [
    "AccessLevel",
    "ContentBlock",
    "CycleDetectedError",
    "DelegationRecord",
    "Edge",
    "EdgeId",
    "ErrorInfo",
    "Flow",
    "FlowContext",
    "FlowGraph",
    "FlowId",
    "FlowRepositoryPort",
    "FlowValidationError",
    "FlowVersion",
    "InMemoryFlowRepository",
    "InMemoryRunRepository",
    "InvalidTransitionError",
    "Message",
    "MessageRole",
    "Node",
    "NodeId",
    "NodeNotFoundError",
    "NodeRun",
    "NodeRunId",
    "PortBinding",
    "PortSpec",
    "PortTypeMismatchError",
    "Position",
    "Run",
    "RunId",
    "RunRepositoryPort",
    "RunStatus",
    "Session",
    "SessionContext",
    "SessionId",
    "Usage",
    "append_message",
    "build_graph",
    "can_transition",
    "create_flow",
    "create_session",
    "delete_flow",
    "find_cycle",
    "get_flow",
    "list_flows",
    "topological_sort",
    "update_flow",
    "validate_flow",
]
