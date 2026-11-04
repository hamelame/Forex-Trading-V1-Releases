"""One-shot read-only price check. Run in the mobile service's Render Shell."""
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "https://demo-api.ig.com/gateway/deal"
FIELDS = ("IG_DEMO_API_KEY", "IG_DEMO_USERNAME", "IG_DEMO_PASSWORD")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def call(path, headers, version, body=None):
    if not (path == "/session" if body is not None else path.startswith("/markets")):
        raise ValueError("Unsupported operation")
    req = Request(HOST + path, headers={**headers, "Accept": "application/json",
                  "Content-Type": "application/json", "Version": str(version)},
                  data=json.dumps(body).encode() if body is not None else None)
    with build_opener(NoRedirect()).open(req, timeout=10) as res:
        result = json.loads(res.read(1000000))
        return result, res.headers


def candidates(rows, asset):
    pattern = r"\bgold\b" if asset == "Gold" else r"\b(wti|us (light )?crude|west texas)\b"
    selected = []
    for row in rows:
        name = str(row.get("instrumentName", ""))
        epic = str(row.get("epic", ""))
        if row.get("instrumentType") != "COMMODITIES" or not re.search(pattern, name, re.I):
            continue
        if re.search(r"\b(option|bull|bear|brent|ratio|spread)\b", name, re.I):
            continue
        if not re.fullmatch(r"[A-Za-z0-9._-]+", epic):
            continue
        selected.append(row)
    selected.sort(key=lambda r: (r.get("expiry") not in ("DFB", "-"), r.get("marketStatus") != "TRADEABLE"))
    return selected[:1]


def utc_age(stamp, now):
    if not isinstance(stamp, str) or not re.fullmatch(r"\d\d:\d\d:\d\d", stamp):
        return None
    try:
        h,m,s = map(int, stamp.split(":"))
        age = (now - now.replace(hour=h, minute=m, second=s, microsecond=0)).total_seconds()
        return int(age % 86400)
    except ValueError:
        return None


def sample(term, asset, headers, pinned=None):
    results, _ = call("/markets?searchTerm=" + quote(term), headers, 1)
    rows = results.get("markets", [])
    rows = candidates(rows, asset) if pinned is None else [r for r in rows if r.get("epic") == pinned]
    if not rows:
        print(asset, "no matching commodity quote")
        return None
    row = rows[0]
    epic = row["epic"]
    data, _ = call("/markets/" + quote(epic, safe=""), headers, 3)
    snap, inst = data.get("snapshot", {}), data.get("instrument", {})
    stamp = row.get("updateTimeUTC")
    age = utc_age(stamp, datetime.now(timezone.utc))
    bid, offer = row.get("bid"), row.get("offer")
    valid = all(type(x) in (float,int) and math.isfinite(x) for x in (bid, offer))
    delay = snap.get("delayTime")
    ok = (inst.get("epic") == epic and type(delay) in (int,float) and delay == 0
          and row.get("marketStatus") == snap.get("marketStatus") == "TRADEABLE"
          and age is not None and age <= 180 and valid and 0 < bid <= offer)
    print(asset, epic, row.get("instrumentName"), row.get("expiry"), "bid", bid, "offer", offer,
          "delay_minutes", delay, "quote_age_seconds", age, "fresh", ok, flush=True)
    return epic, stamp, ok


def main():
    missing = [k for k in FIELDS if not os.getenv(k, "").strip()]
    if missing:
        print("Missing Render settings:", ", ".join(missing))
        return
    headers = {"X-IG-API-KEY": os.environ[FIELDS[0]].strip()}
    auth, tokens = call("/session", headers, 2, {"identifier": os.environ[FIELDS[1]].strip(),
                        "password": os.environ[FIELDS[2]].strip()})
    if auth.get("reroutingEnvironment") not in (None, "DEMO"):
        raise ValueError("Unexpected demo environment")
    headers.update({"CST": tokens["CST"], "X-SECURITY-TOKEN": tokens["X-SECURITY-TOKEN"]})
    markets = [("Gold", "Gold"), ("US Crude", "WTI")]
    first = [sample(term, asset, headers) for term,asset in markets]
    if any(first):
        time.sleep(15)
    for (term,asset), previous in zip(markets, first):
        if previous:
            again = sample(term, asset, headers, previous[0])
            advancing = bool(previous[2] and again and again[2] and previous[1] != again[1])
            print(asset, "two fresh updates confirmed" if advancing else "fresh updates unconfirmed")


if __name__ == "__main__":
    try:
        main()
    except HTTPError as exc:
        print("IG DEMO HTTP", exc.code, "at", exc.url.split("?")[0])
    except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
        print("IG DEMO check incomplete:", type(exc).__name__)
