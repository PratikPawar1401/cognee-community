"""Real cognee storage/graph cleanup; only LLM and embedding calls are mocked."""

import asyncio
import importlib

import cognee
import pytest
from cognee.infrastructure.databases.graph import get_graph_engine
from cognee.infrastructure.databases.vector.embeddings.LiteLLMEmbeddingEngine import (
    LiteLLMEmbeddingEngine,
)
from cognee.infrastructure.llm import LLMGateway
from conftest import FakeAPI, conversation

from cognee_community_connector_otter import otter_source
from cognee_community_connector_otter.client import OtterHTTPError

ALPHA = "OtterAlphacorp"
BETA = "OtterBetacorp"


async def structured_output(text_input=None, system_prompt=None, response_model=str, **_kwargs):
    from cognee.shared.data_models import KnowledgeGraph, SummarizedContent
    from cognee.shared.data_models import Node as KGNode

    if response_model is str:
        return "Test answer"
    if response_model == SummarizedContent:
        return SummarizedContent(summary="Test summary", description="Test summary")
    if response_model == KnowledgeGraph:
        nodes = [
            KGNode(id=name, name=name, type="Company", description=f"{name} company")
            for name in (ALPHA, BETA)
            if name in (text_input or "")
        ]
        return KnowledgeGraph(nodes=nodes, edges=[])
    return response_model()


def test_graph_cleanup_including_final_document_and_failure_preservation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cognee.config.data_root_directory(str(tmp_path / "data"))
    cognee.config.system_root_directory(str(tmp_path / "system"))
    cognee.config.set_relational_db_config({"db_provider": "sqlite"})
    storage = importlib.import_module("cognee.tasks.storage.add_data_points")

    async def no_index(*_args, **_kwargs):
        return None

    async def embedding(self, text):
        return [[0.0] * self.get_vector_size() for _ in text]

    graph_extractions = []

    async def counted_output(text_input=None, system_prompt=None, response_model=str, **kwargs):
        if getattr(response_model, "__name__", "") == "KnowledgeGraph":
            graph_extractions.append(text_input)
        return await structured_output(text_input, system_prompt, response_model, **kwargs)

    monkeypatch.setattr(storage, "index_data_points", no_index)
    monkeypatch.setattr(storage, "index_graph_edges", no_index)
    monkeypatch.setattr(LLMGateway, "acreate_structured_output", counted_output)
    monkeypatch.setattr(LiteLLMEmbeddingEngine, "embed_text", embedding)
    api = FakeAPI([conversation("a"), conversation("b")])
    for item, name in zip(api.records, (ALPHA, BETA), strict=True):
        item["relationships"]["transcript"]["content"] = f"Robin 00:00\n{name} is a company."

    async def sync():
        await cognee.add(
            otter_source(client=api),
            dataset_name="otter-test",
            primary_key="id",
            write_disposition="replace",
            max_rows_per_table=0,
        )

    async def has_node(token):
        nodes, _ = await (await get_graph_engine()).get_graph_data()
        return any(token in str(props) for _, props in nodes)

    async def scenario():
        await cognee.prune.prune_data()
        await cognee.prune.prune_system(metadata=True)
        try:
            await sync()
            await cognee.cognify(datasets=["otter-test"])
            assert await has_node(ALPHA)
            assert await has_node(BETA)
            initial_extractions = len(graph_extractions)
            assert initial_extractions > 0
            await sync()
            await cognee.cognify(datasets=["otter-test"])
            assert len(graph_extractions) == initial_extractions
            api.errors["b"] = OtterHTTPError(403)
            with pytest.raises(Exception, match="403"):
                await sync()
            assert await has_node(ALPHA)
            assert await has_node(BETA)
            api.errors.clear()
            api.records.pop()
            await sync()
            assert await has_node(ALPHA)
            assert not await has_node(BETA)
            api.records.clear()
            await sync()
            assert not await has_node(ALPHA)
            assert not await has_node(BETA)
        finally:
            await cognee.prune.prune_data()
            await cognee.prune.prune_system(metadata=True)

    asyncio.run(scenario())
