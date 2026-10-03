# WarEra MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

A small, **read-only** [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
look things up in the browser game **WarEra** — players, companies, countries, markets, battles and
events — and answer questions about them in plain language.

> ### ⚠️ Unofficial project
> This is an **unofficial, community-made** project. It is **not affiliated with, endorsed by,
> sponsored by, or supported by** the WarEra developers or publishers in any way. "WarEra" and
> related names belong to their respective owners.
>
> It is a personal open-source hobby project, built in my free time **with the help of AI**.
> It relies on WarEra's public web API, which is **undocumented and may change or break at any time**.
> Use it at your own risk and please be considerate with the game's servers.

## What is this?

MCP (Model Context Protocol) is a standard way to plug tools into AI assistants such as Claude
Desktop. This server gives an assistant a handful of **purpose-built, game-aware tools** instead of
raw web requests, so you can simply ask:

- *"What does player Kiro own, and what are their production bonuses?"*
- *"What is the current price of iron, and how deep is the order book?"*
- *"Which countries is Freedonia at war with, and are there active battles?"*
- *"Who is dealing the most damage in this battle?"*

The server fetches the data, tidies it up, and hands the assistant a compact, size-limited answer. Tools accept optional request-scoped WarEra credentials (`api_key` or `jwt`) in `player_context`; the project never provides a default or global WarEra credential.

## Tools

| Area | Tools |
| --- | --- |
| Players | `get_player`, `get_player_companies` |
| Companies | `get_company_overview` |
| World | `get_country_overview`, `get_country_wars`, `get_region` |
| Market | `get_market_price`, `search_market`, `get_work_market` |
| Battles | `search_battles`, `get_battle`, `get_battle_ranking` |
| Events | `search_events` |

Every tool is marked read-only. There is deliberately **no** generic "call any endpoint" tool.

## Quick start

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone <this-repository-url> warera-mcp
cd warera-mcp
uv sync
uv run warera-mcp            # serves over stdio
```

**Use it from an MCP client** (for example Claude Desktop) by adding it to the client's config:

```json
{
  "mcpServers": {
    "warera": {
      "command": "uv",
      "args": ["--directory", "/path/to/warera-mcp", "run", "warera-mcp"]
    }
  }
}
```

**Run it as a small web service** instead:

```bash
uv run warera-mcp --transport streamable-http --port 8000   # MCP endpoint: http://127.0.0.1:8000/mcp
```

If you expose it beyond your own machine, turn on client authentication and set trusted hosts
(see `.env.example`). The server prints a warning when it is reachable without them.

## How it works (the short version)

- **Read-only by design.** It can only *read* a short, reviewed list of public WarEra queries.
  Nothing in it can change anything in the game.
- **Meaningful tools, not raw data.** Each tool answers a question ("who owns what?") by combining
  a few upstream calls and returning a tidy, normalized result.
- **Bounded.** Results, number of upstream calls, response size and time per request are all capped,
  so one question can't run away.
- **Stateless and lightweight.** No database and no accounts. Only a small in-memory cache of
  *public* data.
- **Game text is treated as untrusted.** Player and company names are cleaned and length-limited
  before the assistant sees them.

## Privacy & security

- The tools read WarEra data and current public operations can be called anonymously.
- If supplied, `player_context` carries the caller's own request-scoped `api_key` or `jwt`.
- The project has no default or global WarEra credential.
- Submitted credentials are not logged, cached or persisted by the server, but they are sent in the MCP tool request and may be visible in the LLM/client conversation history.
- Nothing you ask is stored.
- Found a security problem? Please report it privately (for example via your hosting platform's
  security-advisory feature) rather than opening a public issue.

## Configuration

Settings are read from `WARERA_MCP_*` environment variables. Copy `.env.example` for the full,
commented list. The WarEra host itself is fixed and cannot be redirected.

## Development

```bash
uv sync
uv run pytest          # tests run offline against a stubbed API
uv run ruff check .    # lint
uv run mypy            # strict type checking
```

The test suite never calls the real game API unless you opt in with `WARERA_MCP_LIVE_TESTS=1`.

## Contributing

Issues and pull requests are welcome. Please keep changes small and focused, add or update tests,
and make sure `ruff`, `mypy` and `pytest` pass. The one hard rule: **this project stays read-only** —
changes that add anything able to modify game state will not be accepted.

## Acknowledgements

Built with the help of AI tooling, by a player, for players. Thanks to the WarEra community for
sharing what they've learned about the game.

## License

Released under the [MIT License](LICENSE). Provided "as is", without warranty of any kind.
