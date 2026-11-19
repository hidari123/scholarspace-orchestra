# scholarspace-orchestra

scholarspace 编排引擎 —— 把 LLM、工具、检索、代码、人审串成可复现的工作流。

> **独立仓**（A 类）：单独能跑，也能被别人引用。

---

## 这是什么

```
用户请求 → Flow（流程图）→ DAG 调度器 → Node（节点）→ 结果
```

**Flow** 是一张有向无环图：

```
        ┌──────┐
   ┌───▶│ 检索  │───┐
   │    └──────┘   │
┌──┴──┐          ┌──┴──┐
│ LLM │─────────▶│ 汇总 │
└──┬──┘          └──┬──┘
   │                │
   └──────▶┌───────┘
          ▼
      ┌────────┐
      │ 人工审批 │──▶（等待人工输入）
      └────────┘
```

---

## 包结构

```
libs/
├── orchestra-core/       领域模型 + DAG 校验（零外部依赖）
│   └── src/orchestra_core/
│       ├── domain/
│       │   ├── entities.py      Flow / Node / Edge / FlowGraph / Run
│       │   ├── value_objects.py FlowId / NodeId / Position / AccessLevel
│       │   ├── run_status.py    RunStatus + 状态机
│       │   ├── graph.py         拓扑排序 + 环检测
│       │   └── errors.py        FlowValidationError / CycleDetectedError
│       ├── application/         用例编排 + 端口协议
│       └── infrastructure/      存储实现
│
└── orchestra-runtime/    DAG 调度器 + 节点执行
    └── src/orchestra_runtime/
        ├── executor.py     NodeExecutor 协议 + ExecutionContext
        └── scheduler.py    Scheduler（拓扑调度 / 数据绑定 / 重试 / 成本）
```

⚠️ **core 与 runtime 的分工**：
core 管「图是什么」，runtime 管「图怎么跑」。

---

## 三个核心机制

### ① DAG 校验 —— 保存时就拦住环

```python
from orchestra_core import build_graph, CycleDetectedError

try:
    graph = build_graph(flow)
except CycleDetectedError as error:
    print(error.cycle)   # ('a', 'b', 'c')
```

⚠️ **环必须在保存时拦住** —— 运行时才发现的话，
run 会永远卡在等输入的状态。

⚠️ **拓扑序是确定的** —— 用节点在 flow 里的顺序做初始队列，
不是 `set`。同样的图每次排序结果不同的话没法复现问题。

### ② 数据绑定 —— 三种来源

```python
inputs = {
    "text": "来自上游节点的输出",
    "question": "${初始变量}",      # 引用流程变量
    "limit": 10,                     # 常量
}
```

⚠️ **边（Edge）与绑定（PortBinding）是两回事**：
边管图结构（依赖、拓扑序），绑定管数据流（谁接谁）。

### ③ 失败与跳过 —— 都要能定位到节点

| 情况 | 结果 |
|---|---|
| 节点执行失败 | run 失败，`error.failed_node_id` 指向具体节点 |
| 节点类型没注册执行器 | run 失败，`executor_not_found` |
| 分支跳过导致下游不可达 | run 失败，`unreachable_nodes` |
| 重试 | 只对 `retryable:` 前缀的错误码重试 |

⚠️ **跳过 ≠ 不可达**：
「分支未命中」本身是正常业务逻辑，
但「因为分支未命中导致下游跑不到」是**流程设计问题**，用户需要知道。

---

## 用法

```python
from orchestra_core import Flow, Node, build_graph
from orchestra_runtime import Scheduler

# 1. 定义流程
flow = Flow(
    flow_id=flow_id,
    name="论文摘要",
    nodes=(
        Node(node_id="llm_1", node_type="llm", position=Position(0, 0)),
        Node(node_id="llm_2", node_type="llm", position=Position(0, 100)),
    ),
    edges=(...),
)

# 2. 校验（会拦住环）
graph = build_graph(flow)

# 3. 执行
scheduler = Scheduler({"llm": my_llm_executor})
result = await scheduler.run(graph, run, variables={"question": "什么是DAG"})

print(result.status, result.usage.cost)
```

---

## 加一个节点

实现 `NodeExecutor` 协议就行，调度器不用改：

```python
class MyNodeExecutor(NodeExecutor):
    @property
    def node_type(self) -> str:
        return "my_node"

    async def execute(self, node, inputs, ctx):
        return NodeExecutionResult(outputs={"out": "结果"})
```

⚠️ **节点执行器要自己声明错误可重试** —— 错误码加 `retryable:` 前缀。
默认**不可重试** —— 盲目重试会烧钱（LLM 节点尤其）。

---

## 开发

```bash
cd libs/orchestra-core
.venv/Scripts/python.exe -m pytest      # 32 tests
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m mypy src/

cd ../orchestra-runtime
.venv/Scripts/python.exe -m pytest      # 18 tests
```

---

## 与其他包的关系

```
orchestra ──publish/subscribe──→ events（事件服务）
    │
    ├──LLMPort──→ llm（主仓服务）
    │
    └──节点插件──→ slides / paper / community（通过 Port 接口，不直接 import）
```

⚠️ **学术专用节点**（`paper_insight` / `slide_generate` / `citation_format`）
通过 **Port 接口**调用其他包，**不直接 import** ——
这样 orchestra 保持独立可发布。

---

## 已知限制

- **没有内置节点实现** —— 目前只有执行器协议与调度器，
  具体节点（LLM / 工具 / 分支 / 合并）待补
- **没有 HTTP 层** —— 会话管理、节点市场、SSE 端点待补
- **成本闸门未接** —— `ExecutionContext` 有 `budget_limit` 字段但没 enforcement
- **Redis 实现缺失** —— 只在内存里跑
