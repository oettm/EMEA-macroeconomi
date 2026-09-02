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
      │ runs weekly via
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
  a weekly cron, and commits `data.json` only if it actually changed. Weekly
  rather than monthly even though the releases are monthly: GitHub's shared
  scheduler is best-effort and can delay or drop a run, and a weekly cadence
  means a missed run costs days instead of a whole month.
- **GitHub Pages** serves `index.html` at a stable URL, so colleagues always
  see the latest committed data with zero manual work.

## Indicators & sources

| Indicator | Source | Automation |
|---|---|---|
| GDP growth (QoQ %) | Eurostat `namq_10_gdp` | Fully automated |
| CPI / HICP (YoY %) | Eurostat `prc_hicp_manr` | Fully automated |
| Unemployment rate (%) | Eurostat `une_rt_m` (**EA21**, see below) | Fully automated |
| ECB main refinancing rate (%) | ECB Data Portal `FM` dataflow | Fully automated |
| EUR/USD | ECB Data Portal `EXR` dataflow | Fully automated |
| TTF gas price (€/MWh) | Best-effort (Yahoo Finance `TTF=F`) → `manual_overrides.json` → carry-forward | **Needs occasional manual attention** |
| Manufacturing PMI | Best-effort (scraped) → `manual_overrides.json` → carry-forward | **Needs occasional manual attention** |
| Trade policy risks | Best-effort (Claude + web search) → `manual_overrides.json` → carry-forward | Fully automated if `ANTHROPIC_API_KEY` is set; otherwise edit `notes.trade_policy_risks` in `manual_overrides.json` |

Geography is `EA20` (Euro area, 20 members) by default — change the `GEO`
constant at the top of `fetch_data.py` to retarget.

**One deliberate exception: unemployment is `EA21`.** Eurostat does not
disseminate a monthly EA20 unemployment aggregate at all — `une_rt_m`
publishes only EA21 (the euro area since Bulgaria adopted the euro in January
2026) and EU27_2020 — so that card covers one country more than the others.
The difference is immaterial at this resolution (Bulgaria is ~0.5% of euro
area GDP), and the page discloses it where it matters rather than in a
footnote: an `EA21` chip on the card itself, plus a line in the card note and
the page footer. `fetch_data.py` writes that chip automatically for any
indicator whose `geo` differs from `GEO`, so this stays honest if another
series ever has to move too.

### Forward-looking projections (GDP & unemployment)

Those two cards also carry the official **Eurosystem staff projection** for
the current and next calendar year, from the ECB Data Portal's Macroeconomic
Projection Database (`MPD` dataflow — free, keyless, same API family as the
MRO rate and EUR/USD). GDP uses `YER` (real GDP, annual growth rate),
unemployment uses `URX` (unemployment rate, percentage).

The projection *round* (March / June / September / December) is **not**
hardcoded: the query leaves the round dimension blank, fetches every round,
and picks the newest one that actually covers the years being displayed — so
a new round appears on the dashboard the week the ECB publishes it, with no
code change. If the fetch fails, the stored projection is kept and flagged
`"not refreshed"` on the card rather than blanked.

Note the GDP card mixes two measures on purpose: the headline number is
**quarterly** growth (the freshest read on momentum) while the projection is
**annual** — which is why the projection line is labelled `ANNUAL`.

Every indicator also carries a short editorial `note` (the "driver" comment
shown when a card is expanded), sourced from `manual_overrides.json`'s
`notes` object — see [DEPLOY.md](DEPLOY.md) §5b. For `trade_policy_risks`
specifically, that note is generated automatically each run: `fetch_data.py`
asks Claude (with its web-search tool) to research current European trade
and geopolitical news and write a short executive summary, and only falls
back to the manual note if that call fails or `ANTHROPIC_API_KEY` isn't set
— see [DEPLOY.md](DEPLOY.md) §6.

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
| `.github/workflows/update.yml` | Weekly cron + manual-dispatch pipeline. |
| `.github/workflows/keepalive.yml` | Pushes an empty commit if the repo goes quiet for 40+ days, so GitHub never auto-disables the scheduled workflows. |
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
      │ runs weekly via
