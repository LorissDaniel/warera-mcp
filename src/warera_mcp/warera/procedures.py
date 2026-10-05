"""Reviewed, read-only WarEra operation registry.

Every upstream operation the server may call is declared here with its
transport method, allowed inputs, credential requirement, cache class and the
evidence that justified its inclusion. The client accepts a
:class:`ProcedureSpec` — never a free-form procedure string — so there is no
path from tool input to an arbitrary upstream call.

Only operations reviewed as *read-game-state* operations are listed. Mutating
procedures are intentionally absent; the registry is the read-only allowlist
referenced by the security tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from warera_mcp.auth.requirements import AuthRequirement, CredentialKind


class Domain(StrEnum):
    """Coarse capability grouping used for metrics labels and docs."""

    MARKET = "market"
    WORLD = "world"
    PLAYERS = "players"
    COMPANIES = "companies"
    WORK = "work"
    BATTLES = "battles"
    EVENTS = "events"
    ARTICLES = "articles"
    CONFIGURATION = "configuration"


class ProcedureParamError(ValueError):
    """Raised when caller params violate a procedure's declared input contract."""


class UnknownProcedureError(KeyError):
    """Raised when a name is not present in the read-only allowlist."""


@dataclass(frozen=True, slots=True)
class ProcedureSpec:
    """Declarative contract of one reviewed read-only operation."""

    name: str
    domain: Domain
    auth: AuthRequirement
    required_params: frozenset[str] = frozenset()
    optional_params: frozenset[str] = frozenset()
    ttl_seconds: float | None = None
    evidence: str = ""
    schema_version: int = 1

    @property
    def accepted_params(self) -> frozenset[str]:
        return self.required_params | self.optional_params

    @property
    def is_public(self) -> bool:
        return self.auth.is_public

    @property
    def cacheable(self) -> bool:
        """Only public, non-personal responses may be shared-cached."""
        return self.ttl_seconds is not None and self.is_public

    def validate_params(self, params: Mapping[str, object]) -> None:
        """Reject unknown keys and missing required keys.

        Values are not echoed in the message to avoid surfacing caller data.
        """
        unknown = set(params) - self.accepted_params
        if unknown:
            raise ProcedureParamError(
                f"procedure '{self.name}' received unsupported parameters: "
                f"{', '.join(sorted(unknown))}"
            )
        missing = self.required_params - set(params)
        if missing:
            raise ProcedureParamError(
                f"procedure '{self.name}' is missing required parameters: "
                f"{', '.join(sorted(missing))}"
            )


_PUBLIC: Final = AuthRequirement.public()
_API_KEY_OR_JWT: Final = AuthRequirement.any_of(CredentialKind.API_KEY, CredentialKind.JWT)


def _p(
    name: str,
    domain: Domain,
    *,
    auth: AuthRequirement = _PUBLIC,
    required: frozenset[str] = frozenset(),
    optional: frozenset[str] = frozenset(),
    ttl: float | None = None,
    evidence: str,
) -> ProcedureSpec:
    return ProcedureSpec(
        name=name,
        domain=domain,
        auth=auth,
        required_params=required,
        optional_params=optional,
        ttl_seconds=ttl,
        evidence=evidence,
    )


