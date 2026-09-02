#!/usr/bin/env python3
"""
Fetches the latest Euro area macroeconomic indicators and writes data.json
for the executive dashboard (index.html).

Design:
  - 4 "clean" indicators (GDP, CPI, ECB rate, EUR/USD) come from official,
    free, no-key APIs (Eurostat SDMX-JSON, ECB Data Portal SDMX-JSON).
    These are expected to always work. If a dimension/geo code is wrong the
    parser raises a clear, loud error instead of silently writing empty data
    -- but that error is contained to the one indicator: its last known
    history is carried forward flagged "stale": true (the card shows a stale
    badge) so a single broken source can't cost the other five their update.
  - 2 "fragile" indicators (TTF gas price, Manufacturing PMI) have NO free
    official API. Each uses a 3-step fallback chain so the pipeline never
    breaks:
      1. best-effort automated fetch (an unofficial/scraped source)
      2. manual_overrides.json (user-edited, ~5 min/quarter maintenance)
      3. carry forward the previous period's value, flagged "stale": true
  - trade_policy_risks is purely qualitative (no API, no override) and is
    left untouched by this script; edit its "note" directly in data.json.

Run: python3 fetch_data.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests

# --------------------------------------------------------------------------
# CONFIG — geography, endpoints, dimension codes. Verify against each
# dataset's metadata before trusting; parsers below fail loudly on empty
# results so a wrong code combo is caught immediately rather than silently
# writing empty/zero data.
# --------------------------------------------------------------------------

GEO = "EA20"  # Euro area (20 members incl. Croatia). Change here to retarget.

REQUEST_TIMEOUT = 30
USER_AGENT = "euro-macro-dashboard/1.0 (+https://github.com/)"

CONFIG = {
    # ---- CLEAN: official SDMX-JSON APIs, no key required -----------------
    "gdp_growth": {
        "kind": "eurostat",
        "url": (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
            f"data/namq_10_gdp?format=JSON&geo={GEO}&na_item=B1GQ"
            "&unit=CLV_PCH_PRE&s_adj=SCA&sinceTimePeriod=2015-Q1"
        ),
        "label": "GDP growth (QoQ)",
        "unit": "%",
        "round": 1,
    },
    "cpi": {
        "kind": "eurostat",
        # prc_hicp_minr (vs. prc_hicp_manr) is Eurostat's more current HICP
        # release -- same official source, but published with materially
        # less lag, so "latest" tracks much closer to the present month.
        "url": (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
            f"data/prc_hicp_minr?format=JSON&geo={GEO}&unit=RCH_A&coicop18=TOTAL"
            "&sinceTimePeriod=2015-01"
        ),
        "label": "CPI (HICP, YoY)",
        "unit": "%",
        "round": 1,
    },
    "unemployment": {
        "kind": "eurostat",
        # EA21, not EA20 like every other card. Not a typo: Eurostat does not
        # disseminate a monthly EA20 unemployment aggregate at all -- une_rt_m
        # publishes only EA21 (the euro area since Bulgaria joined in January
        # 2026) and EU27_2020. So this one card covers one country more than
        # the others; the difference is immaterial at this resolution
        # (Bulgaria is ~0.5% of euro area GDP) and the card's note says so.
        "geo": "EA21",
        "url": (
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/"
            "data/une_rt_m?format=JSON&geo=EA21&s_adj=SA&age=TOTAL&sex=T"
            "&unit=PC_ACT&sinceTimePeriod=2015-01"
        ),
        "label": "Unemployment rate",
        "unit": "%",
        "round": 1,
    },
    "interest_rates": {
        "kind": "ecb",
        "url": (
            "https://data-api.ecb.europa.eu/service/data/FM/"
            "B.U2.EUR.4F.KR.MRR_FR.LEV?format=jsondata&startPeriod=2015-01-01"
        ),
        "label": "ECB main refinancing rate",
        "unit": "%",
        "round": 2,
    },
    "currency": {
        "kind": "ecb",
        "url": (
            "https://data-api.ecb.europa.eu/service/data/EXR/"
            "D.USD.EUR.SP00.A?format=jsondata&startPeriod=2015-01-01"
        ),
        "label": "EUR/USD",
        "unit": "USD",
        "round": 4,
        "resample_monthly": True,  # ECB EXR is daily; keep charts readable
    },
    # ---- FRAGILE: no free official API, best-effort + fallback chain -----
    "energy_prices": {
        "kind": "ttf_best_effort",
        # Unofficial Yahoo Finance chart endpoint for Dutch TTF gas futures
        # (front-month continuous contract), quoted in EUR/MWh. This is NOT
        # an official ICE/EEX feed and can change shape or disappear without
        # notice -- that's exactly why the fallback chain exists.
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/TTF=F?range=10y&interval=1mo",
        "label": "Energy prices (TTF gas)",
        "unit": "€/MWh",
        "round": 1,
    },
    "manufacturing_pmi": {
        "kind": "pmi_best_effort",
        # Best-effort scrape of the public TradingEconomics page's meta
        # description, e.g. "...increased to 52 points in July from 51.40
        # points in June of 2026." Brittle HTML scraping by design -- this
        # is the indicator most likely to need a manual override.
        "url": "https://tradingeconomics.com/euro-area/manufacturing-pmi",
        "label": "Manufacturing PMI",
        "unit": "index",
        "round": 1,
    },
}

# ---- FORWARD-LOOKING: ECB/Eurosystem staff projections -------------------
# The cards above are all backward-looking actuals. These two also carry the
# official Eurosystem projection for the current and next calendar year,
# taken from the ECB Data Portal's Macroeconomic Projection Database (MPD) --
# free, keyless, same API family as the MRO rate and EUR/USD.
#
# MPD key: FREQ.REF_AREA.PD_ITEM.SERIES_DENOM.PD_SEAS_EX.PD_ORIGIN
#   A     annual  |  U2  euro area  |  0000  finalised/latest data
#   PD_SEAS_EX is the projection ROUND (W=March, G=June, S=September,
#   A=December, plus a 2-digit year). It is left blank in the query on
#   purpose: rather than hardcoding a round that goes out of date every
#   quarter, every round is fetched and the newest one is picked at runtime,
#   so the September round appears on the dashboard the week it is published.
PROJECTION_SOURCE = "ECB/Eurosystem staff macroeconomic projections"
MPD_URL = (
    "https://data-api.ecb.europa.eu/service/data/MPD/A.U2.{item}.{denom}..0000"
    "?format=jsondata&startPeriod={start_year}"
)
SEASON_ORDER = {"W": 1, "G": 2, "S": 3, "A": 4}  # within a year: Mar < Jun < Sep < Dec

PROJECTIONS = {
    "gdp_growth": {
        "pd_item": "YER",   # real GDP
        "denom": "A",       # annual growth rate
        # "annual" matters on this card specifically: the headline number
        # above it is QUARTERLY growth, so an unqualified projection would
        # read as a wildly optimistic quarter.
        "unit_label": "annual",
        # A growth rate reads as a signed change (+1.2%); an unemployment
        # rate is a level (6.2%) and a "+" in front of it would be nonsense.
        "signed": True,
        "round": 1,
    },
    "unemployment": {
        "pd_item": "URX",   # unemployment rate
        "denom": "F",       # percentage
        # No qualifier needed: a projected unemployment rate is the same
        # measure as the headline above it, just later.
        "unit_label": None,
        "signed": False,
        "round": 1,
    },
}

# Indicators that are purely qualitative -- never fetched, never overridden.
PASSTHROUGH_INDICATORS = ["trade_policy_risks"]

ROOT = Path(__file__).resolve().parent
DATA_JSON_PATH = ROOT / "data.json"
MANUAL_OVERRIDES_PATH = ROOT / "manual_overrides.json"

MONTH_NAMES = {
    "January": "01", "February": "02", "March": "03", "April": "04",
    "May": "05", "June": "06", "July": "07", "August": "08",
    "September": "09", "October": "10", "November": "11", "December": "12",
}


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def http_get(url: str) -> requests.Response:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp


def warn(message: str) -> None:
    """Log a degraded-but-survivable condition. The ::warning:: prefix makes
    GitHub Actions surface it on the run summary page instead of burying it
    in the log, so a source that quietly dies is still noticed. Printed to
    stdout because that's the only stream Actions parses for commands."""
    print(f"::warning::{message}")