.github/workflows/update_indices.yml (cron + manual dispatch)
```

- **`fetch_indices.py`** pulls every series from official free APIs
  (Eurostat SDMX for the 4 index-based indicators, FRED — or a keyless quote
  — for Brent) and writes `indices.json`. There is no manual override file
  here: nothing on this page is ever hand-edited. A source that breaks
  degrades only its own series, which keeps its last known history flagged
  `"stale": true` (rendered as a "No update since …" badge) while every other
  series updates normally; only a run that can produce nothing at all fails.
- **`indices.json`** is the single source of truth `indices.html` reads —
  same "static file, no live API calls from the browser" pattern as
  `data.json`.
- **`indices.html`** renders one click-to-expand card per index (mirroring
  the macro dashboard's card interaction), plus a compact legend at the very
  bottom of the page. Collapsed, a card shows each series by **name** (not
  NACE code), its latest value, and YoY change. Clicking it reveals the
  Chart.js line chart and the static structural-drivers box (what
  structurally pushes the index up or down — not live commentary). Charts
  render lazily on first expand (cheaper initial load); a `beforeprint`
  handler force-expands and renders every chart first, so a PDF export is
  never missing a chart just because no one clicked that card. Same
  CDN-degradation guarantee as the macro dashboard: if Chart.js fails to
  load, every card still shows its current figures and drivers — only the
  line charts themselves are replaced with a plain-text notice. The legend
  at the bottom is plain static HTML — six rows, one per index, purely
  descriptive; it never fetches or renders from `indices.json`.
- **GitHub Actions** (`.github/workflows/update_indices.yml`) runs on a
  weekly cron (offset 30 minutes from the macro workflow so the two don't
  normally race on the same branch — and both push with a rebase and retry,
  for the times GitHub's scheduler delays one into the other) and commits
  `indices.json` only if it changed.

### Indices & sources

| Index | Series | Source | Frequency |
|---|---|---|---|
| Wood PPI | C16 | Eurostat `sts_inpp_m` | Monthly |
| Paper & Paperboard | C1712, C1721, C1722 | Eurostat `sts_inpp_m` | Monthly |
| Transport SPPI | H49, H52 | Eurostat `sts_sepp_q` | Quarterly |
| Euro area trade balance | Extra-EA21 balance, all goods | Eurostat `ext_st_easitc` | Monthly |
| Brent crude oil | MCOILBRENTEU (FRED) or BZ=F front-month (keyless fallback) | FRED / Yahoo | Monthly |

Two things worth knowing before you trust these numbers at a glance:

- **C1711 (pulp) is deliberately not charted.** Eurostat's EA20 aggregate for
  pulp producer prices stopped being published in 2022-06 — verified live
  against the API, not a bug. A line frozen since 2022 isn't useful for
  monthly tracking, so it was dropped from the chart (originally "Pulp &
  Paper", now "Paper & Paperboard") rather than shown stale for years. The
  on-page legend was deliberately trimmed to 6 entries (see below) and no
  longer documents this — this README is the reference for it.
- **H494 (road freight) doesn't exist as an EA aggregate at all** — confirmed
  empty at every unit/geo combination tried. The Transport SPPI chart uses
  **H49** (the broader "land transport & transport via pipelines" parent
  category, which road freight dominates by volume) as the closest available
  proxy instead, exactly as H492 (rail) already had to be excluded per the
  original brief. Transport isn't part of the on-page legend's 6 entries
  either — again, this README is the reference for the substitution.

### FRED API key (optional)

Brent crude prefers FRED's official Europe Brent spot series, which requires
a free key, read from the `FRED_API_KEY` environment variable and **never
hardcoded** — see [DEPLOY.md](DEPLOY.md) for how to get one and set it as a
GitHub secret.

Without a key the pipeline still runs: it falls back to the front-month Brent
futures contract (BZ=F) from the same unofficial Yahoo endpoint the macro
dashboard uses for TTF, averaging daily closes per month to stay on FRED's
monthly-average methodology rather than its month-end close. Because that's
an approximation of a different-but-adjacent series, it is only allowed to
**extend** the history forward — it never rewrites a month FRED published.
Add the key later and the next run silently back-fills those months with the
official values.

### Files (Industry Indices)

| File | Purpose |
|---|---|
| `fetch_indices.py` | Fetches data, writes `indices.json`. CONFIG block at the top. |
| `indices.json` | Generated data store for this page only. Fully generated — never hand-edited, no manual fields. |
| `indices.html` | The Industry Indices page itself. |
| `.github/workflows/update_indices.yml` | Weekly cron + manual-dispatch pipeline, separate from the macro dashboard's. |
