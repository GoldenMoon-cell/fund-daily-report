# -*- coding: utf-8 -*-
"""Pure fund-ledger calculations.

Functions here do not read files, access the network, or import Qt. Keeping
these rules pure makes the accounting contract independently testable and is
the foundation for the v3 navigation pages.
"""

PB_FIRST_KEYWORDS = (
    "红利", "银行", "地产", "证券", "金融", "价值", "周期", "煤炭", "钢铁", "基建",
)
INDEX_ALIASES = [
    ("纳斯达克100", "纳指100"),
    ("纳斯达克", "纳指100"),
    ("恒生ETF", "恒生指数"),
]
NO_VAL_KEYWORDS = {
    "货币": "货币",
    "债": "债基",
    "黄金": "商品",
    "上海金": "商品",
    "原油": "商品",
    "石油": "商品",
    "豆粕": "商品",
    "能源化工": "商品",
    "REIT": "商品",
}
DUAL_METRIC_INDICES = ("恒生指数",)


def round_holding_record(record):
    """Normalize persisted numeric precision without dropping extra fields."""
    if not isinstance(record, dict):
        return record
    out = dict(record)
    if "shares" in out:
        out["shares"] = round(float(out["shares"]), 4)
    if "cost" in out:
        out["cost"] = round(float(out["cost"]), 4)
    if "principal" in out:
        out["principal"] = round(float(out["principal"]), 2)
    return out


def merge_holdings(nested):
    """Merge account-scoped holdings into one code-scoped view."""
    out = {}
    for holdings in (nested or {}).values():
        if not isinstance(holdings, dict):
            continue
        for code, record in holdings.items():
            if not isinstance(record, dict):
                continue
            shares = float(record.get("shares") or 0)
            principal = float(record.get("principal") or 0)
            buy_date = (record.get("buy_date") or "").strip()
            if code not in out:
                out[code] = {
                    "shares": shares,
                    "principal": principal,
                    "buy_date": buy_date,
                }
            else:
                out[code]["shares"] += shares
                out[code]["principal"] += principal
                old_date = out[code]["buy_date"]
                if buy_date and (not old_date or buy_date < old_date):
                    out[code]["buy_date"] = buy_date
    for record in out.values():
        shares = record["shares"]
        principal = record["principal"]
        record["cost"] = round(principal / shares, 4) if shares > 0 else 0.0
    return out


def resolve_holdings(holdings, price_map):
    """Resolve per-share cost and detect rows where principal was entered as cost."""
    info = {}
    for code, record in (holdings or {}).items():
        nav = (price_map or {}).get(code, 0)
        try:
            shares = float(record.get("shares") or 0)
            raw = float(record.get("cost") or 0)
        except Exception:
            shares = 0.0
            raw = 0.0
        cost_per_share = raw
        principal_per_share = raw / shares if shares > 0 else 0.0

        def near(value, current_nav=nav):
            return current_nav > 0 and value > 0 and 0.5 * current_nav <= value <= 2.0 * current_nav

        info[code] = {
            "shares": shares,
            "raw": raw,
            "per_near": near(cost_per_share),
            "principal_near": near(principal_per_share),
        }

    count = max(1, len(info))
    per_score = sum(1 for value in info.values() if value["per_near"])
    principal_score = sum(1 for value in info.values() if value["principal_near"])
    global_principal = principal_score > per_score and principal_score >= 0.6 * count

    resolved = {}
    corrected = set()
    for code, record in (holdings or {}).items():
        value = info[code]
        shares = value["shares"]
        raw = value["raw"]
        was_corrected = False
        cost = raw
        principal = record.get("principal")
        if global_principal and raw > 0 and shares > 0:
            if value["per_near"] and not value["principal_near"]:
                cost = raw
            else:
                cost = raw / shares
                principal = raw
                was_corrected = True
        resolved[code] = {
            "shares": shares,
            "cost": cost,
            "principal": principal,
            "corrected": was_corrected,
        }
        if was_corrected:
            corrected.add(code)
    return resolved, corrected


def nav_percentile(nav_list, nav):
    values = [value for value in nav_list if value and value > 0]
    if nav is None or nav <= 0 or len(values) < 60:
        return None
    below = sum(1 for value in values if value < nav)
    return round(below / len(values) * 100, 1)


def val_level(percentile):
    if percentile is None:
        return None
    if percentile >= 80:
        return "hot"
    if percentile <= 20:
        return "cold"
    return "mid"


def take_profit_level(percentile):
    if percentile is None:
        return None
    if percentile >= 20:
        return 20
    if percentile >= 15:
        return 15
    return None


def concentration_stats(resolved, price_map, name_map=None):
    market_values = []
    for code, record in (resolved or {}).items():
        shares = float((record or {}).get("shares") or 0)
        nav = (price_map or {}).get(code, 0)
        if shares > 0 and nav and nav > 0:
            market_values.append((code, shares * float(nav)))
    total = sum(value for _, value in market_values)
    if total <= 0:
        return None
    market_values.sort(key=lambda item: -item[1])
    names = name_map or {}
    return {
        "total": total,
        "n": len(market_values),
        "top1_pct": round(market_values[0][1] / total * 100, 1),
        "top1_name": names.get(market_values[0][0], market_values[0][0]),
        "top3_pct": round(sum(value for _, value in market_values[:3]) / total * 100, 1),
    }