# --------------------------------------------------------------------------
# Parsers for the 4 clean indicators
# --------------------------------------------------------------------------

def parse_eurostat(payload: dict, key: str, geo: str = GEO) -> list[dict]:
    """Decode a Eurostat JSON-stat 2.0-ish response into [{date, value}, ...].

    Eurostat's dissemination API nests dimensions with each non-'time'
    dimension expected to be pinned to a single code by the query string
    (size == 1). If that's not true, the URL's dimension codes are too
    broad and we fail loudly rather than silently averaging/picking one.
    """
    dim_ids = payload.get("id")
    sizes = payload.get("size")
    if not dim_ids or not sizes or "time" not in dim_ids:
        raise RuntimeError(f"[{key}] Unexpected Eurostat response shape (no id/size/time): {payload.get('label')}")

    non_time = [(d, s) for d, s in zip(dim_ids, sizes) if d != "time"]
    if any(s != 1 for _, s in non_time):
        raise RuntimeError(
            f"[{key}] Eurostat query is not selective enough -- expected exactly one series "
            f"but dimensions {non_time} have size != 1. Check dimension codes in CONFIG."
        )

    # All non-time dims are size 1, so the flat value index == the time index
    # (time's stride is 1 regardless of its position, since every other
    # dimension contributes a factor of 1 to the stride product).
    time_index = payload["dimension"]["time"]["category"]["index"]
    values = payload.get("value", {})
    if not values:
        raise RuntimeError(
            f"[{key}] Eurostat returned zero observations for geo={geo}. "
            f"The dimension code combo is likely wrong -- check the query manually."
        )

    out = []
    for period, idx in time_index.items():
        v = values.get(str(idx))
        if v is not None:
            out.append({"date": period, "value": v})
    out.sort(key=lambda r: r["date"])
    if not out:
        raise RuntimeError(f"[{key}] Eurostat response had a time index but no matching values.")
    return out


