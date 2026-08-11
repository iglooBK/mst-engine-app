#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLOUTOS // CME GOLD OFFICIAL CACHE PRODUCTION v1.0

Source:
  CME Group public Gold Futures settlement service
  productId = 437

Output:
  cme-gold-latest.json

Design:
  - PUBLIC CME GET only
  - No Yuanta login / credentials / trading
  - Selects the nearest non-expired listed Gold contract row from CME
  - Excludes the "Total" summary row
  - Preserves the official settlement trade date
"""

from __future__ import annotations

import json
import re
import sys
from calendar import monthrange
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://www.cmegroup.com"
PRODUCT_ID = "437"
TRADEDATE_URL = f"{BASE}/CmeWS/mvc/Settlements/Futures/TradeDate/{PRODUCT_ID}"
SETTLEMENT_URL = f"{BASE}/CmeWS/mvc/Settlements/Futures/Settlements/{PRODUCT_ID}/FUT"
SOURCE_PAGE = f"{BASE}/markets/metals/precious/gold.settlements.html"
OUTPUT = Path("cme-gold-latest.json")
UA = "Mozilla/5.0 PLOUTOS-CME-GOLD-PRODUCTION/1.0"

MONTHS = {
    "JAN":1, "FEB":2, "MAR":3, "APR":4, "MAY":5, "JUN":6,
    "JUL":7, "AUG":8, "SEP":9, "OCT":10, "NOV":11, "DEC":12,
}

def fetch_json(url, timeout=30):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": SOURCE_PAGE,
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = r.headers.get_content_charset() or "utf-8"
        text = raw.decode(enc, errors="replace")
        obj = json.loads(text)
        if isinstance(obj, dict) and "data" in obj and len(obj) <= 4 and obj["data"] is not None:
            obj = obj["data"]
        return obj

def parse_number(value):
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    # CME can append flags such as B/A to displayed values.
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None

def trade_dates(obj):
    rows = obj if isinstance(obj, list) else (
        obj.get("tradeDates") or obj.get("dates") or obj.get("data") or []
        if isinstance(obj, dict) else []
    )
    ans = []
    for row in rows:
        if isinstance(row, (list, tuple)) and row:
            ans.append(str(row[0]))
        elif isinstance(row, dict):
            d = row.get("tradeDate") or row.get("date") or row.get("text")
            if d:
                ans.append(str(d))
        elif isinstance(row, str):
            ans.append(row)
    return ans

def parse_trade_date(s):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unsupported CME trade date: {s!r}")

def parse_contract_month(label):
    m = re.fullmatch(r"\s*([A-Z]{3})\s+(\d{2})\s*", str(label).upper())
    if not m or m.group(1) not in MONTHS:
        return None
    month = MONTHS[m.group(1)]
    year = 2000 + int(m.group(2))
    return year, month

def contract_code(year, month):
    # Match the naming style already used by the Yuanta bridge, e.g. GC2608.
    return f"GC{year % 100:02d}{month:02d}"

def choose_nearest_contract(rows, trade_day):
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("month", "")).strip()
        if not label or label.lower() == "total":
            continue

        ym = parse_contract_month(label)
        settle = parse_number(row.get("settle") if "settle" in row else row.get("settlement"))
        if not ym or settle is None:
            continue

        y, m = ym
        # Settlement rows are monthly labels. For cache selection we keep the
        # current calendar month eligible through month-end, then move forward.
        end_of_month = date(y, m, monthrange(y, m)[1])
        if end_of_month < trade_day:
            continue

        candidates.append((y, m, settle, row))

    if not candidates:
        raise RuntimeError("No eligible non-expired Gold settlement contract found.")

    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0]

def main():
    print("=" * 88)
    print("PLOUTOS // CME GOLD OFFICIAL CACHE PRODUCTION v1.0")
    print("[SOURCE] CME Group Gold Futures | productId=437")
    print("[SECURITY] PUBLIC DATA ONLY | NO LOGIN | NO CREDENTIALS | NO TRADING")
    print("=" * 88)

    td_obj = fetch_json(TRADEDATE_URL)
    dates = trade_dates(td_obj)
    if not dates:
        raise RuntimeError("CME TradeDate API returned no usable dates.")

    selected_date = dates[0]
    trade_day = parse_trade_date(selected_date)
    print(f"[STEP 1 OK] Latest CME trade date = {selected_date}")

    qs = urlencode({
        "strategy": "DEFAULT",
        "tradeDate": selected_date,
        "pageSize": 500,
    })
    settlement_endpoint = SETTLEMENT_URL + "?" + qs
    st_obj = fetch_json(settlement_endpoint)

    if not isinstance(st_obj, dict):
        raise RuntimeError("Unexpected CME settlement response type.")

    rows = st_obj.get("settlements") or []
    if not rows:
        raise RuntimeError("CME settlement response contains no settlement rows.")

    y, m, settle, row = choose_nearest_contract(rows, trade_day)
    label = str(row.get("month")).strip()
    code = contract_code(y, m)

    previous = None
    # If CME exposes a prior-settlement-like field in future schema versions,
    # preserve it without inventing a value.
    for key in ("previousSettlement", "prevSettlement", "previousSettle"):
        if key in row:
            previous = parse_number(row.get(key))
            break

    payload = {
        "schemaVersion": "ploutos-cme-gold-cache-v1",
        "market": "CME",
        "exchange": "COMEX",
        "symbol": "GC",
        "name": "Gold Futures",
        "productId": int(PRODUCT_ID),
        "contractMonth": f"{y:04d}{m:02d}",
        "contractLabel": label,
        "contractCode": code,
        "settlementPrice": settle,
        "tradeDate": trade_day.isoformat(),
        "officialTradeDate": selected_date,
        "previousSettlementPrice": previous,
        "source": "CME Group Official Settlement",
        "sourcePage": SOURCE_PAGE,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    # Keep useful official fields for diagnostics/UI without depending on them.
    optional = {
        "open": parse_number(row.get("open")),
        "high": parse_number(row.get("high")),
        "low": parse_number(row.get("low")),
        "volume": parse_number(row.get("volume")),
        "openInterest": parse_number(row.get("openInterest")),
    }
    payload.update({k:v for k,v in optional.items() if v is not None})

    tmp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUTPUT)

    print(f"[STEP 2 OK] Settlement rows = {len(rows)}")
    print(f"[STEP 3 OK] Selected contract = {label} / {code}")
    print(f"[STEP 3 OK] Settlement price = {settle:,.1f}")
    print(f"[OUTPUT] {OUTPUT.resolve()}")
    print("[RESULT] OK")
    print("=" * 88)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[RESULT] FAIL | {type(e).__name__}: {e}")
        sys.exit(1)
