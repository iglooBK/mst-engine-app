#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLOUTOS // ICE DXY OFFICIAL CACHE PRODUCTION v1.0

PUBLIC ICE DATA ONLY | NO LOGIN | NO CREDENTIALS | NO TRADING

Official source chain:
  ICE Report 297 API
    -> US Dollar Index
    -> U.S. Dollar Historical Prices
    -> US_Dollar_Index_Historical_Prices.xls

Output:
  ice-dxy-latest.json

IMPORTANT:
  The ICE workbook labels the nearby value as CLOSE, not Settlement.
  Therefore this cache intentionally exposes:
      closePrice
  and does NOT mislabel it as a settlement price.

Dependency:
  xlrd
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    import xlrd
except ImportError:
    print("[RESULT] FAIL | Missing dependency: xlrd")
    print("[TIP] Run: python -m pip install xlrd")
    sys.exit(2)

BASE = "https://www.ice.com"
REPORT_ID = "297"
REPORT_PAGE = f"{BASE}/report/{REPORT_ID}"
CRITERIA_URL = f"{BASE}/marketdata/api/reports/{REPORT_ID}/criteria"
RESULTS_URL = f"{BASE}/marketdata/api/reports/{REPORT_ID}/results"

FALLBACK_XLS_PATH = "/publicdocs/futures_us_reports/usd_index/US_Dollar_Index_Historical_Prices.xls"
OUTPUT = Path("ice-dxy-latest.json")
TMP_XLS = Path("US_Dollar_Index_Historical_Prices.xls")

UA = "Mozilla/5.0 PLOUTOS-ICE-DXY-PRODUCTION/1.0"

def get_json(url, timeout=30):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": REPORT_PAGE,
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = r.headers.get_content_charset() or "utf-8"
        return json.loads(raw.decode(enc, errors="replace"))