def parse_ecb(payload: dict, key: str, resample_monthly: bool = False) -> list[dict]:
    """Decode an ECB Data Portal SDMX-JSON ('jsondata') response."""
    try:
        dataset = payload["dataSets"][0]
        series_dict = dataset["series"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"[{key}] Unexpected ECB response shape: missing dataSets/series ({exc})")

    if not series_dict:
        raise RuntimeError(f"[{key}] ECB returned no series for the given filter. Check dimension codes in CONFIG.")
    if len(series_dict) > 1:
        raise RuntimeError(
            f"[{key}] ECB query returned {len(series_dict)} series but expected exactly 1 -- "
            "the SDMX key in CONFIG is not selective enough."
        )

    series_key = next(iter(series_dict))
    observations = series_dict[series_key]["observations"]
    time_values = payload["structure"]["dimensions"]["observation"][0]["values"]
    # time_values[i]["id"] is the date label for observation index i

    out = []
    for idx_str, obs in observations.items():
        idx = int(idx_str)
        if obs and obs[0] is not None and idx < len(time_values):
            out.append({"date": time_values[idx]["id"], "value": obs[0]})
    out.sort(key=lambda r: r["date"])
    if not out:
        raise RuntimeError(f"[{key}] ECB response had series but no numeric observations.")

    if resample_monthly:
        by_month: "OrderedDict[str, dict]" = OrderedDict()
        for row in out:
            month = row["date"][:7]  # YYYY-MM-DD -> YYYY-MM
            by_month[month] = row  # last day seen per month wins (data is date-sorted)
        out = [{"date": m, "value": r["value"]} for m, r in by_month.items()]

    return out


