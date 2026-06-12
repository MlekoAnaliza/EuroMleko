# EU Dairy Cycle Monitor — weekly report

An automated model that scores five leading signals of the European raw-milk
cycle, decides where the cycle stands (trough → turn → recovery), writes a
plain-language interpretation, and emails it to you every week.

The scoring and interpretation logic is identical to the companion HTML
dashboard, so the two always agree.

---

## What's in here

| File | Purpose |
|---|---|
| `dairy_monitor.py` | The model: scores, interprets, emails, logs history. |
| `readings.json` | The week's input figures. **You keep this current.** |
| `readings.example.json` | Reference copy with field explanations. |
| `fetch.py` | *Optional* best-effort auto-fetch adapters (off by default). |
| `requirements.txt` | Only needed if you enable auto-fetch. |
| `sample_report.html` | What the emailed report looks like. |

No third-party packages are needed to run the core model — standard library only.

---

## The five signals (and why they're weighted)

| Signal | Source | Weight | Reads on |
|---|---|---:|---|
| EU milk collection, YoY | Milk Market Observatory (monthly) | 1.4 | the trigger — surplus building vs tightening |
| Cow slaughter, YoY | Eurostat / USDA (monthly) | 1.2 | whether the herd correction has engaged |
| EU butter spot | EU weekly commodity prices | 1.0 | fat-stock clearance |
| EU SMP spot | EU weekly commodity prices | 1.0 | the other gate on a broad rebound |
| EU cheese spot | EU weekly commodity prices | 0.9 | the resilient, margin-driving segment |
| Feed / energy index | feed wheat · fertiliser · Brent | 0.6 | margin squeeze (two-sided) |

Each scores −2 (oversupply/bearish) to +2 (recovery/bullish). The weighted
**composite** runs −2 to +2 and maps to a phase: *Deep oversupply →
Bottoming → Turning → Early recovery → Recovery.*

---

## Weekly workflow

1. Open `readings.json` and update the figures from the source releases.
   - `collection.yoy` and `slaughter.yoy`: latest year-on-year %.
   - `butter` / `smp` / `cheese`: `now` = latest spot €/t, `prev` = ~4 weeks ago.
   - `feed`: your feed+energy index, `now` and `prev`.
   - Couldn't refresh one this week? Add its id to `"stale"` — the report flags it.
2. Run `python3 dairy_monitor.py` (or let the scheduler do it).
3. The report lands in your inbox and the composite is appended to `history.csv`.

That's the whole loop. Updating ~6 numbers a week keeps it fully reliable; the
scoring, interpretation, email, and history are automatic.

---

## Email setup

The model reads SMTP settings from environment variables:

```
SMTP_HOST   e.g. smtp.gmail.com
SMTP_PORT   587 (default)
SMTP_USER   your login
SMTP_PASS   an app password (not your main password)
MAIL_FROM   optional, defaults to SMTP_USER
MAIL_TO     recipient(s), comma-separated
```

If `SMTP_HOST` is unset, the model still runs and saves the report to
`last_report.html` — handy for a dry run.

Quick test:
```bash
SMTP_HOST=smtp.gmail.com SMTP_USER=you@gmail.com SMTP_PASS=app_password \
MAIL_TO=you@company.com python3 dairy_monitor.py
```

---

## Set-and-forget scheduling

### Option A — GitHub Actions (free, no server, recommended)

Put this repo on GitHub. Add the SMTP values under
**Settings → Secrets and variables → Actions**. Add
`.github/workflows/weekly.yml`:

```yaml
name: Weekly dairy report
on:
  schedule:
    - cron: "0 7 * * 1"   # every Monday 07:00 UTC
  workflow_dispatch: {}    # lets you run it manually too
jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python dairy_monitor.py
        env:
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          MAIL_TO:   ${{ secrets.MAIL_TO }}
      - name: Commit updated history
        run: |
          git config user.name "dairy-bot"
          git config user.email "bot@users.noreply.github.com"
          git add history.csv readings.json && git commit -m "weekly run" || true
          git push || true
```

