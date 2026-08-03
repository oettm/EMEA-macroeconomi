# Euro area — Macro dashboard

A self-updating executive dashboard of Euro area macroeconomic indicators for
pulp & paper business planning: GDP growth, inflation (HICP), the ECB policy
rate, EUR/USD, TTF gas prices, manufacturing PMI, and a qualitative trade
policy risk note.

The site has two pages, linked to each other in-page: this macro dashboard
(`index.html`) and a second, separate **[Industry Indices](#industry-indices-second-page)**
page (`indices.html`) covering wood/pulp/paper producer prices, transport
prices, the euro area trade balance, and Brent crude. They share the same
design and the same GitHub Pages deployment, but run on independent data
files and independent update pipelines — a failure in one never affects the
other.

**Live dashboard:** see [DEPLOY.md](DEPLOY.md) for the GitHub Pages URL once enabled.

## How it works

```
fetch_data.py  ──writes──▶  data.json  ──read by──▶  index.html  ──served by──▶  GitHub Pages
      ▲
      │ runs monthly via
.github/workflows/update.yml (cron + manual dispatch)
```

- **`fetch_data.py`** pulls the latest values from official free APIs
  (Eurostat SDMX, ECB Data Portal) for 4 of the 7 indicators, applies a
  best-effort → manual override → carry-forward fallback chain for the 2
  indicators with no free official API (TTF gas, PMI), and writes the result
  to `data.json`.
- **`data.json`** is the single source of truth the dashboard reads. It's
  committed to the repo, so `index.html` never talks to any API directly —
  it just fetches this static file.
- **`index.html`** is a self-contained page (inline CSS/JS, Chart.js from a
  CDN for the trend charts) that renders the scorecard and trend charts from
  `data.json`. If the CDN is unreachable the scorecard (with dependency-free
  inline sparklines) still renders fully; only the larger trend-chart section
  degrades.
- **GitHub Actions** (`.github/workflows/update.yml`) runs `fetch_data.py` on
  a monthly cron, and commits `data.json` only if it actually changed.
- **GitHub Pages** serves `index.html` at a stable URL, so colleagues always
  see the latest committed data with zero manual work.

## Indicators & sources

| Indicator | Source | Automation |
|---|---|---|
| GDP growth (QoQ %) | Eurostat `namq_10_gdp` | Fully automated |
| CPI / HICP (YoY %) | Eurostat `prc_hicp_manr` | Fully automated |
| ECB main refinancing rate (%) | ECB Data Portal `FM` dataflow | Fully automated |
| EUR/USD | ECB Data Portal `EXR` dataflow | Fully automated |
| TTF gas price (€/MWh) | Best-effort (Yahoo Finance `TTF=F`) → `manual_overrides.json` → carry-forward | **Needs occasional manual attention** |
| Manufacturing PMI | Best-effort (scraped) → `manual_overrides.json` → carry-forward | **Needs occasional manual attention** |
| Trade policy risks | Manual, qualitative only (no API) | Edit `notes.trade_policy_risks` in `manual_overrides.json` |

Geography is `EA20` (Euro area, 20 members) by default — change the `GEO`
constant at the top of `fetch_data.py` to retarget.

Every indicator also carries a short editorial `note` (the "driver" comment
shown when a card is expanded), sourced from `manual_overrides.json`'s
`notes` object — see [DEPLOY.md](DEPLOY.md) §5b.

## Local usage

```bash
pip install -r requirements.txt
python3 fetch_data.py          # updates data.json in place
python3 -m http.server 8000    # serve locally (fetch() needs http://, not file://)
# open http://localhost:8000
```

## Files

| File | Purpose |
|---|---|
| `fetch_data.py` | Fetches data, writes `data.json`. CONFIG block at the top. |
| `data.json` | Generated data store. Never hand-edit — everything in it is derived from the APIs plus `manual_overrides.json`. |
| `manual_overrides.json` | User-editable: fallback values for TTF gas price & PMI, plus the `notes` object (the "driver" commentary on every card, and the full text of the qualitative trade-policy-risks card). See [DEPLOY.md](DEPLOY.md). |
| `index.html` | The dashboard itself. |
| `.github/workflows/update.yml` | Monthly cron + manual-dispatch pipeline. |
| `requirements.txt` | Python dependency (just `requests`). |
| `DEPLOY.md` | How to enable Pages, and the recurring manual-override task. |

## Why TTF and PMI need a fallback chain

There is no free, keyless, official API for Dutch TTF gas futures or for
S&P Global's Manufacturing PMI. `fetch_data.py` tries a best-effort automated
fetch for each (an unofficial Yahoo Finance quote for TTF, a scrape of a
public page for PMI); either can break silently if the source changes shape.
When that happens, the script falls back to `manual_overrides.json`, and if
that's also unset, it carries the previous value forward and marks it
`"stale": true` in `data.json` — the dashboard always renders, it just tells
you the number may be a period old.

---

## Industry Indices (second page)

A second, self-contained executive page — `indices.html` — covering the
industry/commodity indicators most relevant to a pulp & paper business:
wood producer prices, pulp/paper/packaging/tissue producer prices, road/land
transport & warehousing prices, the euro area's trade balance, and Brent
crude oil. Fully independent of the macro dashboard above: separate script,
separate data file, separate workflow — nothing here can break `data.json`
or `index.html`, and vice versa.

```
fetch_indices.py  ──writes──▶  indices.json  ──read by──▶  indices.html  ──served by──▶  GitHub Pages
      ▲
      │ runs monthly via
.github/workflows/update_indices.yml (cron + manual dispatch)
```

- **`fetch_indices.py`** pulls every series from official free APIs
  (Eurostat SDMX for the 4 index-based indicators, FRED for Brent) and writes
  `indices.json`. Unlike the macro dashboard's TTF/PMI, there is no manual
  fallback chain here — every source is a reliable free API, so an empty
  result is treated as a real failure and the script exits loudly rather than
  writing partial data.
- **`indices.json`** is the single source of truth `indices.html` reads —
  same "static file, no live API calls from the browser" pattern as
  `data.json`.
- **`indices.html`** renders NACE legend + per-index cards (value, YoY,
  direction, structural drivers, optional note) + Chart.js line charts. Same
  CDN-degradation guarantee as the macro dashboard: if Chart.js fails to
  load, every card still shows its current figures, drivers and the NACE
  legend — only the line charts themselves are replaced with a plain-text
  notice.
- **GitHub Actions** (`.github/workflows/update_indices.yml`) runs on a
  monthly cron (offset 30 minutes from the macro workflow so the two never
  race on the same commit) and commits `indices.json` only if it changed.

### Indices & sources

| Index | Series | Source | Frequency |
|---|---|---|---|
| Wood PPI | C16 | Eurostat `sts_inpp_m` | Monthly |
| Pulp & Paper | C1711, C1712, C1721, C1722 | Eurostat `sts_inpp_m` | Monthly |
| Transport SPPI | H49, H52 | Eurostat `sts_sepp_q` | Quarterly |
| Euro area trade balance | Extra-EA21 balance, all goods | Eurostat `ext_st_easitc` | Monthly |
| Brent crude oil | MCOILBRENTEU | FRED | Monthly |

Two things worth knowing before you trust these numbers at a glance:

- **C1711 (pulp) goes stale on purpose.** Eurostat's EA20 aggregate for pulp
  producer prices stopped being published in 2022-06 — verified live against
  the API, not a bug. The chart still shows its real history up to that
  point and the card is flagged "no update since 2022-06"; the other 3
  pulp/paper series keep updating normally.
- **H494 (road freight) doesn't exist as an EA aggregate at all** — confirmed
  empty at every unit/geo combination tried. The Transport SPPI chart uses
  **H49** (the broader "land transport & transport via pipelines" parent
  category, which road freight dominates by volume) as the closest available
  proxy instead, exactly as H492 (rail) already had to be excluded per the
  original brief. Both substitutions are called out in the NACE legend on
  the page itself, not just here.

### FRED API key

Brent crude comes from the FRED API, which requires a free key. It's read
from the `FRED_API_KEY` environment variable and is **never hardcoded** —
see [DEPLOY.md](DEPLOY.md) for how to get one and set it as a GitHub secret.

### Files (Industry Indices)

| File | Purpose |
|---|---|
| `fetch_indices.py` | Fetches data, writes `indices.json`. CONFIG block at the top. |
| `indices.json` | Generated data store for this page only. The one hand-editable field is each index's `note` — the script preserves it across runs. |
| `indices.html` | The Industry Indices page itself. |
| `.github/workflows/update_indices.yml` | Monthly cron + manual-dispatch pipeline, separate from the macro dashboard's. |
