#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLOUTOS // JPX OFFICIAL NIKKEI 225 MARKET CACHE PRODUCTION v1.0

Purpose
-------
Fetch JPX's official "Settlement Prices for Futures and Options" CSV,
select ONLY standard Nikkei 225 Futures (exclude mini/options), choose
the nearest valid contract month, and write jpx-market-latest.json.

Security
--------
PUBLIC JPX DATA ONLY.
No Yuanta API/login/account/password/certificate/token.
No trading/order functions.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

PAGE_URL = "https://www.jpx.co.jp/english/markets/derivatives/settlement-price/"
OUT = Path(__file__).resolve().parent / "jpx-market-latest.json"
UA = "Mozilla/5.0 PLOUTOS-GitHub-Actions/JPX-Nikkei225-Cache-1.0"


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href)


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,text/csv,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def decode_text(b: bytes) -> tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return b.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace"), "utf-8-replace"


def norm(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def num(v):
    s = norm(v).replace(",", "")
    if not s or s in {"-", "--", "N/A"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def discover_csv(html: str) -> list[str]:
    p = LinkParser()
    p.feed(html)

    links = []
    for href in p.links:
        u = urljoin(PAGE_URL, href)
        if ".csv" in u.lower():
            links.append(u)

    embedded = re.findall(r"""["']([^"']+\.csv(?:\?[^"']*)?)["']""", html, flags=re.I)
    for href in embedded:
        u = urljoin(PAGE_URL, href)
        if u not in links:
            links.append(u)

    return sorted(
        dict.fromkeys(links),
        key=lambda u: (
            0 if any(k in u.lower() for k in ("settlement", "derivative", "ose", "future")) else 1,
            u,
        ),
    )


def extract_publication_date(page_html: str) -> str | None:
    """
    JPX page text normally contains:
      As of the setting of Settlement Prices (Jul. 28, 2026)
    Return YYYY-MM-DD where possible.
    """
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    m = re.search(
        r"As\s+of\s+the\s+setting\s+of\s+Settlement\s+Prices\s*\(\s*"
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),\s*(\d{4})\s*\)",
        page_html,
        flags=re.I,
    )
    if not m:
        return None
    mon = months.get(m.group(1)[:3].lower())
    if not mon:
        return None
    return f"{int(m.group(3)):04d}-{mon:02d}-{int(m.group(2)):02d}"


def header_index(header: list[str], *names: str) -> int | None:
    cleaned = [norm(x).lower() for x in header]
    for name in names:
        key = norm(name).lower()
        for i, x in enumerate(cleaned):
            if x == key:
                return i
    return None


def is_standard_nikkei225_future(issue_name: str, underlying_name: str) -> bool:
    """
    Standard large Nikkei 225 future observed in JPX CSV:
      FUT_225_260910
    Mini observed:
      FUT_225M_260813
    Options carry Put/Call and strike information.

    Keep only FUT_225_... and explicitly exclude FUT_225M_...
    """
    issue = norm(issue_name).upper()
    underlying = norm(underlying_name).lower()
    return (
        bool(re.fullmatch(r"FUT_225_\d+", issue))
        and not issue.startswith("FUT_225M_")
        and "nikkei 225" in underlying
    )


def parse_csv(rows: list[list[str]]) -> tuple[list[dict], dict]:
    if not rows:
        return [], {}

    # Find the real header row instead of assuming row 0, because JPX CSV
    # may contain explanatory lines before the header.
    header_pos = None
    for i, r in enumerate(rows[:30]):
        joined = " | ".join(norm(x) for x in r).lower()
        if "issue code" in joined and "issue name" in joined and "contract month" in joined:
            header_pos = i
            break

    if header_pos is None:
        raise ValueError("JPX CSV header row not found")

    header = rows[header_pos]
    idx_issue_code = header_index(header, "Issue Code")
    idx_issue_name = header_index(header, "Issue Name")
    idx_put_call = header_index(header, "Put/Call", "Put / Call")
    idx_contract = header_index(header, "Contract Month")
    idx_strike = header_index(header, "Strike Price")
    idx_settle = header_index(header, "Settlement Price")
    idx_theo = header_index(header, "Theoretical Price")
    idx_underlying_price = header_index(header, "Underlying Price")
    idx_days = header_index(header, "Days until Maturity")
    idx_underlying_name = header_index(header, "Underlying Name")

    required = {
        "Issue Code": idx_issue_code,
        "Issue Name": idx_issue_name,
        "Contract Month": idx_contract,
        "Settlement Price": idx_settle,
        "Underlying Name": idx_underlying_name,
    }
    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError("Missing required JPX CSV columns: " + ", ".join(missing))

    parsed = []
    for r in rows[header_pos + 1:]:
        if not any(norm(x) for x in r):
            continue

        def cell(idx):
            return r[idx] if idx is not None and idx < len(r) else ""

        issue_name = norm(cell(idx_issue_name))
        underlying_name = norm(cell(idx_underlying_name))
        put_call = norm(cell(idx_put_call))
        strike = norm(cell(idx_strike))

        if not is_standard_nikkei225_future(issue_name, underlying_name):
            continue

        # Extra option guard: standard futures must not be Put/Call issues.
        if put_call:
            continue
        if strike:
            # Futures rows observed in JPX file have blank strike price.
            continue

        contract_month = re.sub(r"\D", "", norm(cell(idx_contract)))
        settlement = num(cell(idx_settle))
        if not re.fullmatch(r"\d{6}", contract_month) or settlement is None:
            continue

        parsed.append({
            "issueCode": norm(cell(idx_issue_code)),
            "issueName": issue_name,
            "product": "Nikkei 225 Futures",
            "contractMonth": contract_month,
            "settlementPrice": settlement,
            "theoreticalPrice": num(cell(idx_theo)),
            "underlyingPrice": num(cell(idx_underlying_price)),
            "daysUntilMaturity": int(num(cell(idx_days))) if num(cell(idx_days)) is not None else None,
            "underlyingName": underlying_name,
        })

    meta = {
        "headerRow": header,
        "headerPosition": header_pos,
        "matchedStandardFutures": len(parsed),
    }
    return parsed, meta


