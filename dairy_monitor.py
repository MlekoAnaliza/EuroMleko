#!/usr/bin/env python3
"""
EU Dairy Cycle Monitor — weekly report generator.

What it does, every time it runs:
  1. Loads the latest indicator readings (from readings.json, or best-effort fetch).
  2. Scores each of the five signals against the cycle thresholds.
  3. Computes a weighted composite and the cycle phase.
  4. Writes a plain-language interpretation of where the cycle stands and what
     it means for raw-milk and dairy-product prices.
  5. Emails the report to you and appends the composite to history.csv.

The scoring + interpretation logic mirrors the HTML dashboard exactly, so the
two always agree.

Designed to run unattended on a schedule (GitHub Actions / cron / Task
Scheduler). See README.md.
"""

import json
import os
import csv
import smtplib
import datetime as dt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE = Path(__file__).resolve().parent
READINGS_FILE = BASE / "readings.json"
HISTORY_FILE = BASE / "history.csv"

# --------------------------------------------------------------------------
# MODEL DEFINITION  (identical logic to the dashboard)
# --------------------------------------------------------------------------
SIGNAL_WORD = {-2: "Oversupply", -1: "Soft", 0: "Turning", 1: "Firming", 2: "Recovery"}
SIGNAL_HEX = {-2: "#4a7a9b", -1: "#5b9b9a", 0: "#b8923c", 1: "#c98a44", 2: "#c9683a"}


def pct(now, prev):
    if not prev:
        return 0.0
    return (now - prev) / prev * 100.0


def score_trend(change_pct):
    if change_pct <= -3: return -2
    if change_pct <= -1: return -1
    if change_pct < 1:   return 0
    if change_pct < 3:   return 1
    return 2


def trend_desc(s, label):
    return {-2: f"{label} falling fast", -1: f"{label} easing", 0: f"{label} flat",
            1: f"{label} firming", 2: f"{label} rallying"}[s]


# Each indicator: how to score it, how to describe it, its weight and a note.
INDICATORS = [
    {
        "id": "collection", "name": "EU milk collection, YoY", "weight": 1.4,
        "score": lambda v: (-2 if v["yoy"] >= 3 else -1 if v["yoy"] >= 1 else
                            0 if v["yoy"] > -0.5 else 1 if v["yoy"] > -2 else 2),
        "desc": lambda s: {-2: "collection still surging — surplus building hard",
                           -1: "collection growing — surplus still accumulating",
                           0: "collection roughly flat — supply balancing",
                           1: "collection contracting — supply tightening",
                           2: "collection falling sharply — clear tightening"}[s],
        "fmt": lambda v: f"{v['yoy']:+.1f}% YoY",
        "note": "The trigger. Rebound is confirmed when this turns negative YoY.",
    },
    {
        "id": "butter", "name": "EU butter spot", "weight": 1.0,
        "score": lambda v: score_trend(pct(v["now"], v["prev"])),
        "desc": lambda s: trend_desc(s, "butter"),
        "fmt": lambda v: f"€{v['now']:.0f}/t ({pct(v['now'], v['prev']):+.1f}% 4wk)",
        "note": "Fat side is stock-heavy. Firming signals butter inventory is clearing.",
    },
    {
        "id": "smp", "name": "EU skim milk powder spot", "weight": 1.0,
        "score": lambda v: score_trend(pct(v["now"], v["prev"])),
        "desc": lambda s: trend_desc(s, "SMP"),
        "fmt": lambda v: f"€{v['now']:.0f}/t ({pct(v['now'], v['prev']):+.1f}% 4wk)",
        "note": "With butter, the key gate on a broad farm-gate rebound.",
    },
    {
        "id": "cheese", "name": "EU cheese spot", "weight": 0.9,
        "score": lambda v: score_trend(pct(v["now"], v["prev"])),
        "desc": lambda s: trend_desc(s, "cheese"),
        "fmt": lambda v: f"€{v['now']:.0f}/t ({pct(v['now'], v['prev']):+.1f}% 4wk)",
        "note": "The resilient, margin-driving segment; firm cheese pulls raw-milk bids up.",
    },
    {
        "id": "slaughter", "name": "Cow slaughter, YoY", "weight": 1.2,
        "score": lambda v: (2 if v["yoy"] >= 5 else 1 if v["yoy"] >= 2 else
                            0 if v["yoy"] > -2 else -1 if v["yoy"] > -5 else -2),
        "desc": lambda s: {-2: "slaughter well down — herd correction stalled",
                           -1: "slaughter soft — correction slow",
                           0: "slaughter near normal",
                           1: "slaughter rising — herd shrinking",
                           2: "slaughter high — fast herd reduction"}[s],
        "fmt": lambda v: f"{v['yoy']:+.1f}% YoY",
        "note": "Low cull rates / scarce heifers prolong oversupply.",
    },
    {
        "id": "feed", "name": "Feed / energy cost index", "weight": 0.6,
        "score": lambda v: (1 if pct(v["now"], v["prev"]) >= 5 else
                            -1 if pct(v["now"], v["prev"]) <= -5 else 0),
        "desc": lambda s: {-1: "cheap inputs — oversupply sustained",
                           0: "input costs steady",
                           1: "input costs rising — squeezes margins, speeds correction"}[s],
        "fmt": lambda v: f"idx {v['now']:.0f} ({pct(v['now'], v['prev']):+.1f}% 4wk)",
        "note": "Two-sided: rising costs speed the supply cut but also dampen demand.",
    },
]


