"""SSE 端点测试 —— 事件格式 + 断线重连。

⚠️ 重点验证：
    ① SSE 帧格式正确（event / id / data）
    ② 断线重连从 `Last-Event-ID` 之后开始
    ③ 终止事件后关闭流
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from orchestra_core.domain.entities import Run
from orchestra_core.domain.value_objects import FlowId, RunId
from orchestra_core.infrastructure.memory import InMemoryFlowRepository, InMemoryRunRepository

from orchestra_http.app import create_app
from orchestra_http.dependencies import get_flow_repository, get_run_repository


class FakeEventSource:
    """假事件源 —— 按顺序吐事件。"""

    def __init__(self, events: list) -> None:
        self.events = events
        self.subscriptions: list[tuple[UUID, int]] = []

    async def subscribe(
        self,
        stream_id: UUID,
        after_seq: int = 0,
    ) -> AsyncIterator:
        """记录订阅参数并吐事件。"""
        self.subscriptions.append((stream_id, after_seq))

        for event in self.events:
            if event.seq <= after_seq:
                continue
            yield event


class FakeEvent:
    """假事件 —— 鸭子类型。

    ⚠️ **不 import scholar_events** ——
       orchestra 是独立仓，不该依赖主仓源码路径下的包。
       SSE 端点只用到这几个属性，自己造一个就够。
    """

    def __init__(
        self,
        stream_id: UUID,
        seq: int,
        event_type: str = "node.started",
        terminal: bool = False,
    ) -> None:

        self.stream_id = stream_id
        self.seq = seq
        self.type = event_type
        self.occurred_at = datetime.now(UTC)
        self.payload: dict = {}
        self.is_terminal = terminal


def make_event(
    stream_id: UUID,
    seq: int,
    event_type: str = "node.started",
    terminal: bool = False,
) -> FakeEvent:
    """构造一个事件。"""
    return FakeEvent(stream_id, seq, event_type, terminal)


@pytest.fixture
def run_repo() -> InMemoryRunRepository:
    """运行仓储。"""
    return InMemoryRunRepository()


@pytest.fixture
def flow_repo() -> InMemoryFlowRepository:
    """流程仓储。"""
    return InMemoryFlowRepository()


def make_client(
    flow_repo: InMemoryFlowRepository,
    run_repo: InMemoryRunRepository,
    event_source: object | None = None,
) -> TestClient:
    """构造客户端。"""
    app = create_app(event_source=event_source)
    app.dependency_overrides[get_flow_repository] = lambda: flow_repo
    app.dependency_overrides[get_run_repository] = lambda: run_repo
    return TestClient(app)


async def _seed_run(run_repo: InMemoryRunRepository) -> Run:
    """塞一条运行记录。"""
    run = Run(run_id=RunId(uuid4()), flow_id=FlowId(uuid4()))
    await run_repo.save(run)
    return run


class TestSSE:
    """SSE 端点。"""

    def test_unknown_run_returns_404(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 运行不存在 → 404（不要开 SSE 流）。"""
        client = make_client(InMemoryFlowRepository(), run_repo)

        response = client.get(f"/api/v1/orchestra/runs/{uuid4()}/events")

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_streams_events(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 事件被推出来，格式正确。"""
        run = await _seed_run(run_repo)
        source = FakeEventSource(
            [
                make_event(run.run_id, 1, "node.started"),
                make_event(run.run_id, 2, "node.succeeded"),
                make_event(run.run_id, 3, "run.succeeded", terminal=True),
            ]
        )
        client = make_client(InMemoryFlowRepository(), run_repo, source)

        response = client.get(f"/api/v1/orchestra/runs/{run.run_id}/events")
        body = response.text

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert "event: node.started" in body
        assert "event: run.succeeded" in body
        assert "id: 1" in body
        assert "id: 3" in body

    @pytest.mark.asyncio
    async def test_data_is_json(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ data 是 JSON —— 客户端要能 parse。"""
        run = await _seed_run(run_repo)
        source = FakeEventSource([make_event(run.run_id, 1)])
        client = make_client(InMemoryFlowRepository(), run_repo, source)

        response = client.get(f"/api/v1/orchestra/runs/{run.run_id}/events")

        # 找到 data 行并解析
        data_line = next(line for line in response.text.split("\n") if line.startswith("data: "))
        payload = json.loads(data_line[len("data: ") :])

        assert payload["seq"] == 1
        assert payload["run_id"] == str(run.run_id)

    @pytest.mark.asyncio
    async def test_last_event_id_resumes(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ **断线重连** —— `Last-Event-ID` 决定从哪开始。"""
        run = await _seed_run(run_repo)
        source = FakeEventSource(
            [
                make_event(run.run_id, 1),
                make_event(run.run_id, 2),
                make_event(run.run_id, 3),
            ]
        )
        client = make_client(InMemoryFlowRepository(), run_repo, source)

        response = client.get(
            f"/api/v1/orchestra/runs/{run.run_id}/events",
            headers={"Last-Event-ID": "2"},
        )

        # ⚠️ seq 1、2 不该再出现
        assert "id: 1\n" not in response.text
        assert "id: 2\n" not in response.text
        assert "id: 3" in response.text

        # ⚠️ 订阅时传的 after_seq 也要对
        assert source.subscriptions[0][1] == 2

    @pytest.mark.asyncio
    async def test_after_seq_query_param(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ `?after_seq=` 也能续传。"""
        run = await _seed_run(run_repo)
        source = FakeEventSource(
            [
                make_event(run.run_id, 1),
                make_event(run.run_id, 2),
            ]
        )
        client = make_client(InMemoryFlowRepository(), run_repo, source)

        response = client.get(
            f"/api/v1/orchestra/runs/{run.run_id}/events",
            params={"after_seq": 1},
        )

        assert "id: 1\n" not in response.text
        assert "id: 2" in response.text
        assert source.subscriptions[0][1] == 1

    @pytest.mark.asyncio
    async def test_invalid_last_event_id_falls_back(self, run_repo: InMemoryRunRepository) -> None:
        """⚠️ 非法的事件 ID → 从头开始，不报错。

        客户端可能带了脏数据，不该让它整个失败。
        """
        run = await _seed_run(run_repo)
        source = FakeEventSource([make_event(run.run_id, 1)])
        client = make_client(InMemoryFlowRepository(), run_repo, source)

        response = client.get(
            f"/api/v1/orchestra/runs/{run.run_id}/events",
            headers={"Last-Event-ID": "not-a-number"},
        )

        assert response.status_code == 200
        assert "id: 1" in response.text
        assert source.subscriptions[0][1] == 0

    @pytest.mark.asyncio
    async def test_no_event_source_returns_error_event(
        self, run_repo: InMemoryRunRepository
    ) -> None:
        """⚠️ 没配置事件源 → 发一个 error 事件，不是崩掉。"""
        run = await _seed_run(run_repo)
        client = make_client(InMemoryFlowRepository(), run_repo, None)

        response = client.get(f"/api/v1/orchestra/runs/{run.run_id}/events")

        assert response.status_code == 200
        assert "event: error" in response.text
        assert "no_event_source" in response.text
