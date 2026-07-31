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

TTF gas price and Manufacturing PMI have **no free official API**.
`fetch_data.py` first tries a best-effort automated fetch for both; that
fetch can silently start failing if the underlying (unofficial) source
changes its page/response shape. When it does, the pipeline falls back to
`manual_overrides.json`, and if that's stale too, it carries the last known
value forward and flags it `"stale": true` on the dashboard — so nothing
breaks, but the number can go quietly out of date if you never look at it.

**Roughly once a quarter** (or whenever you notice a "as of last period" tag
on TTF or PMI in the dashboard), open `manual_overrides.json` and update:

```json
{
  "energy_prices": {
    "value": 43.2,
    "date": "2026-06",
    "note": "ICE endex TTF front-month, €/MWh"
  },
  "manufacturing_pmi": {
    "value": 51.6,
    "date": "2026-05",
    "note": "HCOB Euro area Manufacturing PMI (S&P Global)"
  }
}
```

- `value`: the latest published figure.
- `date`: `"YYYY-MM"`, the month the figure refers to (not today's date).
- `note`: free text, shown on the dashboard card when expanded.

Where to find current figures:
- **TTF gas (€/MWh):** ICE endex or EEX front-month Dutch TTF quote (financial
  press, e.g. Reuters/Bloomberg, or your energy desk/broker).
- **Manufacturing PMI:** the monthly HCOB/S&P Global Eurozone Manufacturing
  PMI press release (released ~1st business day of the month, on
  spglobal.com/marketintelligence or via press coverage).

Commit and push the change (or open a PR) — the next scheduled run, or a
manual **Run workflow** click, will pick it up. You do **not** need to touch
`data.json` by hand; `fetch_data.py` merges the override in and computes
`latest`/`previous`/history automatically.