def phase_of(c):
    if c <= -1.2: return "Deep oversupply"
    if c <= -0.5: return "Oversupplied · bottoming"
    if c < 0.3:   return "Balancing · turning point near"
    if c <= 1.0:  return "Tightening · early recovery"
    return "Recovery · prices firming"


def accent_for(c):
    if c <= -1.2: return SIGNAL_HEX[-2]
    if c <= -0.5: return SIGNAL_HEX[-1]
    if c < 0.3:   return SIGNAL_HEX[0]
    if c <= 1.0:  return SIGNAL_HEX[1]
    return SIGNAL_HEX[2]


def compute(readings):
    """Return composite score and per-indicator detail."""
    wsum = w = 0.0
    rows = []
    stale = []
    for ind in INDICATORS:
        v = readings["values"].get(ind["id"], {})
        sc = ind["score"](v)
        rows.append({"ind": ind, "v": v, "score": sc})
        wsum += sc * ind["weight"]
        w += ind["weight"]
        if ind["id"] in readings.get("stale", []):
            stale.append(ind["name"])
    return wsum / w, rows, stale


# --------------------------------------------------------------------------
# NARRATIVE  (mirrors the dashboard interpretation)
# --------------------------------------------------------------------------
def cap(s):
    return s[0].upper() + s[1:]


def build_narrative(rows, composite):
    by = {r["ind"]["id"]: r for r in rows}
    col, sl = by["collection"], by["slaughter"]
    bu, sm, ch, fe = by["butter"], by["smp"], by["cheese"], by["feed"]

    supply = (col["score"] * 1.4 + sl["score"] * 1.2) / 2.6
    commodity = (bu["score"] + sm["score"] + ch["score"]) / 3.0

    d = lambda r: r["ind"]["desc"](r["score"])

    if supply <= -0.6:
        p1 = (f"Supply is still the binding constraint. {cap(d(col))}, and {d(sl)} — "
              f"the herd correction that ends the glut hasn't engaged yet.")
    elif supply < 0.4:
        p1 = (f"The supply side is starting to balance. {cap(d(col))}; {d(sl)}. "
              f"This is the early machinery of a turn.")
    else:
        p1 = (f"The supply correction is underway. {cap(d(col))} and {d(sl)} — "
              f"the classic trigger for a farm-gate rebound.")

    p2 = f"On the commodity side, {d(bu)}, {d(sm)}, {d(ch)}. "
    if commodity >= 0.7:
        p2 += "Broad firmness here should lift farm-gate prices with the usual 3–6 month lag."
    elif commodity >= 0:
        p2 += ("The picture is mixed — watch whether butter and SMP clear inventory "
               "together, which is the gate on a broad rebound.")
    else:
        p2 += "Commodities remain heavy, so any farm-gate relief is capped for now."
    p2 += f" {cap(d(fe))}."

    p3 = f"Composite of {composite:+.2f} places the cycle at {phase_of(composite)}. "
    if composite <= -0.5:
        p3 += ("Treat current raw-milk prices as near-trough but not yet turning — hold off "
               "reading a rebound until collection rolls over.")
    elif composite < 0.3:
        p3 += ("The inflection looks close: the next one to two monthly collection prints "
               "are the ones to watch.")
    elif composite <= 1.0:
        p3 += ("Early recovery signals are live; farm-gate strengthening typically follows "
               "over the next one to two quarters.")
    else:
        p3 += "Recovery is established — expect farm-gate prices to keep firming as the lag plays through."

    return [p1, p2, p3]


# --------------------------------------------------------------------------
# DATA INGESTION
#   Default: read readings.json (you maintain it weekly, or a feed writes it).
#   Optional: enable fetch.py adapters for best-effort auto-pull (verify them).
# --------------------------------------------------------------------------
def load_readings():
    if not READINGS_FILE.exists():
        raise SystemExit(f"Missing {READINGS_FILE}. Copy readings.example.json to readings.json and fill it in.")
    with open(READINGS_FILE) as f:
        data = json.load(f)

    if os.environ.get("DAIRY_AUTOFETCH") == "1":
        try:
            import fetch  # optional module you verify before enabling
            data = fetch.update(data)
        except Exception as e:
            data.setdefault("notes", []).append(f"Auto-fetch failed, using last known values: {e}")
    return data


def append_history(date, composite, phase):
    new = not HISTORY_FILE.exists()
    with open(HISTORY_FILE, "a", newline="") as f:
        wr = csv.writer(f)
        if new:
            wr.writerow(["date", "composite", "phase"])
        wr.writerow([date, f"{composite:.3f}", phase])


