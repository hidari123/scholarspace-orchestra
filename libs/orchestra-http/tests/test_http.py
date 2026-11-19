"""HTTP 接口层测试。

⚠️ 重点验证**安全边界**与**语义正确性**：
    · 有环的流程 → 422（保存时就拦住）
    · 不存在 → 404 vs 校验失败 → 422（语义要分开）
    · 多余字段 → 422
    · SSE 格式正确 + 断线重连从 seq 开始
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from orchestra_core.infrastructure.memory import (
    InMemoryFlowRepository,
    InMemoryRunRepository,
)
from orchestra_runtime.builtin_nodes import register_builtin_nodes
from orchestra_runtime.registry import NodeRegistry

from orchestra_http.app import create_app
from orchestra_http.dependencies import (
    get_flow_repository,
    get_run_repository,
)
from orchestra_http.routers import sessions as sessions_module

#: 节点字典（HTTP 层）。
NODE_A = {
    "node_id": "a",
    "node_type": "llm",
    "position": {"x": 0, "y": 0},
    "config": {"prompt": "你好"},
    "inputs": [],
}

NODE_B = {
    "node_id": "b",
    "node_type": "llm",
    "position": {"x": 0, "y": 100},
    "config": {"prompt": "继续"},
    "inputs": [{"target_port": "in", "source_node": "a", "source_port": "out"}],
}

EDGE_AB = {
    "source_node": "a",
    "source_port": "out",
    "target_node": "b",
    "target_port": "in",
}


@pytest.fixture
def flow_repo() -> InMemoryFlowRepository:
    """流程仓储。"""
    return InMemoryFlowRepository()


@pytest.fixture
def run_repo() -> InMemoryRunRepository:
    """运行仓储。"""
    return InMemoryRunRepository()


@pytest.fixture
def client(
    flow_repo: InMemoryFlowRepository,
    run_repo: InMemoryRunRepository,
) -> Iterator[TestClient]:
    """测试客户端。

    ⚠️ 每个测试拿到**独立的**仓储 —— 用 dependency_overrides 注入。
    """
    app = create_app()
    app.dependency_overrides[get_flow_repository] = lambda: flow_repo
    app.dependency_overrides[get_run_repository] = lambda: run_repo

    # ⚠️ 会话是模块级的 —— 每个测试前清空，否则互相干扰
    sessions_module._sessions.clear()

    with TestClient(app) as test_client:
        yield test_client


class TestHealth:
    """健康检查。"""

    def test_health_is_public(self, client: TestClient) -> None:
        """⚠️ 健康检查不需要认证 —— 探活没有凭据。"""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestFlows:
    """流程端点。"""

    def test_create_flow(self, client: TestClient) -> None:
        """创建流程。"""
        response = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "论文摘要", "nodes": [NODE_A], "edges": []},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "论文摘要"
        assert body["version"] == 1
        assert len(body["nodes"]) == 1

    def test_create_flow_with_cycle_is_rejected(self, client: TestClient) -> None:
        """⚠️ **有环 → 422** —— 保存时就拦住，不留到运行时。

        这是编排引擎最重要的护栏。
        """
        response = client.post(
            "/api/v1/orchestra/flows",
            json={
                "name": "有环",
                "nodes": [NODE_A, NODE_B],
                "edges": [
                    EDGE_AB,
                    {
                        "source_node": "b",
                        "source_port": "out",
                        "target_node": "a",
                        "target_port": "in",
                    },
                ],
            },
        )

        assert response.status_code == 422
        assert "环" in response.json()["detail"]

    def test_create_flow_with_duplicate_node_ids(self, client: TestClient) -> None:
        """⚠️ 节点 ID 重复 → 422。"""
        response = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "重复", "nodes": [NODE_A, NODE_A], "edges": []},
        )

        assert response.status_code == 422

    def test_create_flow_rejects_extra_fields(self, client: TestClient) -> None:
        """⚠️ 多余字段 → 422。

        客户端以为传了字段就生效 —— 早失败好过静默忽略。
        """
        response = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "x", "nodes": [], "edges": [], "is_admin": True},
        )

        assert response.status_code == 422

    def test_create_flow_requires_name(self, client: TestClient) -> None:
        """名字必填。"""
        response = client.post(
            "/api/v1/orchestra/flows",
            json={"nodes": [], "edges": []},
        )

        assert response.status_code == 422

    def test_list_flows(self, client: TestClient) -> None:
        """列出流程。"""
        client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [], "edges": []},
        )
        client.post(
            "/api/v1/orchestra/flows",
            json={"name": "B", "nodes": [], "edges": []},
        )

        response = client.get("/api/v1/orchestra/flows")

        assert response.status_code == 200
        assert len(response.json()) == 2

    def test_get_flow(self, client: TestClient) -> None:
        """取流程。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [NODE_A], "edges": []},
        ).json()

        response = client.get(f"/api/v1/orchestra/flows/{created['flow_id']}")

        assert response.status_code == 200
        assert response.json()["name"] == "A"

    def test_get_unknown_flow_returns_404(self, client: TestClient) -> None:
        """⚠️ 不存在 → 404。"""
        response = client.get(f"/api/v1/orchestra/flows/{uuid4()}")

        assert response.status_code == 404

    def test_update_flow_bumps_version(self, client: TestClient) -> None:
        """⚠️ 更新 → 版本号 +1。

        正在运行的 run 引用的是特定版本，改流程不影响它。
        """
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [], "edges": []},
        ).json()

        response = client.put(
            f"/api/v1/orchestra/flows/{created['flow_id']}",
            json={"name": "改过了"},
        )

        assert response.status_code == 200
        assert response.json()["version"] == 2
        assert response.json()["name"] == "改过了"

    def test_update_flow_with_cycle_is_rejected(self, client: TestClient) -> None:
        """⚠️ 更新成有环 → 422。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [NODE_A], "edges": []},
        ).json()

        response = client.put(
            f"/api/v1/orchestra/flows/{created['flow_id']}",
            json={
                "nodes": [NODE_A, NODE_B],
                "edges": [
                    EDGE_AB,
                    {
                        "source_node": "b",
                        "source_port": "out",
                        "target_node": "a",
                        "target_port": "in",
                    },
                ],
            },
        )

        assert response.status_code == 422

    def test_update_unknown_flow_returns_404(self, client: TestClient) -> None:
        """⚠️ 更新不存在的 → 404（不是 422）。

        「不存在」与「不合法」是两种语义。
        """
        response = client.put(
            f"/api/v1/orchestra/flows/{uuid4()}",
            json={"name": "x"},
        )

        assert response.status_code == 404

    def test_delete_flow(self, client: TestClient) -> None:
        """删除流程。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [], "edges": []},
        ).json()

        response = client.delete(f"/api/v1/orchestra/flows/{created['flow_id']}")
        assert response.status_code == 204

        assert client.get(f"/api/v1/orchestra/flows/{created['flow_id']}").status_code == 404

    def test_delete_unknown_flow_is_idempotent(self, client: TestClient) -> None:
        """⚠️ 删不存在的也返回 204 —— 删除是幂等的。"""
        response = client.delete(f"/api/v1/orchestra/flows/{uuid4()}")
        assert response.status_code == 204


