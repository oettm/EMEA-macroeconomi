#!/usr/bin/env python3
"""
Fetches Euro area industry/commodity indicators relevant to a pulp & paper
business and writes indices.json for the "Industry Indices" page
(indices.html). Fully separate from fetch_data.py / data.json (the
macroeconomic dashboard) -- same auto-update philosophy, own data file.

All 5 indices here are backed by official, free APIs (Eurostat SDMX-JSON,
FRED). Unlike the macro dashboard's TTF/PMI, there is no manual-override
fallback chain -- if a query returns empty, that's a real problem (wrong
dimension code, or the source genuinely stopped publishing) and the script
fails loudly rather than writing empty/fabricated data. The one exception:
individual series within a multi-line chart (see PULP_PAPER_SERIES /
TRANSPORT_SPPI_SERIES) are allowed to go stale independently of their
siblings -- Eurostat sometimes stops publishing one narrow NACE code while
the rest of the group keeps updating (see the pulp/C1711 note below).

The only hand-editable input is indices.json itself: each index's "note"
field is preserved across runs (this script never overwrites a note you've
written), and is otherwise left null.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

GEO = "EA20"
REQUEST_TIMEOUT = 30
USER_AGENT = "euro-macro-dashboard/1.0 (+https://github.com/)"

ROOT = Path(__file__).resolve().parent
INDICES_JSON_PATH = ROOT / "indices.json"

EUROSTAT_BASE = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

# --- Wood PPI: single series -----------------------------------------------
WOOD_PPI = {
    "label": "Wood PPI",
    "unit": "index (2021=100)",
    "frequency": "monthly",
    "round": 1,
    "yoy_style": "pct",
    "series": [
        {
            "code": "C16",
            "label": "Wood & products of wood/cork, excl. furniture (C16)",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C16&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
    ],
}

# --- Pulp & paper: 4 lines on one chart, same base (2021=100) --------------
# NOTE verified live against the API: C1711 (pulp) has NOT been published as
# an EA20 aggregate since 2022-06 -- not a wrong dimension code, Eurostat
# genuinely stopped releasing that combination. It's kept in the chart (real
# history through mid-2022 is still useful context) and flagged "stale" by
# the generic staleness check below, which compares each series' latest date
# against the freshest sibling in the same group.
PULP_PAPER = {
    "label": "Pulp & Paper",
    "unit": "index (2021=100)",
    "frequency": "monthly",
    "round": 1,
    "yoy_style": "pct",
    "series": [
        {
            "code": "C1711",
            "label": "Pulp (C1711)",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1711&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
        {
            "code": "C1712",
            "label": "Paper & paperboard (C1712)",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1712&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
        {
            "code": "C1721",
            "label": "Corrugated paper/board & containers (C1721)",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1721&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
        {
            "code": "C1722",
            "label": "Household & sanitary goods / tissue (C1722)",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1722&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
    ],
}

# --- Transport SPPI: 2 lines, quarterly ------------------------------------
# NOTE verified live against the API: H494 (road freight & removal) alone has
# ZERO published EA20/EA19 observations at any unit/time -- Eurostat simply
# does not disseminate that narrow aggregate (country coverage is too thin).
# H49 (Land transport and transport via pipelines -- the parent category,
# which road freight dominates by volume) DOES have a real EA20 series and is
# used here as the closest available proxy. This mirrors how H492 (rail) is
# handled: flagged in the NACE legend as not available as an EA aggregate.
TRANSPORT_SPPI = {
    "label": "Transport SPPI",
    "unit": "index (2021=100)",
    "frequency": "quarterly",
    "round": 1,
    "yoy_style": "pct",
    "series": [
        {
            "code": "H49",
            "label": "Land transport & transport via pipelines (H49) -- proxy for road freight; H494 has no published EA aggregate",
            "url": (
                f"{EUROSTAT_BASE}/sts_sepp_q?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=H49&s_adj=NSA&unit=I21&sinceTimePeriod=2015-Q1"
            ),
        },
        {
            "code": "H52",
            "label": "Warehousing & support activities for transport (H52)",
            "url": (
                f"{EUROSTAT_BASE}/sts_sepp_q?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=H52&s_adj=NSA&unit=I21&sinceTimePeriod=2015-Q1"
            ),
        },
    ],
}

# --- Trade balance: single series, level (not %) YoY -----------------------
# NOTE the dataset code in the original brief (ext_st_ea20sitc) does not
# exist; the correct Eurostat code for this series is ext_st_easitc. Balance
# is fetched directly (stk_flow=BAL_RT) rather than computed from EXP-IMP --
# Eurostat publishes it pre-computed and it's simpler/less error-prone to
# take it as-is. partner=EXT_EA21 means "trade with the rest of the world"
# (extra-euro-area), which is what "trade balance" means at the area level.
TRADE_BALANCE = {
    "label": "Euro area trade balance",
    "unit": "€ million",
    "frequency": "monthly",
    "round": 0,
    "yoy_style": "level",
    "series": [
        {
            "code": "BAL",
            "label": "Balance, extra-euro-area trade in goods",
            "url": (
                f"{EUROSTAT_BASE}/ext_st_easitc?format=JSON&stk_flow=BAL_RT"
                "&indic_et=TRD_VAL&partner=EXT_EA21&sitc06=TOTAL&sinceTimePeriod=2015-01"
            ),
        },
    ],
}

# --- Brent crude: FRED, needs API key --------------------------------------
BRENT = {
    "label": "Brent crude oil",
    "unit": "USD/barrel",
    "frequency": "monthly",
    "round": 2,
    "yoy_style": "pct",
    "series": [
        {
            "code": "BRENT",
            "label": "Brent crude, Europe (FRED MCOILBRENTEU)",
            "fred_series_id": "MCOILBRENTEU",
        },
    ],
}

INDICES_CONFIG = {
    "wood_ppi": WOOD_PPI,
    "pulp_paper": PULP_PAPER,
    "transport_sppi": TRANSPORT_SPPI,
    "trade_balance": TRADE_BALANCE,
    "brent": BRENT,
}

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def http_get(url: str) -> requests.Response:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp


# --------------------------------------------------------------------------
# Eurostat JSON-stat parsing (mirrors fetch_data.py's proven pattern)
# --------------------------------------------------------------------------

def parse_eurostat(payload: dict, label: str) -> list[dict]:
    """Decode a Eurostat JSON-stat response into [{date, value}, ...].

    Guards against Eurostat's habit of OMITTING missing periods from the
    `value` dict entirely (rather than sending null) -- a period whose index
    has no matching key in `value` is simply skipped, never KeyError'd.
    """
    dim_ids = payload.get("id")
    sizes = payload.get("size")
    if not dim_ids or not sizes or "time" not in dim_ids:
        raise RuntimeError(f"[{label}] Unexpected Eurostat response shape (no id/size/time).")

    non_time = [(d, s) for d, s in zip(dim_ids, sizes) if d != "time"]
    if any(s != 1 for _, s in non_time):
        raise RuntimeError(
            f"[{label}] Eurostat query is not selective enough -- dimensions {non_time} "
            "have size != 1. Check dimension codes in CONFIG."
        )

    time_index = payload["dimension"]["time"]["category"]["index"]
    values = payload.get("value", {})

    out = []
    for period, idx in time_index.items():
        v = values.get(str(idx))  # missing-period guard: absent key -> skip, not KeyError
        if v is not None:
            out.append({"date": period, "value": v})
    out.sort(key=lambda r: r["date"])
    if not out:
        raise RuntimeError(
            f"[{label}] Eurostat returned zero observations. Check geo/dimension codes -- "
            "this combination may not exist or may not be disseminated at this aggregate level."
        )
    return out


def fetch_eurostat_series(url: str, label: str) -> list[dict]:
    resp = http_get(url)
    payload = resp.json()
    return parse_eurostat(payload, label)


def fetch_fred_series(series_id: str, label: str) -> list[dict]:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise RuntimeError(
            f"[{label}] FRED_API_KEY environment variable is not set. Get a free key at "
            "https://fredaccount.stlouisfed.org/apikeys and export it locally, or set it as "
            "the 'FRED_API_KEY' GitHub Actions secret (see DEPLOY.md)."
        )
    url = f"{FRED_OBSERVATIONS_URL}?series_id={series_id}&api_key={api_key}&file_type=json&observation_start=2015-01-01"
    resp = http_get(url)
    payload = resp.json()
    if "error_code" in payload:
        # Deliberately do not include the request URL here -- it contains the API key.
        raise RuntimeError(f"[{label}] FRED API error {payload['error_code']}: {payload.get('error_message')}")

    out = []
    for obs in payload.get("observations", []):
        if obs.get("value") == ".":  # FRED's convention for a missing observation
            continue
        period = obs["date"][:7]  # YYYY-MM-DD -> YYYY-MM
        out.append({"date": period, "value": float(obs["value"])})
    out.sort(key=lambda r: r["date"])
    if not out:
        raise RuntimeError(f"[{label}] FRED returned no usable observations.")
    return out


# --------------------------------------------------------------------------
# YoY (date-matched, never by array position) + direction
# --------------------------------------------------------------------------

def _year_ago_period(period: str) -> str:
    if "-Q" in period:
        year, q = period.split("-Q")
        return f"{int(year) - 1}-Q{q}"
    year, month = period.split("-")
    return f"{int(year) - 1}-{month}"


def compute_yoy(history: list[dict], style: str) -> dict | None:
    """Match the latest point to the SAME calendar period one year earlier
    (never "N positions back" -- gaps in the series would silently misalign
    a position-based comparison). Returns None if that period isn't in the
    history (guards the same missing-period problem as the Eurostat parser).
    """
    if not history:
        return None
    latest = history[-1]
    by_date = {h["date"]: h["value"] for h in history}
    compare_date = _year_ago_period(latest["date"])
    compare_value = by_date.get(compare_date)
    if compare_value is None:
        return None

    if style == "level":
        delta = latest["value"] - compare_value
        direction = "up" if delta > 1e-9 else ("down" if delta < -1e-9 else "flat")
        return {"style": "level", "value": round(delta, 0), "compare_date": compare_date, "direction": direction}

    if compare_value == 0:
        return None
    pct = (latest["value"] - compare_value) / abs(compare_value) * 100
    direction = "up" if pct > 1e-9 else ("down" if pct < -1e-9 else "flat")
    return {"style": "pct", "value": round(pct, 1), "compare_date": compare_date, "direction": direction}


# --------------------------------------------------------------------------
# data.json (indices.json) load / merge
# --------------------------------------------------------------------------

def load_existing_indices() -> dict:
    if INDICES_JSON_PATH.exists():
        with open(INDICES_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"indices": {}}


def build_series(series_cfg: dict, round_to: int, yoy_style: str) -> dict:
    if "fred_series_id" in series_cfg:
        history = fetch_fred_series(series_cfg["fred_series_id"], series_cfg["label"])
    else:
        history = fetch_eurostat_series(series_cfg["url"], series_cfg["label"])

    rounded = [{"date": h["date"], "value": round(float(h["value"]), round_to)} for h in history]
    latest = rounded[-1]
    yoy = compute_yoy(rounded, yoy_style)

    return {
        "code": series_cfg["code"],
        "label": series_cfg["label"],
        "history": rounded,
        "latest": {"value": latest["value"], "date": latest["date"]},
        "yoy": yoy,
        "stale": False,  # corrected below, relative to sibling series in the same index
    }


def mark_staleness(series_list: list[dict]) -> None:
    """A series is stale if its latest date trails the freshest sibling in
    the same multi-line index -- e.g. pulp (C1711) stopped in 2022 while
    paper/corrugated/tissue keep updating monthly. Single-series indices
    have nothing to compare against, so they're never marked stale here.
    """
    if len(series_list) < 2:
        return
    freshest = max(s["latest"]["date"] for s in series_list)
    for s in series_list:
        s["stale"] = s["latest"]["date"] < freshest


def main() -> None:
    data = load_existing_indices()
    existing_indices = data.get("indices", {})
    new_indices = {}

    for key, cfg in INDICES_CONFIG.items():
        print(f"Fetching {key} ({cfg['label']}) ...")
        series_list = [
            build_series(s_cfg, cfg["round"], cfg["yoy_style"])
            for s_cfg in cfg["series"]
        ]
        mark_staleness(series_list)

        for s in series_list:
            yoy_str = f"{s['yoy']['value']:+}{'%' if s['yoy']['style'] == 'pct' else ' ' + cfg['unit']}" if s["yoy"] else "n/a"
            stale_str = " [STALE]" if s["stale"] else ""
            print(f"  -> {s['code']}: {s['latest']['value']} ({s['latest']['date']}) YoY {yoy_str}{stale_str}")

        existing_note = existing_indices.get(key, {}).get("note")
        new_indices[key] = {
            "label": cfg["label"],
            "unit": cfg["unit"],
            "frequency": cfg["frequency"],
            "series": series_list,
            "note": existing_note,
        }

    data["indices"] = new_indices
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(INDICES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nWrote {INDICES_JSON_PATH} (last_updated={data['last_updated']})")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - top-level: fail loudly with full context
        print("\nfetch_indices.py FAILED:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