def fund_valuation_class(fund_name):
    if not fund_name:
        return None
    for keyword in ("货币", "债"):
        if keyword in fund_name:
            return NO_VAL_KEYWORDS[keyword]
    for keyword, category in NO_VAL_KEYWORDS.items():
        if keyword in fund_name:
            return category
    return None


def match_index(fund_name, valuation_map):
    if not fund_name:
        return None
    best = None
    for index_name in valuation_map:
        if index_name in fund_name and (best is None or len(index_name) > len(best)):
            best = index_name
    if best:
        return best
    for alias, index_name in INDEX_ALIASES:
        if alias in fund_name and index_name in valuation_map:
            return index_name
    return None


def pick_index_pct(index_name, valuation):
    if (
        index_name in DUAL_METRIC_INDICES
        and (valuation.get("pe") or 0) > 0
        and (valuation.get("pb") or 0) > 0
    ):
        percentile = round((valuation["pe_pct"] + valuation["pb_pct"]) / 2, 1)
        metric = f"PE{valuation['pe_pct']:.0f}/PB{valuation['pb_pct']:.0f}"
        return percentile, metric
    order = (
        (("pb", "pb_pct"), ("pe", "pe_pct"))
        if any(keyword in index_name for keyword in PB_FIRST_KEYWORDS)
        else (("pe", "pe_pct"), ("pb", "pb_pct"))
    )
    for value_key, percentile_key in order:
        if (valuation.get(value_key) or 0) > 0 and valuation.get(percentile_key) is not None:
            return valuation[percentile_key], value_key.upper()
    return None, None


def val_signal_text(info):
    if not info or info.get("pct") is None:
        return None
    level = val_level(info["pct"])
    level_text = {"hot": "🔴 过热", "cold": "🟢 低估"}.get(level, "🟡 中性")
    metric = info.get("metric") or ""
    text = (
        f"{level_text} · {metric}"
        if "/" in metric
        else f"{level_text} · {metric} {info['pct']:.0f}%分位"
    )
    yield_percent = info.get("yield_pct")
    if info.get("src") == "index" and yield_percent:
        text += f" · 息{yield_percent:.1f}%"
    return text


def val_detail_text(info):
    if not info:
        return ""
    if info.get("src") == "na":
        return f"{info.get('metric', '')}·无估值口径"
    percentile = info.get("pct")
    if percentile is None:
        return ""
    level_text = {"hot": "过热", "cold": "低估"}.get(val_level(percentile), "中性")
    if info.get("src") == "nav":
        return f"估值参考：近 1 年净值分位 {percentile:.0f}%（{level_text}）·主动基金无 PE/PB 口径"
    parts = []
    if info.get("pe"):
        parts.append(f"PE {info['pe']:.2f}（{info.get('pe_pct', 0):.1f}%分位）")
    if info.get("pb"):
        parts.append(f"PB {info['pb']:.2f}（{info.get('pb_pct', 0):.1f}%分位）")
    if info.get("yield_pct"):
        parts.append(f"股息率 {info['yield_pct']:.2f}%")
    return f"估值参考（{info.get('idx', '跟踪指数')}，{level_text}）：" + "｜".join(parts)


def sort_card_codes(codes, mode, val_map=None, chg_map=None, mv_map=None):
    codes = list(codes)
    valuation_map = val_map or {}
    if mode == "val_asc":
        return sorted(
            codes,
            key=lambda code: (
                valuation_map.get(code) is None,
                valuation_map.get(code) if valuation_map.get(code) is not None else 0,
            ),
        )
    if mode == "val_desc":
        return sorted(codes, key=lambda code: (valuation_map.get(code) is None, -(valuation_map.get(code) or 0)))
    if mode == "chg_desc":
        return sorted(codes, key=lambda code: -(chg_map or {}).get(code, -999))
    if mode == "mv_desc":
        return sorted(codes, key=lambda code: -(mv_map or {}).get(code, -1))
    return codes


def replay_trades(code, nav_map, trades, account=None, default_account="默认"):
    shares = cost = principal = 0.0
    dates = sorted(nav_map)
    for trade in trades:
        if trade.get("code") != code:
            continue
        if account is not None and (trade.get("account") or default_account) != account:
            continue
        date = trade.get("date", "")
        nav = nav_map.get(date)
        if not nav:
            candidates = [item for item in dates if item <= date]
            nav = nav_map[candidates[-1]] if candidates else 0.0
        if nav <= 0:
            continue
        amount = float(trade.get("amount") or 0)
        trade_shares = float(trade.get("shares") or 0)
        side = trade.get("side")
        if side == "buy":
            added = trade_shares if trade_shares > 0 else amount / nav
            if added <= 0:
                continue
            shares += added
            principal += amount if amount > 0 else added * nav
            cost = principal / shares if shares else 0.0
        elif side in ("sell", "convert"):
            removed = trade_shares if trade_shares > 0 else (amount / nav if amount else 0.0)
            if removed <= 0:
                continue
            removed = min(removed, shares)
            principal = max(principal - cost * removed, 0.0)
            shares -= removed
            if shares <= 1e-9:
                shares = cost = principal = 0.0
            else:
                cost = principal / shares
        elif side == "open":
            shares = trade_shares
        elif side == "dividend_reinvest":
            shares += amount / nav
    return {"shares": shares, "cost": cost, "principal": principal}
