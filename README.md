# WarEra MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Release: v1.0.0](https://img.shields.io/badge/release-v1.0.0-blue.svg)
![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)

A **read-only** [MCP](https://modelcontextprotocol.io) server that lets an AI assistant
look things up in the browser game **WarEra** — players, companies, countries, markets,
battles, military units, equipment, governments, rankings, workers, transactions, events and articles — and answer questions about them in plain language.

> ### ⚠️ Unofficial project
> This is an **unofficial, community-made** project. It is **not affiliated with, endorsed by,
> sponsored by, or supported by** the WarEra developers or publishers in any way. "WarEra" and
> related names belong to their respective owners.
>
> It is a personal open-source hobby project, built in my free time **with the help of AI**.
> It uses WarEra's web API and its [official documentation](https://api2.warera.io/docs/).
> API behavior and response shapes can change; documented requests are checked against live
> responses where possible. Please be considerate with the game's servers.

## What is this?

MCP (Model Context Protocol) is a standard way to plug tools into AI assistants such as Claude
Desktop. This server gives an assistant **purpose-built, game-aware tools** instead of
raw web requests, so you can simply ask:

- *"What does player Kiro own, and what are their production bonuses?"*
- *"What is the current price of iron, and how deep is the order book?"*
- *"Which countries is Freedonia at war with, and are there active battles?"*
- *"Who is dealing the most damage in this battle?"*
- *"Elencami gli ultimi 10 articoli pubblicati."*
- *"Elencami gli articoli in italiano."*
- *"Riassumi le ultime novità di WarEra tramite gli articoli."*

The server fetches data and returns structured facts with stable identifiers, observation times
and explicit gaps. The assistant can combine those facts for analysis, calculations and simulations.
Successful tools return these facts in both `structuredContent` and a JSON text block in
`content`, after a short summary, so clients that consume only text receive the same data.
The output-byte limit applies to the projected facts before this compatibility copy is added.
For company construction, `get_game_rules(topic="companies")` includes the concrete
cost formula: configured increment × (owned companies + 1). With the current increment
of 50, the sixth company costs 300 concrete. This formula was verified against the
[official companies page](https://app.warera.io/companies) on 2026-10-06; the increment
comes from the fetched configuration. An explicit company number needs no player lookup.
For personalized questions such as production time for "my next company", the server
instructs the assistant to ask for the in-game username when no player identity is known.
Selected tools accept optional request-scoped WarEra credentials (`api_key` or `jwt`) in
`player_context`; the project never provides a default or global WarEra credential.

## Quick start

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/LorissDaniel/warera-mcp.git
cd warera-mcp
uv sync --locked
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

This command binds to loopback by default. For remote use, configure client authentication,
trusted hosts and HTTPS through your deployment or reverse proxy. The server warns about
missing authentication/trusted hosts when configured to bind outside loopback; it cannot
detect every externally reachable proxy configuration. See [.env.example](.env.example) for
the environment variable names. MCP client bearer tokens are separate from WarEra credentials.

If a desktop client cannot find `uv`, use its absolute executable path in `command`.
After updates, restart or reconnect the MCP server so the client refreshes its tool schemas.

## Connect to the hosted MCP

Use this HTTPS MCP endpoint in Claude or ChatGPT:

```text
https://warera-mcp-agkzl5m3va-ew.a.run.app/mcp
```

Remote connections use Streamable HTTP and require no account or token.

### Claude

1. Open **Customize → Connectors → + Add → Add custom connector**.
2. Enter **WarEra** as the name and the HTTPS endpoint above as the server URL.
3. Continue to the authentication settings and choose **No sign in**.
   Leave OAuth settings and request headers empty.
4. Click **Add**, then enable WarEra from **+ → Connectors** in a conversation.
5. Try: *"Using WarEra, what is the current market price of iron?"*

The hosted endpoint requires no MCP client token. WarEra player API keys and
JWTs are separate, optional credentials for selected tools; public tools need none.
For Team or Enterprise, an organization owner or authorized administrator must
add the connector before members can enable it.
See [Claude's remote connector guide](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

### ChatGPT

1. Use ChatGPT on the web. If required, enable **Developer mode** under
   **Settings → Security and login**. Workspace permissions may restrict access.
2. Open **Plugins**, select **+ → Create custom MCP server**, and enter **WarEra**
   with the HTTPS `/mcp` URL above.
3. Select **No authentication**. Leave OAuth client ID and secret empty.
4. Review the connection warning, create the plugin, and install it.
5. Enable WarEra in a conversation and ask a game-data question.

See [OpenAI's custom MCP setup guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).


## Tools

| Area | Tools |
| --- | --- |
| Players | `get_player`, `get_player_companies`, `get_player_resources`, `get_player_equipment` |
| Companies | `get_company_overview`, `get_recommended_regions`, `get_company_upgrades` |
| Discovery | `get_country_players`, `search_entities` |
| World | `get_country_overview`, `get_country_wars`, `get_region`, `get_region_upgrades`, `get_country_government` |
| Market | `get_item_catalog`, `get_market_price`, `get_market_prices`, `search_market`, `get_work_market` |
| Battles | `search_battles`, `get_battle`, `get_battle_ranking`, `get_round`, `get_round_hits`, `get_battle_orders`, `get_battle_loot` |
| Mercenaries | `search_mercenary_auctions` |
| Rankings | `get_global_ranking` |
| Labor | `get_work_offer`, `get_workers` |
| Transactions | `search_transactions` |
| Military units | `search_military_units`, `get_military_unit`, `get_military_unit_members`, `get_military_unit_investments`, `get_military_unit_ranking`, `get_military_unit_upgrades` |
| Events | `search_events` |
| Articles | `search_articles`, `get_article` |
| Official configuration | `get_game_rules`, `get_skill_progression`, `get_item_details`, `get_game_schedule` |

All 44 tools are marked read-only. There is deliberately **no** generic "call any endpoint" tool.

### Locations and player resources

`get_recommended_regions(item_code="iron", include_deposit=false)` asks the game for
its ranked company locations excluding deposit bonuses. It exposes bonus components and
taxes as fractions, plus region/country names when available. API key is required;
JWT is never used for this operation, even when supplied. The ranking covers the recommendations
returned by the game (five in the verified responses), not every world region. `offset` and `limit` page
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

### Multiple material prices

For price comparisons, use one call such as
`get_market_prices(item_codes=["iron", "steel", "fish"])` instead of one
`get_market_price` call per material. Omit `item_codes` to retain the complete
catalog behavior. Lists accept 1–64 codes; duplicates are removed. Filtering is
local over one shared, cached `itemTrading.getPrices` snapshot; it does not issue
one upstream request per material. Prices remain sorted by descending value.

Omit `limit` to return every selected quote. If supplied, it applies after
filtering and sets `truncated=true` when prices are omitted. Missing/unquoted
codes are reported in `missing_item_codes`, while available prices are retained;
`partial=true` indicates missing quotes or truncation. Missing prices must not be
used as zero in calculations. These are global quoted prices; for visible
bids, asks and quantities, use `search_market` for each relevant material. Visible
orders are snapshots and do not guarantee execution or sufficient liquidity.

### Filtered pages and visible market depth

`get_work_market(item_code="iron", region_id=..., citizenship=..., level=18,
energy=10, production=10)` sends the documented offer filters upstream before
pagination. `user_id` filters by the offer's user ID. Zero numeric values are sent
explicitly; omitted values are not inferred from a player profile. The tool does
not independently certify eligibility. **`item_code` selects the wage benchmark,
not the product of the offers.** Wages retain API units; no hourly rate is inferred.

`limit` (1–10) controls the upstream offer page size. Continue with
`page.next_cursor` and the same filters. `minimum_net_wage` remains a local filter
of the fetched page, using gross wage when net wage is absent. A reported net wage
of zero remains zero. An empty filtered page can still have later matches when
`page.has_more=true`. Offer IDs, user/company/region IDs, remaining and initial
quantities, and timestamps support follow-up queries. If offers cannot be read,
`partial=true` and the absent `page` indicate unavailable coverage.

`search_battles` applies `country_id`, `war_id`, `defender_region_id` and
`is_active` upstream. A defender region is the attacked region. The optional
`direction` is `forward` or `backward`; these API pagination directions do not
promise chronological ordering. `search_events` applies `country_id` and
`event_types` upstream. Its parameter schema lists the documented event codes;
input casing is normalized and unsupported codes are rejected before a request.
Output event types are lowercase, with `unknown:` for unrecognized types.
If the API exceeds the requested page size, bounded output carries `partial=true`
and a warning about omitted rows; the cursor may not recover those rows.
For both tools, follow `page.next_cursor` with identical filters and direction
where applicable; a page is not complete historical coverage.

`search_market(item_code="iron", max_orders=25)` forwards `max_orders` as the API's
`limit`, supporting 1–100 visible orders per side. Existing `buy` orders are bids;
`sell` orders are asks. To estimate purchases choose `side="sell"`; to estimate
sale proceeds choose `side="buy"`. With `depth_quantity`, compare returned
`depth.quantity` against the target: the reported VWAP may cover only a partial
fill. The book remains a bounded snapshot, not guaranteed executable liquidity.

`get_player_companies` requests up to 100 company IDs per upstream page using the
verified `perPage` parameter, then retrieves bounded detail batches using `limit`
and the configured fan-out cap. To consume the current page, follow `next_offset`
with the **same** `cursor`. Once `next_offset` is absent, follow `page.next_cursor`
with `offset=0`. `total_count` is returned only when the first upstream page
contains the entire owned list; a final continuation page's size is not a total.
Ownership lists can change between reads; preserve observation times.

### Public player facts

`get_player(user_id=...)` returns the public profile's MU id, military rank,
active flag, creation date, numerical leveling/statistics and reported activity
timestamps, alongside skills and rankings. `fields` can select `profile`,
`location`, `level`, `skills_summary`, `rankings_summary`, `activity` and
`statistics`, `missions` and `equipment`; omitting `fields` selects all groups. The profile's `military_unit_id`
links directly to the MU tools without a membership search.

By default, `get_player` enriches `user.getUserLite` with reviewed game facts from
`user.getUserById`: region and location IDs, company and party references, profile
update time, reported MU maximum level rewarded, equipped-slot item IDs, mission
statistics/claim timestamps and completed-tour flags. The `company_id` reference
does not establish ownership; use `get_player_companies` for owned companies.
`equipment_ids` identifies equipped slots, not inventory quantities or full item
attributes. Missing fields remain unknown; account metadata and UI preferences
are not exposed. `include_full_profile=false` skips enrichment. Focused reads
containing only level, skills or rankings also use lite only. `profile_source`
reports `lite` or `full`; failed or mismatched full-profile reads retain lite facts
with `partial=true` and a warning. Additional maps are bounded to 64 entries.


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

`wealth_breakdown` preserves public `stats.wealth` components: `money`, `items`,
`companies`, `equipments`, `weapons` and `total`, plus additional numeric components
when reported. It belongs to the `statistics` field group and is capped at 64 entries.
Values and the reported total are retained independently; the server does not recompute
or reconcile them with the wealth ranking, which may reflect another snapshot.
The `money` component is not certified as spendable inventory money, and `items`
is an aggregate wealth component, not per-material quantities. Use `get_player_resources`
for available money and materials; that inventory read continues to require JWT.
Missing components remain unknown; reported zeros remain zero.
On 2026-10-05, closely spaced live MCP reads with its public cache disabled found
`stats.wealth.money` different from JWT `inventory.money`, while `market.lockedMoney`
was zero and the public value stayed unchanged before/after the inventory read.
This verifies that the two snapshots are not interchangeable; it does not establish
the statistic's formula or refresh interval. The server does not adjust the public
value into an inferred available balance. Direct requests with `no-cache` headers
and a unique URL still returned the same public number with Cloudflare reporting
`DYNAMIC`. This does not rule out game-side caching or stored statistics. The profile's
`updated_at` is not a wealth calculation timestamp; no age or meaning beyond the
reported component is inferred.

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
lists; use the paginated roster with `include_non_members=true` for full coverage of
the identities returned by the API. Missing values remain unknown. Names are untrusted
player content. Wealth is a ranking value, not a verified inventory balance. Full member
lists, avatar URLs and per-user investment maps are excluded from the dossier; roster
and investment tools expose the analytical data in bounded pages.

`get_military_unit_members(military_unit_id=..., offset=0, limit=10)` pages the
public member roster with owner, manager and commander flags. Missing role data
remains unknown. Set `include_non_members=true` to include owner, managers and
commanders outside the roster. `is_member` distinguishes membership from a
management role, and `total_count` covers the selected set of identities.
Role flags reflect the API's `roles.managers` and `roles.commanders` lists. A listed
responsible user may belong to another MU, and a role reference may not match what
the game UI currently displays; the server does not independently verify effective
permissions. Use `get_player(user_id=...)` for individual profiles.

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
remain outside this read-only project. For MU transaction history use
`search_transactions(military_unit_id=...)` with a request-scoped API key.

### Public identifier discovery

`get_country_players(country_id=..., limit=10)` returns player IDs and account
creation timestamps, with upstream cursor pagination. Follow `page.next_cursor`
with the same country. Account creation is not a country joining date or last
activity; one page is not total or active population. Use `get_player` for details.

`search_entities(search="Loris")` returns candidate IDs grouped by user, MU,
country, region, party and alliance. Offset/limit page the API's retained matches
locally per group; there is no upstream cursor or guarantee of complete search
coverage. Missing groups remain unknown; explicit empty lists are known empty.
It does not certify an exact identity. For an exact username use `get_player`.

### Equipment, upgrades and government

`get_player_equipment(user_id=...)` or `get_player_equipment(username="Loris")`
returns publicly equipped item attributes, condition, skill bonuses and acquisition dates.
Explicit null slots appear in `empty_slots`; omitted slots are unknown. This is the
current loadout, not inventory or tradable stock. Slot maps are capped at 20 and skill
maps at 64 entries.

`get_company_upgrades` covers `storage`, `automatedEngine` and `breakRoom`;
`get_region_upgrades` covers `bunker`, `base` and `pacificationCenter`. Omit
`upgrade_types` for all three, or select a subset. Reported status/level,
money/concrete/steel investments and activation/change dates remain separate.
`absent_upgrade_types` means a successful null response; it does not infer a zero level.
`unavailable_upgrade_types` identifies failed or invalid reads and sets `partial=true`.
Use company/region tools for context and game configuration for supported upgrade costs/effects.

`get_country_government(country_id=..., offset=0, limit=10)` returns president/minister
user IDs, a locally paginated congress roster and reported activity dates. `congress_member_count`
covers the returned roster; a missing roster is unknown. Follow the user IDs with
`get_player`. These references describe reported roles, not independently verified permissions.

### Rounds, orders, loot and mercenary contracts

`get_round(round_id=...)` retrieves a specific round, including battle/country links,
damage, points, hits, ticks and timestamps. `get_round_hits` pages the recent hit arrays
with separate `snapshot_counts` and `has_more` per side. Its offset/limit are local,
not historical API pagination. `include_equipment=true` adds weapon, ammo and up to
10 equipment items as recorded at the hit. A reported missed hit can still have
reported damage; neither is overwritten using an inferred formula.

`get_battle_orders(battle_id=..., side="attacker")` returns a locally paginated
order snapshot with country/MU/user links, priority and activity. The anonymous view
may hide text and rank. Empty text with rank zero is exposed as
`text_and_rank_visibility="restricted_in_anonymous_view"`, with text/rank unknown.
It must not be interpreted as a public zero rank or permission to inspect private orders.

`get_battle_loot(battle_id=..., user_id=...)` preserves reported case counts,
hits, total damage, bounty/contract money and pool-loot numeric facts/references.
A missing summary produces an error, not zero earnings. Pool loot is capped at
20 entries; truncation sets `partial=true`. These are reported rewards, not a current balance.

`search_mercenary_auctions(country_id=..., battle_id=..., status="won")` applies
filters upstream. The schema lists supported status values; omitted status uses the
API default and does not promise all historical statuses. Follow `page.next_cursor`
with the same filters. Budget, minimum damage, current rates/payouts, winner IDs,
battle/round links and expiry remain distinct. `include_bids=true` adds up to
`bid_limit` bids per auction (maximum 20); `bid_count` covers the record and
`bids_truncated` signals omissions. Duration/rate fields retain API units; no
undocumented conversion or guaranteed final payout is inferred.

### Global rankings

`get_global_ranking(ranking_type="userDamages", offset=0, limit=10)` supports all
36 documented user, country, alliance and MU ranking types. It preserves entity IDs,
reported rank/value/tier, tier thresholds and country/MU links when supplied.
There is no automatic profile fan-out; use linked tools as needed. Offsets page the
snapshot returned by the API, not the entire world population; `snapshot_count`
reports that snapshot's size. No historical week selector is documented. For MU name
expansion use `get_military_unit_ranking`. Wealth rankings are not spendable inventory.

### Work details and API-key transactions

`get_work_offer(work_offer_id=...)` or `get_work_offer(company_id=...)` reads one
public offer; supply exactly one identifier. It includes wage/net wage, offer quantities,
minimum energy/production/level when supplied, linked entities and timestamps.
Use `get_work_market` for filtered offer discovery.

`get_workers` and `search_transactions` require `player_context={"api_key":"..."}`.
They were verified with API key on 2026-10-05; neither requires JWT and neither uses
shared caching. Credentials are never returned in the result.

`get_workers(company_id=...)` lists company workers; `get_workers(user_id=...)`
lists workers grouped across that user's employer company portfolio, not the user's
own employment. Supply exactly one scope. Wage, fidelity, joining/locking dates and
worker IDs support profile/company joins. Offset/limit page the returned snapshot
locally. `snapshot_count` and the separately reported `reported_total_workers_count`
are distinct observations; missing or failed counts remain unknown. Empty worker
arrays are known empty lists. Company summaries are capped at 20, with explicit truncation.

`search_transactions(user_id=..., military_unit_id=..., transaction_types=["donation"])
also supports country, party and item-code filters. All filters apply upstream; omission
leaves the API scope unfiltered. Follow `page.next_cursor` with identical filters.
Transactions preserve reported money/quantity, timestamps, item attributes and separate
buyer/seller user, MU, country and party IDs. Keep those API labels: for a donation,
“buyer” is not automatically a goods buyer. Reported money is neither a unit price nor
a signed cash flow; establish the transaction semantics before calculating totals.
A page alone does not establish complete income, expenditure or historical coverage.

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

## How it works (the short version)

- **Read-only by design.** The client calls only explicitly reviewed read operations.
  Most are public; selected reads require request-scoped authentication. State-changing
  operations and the potentially view-counting full article endpoint are excluded.
- **Structured, linkable facts.** Tools project upstream data into documented fields;
  some combine related reads. The assistant chooses further tools using stable IDs or
  item codes and retains snapshot times, units and coverage limits.
- **Bounded.** Results, number of upstream calls, response size and time per request are all capped,
  so one question can't run away.
- **Stateless and lightweight.** No database and no accounts. Only a small in-memory cache of
  *public* data.
- **Game text is treated as untrusted.** Player and company names are cleaned and length-limited
  before the assistant sees them.

## Privacy & security

- Public operations run anonymously and never forward supplied credentials.
- Region recommendations, workers and transactions require API key.
- Inventory and owner orders require JWT; current equipped items are public.
- Configuration tools always read anonymously and do not accept `player_context`.
- If supplied, `player_context` carries the caller's own request-scoped `api_key` or `jwt`.
- The project has no default or global WarEra credential.
- Submitted credentials are not logged, cached or persisted by the server, but they are sent in the MCP tool request and may be visible in the LLM/client conversation history.
- The server has no persistent conversation store. Public responses can be held in its
  in-memory TTL cache, and operational logs record sanitized request metadata. The MCP
  client, hosting platform and reverse proxy have their own storage/logging policies.
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

The upstream response cap defaults to 8,000,000 bytes to accommodate verified global
ranking snapshots larger than 2 MB. The complete HTTP body (including a batch) is
still bounded. Structured tool output remains capped at 262,144 bytes; use bounded
pages and optional detail flags to control output size.

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

## Development

```bash
uv sync --locked
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
