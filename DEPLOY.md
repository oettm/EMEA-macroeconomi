# Deploy

## 1. Push this repo to GitHub

```bash
git remote add origin git@github.com:<your-org>/euro-macro-dashboard.git
git push -u origin main
```

## 2. Enable GitHub Pages

1. On GitHub: **Settings → Pages**.
2. Under **Build and deployment → Source**, choose **Deploy from a branch**.
3. Branch: `main`, folder: `/ (root)`. Save.
4. Wait ~1 minute. GitHub shows the live URL at the top of the Pages settings
   page, of the form:

   ```
   https://<your-org>.github.io/<repo-name>/
   ```

   That URL is stable — it doesn't change on future updates. Bookmark/share
   this with colleagues; it will always show whatever is in `data.json` on
   the `main` branch.

## 3. Confirm Actions has write permission

The workflow needs to `git push` its own commits back to the repo:

1. **Settings → Actions → General → Workflow permissions**.
2. Select **Read and write permissions**. Save.

(No secrets or tokens are needed — all 4 automated data sources are public,
keyless APIs, and the commit uses the built-in `GITHUB_TOKEN`.)

## 4. How the monthly auto-update works

`.github/workflows/update.yml` runs on the 3rd of every month at 06:00 UTC
(a few days after most monthly releases land) and can also be triggered
manually from the **Actions** tab (**Run workflow**). Each run:

1. Installs Python + `requests`.
2. Runs `fetch_data.py`, which re-fetches full history for GDP, CPI, the ECB
   rate, and EUR/USD, and refreshes TTF/PMI through their fallback chain.
3. Commits `data.json` **only if it changed** (a no-op month makes no commit,
   so there's no commit noise).

GitHub Pages then serves the updated `index.html` (unchanged) reading the
updated `data.json` — nothing else to do.

To change the schedule, edit the `cron` line in
`.github/workflows/update.yml` (cron is UTC).

## 5. The recurring manual task: `manual_overrides.json` (~5 min, roughly quarterly)

Everything in this file is picked up automatically on the next run (scheduled
or manual **Run workflow**) — you never touch `data.json` by hand.

### 5a. Fallback values for TTF gas price & Manufacturing PMI

These 2 indicators have **no free official API**. `fetch_data.py` first tries
a best-effort automated fetch for both; that fetch can silently start failing
if the underlying (unofficial) source changes its page/response shape. When
it does, the pipeline falls back to the `value`/`date` below, and if those
are stale too, it carries the last known value forward and flags it
`"stale": true` on the dashboard — so nothing breaks, but the number can go
quietly out of date if you never look at it.

**Roughly once a quarter** (or whenever you notice a "as of last period" tag
on TTF or PMI in the dashboard), update:

```json
{
  "energy_prices": { "value": 43.2, "date": "2026-06" },
  "manufacturing_pmi": { "value": 51.6, "date": "2026-05" }
}
```

- `value`: the latest published figure.
- `date`: `"YYYY-MM"`, the month the figure refers to (not today's date).

Where to find current figures:
- **TTF gas (€/MWh):** ICE endex or EEX front-month Dutch TTF quote (financial
  press, e.g. Reuters/Bloomberg, or your energy desk/broker).
- **Manufacturing PMI:** the monthly HCOB/S&P Global Eurozone Manufacturing
  PMI press release (released ~1st business day of the month, on
  spglobal.com/marketintelligence or via press coverage).

### 5b. Editorial notes — the "driver" commentary on every card

The `notes` object holds the one-line commentary shown when a scorecard card
is expanded: a short "what's driving this number" line for the 6 numeric
indicators, and the full executive summary for the qualitative
`trade_policy_risks` card (this is the *only* place that indicator's content
comes from — there's no API for it).

```json
"notes": {
  "gdp_growth": "...",
  "cpi": "...",
  "interest_rates": "...",
  "energy_prices": "...",
  "currency": "...",
  "manufacturing_pmi": "...",
  "trade_policy_risks": "..."
}
```

Whatever text is here always wins on the next run — just overwrite it to
update. For `trade_policy_risks`, the recommended source is the ECB's own
quarterly **Economic Bulletin** risk assessment
(https://www.ecb.europa.eu/press/economic-bulletin) — summarize the growth
and inflation risk sections in 3-6 sentences, board-level tone, and note the
Bulletin issue/date at the start so readers can judge how fresh it is. For
the 6 numeric indicators, a line or two on the main macro driver (energy
prices, ECB policy stance, trade tensions, etc.) is enough — these don't need
to change every quarter, only when the underlying story shifts materially.

### Applying changes

Commit and push the change (or open a PR) — the next scheduled run, or a
manual **Run workflow** click, will pick it up. `fetch_data.py` merges
`manual_overrides.json` in and computes `latest`/`previous`/history/notes
automatically; `data.json` itself should never be hand-edited.