_PROCEDURES: Final[tuple[ProcedureSpec, ...]] = (
    # ---------------------------------------------------------------- market
    _p(
        "itemTrading.getPrices",
        Domain.MARKET,
        ttl=60,
        evidence="[L,D] public price snapshot; community notes optional itemCode is ignored",
    ),
    _p(
        "tradingOrder.getTopOrders",
        Domain.MARKET,
        required=frozenset({"itemCode"}),
        ttl=20,
        evidence="[L,D] public visible order book; 'top' count/order unspecified upstream",
    ),
    _p(
        "tradingOrder.getAllOrdersByOwner",
        Domain.MARKET,
        auth=AuthRequirement.of(CredentialKind.JWT),
        required=frozenset({"userId"}),
        evidence=(
            "[official client, live 2026-10-05] GET owner aggregates; anonymous 401, "
            "API key 403, JWT 200 for authorized account; no shared cache"
        ),
    ),
    _p(
        "inventory.getById",
        Domain.PLAYERS,
        auth=AuthRequirement.of(CredentialKind.JWT),
        required=frozenset({"userId"}),
        evidence=(
            "[official client, live 2026-10-05] GET money/items.basics/market reservations; "
            "anonymous 401, API key 403, JWT 200 for authorized account; no shared cache"
        ),
    ),
    # ----------------------------------------------------------------- world
    _p(
        "country.getAllCountries",
        Domain.WORLD,
        ttl=900,
        evidence="[L,live] anonymous array; includes warsWith/allies/taxes/economy fields",
    ),
    _p(
        "country.getCountryById",
        Domain.WORLD,
        required=frozenset({"countryId"}),
        ttl=900,
        evidence="[D,live] anonymous detail with the same top-level shape as getAllCountries",
    ),
    _p(
        "region.getRegionsObject",
        Domain.WORLD,
        ttl=900,
        evidence="[L,D,live] anonymous map keyed by region id",
    ),
    _p(
        "region.getById",
        Domain.WORLD,
        required=frozenset({"regionId"}),
        ttl=900,
        evidence="[L,live] anonymous detail incl. deposit/neighbors/resistance",
    ),
    # --------------------------------------------------------------- players
    _p(
        "user.getUserLite",
        Domain.PLAYERS,
        required=frozenset({"userId"}),
        ttl=300,
        evidence="[L,D,live] anonymous public profile projection",
    ),
    _p(
        "search.searchUsers",
        Domain.PLAYERS,
        required=frozenset({"searchText"}),
        optional=frozenset({"limit"}),
        ttl=60,
        evidence="[live] anonymous id list; upstream returned 5 ids for limit:3",
    ),
    _p(
        "search.searchAnything",
        Domain.PLAYERS,
        required=frozenset({"searchText"}),
        ttl=60,
        evidence="[D,live] anonymous entity id groups incl. allianceIds",
    ),
    _p(
        "user.getUsersByCountry",
        Domain.PLAYERS,
        required=frozenset({"countryId"}),
        optional=frozenset({"limit", "cursor"}),
        ttl=300,
        evidence="[D,live] anonymous {items:[{_id,createdAt}],nextCursor}",
    ),
    # ------------------------------------------------------------- companies
    _p(
        "company.getById",
        Domain.COMPANIES,
        required=frozenset({"companyId"}),
        ttl=60,
        evidence="[L,live] anonymous detail; production is not a rate",
    ),
    _p(
        "company.getCompanies",
        Domain.COMPANIES,
        optional=frozenset({"userId", "limit", "cursor"}),
        ttl=60,
        evidence="[live] {userId} returns {items:[id]} without cursor; global page returns 10 ids",
    ),
    _p(
        "company.getProductionBonus",
        Domain.COMPANIES,
        required=frozenset({"companyId"}),
        ttl=600,
        evidence="[L,live] anonymous components in percentage points summing to total",
    ),
    _p(
        "company.getRecommendedRegionIdsByItemCode",
        Domain.COMPANIES,
        auth=_API_KEY_OR_JWT,
        required=frozenset({"itemCode"}),
        optional=frozenset({"includeDeposit"}),
        ttl=600,
        evidence=(
            "[official client, live 2026-10-05] anonymous 401; API key and JWT each 200; "
            "includeDeposit:false returns ranked bonus components with deposit terms zero"
        ),
    ),
    # ------------------------------------------------------------------ work
    _p(
        "workOffer.getWageStats",
        Domain.WORK,
        required=frozenset({"itemCode"}),
        ttl=300,
        evidence="[L,live] anonymous allowedRange{min,max,average} plus top offers",
    ),
    _p(
        "workOffer.getWorkOffersPaginated",
        Domain.WORK,
        optional=frozenset({"limit", "cursor"}),
        ttl=60,
        evidence="[live] anonymous {items,nextCursor}; API filters unverified so applied locally",
    ),
    # --------------------------------------------------------------- battles
    _p(
        "battle.getBattles",
        Domain.BATTLES,
        optional=frozenset({"countryId", "isActive", "limit", "cursor"}),
        ttl=15,
        evidence="[D,live] anonymous page; country filter matched an active battle side",
    ),
    _p(
        "battle.getById",
        Domain.BATTLES,
        required=frozenset({"battleId"}),
        ttl=20,
        evidence="[D,live] anonymous detail with attacker/defender and round data",
    ),
    _p(
        "battle.getLiveBattleData",
        Domain.BATTLES,
        required=frozenset({"battleId"}),
        ttl=8,
        evidence="[live] anonymous {battle,round}; volatile, shortest cache",
    ),
    _p(
        "battleRanking.getRanking",
        Domain.BATTLES,
        required=frozenset({"battleId", "type", "side"}),
        optional=frozenset({"dataType"}),
        ttl=10,
        evidence="[live] all 18 type x side x dataType combinations succeeded anonymously",
    ),
    # ---------------------------------------------------------------- events
    _p(
        "event.getEventsPaginated",
        Domain.EVENTS,
        optional=frozenset({"limit", "cursor"}),
        ttl=30,
        evidence="[L,D,live] anonymous {items,nextCursor}; raw data treated as untrusted",
    ),
    _p("gameConfig.getGameConfig", Domain.CONFIGURATION, ttl=300,
       evidence="[anonymous GET, verified] official game configuration snapshot"),
    _p("gameConfig.getDates", Domain.CONFIGURATION, ttl=30,
       evidence="[anonymous GET, verified] official UTC game schedule"),
    _p(
        "article.getArticlesPaginated",
        Domain.ARTICLES,
        required=frozenset({"type"}),
        optional=frozenset({
            "limit", "cursor", "userId", "categories", "languages", "positiveScoreOnly",
        }),
        ttl=30,
        evidence=(
            "[official docs, live anonymous GET] last feed includes content; languages:it verified"
        ),
    ),
    _p(
        "article.getArticleLiteById",
        Domain.ARTICLES,
        required=frozenset({"articleId"}),
        ttl=30,
        evidence="[official docs, live anonymous GET] title/stats without counting a view",
    ),
    # getArticleById is deliberately absent: it may count a view. The paginated
    # feed already includes article content for summaries without that endpoint.
)

PROCEDURES: Final[Mapping[str, ProcedureSpec]] = MappingProxyType(
    {spec.name: spec for spec in _PROCEDURES}
)

#: Names of every operation the production client may call.
READ_ONLY_PROCEDURES: Final[frozenset[str]] = frozenset(PROCEDURES)

#: Transport method. GET is the only verified read transport; v1 excludes POST
#: batching entirely.
ALLOWED_HTTP_METHOD: Final[str] = "GET"


def get_procedure(name: str) -> ProcedureSpec:
    """Return the reviewed spec for *name* or raise :class:`UnknownProcedureError`."""
    try:
        return PROCEDURES[name]
    except KeyError:
        raise UnknownProcedureError(
            f"procedure {name!r} is not in the reviewed read-only allowlist"
        ) from None


__all__ = [
    "ALLOWED_HTTP_METHOD",
    "PROCEDURES",
    "READ_ONLY_PROCEDURES",
    "Domain",
    "ProcedureParamError",
    "ProcedureSpec",
    "UnknownProcedureError",
    "get_procedure",
]