# --------------------------------------------------------------------------
# Best-effort fetchers for the 2 fragile indicators (step 1 of the chain)
# --------------------------------------------------------------------------

def fetch_ttf_best_effort(url: str) -> list[dict] | None:
    try:
        resp = http_get(url)
        payload = resp.json()
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        by_month: "OrderedDict[str, float]" = OrderedDict()
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
            by_month[month] = close  # keep last (most recent) observation per month
        if not by_month:
            return None
        return [{"date": m, "value": v} for m, v in by_month.items()]
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        warn(f"[energy_prices] best-effort fetch failed ({exc.__class__.__name__}: {exc}) -- "
             f"falling back to manual_overrides.json")
        return None


def fetch_pmi_best_effort(url: str) -> dict | None:
    """Scrape the page's <meta name="description"> for a sentence like:
    "...increased to 52 points in July from 51.40 points in June of 2026."
    Returns the single latest {date, value} point, or None on any failure.
    """
    try:
        resp = http_get(url)
        html = resp.text
        m = re.search(r'name="description"\s+content="([^"]+)"', html)
        if not m:
            return None
        desc = m.group(1)
        point = re.search(
            r"to\s+([\d.]+)\s+points?\s+in\s+(\w+)\s+from\s+[\d.]+\s+points?\s+in\s+\w+\s+of\s+(\d{4})",
            desc,
        )
        if not point:
            return None
        value = float(point.group(1))
        month_name, year = point.group(2), point.group(3)
        month_num = MONTH_NAMES.get(month_name)
        if not month_num:
            return None
        return {"date": f"{year}-{month_num}", "value": value}
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        warn(f"[manufacturing_pmi] best-effort fetch failed ({exc.__class__.__name__}: {exc}) -- "
             f"falling back to manual_overrides.json")
        return None


# --------------------------------------------------------------------------
# data.json load / seed
# --------------------------------------------------------------------------

