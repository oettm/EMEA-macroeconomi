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

(No secrets are strictly required: every data source is either a public
keyless API or has a keyless fallback, and the commit uses the built-in
`GITHUB_TOKEN`. Two optional secrets improve things — `ANTHROPIC_API_KEY`
for the auto-written trade-policy summary, `FRED_API_KEY` for the official
Brent series — and the pipeline runs without either.)

## 4. How the weekly auto-update works

`.github/workflows/update.yml` runs every Monday at 06:17 UTC and can also be
triggered manually from the **Actions** tab (**Run workflow**). Each run:

1. Installs Python + `requests`.
2. Runs `fetch_data.py`, which re-fetches full history for GDP, CPI,
   unemployment, the ECB rate, and EUR/USD, refreshes TTF/PMI through their
   fallback chain, and picks up the latest Eurosystem staff projection round
   for the GDP and unemployment cards.
3. Commits `data.json` **only if it changed** (a no-op week makes no commit,
   so there's no commit noise).

GitHub Pages then serves the updated `index.html` (unchanged) reading the
updated `data.json` — nothing else to do.

Weekly, even though every underlying release is monthly or quarterly: GitHub's
cron is best-effort — runs get delayed by hours at busy times and can be
dropped entirely — so a weekly cadence means a lost run costs days rather
than a full month. The runs that find nothing new cost nothing, since they
end without a commit.

Three things keep the schedule from quietly dying:

- **A broken source no longer fails the run, but it does say so.** Each
  indicator is fetched independently: if one source breaks, its last known
  history is carried forward flagged `"stale": true` (the card shows a stale
  badge) and every other indicator still updates. Every fallback that leaves
  a figure or a note un-refreshed also prints a ⚠ annotation on the run
  summary in the Actions tab — including an `ANTHROPIC_API_KEY` that is set
  but rejected, which would otherwise freeze the trade-policy note
  indefinitely with no visible sign.
- **Pushes are rebased and retried.** The two data workflows share a branch,
  and a delayed run can overlap the other; without the rebase the second push
  is rejected for a conflict that doesn't really exist.
- **`.github/workflows/keepalive.yml`** guards the 60-day rule below.

To change the schedule, edit the `cron` line in
`.github/workflows/update.yml` (cron is UTC). Avoid on-the-hour times —
they're the most contended slot on GitHub's scheduler.

### GitHub disables scheduled workflows after 60 days of inactivity

That's a platform rule, and it's silent: no commits or pushes to the repo for
60 days and every cron in it stops, dashboard included. In normal operation
the weekly runs supply that activity themselves, but they commit only when
data actually changed. `keepalive.yml` closes the gap: it checks every
Thursday how old the last commit is and pushes an empty commit once the repo
has been quiet for 40+ days. Nothing to maintain — it does nothing at all
while the repo is active.

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
is expanded: a short "what's driving this number" line for the 7 numeric
indicators, and the full executive summary for the qualitative
`trade_policy_risks` card. As of §6 below, `trade_policy_risks` is normally
generated automatically each run — the entry here only matters as its
fallback (`ANTHROPIC_API_KEY` unset, or the call fails).

```json
"notes": {
  "gdp_growth": "...",
  "cpi": "...",
  "unemployment": "...",
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
the 7 numeric indicators, a line or two on the main macro driver (energy
prices, ECB policy stance, trade tensions, etc.) is enough — these don't need
to change every quarter, only when the underlying story shifts materially.

### Applying changes

Commit and push the change (or open a PR) — the next scheduled run, or a
manual **Run workflow** click, will pick it up. `fetch_data.py` merges
`manual_overrides.json` in and computes `latest`/`previous`/history/notes
automatically; `data.json` itself should never be hand-edited.

---

## 6. The Industry Indices page (`indices.html`)

Separate pipeline, separate page, same Pages deployment from step 2 above —
no extra Pages setup needed, `indices.html` is served from the same repo
root at `https://<your-org>.github.io/<repo-name>/indices.html` the moment
you push it.

### 6a. Set the `FRED_API_KEY` secret (optional, one-time, ~2 minutes)

Brent crude prefers FRED's official Europe Brent spot series, which requires
a free key. **This is optional**: with no key set, `fetch_indices.py` falls
back to the front-month Brent futures contract (BZ=F) from the same keyless
Yahoo endpoint the macro dashboard uses for TTF, averaging daily closes per
month to match FRED's monthly-average methodology. The fallback only extends
the series forward — it never rewrites a month FRED published — so setting
the key later back-fills those months with official values on the next run.

1. Get a key: **https://fredaccount.stlouisfed.org/apikeys** → create a free
   account if you don't have one → **Request API Key** → a 32-character key
   appears instantly.
2. On GitHub: **Settings → Secrets and variables → Actions → New repository
   secret**.
3. Name: `FRED_API_KEY`. Value: paste the key. **Add secret**.

That's it — `.github/workflows/update_indices.yml` passes it to
`fetch_indices.py` as an environment variable; it's never written to any
file in the repo. To run `fetch_indices.py` locally, export it in your own
shell instead: `export FRED_API_KEY=your_key_here`.

### 6b. How the weekly auto-update works

`.github/workflows/update_indices.yml` runs every Monday at 06:47 UTC (30
minutes after the macro dashboard's workflow, so the two don't normally
collide on the same branch) and can also be triggered manually from the
**Actions** tab (**Run workflow**). Each run re-fetches full history for all
5 indices and commits `indices.json` **only if it changed**.

Same resilience as §4: one dead source degrades one series — it keeps its
stored history, flagged stale, and the page renders a "No update since …"
badge on that line while everything else updates. The run only fails if
nothing at all could be produced.

### 6c. No manual touch-points

Unlike the macro dashboard (§5 above), this page has **nothing** to
hand-edit. There's no `manual_overrides.json` equivalent: every source is a
free API (with a keyless fallback for Brent), degradation is automatic, and
`indices.json` carries no editable fields either. The "Structural drivers"
box shown on every card (the "pushes it up / pushes it down" bullets) is
static content baked into `indices.html` itself; edit it there directly if
the structural story for an index changes, but it's not meant to be a
per-run task. The legend at the bottom of the page is likewise static HTML,
not sourced from any data file.
