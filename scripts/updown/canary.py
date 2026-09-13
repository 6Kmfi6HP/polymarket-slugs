"""Schema-drift canary.

Every pull compares the union of API field names against the field sets
observed during the 2026-08 audit (300 sampled markets).  Any new field is
appended to ``data/raw/_canary.jsonl`` so that capture decisions stay
explicit instead of silently losing data.
"""

# market top-level fields observed as of the 2026-08 audit (92, after the
# presentation denylist strip)
KNOWN_MARKET_FIELDS = {
    "acceptingOrders", "acceptingOrdersTimestamp", "active", "approved",
    "archived", "automaticallyActive", "automaticallyResolved", "bestAsk",
    "bestBid", "clearBookOnStart", "clobTokenIds", "closed", "closedTime",
    "comboStatus", "competitive", "conditionId", "createdAt",
    "cryptoMarketConfig", "cryptoMarketConfigId", "customLiveness", "cyom",
    "deploying", "deployingTimestamp", "enableOrderBook", "endDate",
    "endDateIso", "events", "eventStartTime", "featured", "feeSchedule",
    "feeType", "feesEnabled", "funded", "groupItemThreshold",
    "hasReviewedDates", "holdingRewardsEnabled", "id", "lastTradePrice",
    "liquidity", "liquidityAmm", "liquidityClob", "liquidityNum",
    "makerBaseFee", "makerRebatesFeeShareBps", "manualActivation",
    "marketMakerAddress", "negRisk", "negRiskOther", "new",
    "oneDayPriceChange", "oneHourPriceChange", "orderMinSize",
    "orderPriceMinTickSize", "outcomePrices", "outcomes",
    "pagerDutyNotificationEnabled", "pendingDeployment", "question",
    "questionID", "ready", "requiresTranslation", "resolutionSource",
    "resolvedBy", "restricted", "rewardsMaxSpread", "rewardsMinSize",
    "rfqEnabled", "showGmpOutcome", "showGmpSeries", "slug", "spread",
    "startDate", "startDateIso", "takerBaseFee", "umaBond", "umaEndDate",
    "umaResolutionStatus", "umaResolutionStatuses", "umaReward", "updatedAt",
    "version", "volume", "volume1mo", "volume1moClob", "volume1wk",
    "volume1wkClob", "volume1yr", "volume1yrClob", "volume24hr",
    "volume24hrClob", "volumeClob", "volumeNum",
}

KNOWN_EVENT_FIELDS = {
    "active", "archived", "automaticallyActive", "automaticallyResolved",
    "closed", "closedTime", "commentCount", "competitive", "createdAt",
    "creationDate", "cyom", "deploying", "enableNegRisk", "enableOrderBook",
    "endDate", "eventMetadata", "featured", "id", "liquidity",
    "liquidityAmm", "liquidityClob", "negRisk", "negRiskAugmented", "new",
    "openInterest", "pendingDeployment", "requiresTranslation",
    "resolutionSource", "restricted", "series", "seriesSlug",
    "showAllOutcomes", "showMarketImages", "slug", "startDate", "startTime",
    "ticker", "title", "updatedAt", "version", "volume", "volume1mo",
    "volume1wk", "volume1yr", "volume24hr",
}

KNOWN_EVENT_METADATA_FIELDS = {"finalPrice", "priceToBeat"}


def diff_keys(markets) -> dict:
    """Return field names never seen in the audit, grouped by payload level."""
    seen_top, seen_event, seen_em = set(), set(), set()
    for market in markets:
        if not isinstance(market, dict):
            continue
        seen_top.update(market.keys())
        events = market.get("events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                seen_event.update(event.keys())
                meta = event.get("eventMetadata")
                if isinstance(meta, dict):
                    seen_em.update(meta.keys())
    return {
        "market": seen_top - KNOWN_MARKET_FIELDS,
        "event": seen_event - KNOWN_EVENT_FIELDS,
        "event_metadata": seen_em - KNOWN_EVENT_METADATA_FIELDS,
    }