def load_existing_data() -> dict:
    if DATA_JSON_PATH.exists():
        with open(DATA_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    raise RuntimeError(
        f"{DATA_JSON_PATH} not found. Seed it first (see README) -- this script updates an "
        "existing data.json rather than fabricating one from scratch."
    )


def load_manual_overrides() -> dict:
    if MANUAL_OVERRIDES_PATH.exists():
        with open(MANUAL_OVERRIDES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def resolve_note(key: str, existing_note, overrides: dict):
    """Editorial commentary (driver of the move, or -- for trade_policy_risks --
    the executive summary) always comes from manual_overrides.json["notes"] when
    present there, regardless of which fetch path produced the indicator's
    numbers. If the key isn't listed there, whatever note already existed in
    data.json is kept as-is (so removing a line from overrides doesn't wipe it).
    """
    note = overrides.get("notes", {}).get(key)
    return note if note else existing_note


def fetch_trade_policy_summary() -> str | None:
    """Best-effort: ask Claude (with its web-search tool) to research current
    European trade-policy / geopolitical / energy news and write a short
    executive summary for trade_policy_risks. This is the first step of the
    same 3-step fallback chain as TTF/PMI (best-effort -> manual_overrides.json
    -> carry forward) -- it just never produces numeric history, only text.

    Requires ANTHROPIC_API_KEY in the environment (a GitHub Actions secret in
    CI). If it's unset, or the call fails for any reason, returns None and the
    caller falls back to manual_overrides.json.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # No key configured is a deliberate setup choice, not a fault: the
        # note simply stays whatever manual_overrides.json says. A key that
        # IS set but fails (below) is a fault, and gets an annotation.
        print("  [trade_policy_risks] no ANTHROPIC_API_KEY set -- the note will not be "
              "auto-refreshed; it comes from manual_overrides.json.")
        return None
    try:
        import anthropic

        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
            messages=[{
                "role": "user",
                "content": (
                    "Search for European economic and trade-policy news from the past "
                    "7-10 days that would matter to a European pulp & paper manufacturer: "
                    "tariff disputes (especially EU-US), the war in Ukraine and Middle East "
                    "geopolitical risk, energy prices, and anything affecting chemicals or "
                    "pulp/paper inputs and export markets. Then write a 4-5 line executive "
                    "summary in plain prose (no headers, no bullet points) covering the most "
                    "relevant developments, naming sources and approximate dates inline. "
                    "Board-level tone: concise, factual, no speculation."
                ),
            }],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        # ANTHROPIC_API_KEY is configured but didn't work (expired/revoked key,
        # rate limit, outage). The dashboard keeps rendering the stored note,
        # which is exactly why this needs to be loud: without an annotation the
        # card just quietly stops being current and nobody notices for months.
        warn(f"[trade_policy_risks] LLM web-search summary failed "
             f"({exc.__class__.__name__}: {exc}) -- falling back to manual_overrides.json. "
             f"Check the ANTHROPIC_API_KEY secret.")
        return None


# --------------------------------------------------------------------------
# ECB/Eurosystem staff projections (forward-looking, current + next year)
# --------------------------------------------------------------------------

def _round_sort_key(round_code: str):
    """('G26') -> (2026, 2) so rounds sort chronologically. None if unparseable."""
    m = re.fullmatch(r"([WGSA])(\d{2})", round_code)
    if not m:
        return None
    return (2000 + int(m.group(2)), SEASON_ORDER[m.group(1)])


def fetch_projection(key: str, cfg: dict) -> dict:
    """Latest published projection round for one MPD item, as the current and
    next calendar year. Raises on any problem -- the caller decides whether to
    carry the previous projection forward."""
    this_year = datetime.now(timezone.utc).year
    payload = http_get(MPD_URL.format(
        item=cfg["pd_item"], denom=cfg["denom"], start_year=this_year)).json()

    series = payload.get("dataSets", [{}])[0].get("series")
    if not series:
        raise RuntimeError(f"[{key}] MPD returned no series for {cfg['pd_item']}.")

    dims = payload["structure"]["dimensions"]["series"]
    # Find PD_SEAS_EX by id rather than by a hardcoded position: the series
    # key is positional, but the position is the API's business, not ours.
    try:
        round_pos = next(i for i, d in enumerate(dims) if d["id"] == "PD_SEAS_EX")
    except StopIteration:
        raise RuntimeError(f"[{key}] MPD response has no PD_SEAS_EX dimension.")
    rounds = dims[round_pos]["values"]
    years = [v["id"] for v in payload["structure"]["dimensions"]["observation"][0]["values"]]
    wanted = [str(this_year), str(this_year + 1)]

    best = None  # (sort_key, round_meta, observations)
    for series_key, series_data in series.items():
        idx = [int(i) for i in series_key.split(":")]
        round_meta = rounds[idx[round_pos]]
        sort_key = _round_sort_key(round_meta["id"])
        if sort_key is None:
            continue
        obs = {}
        for pos, value in series_data.get("observations", {}).items():
            if value and value[0] is not None and int(pos) < len(years):
                obs[years[int(pos)]] = value[0]
        # Only consider a round that actually covers a year we want to show:
        # the newest round by code is useless if it stops before next year.
        if not any(y in obs for y in wanted):
            continue
        if best is None or sort_key > best[0]:
            best = (sort_key, round_meta, obs)

    if best is None:
        raise RuntimeError(
            f"[{key}] No MPD round covers {' or '.join(wanted)} for {cfg['pd_item']}."
        )

    _, round_meta, obs = best
    return {
        "source": PROJECTION_SOURCE,
        "round": round_meta.get("name", round_meta["id"]),
        "round_code": round_meta["id"],
        "unit_label": cfg["unit_label"],
        "signed": cfg["signed"],
        "values": [{"year": y, "value": round(obs[y], cfg["round"])} for y in wanted if y in obs],
        "stale": False,
    }


def resolve_projection(key: str, cfg: dict, existing: dict) -> dict | None:
    """Best-effort, exactly like every other fragile source here: a failed
    fetch keeps the projection already on disk, flagged stale, rather than
    blanking the line or failing the run."""
    try:
        result = fetch_projection(key, cfg)
        print(f"  [{key}] projection ({result['round']}): " +
              ", ".join(f"{v['year']} {v['value']}" for v in result["values"]))
        return result
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        previous = existing.get("projection")
        if not previous:
            warn(f"[{key}] projection fetch failed ({exc.__class__.__name__}: {exc}) "
                 f"and none is stored -- the card will show actuals only.")
            return None
        warn(f"[{key}] projection fetch failed ({exc.__class__.__name__}: {exc}) -- "
             f"keeping the stored {previous.get('round')} round, flagged stale.")
        stale_copy = dict(previous)
        stale_copy["stale"] = True
        return stale_copy


# --------------------------------------------------------------------------
# History merge helpers
# --------------------------------------------------------------------------

def finalize_indicator(existing: dict, cfg: dict, history: list[dict], stale: bool) -> dict:
    """Sort/dedupe history, round values, and derive latest/previous."""
    by_date = OrderedDict()
    for row in history:
        by_date[row["date"]] = round(float(row["value"]), cfg["round"])
    dates = sorted(by_date.keys())
    hist = [{"date": d, "value": by_date[d]} for d in dates]

    if not hist:
        raise RuntimeError(f"No history to finalize for {cfg['label']}")

    latest_date = dates[-1]
    latest_value = by_date[latest_date]
    previous = None
    if len(dates) > 1:
        prev_date = dates[-2]
        previous = {"value": by_date[prev_date], "date": prev_date}

    return {
        "label": cfg["label"],
        "unit": cfg["unit"],
        "latest": {"value": latest_value, "date": latest_date, "stale": stale},
        "previous": previous,
        "history": hist,
        "note": existing.get("note"),
    }


def update_clean_indicator(key: str, cfg: dict, existing: dict) -> dict:
    print(f"Fetching {key} ({cfg['label']}) ...")
    resp = http_get(cfg["url"])
    payload = resp.json()

    if cfg["kind"] == "eurostat":
        history = parse_eurostat(payload, key, geo=cfg.get("geo", GEO))
    elif cfg["kind"] == "ecb":
        history = parse_ecb(payload, key, resample_monthly=cfg.get("resample_monthly", False))
    else:
        raise RuntimeError(f"Unknown clean indicator kind: {cfg['kind']}")

    result = finalize_indicator(existing, cfg, history, stale=False)
    print(f"  -> latest: {result['latest']['value']}{cfg['unit']} ({result['latest']['date']}), "
          f"{len(result['history'])} history points")
    return result


def carry_forward_clean_indicator(key: str, cfg: dict, existing: dict, exc: Exception) -> dict:
    """A clean source failed. Keep its stored history, flagged stale, so the
    rest of the dashboard still updates. Nothing to keep -> re-raise, since
    there'd be no numbers at all to show."""
    history = [{"date": r["date"], "value": r["value"]} for r in existing.get("history", [])]
    if not history:
        raise RuntimeError(
            f"[{key}] fetch failed and there is no stored history to carry forward: {exc}"
        ) from exc
    result = finalize_indicator(existing, cfg, history, stale=True)
    warn(f"[{key}] fetch failed ({exc.__class__.__name__}: {exc}) -- carrying forward "
         f"{result['latest']['date']} as stale.")
    return result


def update_fragile_indicator(key: str, cfg: dict, existing: dict, overrides: dict) -> dict:
    print(f"Fetching {key} ({cfg['label']}) [fragile: best-effort -> override -> carry-forward] ...")
    existing_history = [{"date": r["date"], "value": r["value"]} for r in existing.get("history", [])]

    # Step 1: best-effort automated fetch
    if cfg["kind"] == "ttf_best_effort":
        fetched = fetch_ttf_best_effort(cfg["url"])
    elif cfg["kind"] == "pmi_best_effort":
        point = fetch_pmi_best_effort(cfg["url"])
        fetched = [point] if point else None
    else:
        raise RuntimeError(f"Unknown fragile indicator kind: {cfg['kind']}")

    if fetched:
        merged = {r["date"]: r["value"] for r in existing_history}
        for r in fetched:
            merged[r["date"]] = r["value"]
        history = [{"date": d, "value": v} for d, v in merged.items()]
        result = finalize_indicator(existing, cfg, history, stale=False)
        print(f"  -> best-effort fetch OK: {result['latest']['value']}{cfg['unit']} ({result['latest']['date']})")
        return result

    # Step 2: manual_overrides.json
    override = overrides.get(key)
    if override and override.get("value") is not None and override.get("date"):
        merged = {r["date"]: r["value"] for r in existing_history}
        merged[override["date"]] = override["value"]
        history = [{"date": d, "value": v} for d, v in merged.items()]
        # An override that doesn't reach past what's already stored brought no
        # new period, so the card is showing an old figure and must say so --
        # otherwise a months-old value renders as if it were current.
        previous_latest = max((r["date"] for r in existing_history), default=None)
        stale = previous_latest is not None and max(merged) <= previous_latest
        result = finalize_indicator(existing, cfg, history, stale=stale)
        print(f"  -> using manual_overrides.json: {result['latest']['value']}{cfg['unit']} "
              f"({result['latest']['date']}){' [stale: no newer period]' if stale else ''}")
        return result

    # Step 3: carry forward previous value, flagged stale
    if not existing_history:
        raise RuntimeError(
            f"[{key}] No best-effort data, no manual override, and no existing history to carry "
            f"forward. Add an entry to manual_overrides.json for '{key}'."
        )
    result = finalize_indicator(existing, cfg, existing_history, stale=True)
    print(f"  -> carrying forward: {result['latest']['value']}{cfg['unit']} ({result['latest']['date']}) [stale]")
    return result


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    data = load_existing_data()
    overrides = load_manual_overrides()
    indicators = data.setdefault("indicators", {})

    for key, cfg in CONFIG.items():
        existing = indicators.get(key, {"history": [], "note": None})
        if cfg["kind"] in ("eurostat", "ecb"):
            try:
                indicators[key] = update_clean_indicator(key, cfg, existing)
            except Exception as exc:  # noqa: BLE001 - one dead source, not a dead run
                indicators[key] = carry_forward_clean_indicator(key, cfg, existing, exc)
        else:
            indicators[key] = update_fragile_indicator(key, cfg, existing, overrides)
        indicators[key]["note"] = resolve_note(key, indicators[key].get("note"), overrides)
        # Flag the one card whose geography differs from the dashboard's, so
        # the page can disclose it where it applies instead of in a footnote
        # nobody reads.
        indicator_geo = cfg.get("geo", GEO)
        if indicator_geo != GEO:
            indicators[key]["geo_note"] = indicator_geo

        if key in PROJECTIONS:
            # Attached after finalize_indicator, which rebuilds the dict from
            # scratch and would otherwise drop the field.
            projection = resolve_projection(key, PROJECTIONS[key], existing)
            if projection:
                indicators[key]["projection"] = projection

    print("Fetching trade_policy_risks summary [best-effort LLM web search -> override -> carry-forward] ...")
    llm_summary = fetch_trade_policy_summary()
    if llm_summary:
        print(f"  -> LLM web-search summary OK ({len(llm_summary)} chars)")
    else:
        print("  -> using manual_overrides.json / existing note")

    for key in PASSTHROUGH_INDICATORS:
        existing = indicators.get(key, {})
        note = llm_summary if (key == "trade_policy_risks" and llm_summary) else resolve_note(key, existing.get("note"), overrides)
        indicators[key] = {
            "label": existing.get("label", "Trade policy risks"),
            "unit": None,
            "latest": {"value": None, "date": None, "stale": False},
            "previous": None,
            "history": [],
            "note": note,
        }

    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(DATA_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nWrote {DATA_JSON_PATH} (last_updated={data['last_updated']})")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - top-level: fail loudly with full context
        print("\nfetch_data.py FAILED:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