def last_composite():
    if not HISTORY_FILE.exists():
        return None
    rows = list(csv.DictReader(open(HISTORY_FILE)))
    return float(rows[-1]["composite"]) if rows else None


# --------------------------------------------------------------------------
# REPORT (HTML email)
# --------------------------------------------------------------------------
def build_email_html(date, composite, rows, narrative, stale, prev_comp):
    accent = accent_for(composite)
    if prev_comp is None:
        dir_txt = "No prior reading on file."
    else:
        diff = composite - prev_comp
        if abs(diff) < 0.04:
            dir_txt = "Unchanged vs last report."
        elif diff > 0:
            dir_txt = f"▲ {diff:+.2f} vs last report — firming."
        else:
            dir_txt = f"▼ {diff:+.2f} vs last report — softening."

    sig_rows = ""
    for r in rows:
        sc = r["score"]
        sig_rows += f"""
        <tr>
          <td style="padding:9px 10px;border-bottom:1px solid #e6e2d8;font-size:14px;color:#1c2027">{r['ind']['name']}</td>
          <td style="padding:9px 10px;border-bottom:1px solid #e6e2d8;font-size:13px;color:#5e6770;font-family:monospace">{r['ind']['fmt'](r['v'])}</td>
          <td style="padding:9px 10px;border-bottom:1px solid #e6e2d8;text-align:right">
            <span style="background:{SIGNAL_HEX[sc]};color:#fff;font-size:11px;font-weight:600;
            padding:3px 9px;border-radius:100px;font-family:monospace">{SIGNAL_WORD[sc]}</span>
          </td>
        </tr>"""

    paras = "".join(f'<p style="margin:0 0 12px;color:#33383f">{p}</p>' for p in narrative)
    stale_note = ""
    if stale:
        stale_note = (f'<p style="margin:14px 0 0;padding:10px 12px;background:#fdf3e3;border-left:3px solid #b8923c;'
                      f'font-size:13px;color:#7a5c1d">⚠ Stale data — last known values used for: {", ".join(stale)}. '
                      f'Verify and update readings.json.</p>')

    return f"""\
<div style="font-family:Helvetica,Arial,sans-serif;max-width:640px;margin:0 auto;background:#f7f5ef;padding:24px">
  <p style="font-family:monospace;font-size:11px;letter-spacing:2px;color:#8b95a1;text-transform:uppercase;margin:0 0 4px">EU Dairy Cycle Monitor · weekly</p>
  <h1 style="font-size:26px;color:#1c2027;margin:0 0 2px">{phase_of(composite)}</h1>
  <p style="margin:0 0 18px;color:#5e6770;font-size:14px">{date} · composite <b style="color:{accent}">{composite:+.2f}</b> · {dir_txt}</p>

  <div style="background:#fff;border:1px solid #e6e2d8;border-left:4px solid {accent};border-radius:4px;padding:18px 20px;margin-bottom:20px">
    <p style="font-family:monospace;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#8b95a1;margin:0 0 12px">Interpretation</p>
    {paras}
    {stale_note}
  </div>

  <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e6e2d8;border-radius:4px;overflow:hidden">
    <tr><th colspan="3" style="text-align:left;padding:11px 10px;font-family:monospace;font-size:11px;
        letter-spacing:2px;text-transform:uppercase;color:#8b95a1;border-bottom:1px solid #e6e2d8;font-weight:600">The five signals</th></tr>
    {sig_rows}
  </table>

  <p style="font-family:monospace;font-size:11px;color:#a0a8b0;margin:18px 0 0;line-height:1.6">
    Scale: composite runs −2 (deep oversupply) to +2 (recovery). Signals weighted —
    collection ×1.4, slaughter ×1.2, butter/SMP ×1.0, cheese ×0.9, feed ×0.6.
    Generated automatically. Not investment advice.
  </p>
</div>"""


def send_email(subject, html):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ["SMTP_USER"]
    pwd = os.environ["SMTP_PASS"]
    sender = os.environ.get("MAIL_FROM", user)
    to = os.environ["MAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.attach(MIMEText("View this report in an HTML-capable client.", "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(sender, [a.strip() for a in to.split(",")], msg.as_string())


# --------------------------------------------------------------------------
def main():
    readings = load_readings()
    date = readings.get("as_of", dt.date.today().isoformat())
    composite, rows, stale = compute(readings)
    narrative = build_narrative(rows, composite)
    phase = phase_of(composite)
    prev = last_composite()

    html = build_email_html(date, composite, rows, narrative, stale, prev)
    subject = f"Dairy cycle: {phase} (composite {composite:+.2f}) — {date}"

    # Always write the report to disk so you have a record even if email fails.
    (BASE / "last_report.html").write_text(html, encoding="utf-8")
    print(subject)
    for p in narrative:
        print(" -", p)

    if os.environ.get("SMTP_HOST"):
        send_email(subject, html)
        print("Email sent.")
    else:
        print("SMTP_HOST not set — skipped email; report saved to last_report.html")

    append_history(date, composite, phase)


if __name__ == "__main__":
    main()