def post_form_json(url, form, timeout=30):
    body = urlencode(form, doseq=True).encode("utf-8")
    req = Request(url, data=body, method="POST", headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": REPORT_PAGE,
        "Origin": BASE,
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        enc = r.headers.get_content_charset() or "utf-8"
        return json.loads(raw.decode(enc, errors="replace"))

def download_file(url, path, timeout=45):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/vnd.ms-excel,application/octet-stream,*/*",
        "Referer": REPORT_PAGE,
        "Cache-Control": "no-cache",
    })
    with urlopen(req, timeout=timeout) as r:
        raw = r.read()
        ctype = r.headers.get("Content-Type", "")
    path.write_bytes(raw)
    return ctype, len(raw)

def report_rows(obj):
    rows = []
    if isinstance(obj, dict):
        if (
            isinstance(obj.get("download"), dict)
            and obj.get("productName") is not None
            and obj.get("publishDate") is not None
        ):
            rows.append(obj)
        for v in obj.values():
            rows.extend(report_rows(v))
    elif isinstance(obj, list):
        for v in obj:
            rows.extend(report_rows(v))
    return rows

def find_usdx_report():
    criteria = get_json(CRITERIA_URL)
    form = {
        "offset": 0,
        "pageNumber": 1,
        "max": 100,
        "productId": "12",
        "reportTypeId": "",
        "year": "",
    }

    data = post_form_json(RESULTS_URL, form)
    rows = report_rows(data)

    candidates = []
    for row in rows:
        blob = json.dumps(row, ensure_ascii=False).lower()
        if (
            row.get("productName") == "US Dollar Index"
            or "us dollar index" in blob
            or "usd_index" in blob
        ):
            dl = row.get("download") or {}
            url = dl.get("url")
            if url:
                candidates.append((row.get("publishDate") or "", row))

    if not candidates:
        return {
            "publishDate": None,
            "productName": "US Dollar Index",
            "download": {
                "label": "U.S. Dollar Historical Prices",
                "url": FALLBACK_XLS_PATH,
            },
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def parse_date_cell(book, cell):
    if cell.ctype == xlrd.XL_CELL_DATE:
        dt = xlrd.xldate_as_datetime(cell.value, book.datemode)
        return dt.date()

    v = cell.value
    if isinstance(v, float) and 20000 < v < 70000:
        try:
            return xlrd.xldate_as_datetime(v, book.datemode).date()
        except Exception:
            pass

    s = str(v).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None

def price_from_ice_raw(v):
    """
    ICE workbook stores 99.789 as 99789, i.e. three implied decimals.
    """
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace(",", "").strip())
    except Exception:
        return None
    if abs(x) >= 1000:
        return x / 1000.0
    return x

def contract_code(contract_yyyymm):
    try:
        n = int(float(contract_yyyymm))
        year = n // 100
        month = n % 100
        return f"DOLINDX{year % 100:02d}{month:02d}"
    except Exception:
        return None

def parse_latest_nearby(path):
    book = xlrd.open_workbook(path)
    if not book.sheet_names():
        raise RuntimeError("ICE XLS contains no worksheet.")

    sh = book.sheet_by_index(0)
    if sh.nrows < 6 or sh.ncols < 12:
        raise RuntimeError("ICE XLS schema is smaller than expected.")

    # Observed official schema:
    # col 0 DATE
    # cols 6..11 NEARBY:
    #   CONTRACT, HIGH, LOW, CLOSE, OPEN INTEREST, VOLUME
    latest = None

    for r in range(5, sh.nrows):
        d = parse_date_cell(book, sh.cell(r, 0))
        if not d:
            continue

        contract = sh.cell_value(r, 6)
        close_raw = sh.cell_value(r, 9)
        close = price_from_ice_raw(close_raw)

        if contract in (None, "") or close is None:
            continue

        item = {
            "date": d,
            "row": r + 1,
            "contractRaw": contract,
            "high": price_from_ice_raw(sh.cell_value(r, 7)),
            "low": price_from_ice_raw(sh.cell_value(r, 8)),
            "close": close,
            "openInterest": str(sh.cell_value(r, 10)).strip(),
            "volume": str(sh.cell_value(r, 11)).strip(),
        }

        if latest is None or item["date"] > latest["date"]:
            latest = item

    if latest is None:
        raise RuntimeError("No usable ICE nearby DXY row found.")

    contract_yyyymm = str(int(float(latest["contractRaw"])))
    latest["contractMonth"] = contract_yyyymm
    latest["contractCode"] = contract_code(contract_yyyymm)
    return latest

def main():
    print("=" * 92)
    print("PLOUTOS // ICE DXY OFFICIAL CACHE PRODUCTION v1.0")
    print("[SOURCE] ICE Report 297 -> U.S. Dollar Historical Prices")
    print("[SECURITY] PUBLIC DATA ONLY | NO LOGIN | NO CREDENTIALS | NO TRADING")
    print("=" * 92)

    report = find_usdx_report()
    dl = report.get("download") or {}
    xls_path = dl.get("url") or FALLBACK_XLS_PATH
    xls_url = xls_path if xls_path.startswith("http") else BASE + xls_path

    print(f"[STEP 1 OK] ICE report publish date = {report.get('publishDate')}")
    print(f"[STEP 1 OK] ICE report label = {dl.get('label')}")
    print(f"[STEP 1 OK] ICE XLS = {xls_path}")

    ctype, size = download_file(xls_url, TMP_XLS)
    print(f"[STEP 2 OK] XLS downloaded | {ctype} | bytes={size:,}")

    latest = parse_latest_nearby(TMP_XLS)

    today_utc = datetime.now(timezone.utc).date()
    age_days = max(0, (today_utc - latest["date"]).days)
    # Informational only. UI should always display as-of date.
    stale_after_days = 5

    payload = {
        "schemaVersion": "ploutos-ice-dxy-cache-v1",
        "market": "ICE",
        "exchange": "ICE Futures U.S.",
        "symbol": "DX",
        "name": "US Dollar Index Futures",
        "productId": 12,
        "contractMonth": latest["contractMonth"],
        "contractCode": latest["contractCode"],
        "priceType": "CLOSE",
        "closePrice": latest["close"],
        "highPrice": latest["high"],
        "lowPrice": latest["low"],
        "dataDate": latest["date"].isoformat(),
        "reportPublishDate": report.get("publishDate"),
        "dataAgeDays": age_days,
        "isStale": age_days > stale_after_days,
        "source": "ICE Official U.S. Dollar Historical Prices",
        "sourceReport": f"ICE Report {REPORT_ID}",
        "sourceFile": xls_path,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
    }

    # Preserve OI/volume as numeric values when possible.
    for key, raw in (("openInterest", latest["openInterest"]), ("volume", latest["volume"])):
        s = raw.replace(",", "").strip()
        try:
            payload[key] = int(float(s))
        except Exception:
            if s:
                payload[key] = raw

    tmp = OUTPUT.with_suffix(OUTPUT.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(OUTPUT)

    print(
        f"[STEP 3 OK] Latest nearby = {latest['contractMonth']} / "
        f"{latest['contractCode']} / CLOSE {latest['close']:.3f}"
    )
    print(f"[STEP 3 OK] Data date = {latest['date'].isoformat()} | age={age_days} days")
    print(f"[OUTPUT] {OUTPUT.resolve()}")
    print("[RESULT] OK")
    print("[NOTE] ICE workbook field is CLOSE, not Settlement.")
    print("=" * 92)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[RESULT] FAIL | {type(e).__name__}: {e}")
        sys.exit(1)