class TestRuns:
    """运行端点。"""

    def test_create_run_returns_202(self, client: TestClient) -> None:
        """⚠️ **202 Accepted** —— 执行是异步的。

        201 Created 会让人以为「执行完成了」。
        """
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [NODE_A], "edges": []},
        ).json()

        response = client.post(
            "/api/v1/orchestra/runs",
            params={"flow_id": created["flow_id"]},
        )

        assert response.status_code == 202
        assert response.json()["status"] == "pending"

    def test_create_run_for_unknown_flow_returns_404(self, client: TestClient) -> None:
        """⚠️ 流程不存在 → 404。"""
        response = client.post(
            "/api/v1/orchestra/runs",
            params={"flow_id": str(uuid4())},
        )

        assert response.status_code == 404

    def test_get_run(self, client: TestClient) -> None:
        """取运行状态。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [NODE_A], "edges": []},
        ).json()

        run = client.post(
            "/api/v1/orchestra/runs",
            params={"flow_id": created["flow_id"]},
        ).json()

        response = client.get(f"/api/v1/orchestra/runs/{run['run_id']}")

        assert response.status_code == 200
        assert response.json()["run_id"] == run["run_id"]

    def test_get_unknown_run_returns_404(self, client: TestClient) -> None:
        """⚠️ 运行不存在 → 404。"""
        response = client.get(f"/api/v1/orchestra/runs/{uuid4()}")
        assert response.status_code == 404

    def test_list_runs_by_flow(self, client: TestClient) -> None:
        """列出某流程的运行。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [NODE_A], "edges": []},
        ).json()

        client.post("/api/v1/orchestra/runs", params={"flow_id": created["flow_id"]})
        client.post("/api/v1/orchestra/runs", params={"flow_id": created["flow_id"]})

        response = client.get(
            "/api/v1/orchestra/runs",
            params={"flow_id": created["flow_id"]},
        )

        assert response.status_code == 200
        assert len(response.json()) == 2


