"""LangChain tools with invocation-local scope and no tenant arguments."""
import asyncio
from functools import wraps
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from langchain_core.tools import StructuredTool

from ..context import current_context
from ..exceptions import InvalidOperationError, VerificationError
from ..predicates import Eq


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RecordArgs(_Args):
    record_id: str


class _SearchArgs(_Args):
    field: str | None = None
    value: str | None = None


class _PutArgs(_RecordArgs):
    data: Any
    metadata: dict[str, str] | None = None


class _RetrievalArgs(_Args):
    query: str
    limit: int = Field(default=10, ge=1, le=1000)


def _tool(function, name, description, schema):
    @wraps(function)
    async def asynchronous(*args, **kwargs):
        # to_thread propagates the invocation's contextvars, not creation scope.
        return await asyncio.to_thread(function, *args, **kwargs)
    return StructuredTool.from_function(func=function, coroutine=asynchronous,
                                        name=name, description=description, args_schema=schema)


def _prefix(prefix):
    if not isinstance(prefix, str) or not prefix.isidentifier() or len(prefix) > 64:
        raise InvalidOperationError("Tool prefix must be an identifier of at most 64 characters")


def memory_tools(guard, *, prefix="grd", include_writes=False):
    """Host authenticates each invocation; tools never capture a tenant at creation."""
    _prefix(prefix)
    def read(record_id):
        record = guard.get(record_id)
        return None if record is None else record.data

    def search(field=None, value=None):
        if (field is None) != (value is None):
            raise InvalidOperationError("Provide both field and value, or neither")
        predicate = None if field is None else Eq(field, value)
        return [{"record_id": record.record_id, "data": record.data}
                for record in guard.search(predicate)]

    tools = [
        _tool(read, prefix + "_read", "Read a record in the authenticated tenant.", _RecordArgs),
        _tool(search, prefix + "_search", "Search records in the authenticated tenant.", _SearchArgs),
    ]
    if include_writes:
        def put(record_id, data, metadata=None):
            return guard.put(record_id, data, metadata=metadata).data
        def delete(record_id):
            return guard.delete(record_id)
        tools.extend([
            _tool(put, prefix + "_put", "Write a record in the authenticated tenant.", _PutArgs),
            _tool(delete, prefix + "_delete", "Delete a record in the authenticated tenant.", _RecordArgs),
        ])
    return tools


def retrieval_tool(retriever, embed_query, *, name="grd_retrieve"):
    _prefix(name)
    def retrieve(query, limit=10):
        context = current_context()
        vector = embed_query(query)
        if current_context() != context:
            raise VerificationError("Embedding callback changed the active context")
        return [{"record_id": hit.record.record_id, "data": hit.record.data, "score": hit.score}
                for hit in retriever.search(vector, limit=limit)]
    return _tool(retrieve, name, "Retrieve documents within the authenticated tenant.", _RetrievalArgs)