You still edit `readings.json` weekly (in the browser on GitHub is fine) before
the Monday run — or wire up `fetch.py` so it self-updates.

### Option B — cron (your own Linux/Mac machine)

```bash
0 7 * * 1 cd /path/to/dairy_monitor && \
  SMTP_HOST=smtp.gmail.com SMTP_USER=you SMTP_PASS=app_pw MAIL_TO=you@co.com \
  /usr/bin/python3 dairy_monitor.py >> run.log 2>&1
```

### Option C — Windows Task Scheduler

Create a Basic Task → Weekly → Start a program → `python` with argument
`dairy_monitor.py` and "Start in" set to this folder. Put the SMTP variables in
the task's environment or a small wrapper `.bat`.

---

## Optional: auto-fetch

Set `DAIRY_AUTOFETCH=1` to have `fetch.py` try to pull figures automatically.
**Leave it off until you've verified each adapter** — the EU sources publish as
PDF/XLSX whose layout changes, so the stubs need you to confirm URLs and column
positions. Any adapter that fails is skipped and its value flagged `stale`, so
the report is never blocked. Whatever you can't automate, keep entering by hand.

---

## How to read the output

- **Composite ≤ −0.5** — near-trough; don't read a rebound until collection rolls over.
- **−0.5 to +0.3** — inflection near; watch the next one to two collection prints.
- **+0.3 to +1.0** — early recovery; farm-gate strengthening usually follows in 1–2 quarters.
- **> +1.0** — recovery established.

`history.csv` is your paper trail — the composite track week over week is the
clearest signal of the turn actually arriving.

Not investment advice. Verify all figures against primary sources before acting.

---

## Distributing the live dashboard to recipients (auto-update)

Use `eu-dairy-cycle-monitor-live.html` for this. Each recipient downloads it once
and keeps it on their computer; every time they open it, it pulls the latest
numbers from one small online file you maintain. No typing on their end.

How the pieces fit:

1. **Create the online data file.** Make a free GitHub repo (e.g. `dairy-data`)
   and put `readings.json` in it. That file is just the six numbers + date.
2. **Point the dashboard at it.** Open `eu-dairy-cycle-monitor-live.html` in a text
   editor, find the line near the top:
   ```
   const DATA_URL = "PASTE_YOUR_DATA_FILE_URL_HERE";
   ```
   and paste the **raw** URL of your file, e.g.
   `https://raw.githubusercontent.com/YOURNAME/dairy-data/main/readings.json`
   (On GitHub, open the file and click the **Raw** button to get this URL.)
   GitHub's raw URLs allow the file to be read from a downloaded page — that's why
   this host works; some others block it.
3. **Send the edited dashboard** to your recipients. When they open it, the dot at
   the top of the page turns and shows "Live data loaded — as of <date>". If they're
   offline or the URL is wrong, it shows the last data received and says so.
4. **Update the numbers weekly.** Two ways:
   - *Hands-off:* the scheduled model (GitHub Action above) runs `dairy_monitor.py`,
     which writes `readings.json`, and the Action commits it back to the repo. Every
     dashboard then shows the new figures on next open.
   - *By hand:* edit `readings.json` directly on GitHub once a week. Everyone's
     dashboard updates automatically — this works even before the auto-fetch
     scrapers are finished.

Reality check on "hands-off": the auto-fetch adapters in `fetch.py` must be verified
against the live EU sources first, and they need occasional maintenance when those
sources change format. Until then, the by-hand route (edit one file weekly) already
delivers the "recipients see the latest automatically" outcome — you just update one
place instead of everyone updating their own copy.

Privacy: the online file contains only market numbers (the same ones in your report).
The dashboard each recipient holds stays a local file; nothing of theirs goes online.
