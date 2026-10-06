# Changelog

## Unreleased

- Expose concrete construction units and the verified company-cost formula,
  including the sixth-company case, and instruct clients to ask for an in-game
  username when personalized lookups require one.
- Include the complete projected facts as JSON text alongside `structuredContent` in
  successful tool results, so clients that consume only `content` receive values,
  recipes, warnings and pagination rather than just a summary heading.

## 1.0.0 — 2026-10-05

First public release, shared with the WarEra community. Project status: beta.

- 44 purpose-built, read-only MCP tools for players, companies, markets, world data,
  battles, military units, rankings, work, transactions, events and articles.
- Official game configuration, verified item recipes, skill progression and schedules.
- Local stdio and remote Streamable HTTP transports.
- Structured snapshots with stable identifiers, observation times, pagination,
  warnings and explicit partial results.
- Anonymous public reads and request-scoped WarEra credentials for selected tools.
- Bounded requests and responses, public-data caching, rate limiting and optional
  MCP client authentication.

This is an unofficial community project. It does not modify game state.
