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
| Market | `get_item_catalog`, `get_market_price`, `get_market_prices`, `search_market`, `get_work_market` |
| Battles | `search_battles`, `get_battle`, `get_battle_ranking` |
| Events | `search_events` |
| Official configuration | `get_game_rules`, `get_skill_progression`, `get_item_details`, `get_game_schedule` |

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
- **Meaningful tools, not raw data.** Each tool answers a cohesive question ("who owns what?") by
  combining a few upstream calls. For questions that span domains, the assistant can combine
  several tools using stable IDs or item codes and retain each result's snapshot time.
- **Bounded.** Results, number of upstream calls, response size and time per request are all capped,
  so one question can't run away.
- **Stateless and lightweight.** No database and no accounts. Only a small in-memory cache of
  *public* data.
- **Game text is treated as untrusted.** Player and company names are cleaned and length-limited
  before the assistant sees them.

## Privacy & security

- The tools read WarEra data and current public operations can be called anonymously.
- Configuration tools always read anonymously and do not accept `player_context`.
- If supplied, `player_context` carries the caller's own request-scoped `api_key` or `jwt`.
- The project has no default or global WarEra credential.
- Submitted credentials are not logged, cached or persisted by the server, but they are sent in the MCP tool request and may be visible in the LLM/client conversation history.
- Nothing you ask is stored.
- Found a security problem? Please report it privately (for example via your hosting platform's
  security-advisory feature) rather than opening a public issue.

## Configuration

Settings are read from `WARERA_MCP_*` environment variables. `.env.example` documents
commonly used settings; the complete list and defaults are in
[`src/warera_mcp/config.py`](src/warera_mcp/config.py). The server does not automatically
load a `.env` file, so pass these variables through your shell, MCP client configuration,
or deployment environment. Command-line options can override the bind host, port and log level.

The upstream defaults to `https://api2.warera.io` and rejects another host unless
`WARERA_MCP_ALLOW_CUSTOM_UPSTREAM=true` is explicitly enabled for local mocks. HTTP
redirects are never followed.

Game rules, skill progression, item details and schedule data are fetched from WarEra's
`gameConfig.getGameConfig` and `gameConfig.getDates` queries. When public caching is
enabled, their cache lifetimes are five minutes and 30 seconds respectively. Set
`WARERA_MCP_CACHE_ENABLED=false` to disable caching; API batching works independently.
Results include the time the server fetched the data and a SHA-256 fingerprint computed
locally from the returned content. The fingerprint is not an official game revision.

These tools expose selected fields from the API responses, including production recipes,
skill levels, upgrade costs and schedule timestamps. They do not provide every game
formula. `get_item_catalog` returns a separate built-in list of item codes; configuration
items and the live market price list can differ from that list. Player skill summaries
are included only when the public profile supplies them.

Company overviews fetch recipes from game configuration when available. Python integrations
can supply a recipe catalog through `ServiceRuntime` to override that lookup; the standard
CLI uses no local recipe overrides. Contract fixtures are used only by tests and are not
production defaults.

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

## API batching

Concurrent upstream reads with identical authentication headers are collected for
up to 300 ms and sent directly to WarEra in a single GET tRPC batch. Identical
reads within that batch share one response; responses are not retained by the
batcher. This works with `WARERA_MCP_CACHE_ENABLED=false`.

Company details and bonuses, battle details and live status, and player profile
enrichment can share batches. Reads that depend on an earlier result still run
in separate stages. Sequential MCP tool invocations cannot share a batch.

Batches send early at 20 pending calls and split before their encoded URL exceeds
8000 bytes. Isolated reads use the original single-query GET format. The existing
response byte limit applies to the entire batch. Queue capacity bounds active
and pending logical reads; cancellation removes unused work without affecting
other callers. Each item retains its own result, error, and retry budget.

Configure batching through:

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `WARERA_MCP_BATCHING_ENABLED` | `true` | Set `false` to use individual requests |
| `WARERA_MCP_BATCH_WINDOW_SECONDS` | `0.3` | Collection delay per stage (0–0.4 seconds) |
| `WARERA_MCP_BATCH_MAX_SIZE` | `20` | Maximum pending calls per batch (1–50) |
| `WARERA_MCP_BATCH_MAX_URL_BYTES` | `8000` | Maximum encoded batch URL size |

An individual query too large for the batch URL budget uses the existing single
GET path. Transport limits and rate limiting apply to physical HTTP requests.
With batching enabled, circuit health is also recorded once per HTTP attempt;
retries check the circuit before sending again. `warera_requests_total` counts
logical reads, while `warera_http_requests_total` counts actual HTTP attempts;
`warera_batches_total` and `warera_batch_size` describe multi-query batches.
Batching reduces HTTP overhead; WarEra may still count each procedure toward its
API quota.
