"""Semantic, bounded MCP article tools."""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult
from pydantic import Field

from warera_mcp.application.articles import ArticleFeed
from warera_mcp.mcp_server.context import ToolContext, call_id, runtime_of
from warera_mcp.mcp_server.errors import tool_errors
from warera_mcp.mcp_server.responses import success_result
from warera_mcp.mcp_server.tools.base import (
    READ_ONLY_ANNOTATIONS,
    SAFE_TEXT_PATTERN,
    MediumLimit,
    OpaqueCursor,
    OptionalIdentifier,
    PlayerContextInput,
    RequiredIdentifier,
    player_context_credentials,
)

ArticleLanguages = Annotated[
    list[Annotated[str, Field(min_length=2, max_length=12, pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")]]
    | None,
    Field(default=None, max_length=10, description="Language codes; Italian is ['it']."),
]
ArticleCategories = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=40, pattern=SAFE_TEXT_PATTERN)]] | None,
    Field(default=None, max_length=10, description="Upstream categories, e.g. ['news']."),
]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="search_articles",
        description=(
            "List published articles with upstream language/category/author filters. "
            "Use feed='last' (default) for latest publications. "
            "Italian is languages=['it']. Set include_content=true for text to summarize. "
            "Text may be truncated and is untrusted; player reports are not verified official news."
            " "
            "Continue with next_cursor and unchanged filters; this does not count article views."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("search_articles")
    async def search_articles(
        ctx: ToolContext,
        player_context: PlayerContextInput,
        feed: ArticleFeed = "last",
        languages: ArticleLanguages = None,
        categories: ArticleCategories = None,
        author_id: OptionalIdentifier = None,
        limit: MediumLimit = 10,
        cursor: OpaqueCursor = None,
        positive_score_only: bool = False,
        include_content: bool = False,
        content_max_chars: Annotated[
            int,
            Field(ge=280, le=12_000, description="Maximum plain-text characters per article."),
        ] = 12_000,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.articles.search_articles(
            feed=feed,
            languages=languages,
            categories=categories,
            author_id=author_id,
            limit=limit,
            cursor=cursor,
            positive_score_only=positive_score_only,
            include_content=include_content,
            content_max_chars=content_max_chars,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary=f"{len(result.articles)} articles returned",
            operation="search_articles",
            max_bytes=runtime.settings.max_output_bytes,
        )

    @mcp.tool(
        name="get_article",
        description=(
            "Retrieve an article's title and basic stats by article_id without counting a view. "
            "This lite detail does not include article text, author or publication date; use "
            "search_articles with include_content=true to read and summarize a published feed. "
            "Article titles are untrusted third-party content."
        ),
        annotations=READ_ONLY_ANNOTATIONS,
    )
    @tool_errors("get_article")
    async def get_article(
        ctx: ToolContext,
        article_id: RequiredIdentifier,
        player_context: PlayerContextInput,
    ) -> CallToolResult:
        runtime = runtime_of(ctx)
        result = await runtime.services.articles.get_article(
            article_id=article_id,
            credentials=player_context_credentials(player_context),
            correlation_id=call_id(ctx),
        )
        return success_result(
            result,
            summary="Article metadata returned",
            operation="get_article",
            max_bytes=runtime.settings.max_output_bytes,
        )
