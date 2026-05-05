import json
from typing import Any

from crewai_tools import MongoDBVectorSearchTool


class TrackedMongoDBVectorSearchTool(MongoDBVectorSearchTool):
    """MongoDBVectorSearchTool that accumulates query-to-chunks mappings to enable evals."""

    _query_chunks: list[dict[str, list[dict[str, Any]]]]

    def __init__(
        self, query_chunks: list[dict[str, list[dict[str, Any]]]], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._query_chunks = query_chunks

    def _run(self, query: str) -> str:
        raw_result = super()._run(query)
        if raw_result:
            try:
                docs = json.loads(raw_result)
                if isinstance(docs, list):
                    self._query_chunks.append({query: docs})
            except (json.JSONDecodeError, TypeError):
                pass
        return raw_result
