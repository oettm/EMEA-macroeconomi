#!/usr/bin/env python3
"""
Fetches Euro area industry/commodity indicators relevant to a pulp & paper
business and writes indices.json for the "Industry Indices" page
(indices.html). Fully separate from fetch_data.py / data.json (the
macroeconomic dashboard) -- same auto-update philosophy, own data file.

All 5 indices here are backed by official, free APIs (Eurostat SDMX-JSON for
the 4 index-based ones, FRED -- or a keyless quote -- for Brent). There is no
manual-override file: nothing here is ever hand-edited.

Failure model: a broken source degrades ONE series, never the whole file.
Each series is fetched independently; if its fetch fails (source down, wrong
dimension code, a narrow NACE aggregate that Eurostat stopped disseminating),
its last known history is carried forward and flagged "stale": true -- which
indices.html renders as a visible "No update since <date>" badge -- while
every other series updates normally. Only a run in which NOTHING could be
produced fails outright. A series is also flagged stale when it simply trails
its siblings in the same multi-line chart.
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
            "label": "Wood & Wood Products",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C16&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
    ],
}

# --- Paper & paperboard: 3 lines on one chart, same base (2021=100) -------
# NOTE C1711 (pulp) is deliberately NOT included here: verified live against
# the API, Eurostat has not published an EA20 aggregate for it since 2022-06
# (not a wrong dimension code -- the source genuinely stopped). A line frozen
# since 2022 isn't useful for monthly tracking, so it was dropped rather than
# charted stale; it's still explained (and flagged) in the NACE legend for
# context, since pulp remains core business vocabulary even though it's not
# charted on this page.
PULP_PAPER = {
    "label": "Paper & Paperboard",
    "unit": "index (2021=100)",
    "frequency": "monthly",
    "round": 1,
    "yoy_style": "pct",
    "series": [
        {
            "code": "C1712",
            "label": "Paper & Paperboard",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1712&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
        {
            "code": "C1721",
            "label": "Corrugated & Containers",
            "url": (
                f"{EUROSTAT_BASE}/sts_inpp_m?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=C1721&s_adj=NSA&unit=I21&sinceTimePeriod=2015-01"
            ),
        },
        {
            "code": "C1722",
            "label": "Household & Sanitary (Tissue)",
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
            "label": "Land Transport & Pipelines",
            "url": (
                f"{EUROSTAT_BASE}/sts_sepp_q?format=JSON&geo={GEO}&indic_bt=PRC_PRR"
                "&nace_r2=H49&s_adj=NSA&unit=I21&sinceTimePeriod=2015-Q1"
            ),
        },
        {
            "code": "H52",
            "label": "Warehousing & Support Activities",
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
            "label": "Trade Balance",
            "url": (
                f"{EUROSTAT_BASE}/ext_st_easitc?format=JSON&stk_flow=BAL_RT"
                "&indic_et=TRD_VAL&partner=EXT_EA21&sitc06=TOTAL&sinceTimePeriod=2015-01"
            ),
        },
    ],
}

# --- Brent crude: FRED when a key is set, keyless quote otherwise ----------
# MCOILBRENTEU (FRED) is the official Europe Brent spot monthly average, but
# FRED requires an API key. So that this pipeline stays hands-off even with
# no key configured, the fallback is the same unofficial Yahoo chart endpoint
# the macro dashboard already uses for TTF, on the front-month Brent futures
# contract (BZ=F). Front-month futures track spot within a small basis --
# immaterial at this chart's resolution -- and whenever a FRED key IS present
# its values overwrite the overlapping months, so adding the key later
# silently self-corrects the history.
BRENT = {
    "label": "Brent crude oil",
    "unit": "USD/barrel",
    "frequency": "monthly",
    "round": 2,
    "yoy_style": "pct",
    "series": [
        {
            "code": "BRENT",
            "label": "Brent Crude Oil",
            "fred_series_id": "MCOILBRENTEU",
            "yahoo_symbol": "BZ=F",
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
# Daily, not monthly, bars: FRED's MCOILBRENTEU is a monthly AVERAGE of daily
# spot prices, so averaging daily closes ourselves keeps the fallback on the
# same methodology. Yahoo's own 1mo bars are month-END closes, which in a
# volatile month differ from the monthly average by >10% -- enough to put a
# visible false step in the chart at the point the two sources meet.
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=2y&interval=1d"


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


def fetch_yahoo_monthly_average(symbol: str, label: str) -> list[dict]:
    """Monthly averages of daily closes from Yahoo's unofficial chart endpoint
    (no key needed). The current month is a month-to-date average."""
    resp = http_get(YAHOO_CHART_URL.format(symbol=symbol))
    payload = resp.json()
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]

    sums: "OrderedDict[str, list]" = OrderedDict()
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        month = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m")
        bucket = sums.setdefault(month, [0.0, 0])
        bucket[0] += close
        bucket[1] += 1
    if not sums:
        raise RuntimeError(f"[{label}] Yahoo returned no usable closes for {symbol}.")
    return [{"date": m, "value": total / n} for m, (total, n) in sums.items()]


def fetch_brent(series_cfg: dict, label: str) -> tuple[list[dict], bool]:
    """Returns (history, authoritative). FRED is authoritative -- its values
    replace whatever is stored. The keyless futures quote is an approximation
    of the same series (front-month futures vs spot), so it is NOT allowed to
    rewrite months FRED already published; it only extends the series forward.
    Adding a FRED key later therefore back-fills the approximated months with
    official values on the next run."""
    if os.environ.get("FRED_API_KEY"):
        try:
            return fetch_fred_series(series_cfg["fred_series_id"], label), True
        except Exception as exc:  # noqa: BLE001 - fall through to the keyless source
            warn(f"[{label}] FRED fetch failed ({exc.__class__.__name__}: {exc}) -- "
                 f"falling back to the keyless {series_cfg['yahoo_symbol']} quote.")
    else:
        print(f"  [{label}] no FRED_API_KEY set -- using the keyless "
              f"{series_cfg['yahoo_symbol']} quote (fills new months only).")
    return fetch_yahoo_monthly_average(series_cfg["yahoo_symbol"], label), False


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


def build_series(series_cfg: dict, round_to: int, yoy_style: str,
                 existing_series: dict | None = None) -> dict:
    if "fred_series_id" in series_cfg:
        history, authoritative = fetch_brent(series_cfg, series_cfg["label"])
    else:
        history, authoritative = fetch_eurostat_series(series_cfg["url"], series_cfg["label"]), True

    # Merge onto the history already on disk instead of replacing it: sources
    # sometimes shorten their published window (the keyless Brent quote covers
    # 2 years, FRED a decade), which would silently truncate the chart. An
    # authoritative source overwrites overlapping periods so official
    # revisions propagate; an approximated one only fills gaps. Neither can
    # absorb a source rebasing its index (2021=100 -> a later base): that
    # needs the stored history dropped so it refetches clean.
    stored = {h["date"]: h["value"] for h in (existing_series or {}).get("history", [])}
    merged = dict(stored)
    for h in history:
        if authoritative or h["date"] not in stored:
            merged[h["date"]] = h["value"]

    rounded = [{"date": d, "value": round(float(v), round_to)} for d, v in sorted(merged.items())]
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


def carry_forward_series(series_cfg: dict, existing_series: dict | None,
                         round_to: int, yoy_style: str) -> dict | None:
    """Last known history for a series whose fetch just failed, flagged stale.
    Returns None when there's nothing on disk to carry forward."""
    history = [{"date": h["date"], "value": h["value"]}
               for h in (existing_series or {}).get("history", [])]
    if not history:
        return None
    latest = history[-1]
    return {
        "code": series_cfg["code"],
        "label": series_cfg["label"],
        "history": history,
        "latest": {"value": latest["value"], "date": latest["date"]},
        "yoy": compute_yoy(history, yoy_style),
        "stale": True,
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
    degraded: list[str] = []

    for key, cfg in INDICES_CONFIG.items():
        print(f"Fetching {key} ({cfg['label']}) ...")
        existing_by_code = {s.get("code"): s
                            for s in existing_indices.get(key, {}).get("series", [])}
        series_list = []
        carried_codes = set()

        for s_cfg in cfg["series"]:
            existing_series = existing_by_code.get(s_cfg["code"])
            try:
                series_list.append(
                    build_series(s_cfg, cfg["round"], cfg["yoy_style"], existing_series)
                )
            except Exception as exc:  # noqa: BLE001 - one dead source, not a dead run
                carried = carry_forward_series(s_cfg, existing_series, cfg["round"], cfg["yoy_style"])
                degraded.append(f"{key}/{s_cfg['code']}")
                if carried is None:
                    warn(f"[{key}/{s_cfg['code']}] fetch failed ({exc.__class__.__name__}: {exc}) "
                         f"and there is no stored history to carry forward -- dropping the series.")
                    continue
                warn(f"[{key}/{s_cfg['code']}] fetch failed ({exc.__class__.__name__}: {exc}) -- "
                     f"carrying forward {carried['latest']['date']} as stale.")
                carried_codes.add(s_cfg["code"])
                series_list.append(carried)

        if not series_list:
            # Nothing fetched and nothing to carry forward: leave whatever the
            # file already had for this index rather than deleting the block.
            warn(f"[{key}] no series could be produced -- leaving the previous block untouched.")
            if key in existing_indices:
                new_indices[key] = existing_indices[key]
            continue

        mark_staleness(series_list)
        for s in series_list:
            if s["code"] in carried_codes:
                s["stale"] = True  # mark_staleness only compares siblings

        for s in series_list:
            yoy_str = f"{s['yoy']['value']:+}{'%' if s['yoy']['style'] == 'pct' else ' ' + cfg['unit']}" if s["yoy"] else "n/a"
            stale_str = " [STALE]" if s["stale"] else ""
            print(f"  -> {s['code']} ({s['label']}): {s['latest']['value']} ({s['latest']['date']}) "
                  f"YoY {yoy_str}{stale_str}")

        new_indices[key] = {
            "label": cfg["label"],
            "unit": cfg["unit"],
            "frequency": cfg["frequency"],
            "series": series_list,
        }

    if not new_indices:
        raise RuntimeError(
            "No index could be built and there was nothing to carry forward -- "
            "refusing to overwrite indices.json with an empty file."
        )

    data["indices"] = new_indices
    data["last_updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    with open(INDICES_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nWrote {INDICES_JSON_PATH} (last_updated={data['last_updated']})")
    if degraded:
        # Deliberately not a non-zero exit: the file was written and every
        # healthy series updated, so failing the run here would turn a
        # long-dead narrow Eurostat aggregate into a permanently red workflow
        # that everyone learns to ignore. The ::warning:: annotations above
        # and the "No update since" badge on the page carry the signal.
        print(f"Degraded series this run ({len(degraded)}): {', '.join(degraded)}")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - top-level: fail loudly with full context
        print("\nfetch_indices.py FAILED:", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
