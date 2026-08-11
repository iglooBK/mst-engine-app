#!/usr/bin/env python3
# PLOUTOS TAIFEX OFFICIAL MARKET CACHE - PRODUCTION
# Outputs backward-compatible TX front/all plus:
#   nasdaq100 / nasdaq100AfterHours         -> UNF
#   semiconductor / semiconductorAfterHours -> SXF
# GitHub-safe: public TAIFEX OpenAPI only. No Yuanta credentials.
from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
OUT = Path(__file__).resolve().parent / "taifex-market-latest.json"

# Production targets: existing TX cache plus TAIFEX-listed US market proxies.
TARGETS = {
    "TX": "tx",
    "UNF": "nasdaq100",
    "SXF": "semiconductor",
}

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

def select_latest_front(rows, contract):
    """Return latest outright monthly front contract, preferring REG session."""
    items = []
    for o in rows:
        if not isinstance(o, dict):
            continue

        c = str(get(o, "契約", "Contract", "商品契約代號", "商品代號") or "").strip().upper()
        if c != contract:
            continue

        month = str(get(
            o, "到期月份(週別)", "到期月份",
            "ContractMonth(Week)", "Contract Month(Week)", "ContractMonth"
        ) or "").strip()

        # Outright monthly contracts only; excludes calendar spreads.
        if not re.fullmatch(r"\d{6}", month):
            continue

        date = get(o, "日期", "交易日期", "Date")
        dk = date_key(date)
        close = num(get(o, "最後成交價", "收盤價", "Last", "Closing Price", "ClosingPrice", "Close"))
        if not dk or close is None:
            continue

        items.append({
            "contract": contract,
            "contractMonth": month,
            "date": str(date or ""),
            "dateKey": dk,
            "close": close,
            "change": num(get(o, "漲跌價", "Change")),
            "changePct": num(get(o, "漲跌%", "漲跌％", "漲跌百分比", "%", "Change Percent", "ChangePercent")),
            "volume": num(get(o, "成交量", "Volume")),
            "settlementPrice": num(get(o, "結算價", "SettlementPrice", "Settlement Price")),
            "bestBid": num(get(o, "最後最佳買價", "BestBid", "Best Bid")),
            "bestAsk": num(get(o, "最後最佳賣價", "BestAsk", "Best Ask")),
            "session": str(get(o, "交易時段", "Trading Session", "TradingSession", "Session") or "").strip(),
        })

    if not items:
        return None, None

    latest_date = max(x["dateKey"] for x in items)
    latest = [x for x in items if x["dateKey"] == latest_date]
    nearest_month = min(int(x["contractMonth"]) for x in latest)
    near = [x for x in latest if int(x["contractMonth"]) == nearest_month]

    reg = next((x for x in near if session_kind(x["session"]) == "REG"), None)
    ah = next((x for x in near if session_kind(x["session"]) == "AH"), None)
    front = reg or near[0]
    return front, ah

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
if not isinstance(rows, list):
    print("ERROR: Unexpected TAIFEX response schema.", file=sys.stderr)
    sys.exit(2)

tx_front, tx_ah = select_latest_front(rows, "TX")
unf_front, unf_ah = select_latest_front(rows, "UNF")
sxf_front, sxf_ah = select_latest_front(rows, "SXF")

# TX is the already-proven production dependency. Do not write a broken cache if it disappears.
if tx_front is None:
    contracts = sorted({
        str(get(o, "契約", "Contract", "商品契約代號", "商品代號") or "").strip().upper()
        for o in rows if isinstance(o, dict)
    })
    print("ERROR: TAIFEX returned no usable outright monthly TX rows.", file=sys.stderr)
    print("Contracts seen:", ", ".join(c for c in contracts if c)[:1500], file=sys.stderr)
    sys.exit(2)

payload = {
    "status": "OK",
    "source": "TAIFEX DailyMarketReportFut",
    "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),

    # Backward-compatible keys used by the current index.html.
    "front": tx_front,
    "all": tx_ah,

    # New US-market fallback cache.
    "nasdaq100": unf_front,
    "semiconductor": sxf_front,

    # Keep after-hours rows too, for later UI/session decisions.
    "nasdaq100AfterHours": unf_ah,
    "semiconductorAfterHours": sxf_ah,

    "availability": {
        "TX": tx_front is not None,
        "UNF": unf_front is not None,
        "SXF": sxf_front is not None,
    },
}

OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

print(
    f"Wrote {OUT.name}: "
    f"TX={tx_front['close']} / "
    f"UNF={unf_front['close'] if unf_front else 'N/A'} / "
    f"SXF={sxf_front['close'] if sxf_front else 'N/A'}"
)

# Diagnostic only: UNF/SXF absence does not break the already-working TX cache.
if unf_front is None:
    print("NOTICE: No usable UNF outright monthly row found in this TAIFEX response.", file=sys.stderr)
if sxf_front is None:
    print("NOTICE: No usable SXF outright monthly row found in this TAIFEX response.", file=sys.stderr)
