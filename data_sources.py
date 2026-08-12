# -*- coding: utf-8 -*-
"""External market-data adapters.

All upstream-specific URLs, headers, parsing, and fallbacks live here. The UI
and domain layers consume normalized Python values and do not depend on a
provider's response shape.
"""

import json
import re
import ssl
import urllib.parse
import urllib.request
from datetime import datetime


SPARK_DAYS = 60
INDEX_VALUATION_URL = "https://danjuanfunds.com/djapi/index_eva/dj"

DEFAULT_SSL_CONTEXT = ssl.create_default_context()
DEFAULT_SSL_CONTEXT.check_hostname = False
DEFAULT_SSL_CONTEXT.verify_mode = ssl.CERT_NONE

DEFAULT_FUND_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://fund.eastmoney.com/",
}
INDEX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def http_text(url, *, timeout=12, headers=None, ssl_context=DEFAULT_SSL_CONTEXT):
    request = urllib.request.Request(url, headers=headers or DEFAULT_FUND_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
        return response.read().decode("utf-8", errors="ignore")


def spark_from_lsjz(text):
    """Parse one Eastmoney LSJZ page and return valid values in source order."""
    try:
        data = json.loads(text)
    except Exception:
        return []
    points = []
    for item in ((data.get("Data") or {}).get("LSJZList") or []):
        try:
            value = float(item.get("DWJZ"))
            if value > 0:
                points.append(value)
        except Exception:
            pass
    return points


def _lsjz_page(code, page, size=SPARK_DAYS, ssl_context=DEFAULT_SSL_CONTEXT):
    url = (
        "https://api.fund.eastmoney.com/f10/lsjz?"
        f"fundCode={code}&pageIndex={page}&pageSize={size}"
    )
    return spark_from_lsjz(
        http_text(
            url,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fundf10.eastmoney.com/"},
            ssl_context=ssl_context,
        )
    )


def fetch_spark(code, *, days=SPARK_DAYS, ssl_context=DEFAULT_SSL_CONTEXT):
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise RuntimeError("基金代码无效")
    points = (
        _lsjz_page(code, 1, days, ssl_context)
        + _lsjz_page(code, 2, days, ssl_context)
        + _lsjz_page(code, 3, days, ssl_context)
    )
    points.reverse()
    return points[-days:]


def fetch_index_valuation(*, timeout=8, ssl_context=DEFAULT_SSL_CONTEXT):
    text = http_text(
        INDEX_VALUATION_URL,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0"},
        ssl_context=ssl_context,
    )
    data = json.loads(text)
    out = {}
    for item in (data.get("data") or {}).get("items") or []:
        name = (item.get("name") or "").strip()
        if name:
            out[name] = {
                "pe": item.get("pe") or 0,
                "pe_pct": round((item.get("pe_percentile") or 0) * 100, 1),
                "pb": item.get("pb") or 0,
                "pb_pct": round((item.get("pb_percentile") or 0) * 100, 1),
                # The upstream field is misspelled as ``yeild``.
                "yield_pct": round((item.get("yeild") or 0) * 100, 2),
            }
    return out


def fetch_one(code, *, ssl_context=DEFAULT_SSL_CONTEXT):
    last_error = ""
    try:
        text = http_text(
            f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(datetime.now().timestamp())}",
            ssl_context=ssl_context,
        )
        match = re.search(r"jsonpgz\((\{.*?\})\)", text)
        if match:
            data = json.loads(match.group(1))
            nav = float(data.get("dwjz", 0))
            change = float(data.get("gszzl", 0) or 0)
            nav_date = (data.get("jzrq") or "").strip()
            if nav > 0:
                return {
                    "code": code,
                    "name": data.get("name", ""),
                    "nav": nav,
                    "est": float(data.get("gsz", 0) or 0),
                    "chg": change,
                    "nav_date": nav_date,
                    "status": "ok",
                    "via": "估值接口",
                }
        last_error = "通道1无jsonpgz"
    except Exception as error:
        last_error = f"通道1异常:{error}"

    try:
        text = http_text(f"https://fund.eastmoney.com/pingzhongdata/{code}.js", ssl_context=ssl_context)
        match = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", text)
        name_match = re.search(r'fS_name\s*=\s*"(.*?)"', text)
        if match:
            rows = json.loads(match.group(1))
            if rows:
                nav = float(rows[-1].get("y", 0))
                change = 0.0
                if len(rows) >= 2 and rows[-2].get("y"):
                    previous = float(rows[-2]["y"])
                    change = round((nav - previous) / previous * 100, 2)
                if nav > 0:
                    try:
                        nav_date = datetime.fromtimestamp(int(rows[-1].get("x", 0)) / 1000).strftime("%Y-%m-%d")
                    except Exception:
                        nav_date = ""
                    return {
                        "code": code,
                        "name": name_match.group(1) if name_match else "",
                        "nav": nav,
                        "est": nav,
                        "chg": change,
                        "nav_date": nav_date,
                        "status": "ok",
                        "via": "详情接口(兜底)",
                    }
        last_error += " | 通道2无净值"
    except Exception as error:
        last_error += f" | 通道2异常:{error}"
    return {
        "code": code,
        "name": "",
        "nav": 0,
        "est": 0,
        "chg": 0,
        "status": "fail",
        "err": last_error.strip(" |"),
    }


