from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from app.db.astra_client import AstraDBCollection  # noqa: F401

from app.models.video import VideoSummary

import logging

logger = logging.getLogger(__name__)


def _collect_docs_from_cursor(cursor):
    """Return a list of docs from an astrapy cursor or a stub list in unit-tests."""

    if asyncio.iscoroutine(cursor):
        return cursor  # caller should await upstream

    if hasattr(cursor, "to_list"):
        return cursor.to_list()

    # In unit tests we sometimes pass a list instead of a real cursor
    return cursor


async def semantic_search_with_threshold(
    *,
    db_table: AstraDBCollection,
    vector_column: str,
    query: str,
    page: int,
    page_size: int,
    similarity_threshold: float = 0.0,
    overfetch_factor: int = 3,
) -> Tuple[List[VideoSummary], int]:
    """Run a vector search and apply a client-side similarity cutoff.

    Parameters
    ----------
    db_table : AstraDBCollection
        Table / collection to query (must contain the *vector_column*).
    vector_column : str
        Name of the vector column to sort on, e.g. ``"content_features"``.
    query : str
        The natural-language query that will be embedded on-the-fly by Astra.
    page / page_size : int
        Standard pagination parameters expected by the public API.
    similarity_threshold : float, optional
        Keep only rows whose ``$similarity`` ≥ this value. Default 0 (no trim).
    overfetch_factor : int, optional
        How many extra rows to ask Astra for. 3× the *page_size* works well
        for typical thresholds around 0.7-0.9.
    """

    from opentelemetry import trace
    import time
    from app.metrics import VECTOR_SEARCH_DURATION_SECONDS

    tracer = trace.get_tracer(__name__)

    if page < 1 or page_size < 1:
        return [], 0

    # Ask Astra for a generous slice so we can trim client-side.
    overfetch = page_size * overfetch_factor * page  # grow with page number

    start_time = time.perf_counter()

    with tracer.start_as_current_span("vector.search") as span:
        span.set_attribute("query", query[:64])  # truncate long queries for span

        cursor = db_table.find(
            filter={},
            sort={vector_column: query},
            limit=overfetch,
            include_similarity=True,  # ⭐
        )

    # Fetch docs.
    docs: List[Dict[str, Any]]
    if hasattr(cursor, "to_list"):
        docs = await cursor.to_list()
    else:
        docs = cursor  # type: ignore[assignment]

    logger.debug(
        "Vector search fetched %s docs (page=%s, overfetch=%s)",
        len(docs),
        page,
        overfetch,
    )

    if similarity_threshold > 0:
        pre_trim = len(docs)
        docs = [d for d in docs if d.get("$similarity", 0) >= similarity_threshold]
        logger.debug(
            "Trimmed by threshold %.2f: %s → %s docs",
            similarity_threshold,
            pre_trim,
            len(docs),
        )

        if docs:
            logger.debug(
                "Top doc similarity after trim: %.3f", docs[0].get("$similarity", -1.0)
            )

        # Record metrics
        duration = time.perf_counter() - start_time
        VECTOR_SEARCH_DURATION_SECONDS.observe(duration)
        span.set_attribute("duration_ms", int(duration * 1000))
        span.set_attribute("total_results", len(docs))

    total = len(docs)

    # Slice to requested page.
    start = (page - 1) * page_size
    end = start + page_size
    page_docs = docs[start:end]

    summaries = [VideoSummary.model_validate(d) for d in page_docs]

    return summaries, total
