#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
OUT = Path(__file__).resolve().parent / "taifex-market-latest.json"

def num(v):
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("%", "")
    if not s or s in {"-", "--", "N/A"}:
        return None
    try:
        return float(s)
    except Exception:
        return None

def get(o, *keys):
    for k in keys:
        if k in o:
            return o[k]
    return None

def date_key(v):
    s = str(v or "").strip()
    m = re.search(r"(\d{4})[./-]?(\d{1,2})[./-]?(\d{1,2})", s)
    if m:
        return f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    m = re.search(r"(\d{3})[./-](\d{1,2})[./-](\d{1,2})", s)
    if m:
        return f"{int(m.group(1))+1911}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    return ""

def session_kind(v):
    s = str(v or "").lower()
    if "盤後" in s or "after" in s or "夜" in s:
        return "AH"
    if "一般" in s or "regular" in s or "日" in s:
        return "REG"
    return "UNK"

req = urllib.request.Request(
    URL,
    headers={
        "User-Agent": "Mozilla/5.0 PLOUTOS-GitHub-Actions/1.0",
        "Accept": "application/json",
    },
)
with urllib.request.urlopen(req, timeout=25) as r:
    raw = json.load(r)

rows = raw if isinstance(raw, list) else raw.get("data", [])
parsed = []

# Verified from GitHub Actions / TAIFEX OpenAPI on 2026-08-11:
# English schema example:
# Date, Contract, ContractMonth(Week), Open, High, Low, Last, Change, %, Volume,
# SettlementPrice, OpenInterest, BestBid, BestAsk, TradingSession, ...
for o in rows:
    if not isinstance(o, dict):
        continue
    contract = str(get(o, "契約", "Contract", "商品契約代號", "商品代號") or "").strip().upper()
    month = str(get(o, "到期月份(週別)", "到期月份", "ContractMonth(Week)", "Contract Month(Week)", "ContractMonth") or "").strip()
    date = get(o, "日期", "交易日期", "Date")
    close = num(get(o, "最後成交價", "收盤價", "Last", "Closing Price", "ClosingPrice", "Close"))
    change = num(get(o, "漲跌價", "Change"))
    pct = num(get(o, "漲跌%", "漲跌％", "漲跌百分比", "%", "Change Percent", "ChangePercent"))
    session = str(get(o, "交易時段", "Trading Session", "TradingSession", "Session") or "").strip()

    # Only outright monthly TX; exclude calendar spreads such as 202608/202609.
    if contract != "TX" or not re.fullmatch(r"\d{6}", month) or close is None:
        continue

    dk = date_key(date)
    if not dk:
        continue

    parsed.append({
        "contract": "TX",
        "contractMonth": month,
        "date": str(date or ""),
        "dateKey": dk,
        "close": close,
        "change": change,
        "changePct": pct,
        "session": session,
    })

if not parsed:
    print("ERROR: TAIFEX returned no usable outright monthly TX rows.", file=sys.stderr)
    if isinstance(rows, list) and rows:
        print("Sample row:", json.dumps(rows[0], ensure_ascii=False), file=sys.stderr)
    sys.exit(2)

latest_date = max(x["dateKey"] for x in parsed)
latest = [x for x in parsed if x["dateKey"] == latest_date]
nearest_month = min(int(x["contractMonth"]) for x in latest)
near = [x for x in latest if int(x["contractMonth"]) == nearest_month]

reg = next((x for x in near if session_kind(x["session"]) == "REG"), None)
ah = next((x for x in near if session_kind(x["session"]) == "AH"), None)
front = reg or near[0]

payload = {
    "status": "OK",
    "source": "TAIFEX DailyMarketReportFut",
    "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "front": front,
    "all": ah,
}

# Stable pretty JSON; Git only commits when actual content changes.
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {OUT.name}: TX {front['contractMonth']} {front['date']} / front={front['close']} / all={ah['close'] if ah else 'N/A'}")