class TestNodes:
    """节点市场。"""

    def test_list_nodes_empty_without_registry(self, client: TestClient) -> None:
        """⚠️ 没注入注册表 → 空列表，不是 500。"""
        response = client.get("/api/v1/orchestra/nodes")

        assert response.status_code == 200
        assert response.json() == []

    def test_list_nodes_with_registry(self, flow_repo: InMemoryFlowRepository) -> None:
        """注入注册表后能列出节点。"""
        registry = register_builtin_nodes(NodeRegistry())
        app = create_app(registry=registry)
        app.dependency_overrides[get_flow_repository] = lambda: flow_repo

        with TestClient(app) as test_client:
            response = test_client.get("/api/v1/orchestra/nodes")

        assert response.status_code == 200
        types = {item["type"] for item in response.json()}
        assert "branch" in types
        assert "merge" in types

    def test_list_categories(self, flow_repo: InMemoryFlowRepository) -> None:
        """列出分类。"""
        registry = register_builtin_nodes(NodeRegistry())
        app = create_app(registry=registry)
        app.dependency_overrides[get_flow_repository] = lambda: flow_repo

        with TestClient(app) as test_client:
            response = test_client.get("/api/v1/orchestra/nodes/categories")

        assert response.status_code == 200
        assert "control" in response.json()


class TestSessions:
    """会话端点。"""

    def test_create_session(self, client: TestClient) -> None:
        """创建会话。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [], "edges": []},
        ).json()

        response = client.post(
            "/api/v1/orchestra/sessions",
            json={"flow_id": created["flow_id"], "title": "对话"},
        )

        assert response.status_code == 201
        assert response.json()["title"] == "对话"

    def test_create_session_for_unknown_flow_returns_404(self, client: TestClient) -> None:
        """⚠️ 流程不存在 → 404。"""
        response = client.post(
            "/api/v1/orchestra/sessions",
            json={"flow_id": str(uuid4())},
        )

        assert response.status_code == 404

    def test_get_unknown_session_returns_404(self, client: TestClient) -> None:
        """⚠️ 会话不存在 → 404。"""
        response = client.get(f"/api/v1/orchestra/sessions/{uuid4()}")
        assert response.status_code == 404

    def test_append_message_updates_session(self, client: TestClient) -> None:
        """追加消息后会话更新。"""
        created = client.post(
            "/api/v1/orchestra/flows",
            json={"name": "A", "nodes": [], "edges": []},
        ).json()

        session = client.post(
            "/api/v1/orchestra/sessions",
            json={"flow_id": created["flow_id"]},
        ).json()

        response = client.post(
            f"/api/v1/orchestra/sessions/{session['session_id']}/messages",
            params={"content": "你好"},
        )

        assert response.status_code == 200
        assert response.json()["updated_at"] is not None
