# WarEra MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

A small, **read-only** [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
look things up in the browser game **WarEra** — players, companies, countries, markets, battles, military units,
events and articles — and answer questions about them in plain language.

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
- *"Elencami gli ultimi 10 articoli pubblicati."*
- *"Elencami gli articoli in italiano."*
- *"Riassumi le ultime novità di WarEra tramite gli articoli."*

The server fetches the data, tidies it up, and hands the assistant a compact, size-limited answer. Tools accept optional request-scoped WarEra credentials (`api_key` or `jwt`) in `player_context`; the project never provides a default or global WarEra credential.

## Tools

| Area | Tools |
| --- | --- |
| Players | `get_player`, `get_player_companies`, `get_player_resources` |
| Companies | `get_company_overview`, `get_recommended_regions` |
| World | `get_country_overview`, `get_country_wars`, `get_region` |
| Market | `get_item_catalog`, `get_market_price`, `get_market_prices`, `search_market`, `get_work_market` |
| Battles | `search_battles`, `get_battle`, `get_battle_ranking` |
| Military units | `search_military_units`, `get_military_unit`, `get_military_unit_members`, `get_military_unit_investments`, `get_military_unit_ranking`, `get_military_unit_upgrades` |
| Events | `search_events` |
| Articles | `search_articles`, `get_article` |
| Official configuration | `get_game_rules`, `get_skill_progression`, `get_item_details`, `get_game_schedule` |

Every tool is marked read-only. There is deliberately **no** generic "call any endpoint" tool.

### Locations and player resources

`get_recommended_regions(item_code="iron", include_deposit=false)` asks the game for
its ranked company locations excluding deposit bonuses. It exposes bonus components and
taxes as fractions, plus region/country names when available. API key or JWT is required;
API key is always selected when both are supplied. The ranking covers the recommendations
returned by the game (currently five), not every world region. `offset` and `limit` page
that returned list; deposit bonuses are never subtracted using an inferred formula.

`get_player_resources(user_id=..., item_codes=["iron", "steel", "fish"])` reads available
money and basic materials from the player's inventory, market reservations, and owner-order
aggregates including quantities for sale. Use an exact `username` instead of `user_id` when
necessary. Set `include_orders=false` to skip the order read. Keep available materials,
market reservations and sell quantities separate: reservations and orders can describe
the same stock. Expected sales proceeds are not available money. Missing/malformed fields
remain unknown; a valid sparse quantity map treats omitted requested codes as zero.

Inventory and owner orders require JWT. On 2026-10-05, authorized live checks returned
401 anonymously, 403 with API key, and 200 with JWT for both reads. These responses are
never shared-cached. The resource tool exposes only selected numeric fields and the requested
player identity, without equipment, managers or other raw inventory fields.

Public operations always run anonymously, even if `player_context` contains credentials.
Credentials are requested only after a tool reports missing authentication. Authentication
is selected independently for each endpoint; there is no automatic retry with a JWT after
an API key is rejected. The project never loads credentials from another project's `.env`.

### Public player facts

`get_player(user_id=...)` returns the public profile's MU id, military rank,
active flag, creation date, numerical leveling/statistics and reported activity
timestamps, alongside skills and rankings. `fields` can select `profile`,
`location`, `level`, `skills_summary`, `rankings_summary`, `activity` and
`statistics`; omitted fields selects all groups. The profile's `military_unit_id`
links directly to the MU tools without a membership search.

`skills` and `rankings` remain compact numerical maps, compatible with older
scalar payloads. Nested skill summaries use reported `total`, falling back to
`value` only when total is absent; they do not recompute the game's formula.
`skill_details` additionally preserves level, current bar, base value, weapon,
equipment, overflow, limited amount, total, total after soft cap, regeneration
and prestige. Named percentage modifiers are exposed in `modifiers_fraction`
(e.g. `militaryRankPercent:6.25` becomes `militaryRank:0.0625`). Skill totals/base
values keep the game's skill-specific units, including percentage-point values
where applicable. They must not be combined blindly with modifier fractions.
Future numeric skill components are kept in `additional_numeric_components`.
`ranking_details` preserves reported value, rank and tier independently.

`leveling`, `stats` and activity map keys retain upstream field codes. Missing
facts are omitted, not replaced with zero. Map outputs are capped at 64 entries,
activity date lists at 20, and all results retain the existing byte budget.
Activity timestamps are public observations, not a guarantee of future activity.
No private inventory read or automatic company/MU/profile fan-out is triggered;
the LLM chooses which linked tools and field groups its analysis requires.

### Military units

`search_military_units(search="Husaru", member_id=..., owner_id=...)` uses the
[official MU API](https://api2.warera.io/docs/) to search by text, membership or
ownership. All filters are optional; `owner_id` maps to upstream `userId`.
The default limit is 10 (maximum 20). Continue with `page.next_cursor` and the
same filters. The API has no documented country filter, so country-wide coverage
must not be inferred from a single page.

`get_military_unit(military_unit_id=...)` returns a compact dossier: identity,
owner, location, member/role counts, level, monthly damage, mercenary reputation,
active upgrade levels, manager/commander ids (up to 20 per role), last-announcement
time and the six reported ranking snapshots. `roles_truncated` flags capped role
lists; use the paginated roster with `include_non_members=true` for full coverage. Missing values
remain unknown. Names are untrusted player content. Wealth is a ranking value,
not a verified inventory balance. Full member lists, avatar URLs and per-user investment maps are excluded from the
dossier; roster and investment tools expose the analytical data in bounded pages.

`get_military_unit_members(military_unit_id=..., offset=0, limit=10)` pages the
public member roster with owner, manager and commander flags. Missing role data
remains unknown. Set `include_non_members=true` to include owner, managers and
commanders outside the roster. `is_member` distinguishes membership from a
management role, and `total_count` covers the selected set of identities.
Use `get_player(user_id=...)` for individual profiles.

`get_military_unit_investments(military_unit_id=..., offset=0, limit=10)` pages
`investedMoneyByUsers`, including former members when reported. Amounts are
reported monetary investments, not treasury or upgrade resource balances. A
missing map returns `available=false` with unknown amounts/count; an explicitly
empty map returns `available=true` and zero entries. Pagination does not infer
investment history or fetch transaction records.
`get_military_unit_upgrades` reads headquarters and dormitories separately,
including disabled/pending status, investments and activation timestamps when
available. A failed upgrade read preserves the other result with `partial=true`.
The dossier's active levels do not include disabled upgrades.

`get_military_unit_ranking(ranking_type="muWeeklyDamages", offset=0, limit=10)`
also supports `muDamages`, `muTerrain`, `muWealth`, `muBounty` and `muReputation`.
Offsets page only the snapshot returned by WarEra; `total_count` is that snapshot's
size. Rosters and rankings may change between calls. Name lookups are limited to
five units and the configured fan-out budget. For a battle's MU contributions,
use `get_battle_ranking(battle_id=..., entity_type="mu")`, which now resolves
MU names with the same bounded, best-effort policy. The ranking tool now accepts
exactly one of `battle_id`, `war_id` or `round_id`, plus `metric="damage"`,
`"points"` or `"money"`. It forwards `limit` and `cursor`, returns `page.next_cursor`
and preserves the reported `item_count`. A legacy oversized response is capped
locally with a warning. A positive count with no rows is marked `partial=true`,
so it cannot be treated as zero participation. Battle dossiers/search results
include `war_id`, current round ids and the MU/country ids with orders on each
side. Round summaries/live snapshots preserve country ids, damage, points, hit
counts, tick counters/points and timestamps from the current nested API shape,
while accepting the older flat fields. Equipment and last-hit payloads remain
outside those compact summaries.

These reads were verified anonymously on 2026-10-05 against OpenAPI 0.17.4-beta
([machine-readable specification](https://api2.warera.io/openapi.json)). They use
GET, public TTL caching and the existing output-byte limit. MU management actions
remain outside this read-only project. `transaction.getPaginatedTransactions`
accepts `muId`, but returned 401 anonymously; authenticated transaction payloads
were not verified and are not exposed by this integration.

### Articles

`search_articles` defaults to the latest published feed (`feed="last"`, `limit=10`,
maximum 20). It forwards language codes (`languages=["it"]` for Italian), categories
(e.g. `["news"]`), `author_id` and `positive_score_only` to WarEra. Other public feeds
are `daily`, `weekly` and `top`; use `last` for chronological news. Pass `page.next_cursor`
with the same filters to continue. Personal `my`/`subscriptions` feeds are not exposed.

Metadata-only results are compact. Set `include_content=true` for summaries: the feed
already includes article bodies, which the server converts from HTML into untrusted
plain text without fetching images or links. Each body is limited to 12,000 characters
(lower it with `content_max_chars`, minimum 280); `content_truncated` and warnings
identify incomplete text. The existing output-byte budget still applies: request fewer
articles or shorter content if necessary. The LLM writes the summary, cites titles/IDs,
authors and publication dates, and distinguishes player claims from official announcements.

`get_article(article_id=...)` returns only title and basic statistics via the lite
endpoint. It does not provide body text. The full-detail `article.getArticleById` endpoint
is deliberately excluded because it can count a view; summaries use the paginated feed.
Both exposed queries were checked anonymously against the
[official API documentation](https://api2.warera.io/docs/).

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

- Public operations run anonymously and never forward supplied credentials.
- Region recommendations prefer API key; inventory and owner orders require JWT.
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

Company overviews fetch recipes from game configuration when available. Raw materials expose
production points per unit through `get_item_details`, have no ingredient recipe, and do not trigger
missing-recipe warnings. Manufactured products still require validated recipe inputs.
Python integrations can supply a recipe catalog through `ServiceRuntime` to override that lookup;
the standard CLI uses no local recipe overrides. Contract fixtures are used only by tests and are not
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