def search_funds(key, *, ssl_context=DEFAULT_SSL_CONTEXT):
    key = (key or "").strip()
    if not key:
        return []
    try:
        url = (
            "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key="
            + urllib.parse.quote(key)
        )
        data = json.loads(
            http_text(
                url,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://fund.eastmoney.com/"},
                ssl_context=ssl_context,
            )
        )
        out = []
        for item in data.get("Datas") or []:
            code = str(item.get("CODE") or "").strip()
            name = str(item.get("NAME") or "").strip()
            if re.search(r"^\d{6}$", code) and name and (code, name) not in out:
                out.append((code, name))
        return out
    except Exception:
        return []


def fetch_history(code, *, ssl_context=DEFAULT_SSL_CONTEXT):
    text = http_text(f"https://fund.eastmoney.com/pingzhongdata/{code}.js", ssl_context=ssl_context)
    name_match = re.search(r'fS_name\s*=\s*"(.*?)"', text)
    match = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", text)
    if not match:
        raise RuntimeError("解析历史净值失败")
    rows = json.loads(match.group(1))
    history = []
    previous = None
    for item in rows:
        timestamp = int(item.get("x", 0))
        nav = float(item.get("y", 0))
        change = item.get("eqt")
        try:
            change = float(change) if change not in (None, "") else None
        except Exception:
            change = None
        if change is None and previous is not None and previous > 0:
            change = round((nav - previous) / previous * 100, 4)
        previous = nav
        history.append((timestamp, nav, change if change is not None else 0.0))

    rank_by_timestamp = {}
    rank_start_match = re.search(r"Data_rateInSimilarType\s*=\s*\[", text)
    rank_body = None
    if rank_start_match:
        start = rank_start_match.end() - 1
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for index in range(start, min(start + 2_000_000, len(text))):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end > start:
            rank_body = text[start : end + 1]
    if rank_body:
        try:
            for pair in json.loads(rank_body):
                if isinstance(pair, dict):
                    timestamp, rank = pair.get("x"), pair.get("y")
                elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    timestamp, rank = pair[0], pair[1]
                else:
                    continue
                try:
                    if timestamp not in (None, "") and rank not in (None, ""):
                        rank_by_timestamp[int(timestamp)] = int(rank)
                except Exception:
                    continue
        except Exception:
            rank_by_timestamp = {}
    return (name_match.group(1) if name_match else ""), history, rank_by_timestamp


def fetch_index_kline(secid, *, ssl_context=DEFAULT_SSL_CONTEXT):
    market = "sh" if secid.startswith("1.") else "sz"
    code = secid.split(".", 1)[1]
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={market}{code},day,,,800,qfq"
    text = http_text(url, timeout=8, headers=INDEX_HEADERS, ssl_context=ssl_context)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError(f"腾讯无json体 头={text[:80]!r}")
    data = json.loads(text[start : end + 1])
    node = ((data.get("data") or {}).get(f"{market}{code}")) or ((data.get("data") or {}).get(code)) or {}
    rows = node.get("day") or node.get("qfqday") or []
    if not rows:
        raise RuntimeError(f"腾讯klines空 node键={list(node.keys())[:6]}")
    out = []
    for row in rows:
        if len(row) < 3:
            continue
        try:
            out.append((row[0], float(row[2])))
        except Exception:
            continue
    if not out:
        raise RuntimeError("腾讯解析0条")
    return out
