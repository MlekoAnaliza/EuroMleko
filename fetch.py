"""
OPTIONAL best-effort auto-fetch adapters.

Enable by setting the env var DAIRY_AUTOFETCH=1. Until you verify these against
the live sources, leave it OFF — the model runs fine from readings.json that you
(or a data feed) maintain by hand.

IMPORTANT — why these are stubs, not finished scrapers:
  * The EU Milk Market Observatory publishes collection and weekly commodity
    prices as PDF / XLSX files whose layout changes periodically. Reliable
    parsing needs you to confirm the current file URL and column positions.
  * Global Dairy Trade and slaughter data come from pages/APIs that may require
    headers or change structure.
So each adapter below shows WHERE to plug in and returns None on any failure;
main keeps the last known value and flags it 'stale' rather than crashing.

Verify each function, then enable. Anything you can't automate, keep filling in
readings.json manually — the report and interpretation are fully automated
regardless.
"""

import datetime as dt

# pip install requests (and openpyxl / pdfplumber if you parse XLSX / PDF)
try:
    import requests
except ImportError:
    requests = None

UA = {"User-Agent": "dairy-monitor/1.0"}


def _safe(fn):
    def wrap(*a, **k):
        if requests is None:
            return None
        try:
            return fn(*a, **k)
        except Exception:
            return None
    return wrap


# ---- Adapter stubs. Fill in URL + parsing, then return the numeric value. ----

@_safe
def fetch_collection_yoy():
    # Source: EU Milk Market Observatory — cow's milk deliveries (YoY %).
    # TODO: confirm the current data URL and extract the latest YoY figure.
    return None


@_safe
def fetch_commodity(name):
    # name in {"butter","smp","cheese"} — EU weekly spot price, EUR/tonne.
    # TODO: confirm the EU weekly dairy commodity price file and column.
    # Return dict {"now": float, "prev": float} or None.
    return None


@_safe
def fetch_slaughter_yoy():
    # Source: Eurostat / USDA cow slaughter, YoY %. Monthly.
    return None


@_safe
def fetch_feed_index():
    # Build from feed wheat + fertiliser + Brent. Return {"now","prev"} or None.
    return None


def update(data):
    """Refresh whatever it can; mark anything that failed as 'stale'."""
    data.setdefault("stale", [])
    vals = data["values"]

    c = fetch_collection_yoy()
    if c is not None:
        vals["collection"]["yoy"] = c
    else:
        data["stale"].append("collection")

    for k in ("butter", "smp", "cheese"):
        r = fetch_commodity(k)
        if r:
            vals[k] = r
        else:
            data["stale"].append(k)

    s = fetch_slaughter_yoy()
    if s is not None:
        vals["slaughter"]["yoy"] = s
    else:
        data["stale"].append("slaughter")

    f = fetch_feed_index()
    if f:
        vals["feed"] = f
    else:
        data["stale"].append("feed")

    data["as_of"] = dt.date.today().isoformat()
    return data