def choose_nearest(rows: list[dict]) -> dict | None:
    if not rows:
        return None

    # Prefer the smallest contract month among still-valid issues.
    # If Days until Maturity is present, exclude already expired negative-day rows.
    valid = [
        x for x in rows
        if x.get("daysUntilMaturity") is None or x["daysUntilMaturity"] >= 0
    ]
    pool = valid or rows
    return min(
        pool,
        key=lambda x: (
            int(x["contractMonth"]),
            x.get("daysUntilMaturity") if x.get("daysUntilMaturity") is not None else 10**9,
        ),
    )


def main():
    print("=" * 92)
    print("PLOUTOS // JPX OFFICIAL NIKKEI 225 MARKET CACHE PRODUCTION v1.0")
    print("[SECURITY] PUBLIC JPX DATA ONLY | NO YUANTA LOGIN | NO CREDENTIALS | NO TRADING")
    print("=" * 92)

    page_b = fetch_bytes(PAGE_URL)
    page_html, page_enc = decode_text(page_b)
    trading_date = extract_publication_date(page_html)
    print(f"[STEP 1 OK] JPX settlement page | encoding={page_enc} | date={trading_date or 'UNKNOWN'}")

    candidates = discover_csv(page_html)
    print(f"[STEP 2] CSV candidates={len(candidates)}")
    if not candidates:
        raise RuntimeError("No JPX settlement CSV link discovered")

    selected_url = None
    rows = None
    csv_encoding = None
    download_errors = []

    for u in candidates:
        try:
            b = fetch_bytes(u)
            txt, enc = decode_text(b)
            head = txt.lstrip()[:200].lower()
            if "<html" in head or "<!doctype" in head:
                raise ValueError("HTML response, not CSV")
            candidate_rows = list(csv.reader(io.StringIO(txt)))
            if not candidate_rows:
                raise ValueError("empty CSV")

            parsed_try, _ = parse_csv(candidate_rows)
            if parsed_try:
                selected_url = u
                rows = candidate_rows
                csv_encoding = enc
                break
            raise ValueError("CSV has no standard Nikkei 225 Futures rows")
        except Exception as exc:
            download_errors.append(f"{u} :: {type(exc).__name__}: {exc}")

    if not selected_url or rows is None:
        print("[ERROR] Could not find a usable JPX settlement CSV.", file=sys.stderr)
        for e in download_errors[:10]:
            print("  " + e, file=sys.stderr)
        sys.exit(2)

    parsed, meta = parse_csv(rows)
    nearest = choose_nearest(parsed)
    if nearest is None:
        print("[ERROR] No standard Nikkei 225 Futures row after filtering.", file=sys.stderr)
        sys.exit(3)

    payload = {
        "status": "OK",
        "source": "JPX Settlement Prices for Futures and Options",
        "sourcePage": PAGE_URL,
        "csvUrl": selected_url,
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tradingDate": trading_date,
        "nikkei225": nearest,
        "contracts": parsed,
        "diagnostic": {
            "csvEncoding": csv_encoding,
            "matchedStandardFutures": meta["matchedStandardFutures"],
            "excludedMiniPattern": "FUT_225M_*",
            "selection": "nearest non-expired standard Nikkei 225 Futures contract",
        },
        "security": {
            "containsAccount": False,
            "containsPassword": False,
            "containsCertificate": False,
            "containsApiSecret": False,
            "requiresYuantaLogin": False,
            "tradingFunctions": False,
        },
    }

    # Production guard:
    # generatedAtUtc changes every run, so compare the actual market payload first.
    # If JPX data is unchanged, do NOT rewrite the file. This lets GitHub Actions
    # "commit only when changed" work correctly and avoids meaningless commits.
    unchanged = False
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text(encoding="utf-8"))
            unchanged = (
                previous.get("status") == payload.get("status")
                and previous.get("tradingDate") == payload.get("tradingDate")
                and previous.get("nikkei225") == payload.get("nikkei225")
                and previous.get("contracts") == payload.get("contracts")
                and previous.get("source") == payload.get("source")
            )
        except Exception:
            unchanged = False

    if unchanged:
        print("[CACHE] JPX market data unchanged; existing jpx-market-latest.json preserved.")
    else:
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("[CACHE] jpx-market-latest.json updated.")

    print("[STEP 3 OK] Standard Nikkei 225 Futures identified")
    print(f"  issueName       = {nearest['issueName']}")
    print(f"  contractMonth   = {nearest['contractMonth']}")
    print(f"  settlementPrice = {nearest['settlementPrice']}")
    print(f"  underlyingPrice = {nearest['underlyingPrice']}")
    print(f"  daysToMaturity  = {nearest['daysUntilMaturity']}")
    print(f"[OUTPUT] {OUT.name}")
    print("[RESULT] OK")
    print("=" * 92)


if __name__ == "__main__":
    main()
