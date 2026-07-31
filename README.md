# Euro area — Macro dashboard

A self-updating executive dashboard of Euro area macroeconomic indicators for
pulp & paper business planning: GDP growth, inflation (HICP), the ECB policy
rate, EUR/USD, TTF gas prices, manufacturing PMI, and a qualitative trade
policy risk note.

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
| Trade policy risks | Manual, qualitative only (no API) | Edit `note` in `data.json` directly |

Geography is `EA20` (Euro area, 20 members) by default — change the `GEO`
constant at the top of `fetch_data.py` to retarget.

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
| `data.json` | Generated data store. Do not hand-edit numbers — edit `manual_overrides.json` or `note` fields instead. |
| `manual_overrides.json` | User-editable fallback values for TTF gas price & PMI. See [DEPLOY.md](DEPLOY.md). |
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
