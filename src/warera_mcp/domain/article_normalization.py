"""Bounded projection of article metadata and HTML into untrusted plain text."""

from __future__ import annotations

from html.parser import HTMLParser

from warera_mcp.domain.models import Article, ArticleStats
from warera_mcp.warera.errors import WareraSchemaError
from warera_mcp.warera.schemas import clean_name, record_of

MAX_ARTICLE_CONTENT = 12_000
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "pre",
        "li",
        "ul",
        "ol",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "td",
        "th",
    }
)


class _ArticleTextParser(HTMLParser):
    """Keep readable text, skipping executable/style payloads and media URLs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def article_text(value: object, *, limit: int) -> tuple[str | None, bool]:
    if not isinstance(value, str):
        return None, False
    parser = _ArticleTextParser()
    parser.feed(value)
    parser.close()
    text = clean_name("".join(parser.parts), max_length=len(value) + 1)
    truncated = text is not None and len(text) > limit
    if truncated and text is not None:
        text = text[: limit - 1].rstrip() + "\u2026"
    return text, truncated


def normalize_article(
    payload: object,
    *,
    include_content: bool = False,
    content_limit: int = MAX_ARTICLE_CONTENT,
) -> Article:
    record = record_of(payload)
    ident = record.ident("_id")
    if ident is None:
        raise WareraSchemaError("article response is missing a valid identifier")
    content, truncated = (
        article_text(record.raw("content"), limit=content_limit)
        if include_content
        else (None, False)
    )
    title, _ = article_text(record.raw("title"), limit=300)
    stats = record.child("stats")
    return Article(
        id=ident,
        title=title,
        author_id=record.ident("author"),
        language=record.opt_str("language"),
        category=record.opt_str("category"),
        published_at=record.timestamp("publishedAt"),
        stats=ArticleStats(
            **{
                key: stats.integer(key)
                for key in ("likes", "dislikes", "score", "views", "comments")
            }
        )
        if stats is not None
        else None,
        content=content,
        content_truncated=truncated,
    )
