# -*- coding: utf-8 -*-
"""基金日报助手 - 桌面版
本地化的基金持仓看板与日报工具：实时估值/历史净值/回撤修复/同类排名/指数对比，
持仓本地存储，支持粘贴导入、交易记账、已清仓标记与 OCR 快照对照。"""
import sys
import json
import urllib.request
import re
import ssl
import traceback
import numpy as np
import os
import shutil
import tempfile
# 工作目录固定为程序所在目录，保证相对路径在源码/脚本/打包环境下行为一致
if getattr(sys, 'frozen', False):
    _BASE = os.path.dirname(sys.executable)
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_BASE)
from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QScrollArea, QSizePolicy, QMessageBox,
    QTextEdit, QDialog, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QButtonGroup, QGraphicsOpacityEffect, QComboBox, QLineEdit, QFileDialog, QSplitter
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPropertyAnimation, QAbstractAnimation
from PySide6.QtGui import QFont, QColor
import pyqtgraph as pg
from snapshot_view import SnapshotDialog, load_latest_snapshot as _load_snap

FUNDS = []   # 看板基金全部来自「自定义基金.json」；初始为空看板，用户经首页『快速添加』自行建立名单
EXTRA_FILE = "自定义基金.json"

def _load_extra():
    try:
        with open(EXTRA_FILE, "r", encoding="utf-8") as f:
            arr = json.load(f)
        out = []; seen = set()
        for it in arr:
            if not isinstance(it, dict): continue
            c = str(it.get("code", "")).strip(); n = str(it.get("name", "")).strip()
            if re.search(r"^\d{6}$", c) and n and c not in seen:
                out.append({"code": c, "name": n}); seen.add(c)
        return out
    except Exception:
        return []
def _save_extra(arr):
    try:
        with open(EXTRA_FILE, "w", encoding="utf-8") as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
for _it in _load_extra():
    if _it["code"] not in {c for c, _ in FUNDS}:
        FUNDS.append((_it["code"], _it["name"]))
NAME_MAP = {c: n for c, n in FUNDS}
HOLD_FILE = "我的持仓.json"
SHOW_FILE = "基金显示.json"   # 显示层状态(已清仓标记等)；缺席=默认，不碰持仓账本
RED, GREEN, GRAY = "#e53935", "#16a34a", "#888888"
TEAL = "#0891b2"
HL = QColor("#fff7d6")
IMP = QColor("#ffe0b2")
REPAIR_THRESHOLD = 0.5
CMP_INDEX = [("1.000300", "沪深300"), ("1.000905", "中证500"), ("1.000016", "上证50"), ("0.399006", "创业板指")]
# 基金类型样例库：仅用于详情页展示基金类型/跟踪指数说明，可自行增删
TRACK = {
    "000001": ("主动混合型", "国内老牌主动混合基金样例（方向以季报为准）"),
    "110011": ("主动混合型(QDII)", "优质企业主题主动选股样例（方向以季报为准）"),
    "161725": ("股票指数型", "跟踪 中证白酒指数（消费行业）"),
    "510300": ("股票指数型", "跟踪 沪深300指数（A股大盘蓝筹）"),
    "000217": ("商品型(黄金)", "跟踪 上海金Au99.99现货合约；跟国内金价走，国内金价≈国际金价×汇率÷31.1035±溢价"),
    "019548": ("股票指数型(QDII)", "跟踪 纳斯达克100指数（美股科技龙头）"),
    "013309": ("股票指数型(QDII)", "跟踪 恒生科技指数（港股科技龙头）"),
    "161120": ("债券指数型", "跟踪 中债-新综合指数（利率债+信用债综合）"),
}

_SSL = ssl.create_default_context(); _SSL.check_hostname = False; _SSL.verify_mode = ssl.CERT_NONE
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://fund.eastmoney.com/"}
_HEADERS_IDX = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def load_holdings():
    try:
        with open(HOLD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_show_state():
    try:
        with open(SHOW_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return set(str(c).strip() for c in (d.get("cleared") or []))
    except Exception:
        return set()


def save_show_state(cleared):
    try:
        with open(SHOW_FILE, "w", encoding="utf-8") as f:
            json.dump({"cleared": sorted(cleared)}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _round_rec(rec):
    """落库前抹浮点尾巴：shares4位/cost4位/principal2位；其余键(如buy_date)原样。"""
    if not isinstance(rec, dict):
        return rec
    out = dict(rec)
    if "shares"    in out: out["shares"]    = round(float(out["shares"]), 4)
    if "cost"      in out: out["cost"]      = round(float(out["cost"]), 4)
    if "principal" in out: out["principal"] = round(float(out["principal"]), 2)
    return out


def save_holdings(d):
    # 写前自动备份(只在原文件存在时备)
    try:
        if os.path.exists(HOLD_FILE):
            shutil.copy2(HOLD_FILE, HOLD_FILE + ".bak")
    except Exception:
        pass
    # 落库前 round 收尾
    d2 = {k: _round_rec(v) for k, v in d.items()}
    # 写入策略：先写同目录临时文件，再 os.replace 替换，避免中断导致 json 损坏
    base = os.path.basename(HOLD_FILE)
    fd, tmp = tempfile.mkstemp(prefix=base + ".", suffix=".tmp",
                               dir=os.path.dirname(os.path.abspath(HOLD_FILE)) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d2, f, ensure_ascii=False, indent=2)
        os.replace(tmp, HOLD_FILE)
    except Exception:
        try: os.remove(tmp)
        except Exception: pass
        raise


def apply_trade(holdings, code, action, *, nav=None, amount=None, shares=None,
                date=None, realized_out=None, cashflow_out=None):
    """在 holdings(完整dict) 上原地记一笔。返回该只最新快照 dict。
       约定：调用方必须先 d=load_holdings() 读全，改完 save_holdings(d) 写全，
       不可拿单只 dict 去 save_holdings，否则覆盖其余基金。
       action ∈ {buy, sell, dividend_reinvest, dividend_cash, convert}。
       buy: 需 nav, amount            → 加仓，补 shares/principal；若原无 buy_date 且给了 date 则补首笔
       sell: 需 nav, shares           → 赎回，减 shares/principal(按成本)，已实现盈亏进 realized_out
       dividend_reinvest: 需 nav, amount → 红利再投，加 shares，principal 不动
       dividend_cash: 需 amount        → 现金分红，份额/成本/本金全不动，金额进 cashflow_out
       convert: 暂按 sell 处理(转出方)，转入方另记一笔 buy
    """
    h = holdings.setdefault(code, {"shares": 0.0, "cost": 0.0, "principal": 0.0})
    sh = float(h.get("shares", 0) or 0)
    cost = float(h.get("cost", 0) or 0)
    prin = float(h.get("principal", 0) or 0)

    if action == "buy":
        add_sh = float(amount) / float(nav)
        new_prin = prin + float(amount)
        new_sh = sh + add_sh
        new_cost = (new_prin / new_sh) if new_sh else 0.0
        h["shares"], h["cost"], h["principal"] = new_sh, new_cost, new_prin
        if date and not (h.get("buy_date") or "").strip():
            h["buy_date"] = date.strip()
    elif action == "sell":
        sell_sh = float(shares)
        if sell_sh > sh + 1e-9:
            raise ValueError(f"赎回份额 {sell_sh} 超过持有 {sh}")
        cost_out = cost * sell_sh                 # 按成本摊出本金
        realized = sell_sh * (float(nav) - cost)  # 已实现盈亏
        new_sh = sh - sell_sh
        new_prin = prin - cost_out
        h["shares"], h["principal"] = new_sh, max(new_prin, 0.0)
        # cost 不变(移动加权平均)；份额归零时成本清零避免脏值
        if new_sh <= 1e-9:
            h["shares"], h["cost"], h["principal"] = 0.0, 0.0, 0.0
        if realized_out is not None: realized_out[0] = realized
    elif action == "dividend_reinvest":
        add_sh = float(amount) / float(nav)
        h["shares"] = sh + add_sh                 # principal 不动
    elif action == "dividend_cash":
        pass                                      # 三值全不动
        if cashflow_out is not None: cashflow_out[0] = float(amount)
    elif action == "convert":
        # 转出方按 sell 记；调用方对转入方再调一次 buy
        sell_sh = float(shares)
        cost_out = cost * sell_sh
        realized = sell_sh * (float(nav) - cost)
        new_sh = sh - sell_sh; new_prin = prin - cost_out
        h["shares"], h["principal"] = new_sh, max(new_prin, 0.0)
        if new_sh <= 1e-9:
            h["shares"], h["cost"], h["principal"] = 0.0, 0.0, 0.0
        if realized_out is not None: realized_out[0] = realized
    else:
        raise ValueError(f"未知 action: {action}")
    return h


def _http(url):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=12, context=_SSL) as r:
        return r.read().decode("utf-8", errors="ignore")


def _http_idx(url):
    req = urllib.request.Request(url, headers=_HEADERS_IDX)
    with urllib.request.urlopen(req, timeout=6, context=_SSL) as r:
        return r.read().decode("utf-8", errors="ignore")


def resolve_holdings(holdings, price_map):
    info = {}
    for code, rec in holdings.items():
        nav = price_map.get(code, 0)
        try:
            sh = float(rec.get("shares") or 0); raw = float(rec.get("cost") or 0)
        except Exception:
            sh = 0.0; raw = 0.0
        c_per = raw; c_pri = raw / sh if sh > 0 else 0.0
        def near(c, _nav=nav):
            return _nav > 0 and c > 0 and 0.5 * _nav <= c <= 2.0 * _nav
        info[code] = dict(sh=sh, raw=raw, per_near=near(c_per), pri_near=near(c_pri))
    n = max(1, len(info))
    score_per = sum(1 for v in info.values() if v["per_near"])
    score_pri = sum(1 for v in info.values() if v["pri_near"])
    global_principal = (score_pri > score_per and score_pri >= 0.6 * n)
    resolved = {}; corrected = set()
    for code, rec in holdings.items():
        v = info[code]; sh = v["sh"]; raw = v["raw"]
        corr = False; cost = raw; prin = rec.get("principal")
        if global_principal and raw > 0 and sh > 0:
            if v["per_near"] and not v["pri_near"]:
                cost = raw
            else:
                cost = raw / sh; prin = raw; corr = True
        resolved[code] = {"shares": sh, "cost": cost, "principal": prin, "corrected": corr}
        if corr:
            corrected.add(code)
    return resolved, corrected


def fetch_one(code):
    last_err = ""
    try:
        txt = _http(f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(datetime.now().timestamp())}")
        m = re.search(r"jsonpgz\((\{.*?\})\)", txt)
        if m:
            d = json.loads(m.group(1)); nav = float(d.get("dwjz", 0)); chg = float(d.get("gszzl", 0) or 0)
            jzrq = (d.get("jzrq") or "").strip()          # 净值日期(接口自带)
            if nav > 0:
                return {"code": code, "name": d.get("name", ""), "nav": nav,
                        "est": float(d.get("gsz", 0) or 0), "chg": chg, "nav_date": jzrq,
                        "status": "ok", "via": "估值接口"}
        last_err = "通道1无jsonpgz"
    except Exception as e:
        last_err = f"通道1异常:{e}"
    try:
        txt = _http(f"https://fund.eastmoney.com/pingzhongdata/{code}.js")
        m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", txt)
        name_m = re.search(r"fS_name\s*=\s*\"(.*?)\"", txt)
        if m:
            arr = json.loads(m.group(1))
            if arr:
                nav = float(arr[-1].get("y", 0)); chg = 0.0
                if len(arr) >= 2 and arr[-2].get("y"):
                    chg = round((nav - float(arr[-2]["y"])) / float(arr[-2]["y"]) * 100, 2)
                if nav > 0:
                    try: _nd = datetime.fromtimestamp(int(arr[-1].get("x", 0))/1000).strftime("%Y-%m-%d")
                    except Exception: _nd = ""
                    return {"code": code, "name": name_m.group(1) if name_m else "",
                            "nav": nav, "est": nav, "chg": chg, "nav_date": _nd,
                            "status": "ok", "via": "详情接口(兜底)"}
        last_err += " | 通道2无净值"
    except Exception as e:
        last_err += f" | 通道2异常:{e}"
    return {"code": code, "name": "", "nav": 0, "est": 0, "chg": 0, "status": "fail", "err": last_err.strip(" |")}


def fetch_history(code):
    txt = _http(f"https://fund.eastmoney.com/pingzhongdata/{code}.js")
    name_m = re.search(r"fS_name\s*=\s*\"(.*?)\"", txt)
    m = re.search(r"Data_netWorthTrend\s*=\s*(\[.*?\]);", txt)
    if not m:
        raise RuntimeError("解析历史净值失败")
    arr = json.loads(m.group(1)); hist = []; prev = None
    for it in arr:
        ts = int(it.get("x", 0)); nav = float(it.get("y", 0)); eqt = it.get("eqt")
        try:
            eqt = float(eqt) if eqt not in (None, "") else None
        except Exception:
            eqt = None
        if eqt is None and prev is not None and prev > 0:
            eqt = round((nav - prev) / prev * 100, 4)
        prev = nav; hist.append((ts, nav, eqt if eqt is not None else 0.0))
    # —— 同类排名解析 ——
    rank_by_ts = {}
    _m_rank = re.search(r"Data_rateInSimilarType\s*=\s*\[", txt)
    mr = None
    if _m_rank:
        _s = _m_rank.end() - 1
        _depth = 0; _in_str = False; _esc = False; _end = -1
        for _j in range(_s, min(_s + 2000000, len(txt))):
            _ch = txt[_j]
            if _esc: _esc = False; continue
            if _ch == '\\': _esc = True; continue
            if _ch == '"': _in_str = not _in_str; continue
            if _in_str: continue
            if _ch == '[': _depth += 1
            elif _ch == ']':
                _depth -= 1
                if _depth == 0: _end = _j; break
        if _end > _s:
            class _R:
                def __init__(self, body): self._b = body
                def group(self, n): return self._b
            mr = _R(txt[_s:_end+1])
    if mr:
        try:
            for pair in json.loads(mr.group(1)):
                if isinstance(pair, dict):
                    rts = pair.get("x"); rkv = pair.get("y")
                elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    rts = pair[0]; rkv = pair[1]
                else:
                    continue
                try:
                    if rts not in (None, "") and rkv not in (None, ""):
                        rank_by_ts[int(rts)] = int(rkv)
                except Exception:
                    continue
        except Exception:
            rank_by_ts = {}
    return (name_m.group(1) if name_m else ""), hist, rank_by_ts


def fetch_index_kline(secid):
    market = "sh" if secid.startswith("1.") else "sz"
    code6 = secid.split(".", 1)[1]
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={market}{code6},day,,,800,qfq")
    req = urllib.request.Request(url, headers=_HEADERS_IDX)
    with urllib.request.urlopen(req, timeout=8, context=_SSL) as r:
        txt = r.read().decode("utf-8", errors="ignore")
    a = txt.find("{"); b = txt.rfind("}")
    if a < 0 or b <= a:
        raise RuntimeError(f"腾讯无json体 头={txt[:80]!r}")
    d = json.loads(txt[a:b+1])
    node = ((d.get("data") or {}).get(f"{market}{code6}")) or ((d.get("data") or {}).get(code6)) or {}
    arr = node.get("day") or node.get("qfqday") or []
    if not arr:
        raise RuntimeError(f"腾讯klines空 node键={list(node.keys())[:6]}")
    out = []
    for row in arr:
        if len(row) < 3:
            continue
        try:
            out.append((row[0], float(row[2])))
        except Exception:
            continue
    if not out:
        raise RuntimeError("腾讯解析0条")
    return out


# ---------- 粘贴文本解析 ----------
def _norm(s):
    return re.sub(r"\s+", "", s or "")

def _norm_name(s):
    """对齐用: 删括号及内容(全/半角)+删空白+小写"""
    s = s or ""
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"\s+", "", s)
    return s.lower()

def _coerce_item(it):
    """单只基金dict: 各种字段名/别名 → 标准键。给啥认啥, 认不到返回None。"""
    if not isinstance(it, dict):
        return None
    def g(*keys):
        for k in keys:
            if k in it and it[k] not in (None, ""):
                return it[k]
        return None
    def fnum(x):
        try: return float(str(x).replace(",", "").replace("%", "").strip())
        except Exception: return None
    name = g("name", "名称", "基金名称", "fund_name", "title")
    code = g("code", "基金代码", "fund_code", "ts_code")
    if isinstance(code, str):
        m = re.search(r"\d{6}", code.strip()); code = m.group(0) if m else code.strip()
    amt  = g("amount", "持有金额", "市值", "持有市值", "market_value", "hold_amount", "value")
    hp   = g("hold_pnl", "持有收益", "持有盈亏", "累计收益", "累计盈亏", "收益", "pnl", "cum_pnl")
    hpr  = g("hold_pnl_rate_pct", "持有收益率", "累计收益率", "收益率", "收益比例", "rate", "pnl_rate")
    sh   = g("shares", "持有份额", "份额", "hold_shares")
    cost = g("cost", "持仓成本价", "成本价", "每份成本", "unit_cost", "avg_cost")
    prin = g("principal", "投入本金", "本金", "cost_amount")
    isc  = g("is_cash", "cash")
    typ  = g("asset_class", "类型", "分类", "category")
    if isc is None and typ is not None:
        t = str(typ); isc = ("现金" in t) or ("货币" in t) or ("cash" in t.lower())
    out = {"name": str(name) if name else "", "code": str(code) if code else ""}
    for k, v in (("amount", amt), ("hold_pnl", hp), ("hold_pnl_rate_pct", hpr),
                 ("shares", sh), ("cost", cost), ("principal", prin)):
        fv = fnum(v)
        if fv is not None: out[k] = fv
    if isc is not None:
        out["is_cash"] = bool(isc)
    elif name and ("余额宝" in str(name) or "现金" in str(name)):
        out["is_cash"] = True
    return out if (out.get("name") or out.get("code")) else None

def _normalize_snap_doc(raw):
    """任意json → 标准外壳 {holdings:[标准元素], totals:{amount,n_fund,n_cash}, nav_date}。"""
    items_raw = []; nav_date = "?"
    if isinstance(raw, list):
        items_raw = raw
    elif isinstance(raw, dict):
        nav_date = raw.get("nav_date") or raw.get("date") or raw.get("recorded_at") or "?"
        if isinstance(raw.get("holdings"), list): items_raw = raw["holdings"]
        elif isinstance(raw.get("funds"), list):  items_raw = raw["funds"]
        elif isinstance(raw.get("data"), list):   items_raw = raw["data"]
        else:
            meta = {"holdings", "funds", "data", "totals", "total", "summary",
                    "nav_date", "date", "recorded_at"}
            for k, v in raw.items():
                if k in meta or not isinstance(v, dict): continue
                vv = dict(v)
                if not vv.get("code") and not vv.get("name"):
                    vv["code"] = k if re.search(r"^\d{6}$", str(k).strip()) else ""
                    if not vv["code"]: vv["name"] = str(k)
                items_raw.append(vv)
    items = [c for c in (_coerce_item(x) for x in items_raw) if c]
    tot_amt = 0.0; n_cash = 0
    for c in items:
        if c.get("is_cash"): n_cash += 1
        if c.get("amount"): tot_amt += float(c["amount"])
    if isinstance(raw, dict) and isinstance(raw.get("totals"), dict):
        try:
            ta = float(raw["totals"].get("amount", 0) or 0)
            if ta > 0: tot_amt = ta
        except Exception: pass
    return {"holdings": items,
            "totals": {"amount": round(tot_amt, 2), "n_fund": len(items) - n_cash, "n_cash": n_cash},
            "nav_date": nav_date}

def parse_holdings_text(text):
    found = {}
    if not text:
        return found
    positions = []
    for code, name in FUNDS:
        pat = r"\s*".join(re.escape(ch) for ch in name)
        for m in re.finditer(pat, text):
            positions.append((m.start(), code, name))
    if not positions:
        return found
    positions.sort(key=lambda x: x[0])
    for idx, (start, code, name) in enumerate(positions):
        end = positions[idx+1][0] if idx+1 < len(positions) else len(text)
        block = text[start:end]
        shares = cost = None
        m = re.search(r"持有份额[^\d\-]{0,6}([\d,]+\.?\d*)", block)
        if m:
            try: shares = float(m.group(1).replace(",", ""))
            except Exception: pass
        m = re.search(r"持仓成本价?[^\d\-]{0,6}([\d,]+\.?\d*)", block)
        if m:
            try: cost = float(m.group(1).replace(",", ""))
            except Exception: pass
        if shares is not None or cost is not None:
            rec = found.get(code, {})
            if shares is not None: rec["shares"] = shares
            if cost is not None: rec["cost"] = cost
            found[code] = rec
    return found


class Worker(QThread):
    done = Signal(list)
    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        def _one(code_name):
            code, name = code_name
            r = fetch_one(code)
            if not r.get("name"): r["name"] = name
            return r
        res = [None] * len(FUNDS)
        with ThreadPoolExecutor(max_workers=8) as ex:
            fut2i = {ex.submit(_one, cn): i for i, cn in enumerate(FUNDS)}
            for fut in as_completed(fut2i):
                i = fut2i[fut]
                try:
                    res[i] = fut.result()
                except Exception as e:
                    res[i] = {"code": FUNDS[i][0], "name": FUNDS[i][1], "nav": 0,
                              "est": 0, "chg": 0, "status": "fail", "err": f"并发异常:{e}"}
        self.done.emit(res)


class HistWorker(QThread):
    done = Signal(str, str, list, object); fail = Signal(str, str)
    def __init__(self, code): super().__init__(); self.code = code
    def run(self):
        try:
            name, hist, rank_by_ts = fetch_history(self.code)
            self.done.emit(self.code, name or NAME_MAP.get(self.code, ""), hist, rank_by_ts)
        except Exception as e:
            self.fail.emit(self.code, str(e))


class IndexWorker(QThread):
    done = Signal(str, str, list); fail = Signal(str, str, str)
    def __init__(self, secid, name): super().__init__(); self.secid = secid; self.name = name
    def run(self):
        try:
            data = fetch_index_kline(self.secid)
            self.done.emit(self.secid, self.name, data)
        except Exception as e:
            tb = traceback.format_exc(limit=2).replace("\n", " ")
            self.fail.emit(self.secid, self.name, f"{type(e).__name__}:{e} | {tb[-150:]}")


class DateAxis(pg.AxisItem):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self._hist = []; self._fmt = "%m-%d"
    def set_data(self, hist, fmt):
        self._hist = hist; self._fmt = fmt
    def tickStrings(self, values, scale, spacing):
        out = []; n = len(self._hist)
        for v in values:
            i = int(round(v))
            out.append(datetime.fromtimestamp(self._hist[i][0]/1000).strftime(self._fmt) if 0 <= i < n else "")
        return out


class BarChart(pg.PlotWidget):
    def __init__(self):
        super().__init__(); self.setBackground("#ffffff"); self.showGrid(x=False, y=True, alpha=80)
        self.getAxis("left").setLabel("今日涨跌 %"); self.setMouseEnabled(x=False, y=False); self.hideButtons()
        self._bar_item = None; self._anim_timer = None
        self._bars_x = []; self._bars_vals = []; self._bars_colors = []; self._bars_names = []

    def draw(self, items, animate=True):
        self.clear(); self._bar_item = None
        if self._anim_timer:
            self._anim_timer.stop(); self._anim_timer = None
        vals = [it[1] for it in items]
        if not any(v != 0 for v in vals):
            t = pg.TextItem("暂无涨跌数据", color=(150,150,150), anchor=(0.5,0.5)); t.setFont(QFont("Microsoft YaHei",11)); self.addItem(t); t.setPos(len(vals)/2,0); return
        names = [it[0][:4] for it in items]; colors = [RED if v >= 0 else GREEN for v in vals]; x = list(range(len(vals)))
        self._bars_x = x; self._bars_vals = vals; self._bars_colors = colors; self._bars_names = names
        self._bar_item = pg.BarGraphItem(x=x, height=[0.0]*len(vals), width=0.6, brushes=colors, pens=colors)
        self.addItem(self._bar_item)
        vmin = min(vals); vmax = max(vals); span = max(vmax - vmin, 0.1); pad = max(span * 0.4, 0.35)
        self.setYRange(vmin - pad, vmax + pad, padding=0)
        self.getAxis("bottom").setTicks([list(zip(x, names))])
        if animate:
            self._step = 0; self._anim_timer = QTimer(self); self._anim_timer.timeout.connect(self._tick); self._anim_timer.start(28)
        else:
            self._finish()

    def _tick(self):
        self._step += 1; p = min(self._step / 12.0, 1.0); e = 1 - (1 - p) ** 3
        if self._bar_item:
            self._bar_item.setOpts(height=[v * e for v in self._bars_vals])
        if p >= 1.0:
            self._anim_timer.stop(); self._anim_timer = None; self._finish()

    def _finish(self):
        if self._bar_item:
            self._bar_item.setOpts(height=self._bars_vals)
        for xi, v in zip(self._bars_x, self._bars_vals):
            t = pg.TextItem(f"{v:+.2f}%", color=(0,0,0), anchor=(0.5, 1 if v >= 0 else 0)); t.setFont(QFont("Microsoft YaHei",8)); self.addItem(t); t.setPos(xi, v)


class ElideLabel(QLabel):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self._full = ""
    def setText(self, t):
        self._full = t or ""; super().setText(self._full); self._elide()
    def resizeEvent(self, e):
        super().resizeEvent(e); self._elide()
    def _elide(self):
        w = self.width()
        if w <= 0 or not self._full:
            return
        super().setText(self.fontMetrics().elidedText(self._full, Qt.ElideRight, w))


class FundCard(QFrame):
    def __init__(self, code):
        super().__init__(); self.code = code; self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet("FundCard{background:#fff;border:1px solid #eee;border-radius:10px;}FundCard:hover{border:1px solid #bcd;}")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QHBoxLayout(self); lay.setContentsMargins(14,12,14,12)
        left = QVBoxLayout(); left.setSpacing(2)
        self.lbl_name = ElideLabel("—"); self.lbl_name.setFont(QFont("Microsoft YaHei",11,QFont.Bold))
        self.lbl_name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); self.lbl_name.setMinimumWidth(0)
        self.lbl_code = QLabel(code); self.lbl_code.setFont(QFont("Microsoft YaHei",8)); self.lbl_code.setStyleSheet("color:#999;")
        left.addWidget(self.lbl_name); left.addWidget(self.lbl_code); lay.addLayout(left,3)
        mid = QVBoxLayout(); mid.setSpacing(2)
        self.lbl_nav = QLabel("净值 —"); self.lbl_nav.setFont(QFont("Microsoft YaHei",9)); self.lbl_nav.setStyleSheet("color:#666;")
        self.lbl_nav.setMinimumWidth(0)
        self.lbl_mv = QLabel(""); self.lbl_mv.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self.lbl_mv.setStyleSheet("color:#222;"); self.lbl_mv.setMinimumWidth(0)
        self.lbl_today = QLabel("今日 —"); self.lbl_today.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self.lbl_today.setMinimumWidth(0)
        self.lbl_pnl = QLabel("累计 —"); self.lbl_pnl.setFont(QFont("Microsoft YaHei",8)); self.lbl_pnl.setMinimumWidth(0)
        mid.addWidget(self.lbl_nav); mid.addWidget(self.lbl_mv); mid.addWidget(self.lbl_today); mid.addWidget(self.lbl_pnl); lay.addLayout(mid,2)
        self.lbl_chg = QLabel("—"); self.lbl_chg.setFont(QFont("Microsoft YaHei",16,QFont.Bold)); self.lbl_chg.setAlignment(Qt.AlignCenter); lay.addWidget(self.lbl_chg,2)
        right = QVBoxLayout(); right.setSpacing(6)
        self.btn_clear = QPushButton("标记清仓"); self.btn_clear.setFont(QFont("Microsoft YaHei",8))
        self.btn_clear.setCheckable(True)
        self.btn_clear.setStyleSheet("QPushButton{padding:4px 8px;border-radius:6px;background:#f5f5f5;color:#888;border:1px solid #ddd;}QPushButton:hover{background:#eee;}QPushButton:checked{background:#e8e8e8;color:#555;border:1px dashed #999;}")
        right.addWidget(self.btn_clear)
        self.btn_detail = QPushButton("详情 →"); self.btn_detail.setFont(QFont("Microsoft YaHei",9))
        self.btn_detail.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#eef3ff;color:#2563eb;border:none;}QPushButton:hover{background:#dbe6ff;}")
        right.addWidget(self.btn_detail,1); lay.addLayout(right,1)

    def set_cleared(self, on):
        self._cleared = bool(on)
        self.btn_clear.setChecked(self._cleared)
        self.btn_clear.setText("🚫 已清仓" if self._cleared else "标记清仓")
        self.btn_clear.setToolTip("已清仓: 不参与柱状图/红黑榜/总持仓统计, 卡片仅观察。点『恢复持有』还原。" if self._cleared else "卖出后盖灰章: 该只不再计入总账与榜单, 卡片置灰仅观察; 持仓记录不受影响, 可随时恢复。")
        if self._cleared:
            self.setStyleSheet("FundCard{background:#f6f6f6;border:1px dashed #bbb;border-radius:10px;}")
            self.lbl_chg.setText("已清仓"); self.lbl_chg.setStyleSheet("color:#999;font-size:12px;")
            self.lbl_mv.setText("仅观察·不计入总账"); self.lbl_mv.setStyleSheet("color:#aaa;")
            self.lbl_today.setText("—"); self.lbl_today.setStyleSheet("color:#bbb;")
            self.lbl_pnl.setText("持仓记录保留"); self.lbl_pnl.setStyleSheet("color:#bbb;")
            self.btn_detail.setEnabled(False); self.btn_detail.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#eee;color:#aaa;border:none;}")
        else:
            self.setStyleSheet("FundCard{background:#fff;border:1px solid #eee;border-radius:10px;}FundCard:hover{border:1px solid #bcd;}")
            self.btn_detail.setEnabled(True); self.btn_detail.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#eef3ff;color:#2563eb;border:none;}QPushButton:hover{background:#dbe6ff;}")

    def update_data(self, d, resolved, snap_rec=None):
        if getattr(self, "_cleared", False): return          # 灰章状态: 行情不覆盖灰章
        self.lbl_name.setText(d.get("name") or "—"); nav = d.get("nav",0)
        if d.get("status") != "ok":
            self.lbl_nav.setText("净值 抓取失败"); self.lbl_nav.setStyleSheet("color:#e53935;")
            self.lbl_chg.setText("—"); self.lbl_chg.setStyleSheet(f"color:{GRAY};")
            self.lbl_mv.setText(""); self.lbl_mv.setStyleSheet("color:#222;")
            self.lbl_today.setText("今日 —"); self.lbl_today.setStyleSheet("color:#bbb;")
            self.lbl_pnl.setText(f"⚠ {d.get('err','')[:18]}"); self.lbl_pnl.setStyleSheet("color:#e53935;font-size:8px;"); return
        self.lbl_nav.setText(f"净值 {nav:.4f}"); self.lbl_nav.setStyleSheet("color:#666;")
        chg = d.get("chg",0); color = RED if chg>0 else (GREEN if chg<0 else GRAY)
        self.lbl_chg.setText(f"{chg:+.2f}%"); self.lbl_chg.setStyleSheet(f"color:{color};")
        # 净值日期决定显示"今日"还是"截至xx-xx"
        nd = (d.get("nav_date") or "").strip()
        today_str = datetime.now().strftime("%Y-%m-%d")
        if nd and nd == today_str:
            day_tag = "今日"
        elif nd:
            day_tag = f"截至{nd[5:]}"
        else:
            day_tag = "今日"
        r2 = resolved.get(self.code)
        if r2 and r2.get("shares") and nav:
            sh = float(r2["shares"]); cost = float(r2.get("cost") or 0)
            mv = sh * nav
            self.lbl_mv.setText(f"持有 ¥{mv:,.2f}"); self.lbl_mv.setStyleSheet("color:#222;")
            if (100 + chg) != 0:
                today_pnl = sh * nav * chg / (100 + chg)
                tc = RED if today_pnl >= 0 else GREEN
                self.lbl_today.setText(f"{day_tag} {today_pnl:+,.2f}元"); self.lbl_today.setStyleSheet(f"color:{tc};")
            else:
                self.lbl_today.setText(f"{day_tag} +0.00元"); self.lbl_today.setStyleSheet(f"color:{GRAY};")
            if cost > 0:
                pnl = (nav-cost)*sh; pct = (nav-cost)/cost*100; pc = RED if pnl>=0 else GREEN
                tag = "🔧" if r2.get("corrected") else ""
                self.lbl_pnl.setText(f"{tag}累计 {pnl:+,.2f}元 ({pct:+.2f}%)"); self.lbl_pnl.setStyleSheet(f"color:{pc};")
            else:
                self.lbl_pnl.setText("累计 未填成本"); self.lbl_pnl.setStyleSheet("color:#bbb;")
        else:
            # 回退: 无手填份额时, 用OCR快照显示(标"≈")
            if snap_rec and nav and d.get("status") == "ok" and not snap_rec.get("is_cash"):
                amt = float(snap_rec.get("amount", 0) or 0)
                if amt > 0:
                    self.lbl_mv.setText(f"≈持有 ¥{amt:,.2f}"); self.lbl_mv.setStyleSheet("color:#888;")
                else:
                    self.lbl_mv.setText(""); self.lbl_mv.setStyleSheet("color:#222;")
                if amt and (100 + chg):
                    et = amt * (chg / (100 + chg)); tc = RED if et >= 0 else GREEN
                    self.lbl_today.setText(f"{day_tag}≈{et:+,.2f}元"); self.lbl_today.setStyleSheet(f"color:{tc};")
                else:
                    self.lbl_today.setText(f"{day_tag} —"); self.lbl_today.setStyleSheet("color:#bbb;")
                hp = snap_rec.get("hold_pnl"); hpr = snap_rec.get("hold_pnl_rate_pct")
                try: hp_f = float(hp)
                except Exception: hp_f = None
                try: hpr_f = float(hpr)
                except Exception: hpr_f = None
                if hp_f is not None:
                    pc = RED if hp_f >= 0 else GREEN
                    rs = f" ({hpr_f:+.2f}%)" if hpr_f is not None else ""
                    self.lbl_pnl.setText(f"累计≈{hp_f:+,.2f}元{rs}"); self.lbl_pnl.setStyleSheet(f"color:{pc};")
                else:
                    self.lbl_pnl.setText("盈亏 未填持仓"); self.lbl_pnl.setStyleSheet("color:#bbb;")
            else:
                self.lbl_mv.setText(""); self.lbl_mv.setStyleSheet("color:#222;")
                self.lbl_today.setText(f"{day_tag} —"); self.lbl_today.setStyleSheet("color:#bbb;")
                self.lbl_pnl.setText("盈亏 未填持仓"); self.lbl_pnl.setStyleSheet("color:#bbb;")

RANGE_DAYS = {"近1月": 30, "近3月": 90, "近6月": 180, "近1年": 365, "全部": None}

class DetailPage(QWidget):
    def __init__(self, on_back):
        super().__init__(); self._hist=[]; self._full=[]; self._code=None; self._my_rec2={}
        self._dd=[]; self._dd_max=0.0; self._dd_max_idx=0
        self._dd_state="none"; self._dd_days=0; self._dd_progress=0.0; self._repair_idx=None; self._view="nav"
        self._buy_date=""
        self._cmp_code=None; self._cmp_name=""; self._cmp_on=False
        self._idx_cache={}; self._idx_err={}; self._fund_y_by_idx={}
        lay = QVBoxLayout(self); lay.setContentsMargins(18,14,18,14); lay.setSpacing(10)
        top = QHBoxLayout()
        self.btn_back = QPushButton("← 返回"); self.btn_back.setFont(QFont("Microsoft YaHei",10))
        self.btn_back.setStyleSheet("QPushButton{padding:8px 14px;border-radius:8px;background:#f0f0f0;border:none;}QPushButton:hover{background:#e3e3e3;}")
        self.btn_back.clicked.connect(on_back); top.addWidget(self.btn_back)
        self.lbl_title = QLabel("基金详情"); self.lbl_title.setFont(QFont("Microsoft YaHei",14,QFont.Bold)); top.addWidget(self.lbl_title)
        top.addStretch()
        self.lbl_my = QLabel(""); self.lbl_my.setFont(QFont("Microsoft YaHei",10)); top.addWidget(self.lbl_my); lay.addLayout(top)
        self.lbl_track = QLabel(""); self.lbl_track.setFont(QFont("Microsoft YaHei",8))
        self.lbl_track.setStyleSheet("QLabel{color:#6b7280;background:#f7f9fc;border:1px solid #eef0f3;border-radius:6px;padding:4px 8px;}")
        self.lbl_track.setWordWrap(True); self.lbl_track.hide(); lay.addWidget(self.lbl_track)
        vbar = QHBoxLayout()
        self._vbg = QButtonGroup(self); self._vbtns = {}
        for i,(k,ico) in enumerate([("nav","📈 净值走势"),("dd","📉 回撤修复"),("rank","🏅 同类排名")]):
            b = QPushButton(ico); b.setCheckable(True); b.setFont(QFont("Microsoft YaHei",9))
            b.setStyleSheet("QPushButton{padding:6px 14px;border:1px solid #ddd;border-radius:7px;background:#fff;}"
                            "QPushButton:checked{background:#374151;color:#fff;border-color:#374151;}")
            self._vbg.addButton(b,i); self._vbtns[k]=b; vbar.addWidget(b)
        self._vbtns["nav"].setChecked(True); self._vbg.buttonClicked.connect(lambda _: self._set_view())
        vbar.addStretch()
        self._cmp_lbl = QLabel("对比"); self._cmp_lbl.setStyleSheet("color:#666;font-size:9px;"); vbar.addWidget(self._cmp_lbl)
        self._cmp_combo = QComboBox(); self._cmp_combo.setFont(QFont("Microsoft YaHei",9))
        self._cmp_combo.addItem("不对比", None)
        for secid, nm in CMP_INDEX:
            self._cmp_combo.addItem(nm, secid)
        self._cmp_combo.setStyleSheet("QComboBox{padding:4px 8px;border:1px solid #ddd;border-radius:7px;background:#fff;}")
        self._cmp_combo.currentIndexChanged.connect(self._on_cmp_change); vbar.addWidget(self._cmp_combo)
        self._cmp_hint = QLabel(""); self._cmp_hint.setStyleSheet("color:#b45309;font-size:8px;"); self._cmp_hint.setWordWrap(True); vbar.addWidget(self._cmp_hint)
        lay.addLayout(vbar)
        rbar = QHBoxLayout(); rbar.addStretch(); self._bg = QButtonGroup(self); self._rbtns = {}
        for i,k in enumerate(RANGE_DAYS.keys()):
            b = QPushButton(k); b.setCheckable(True); b.setFont(QFont("Microsoft YaHei",9))
            b.setStyleSheet("QPushButton{padding:6px 12px;border:1px solid #ddd;border-radius:7px;background:#fff;}QPushButton:checked{background:#2563eb;color:#fff;border-color:#2563eb;}")
            self._bg.addButton(b,i); self._rbtns[k]=b; rbar.addWidget(b)
        self._rbtns["近1年"].setChecked(True); self._bg.buttonClicked.connect(lambda _: self._apply_range()); lay.addLayout(rbar)
        self.dd_box = QFrame(); self.dd_box.setStyleSheet("QFrame{background:#fff5f5;border:1px solid #f3c2c2;border-radius:10px;}")
        dl = QHBoxLayout(self.dd_box); dl.setContentsMargins(14,10,14,10)
        self.lbl_dd_max = QLabel("最大回撤  —"); self.lbl_dd_max.setFont(QFont("Microsoft YaHei",11,QFont.Bold)); self.lbl_dd_max.setStyleSheet("color:#c0392b;")
        self.lbl_dd_rep = QLabel("修复  —"); self.lbl_dd_rep.setFont(QFont("Microsoft YaHei",11,QFont.Bold)); self.lbl_dd_rep.setStyleSheet("color:#888;")
        dl.addWidget(self.lbl_dd_max); dl.addSpacing(28); dl.addWidget(self.lbl_dd_rep); dl.addStretch()
        self.dd_box.hide(); lay.addWidget(self.dd_box)
        chart_box = QFrame(); chart_box.setStyleSheet("QFrame{background:#fff;border:1px solid #eee;border-radius:10px;}")
        cl = QVBoxLayout(chart_box); cl.setContentsMargins(8,8,8,8)
        self._date_axis = DateAxis(orientation='bottom')
        self.plot = pg.PlotWidget(axisItems={'bottom': self._date_axis})
        self.plot.setBackground("#ffffff"); self.plot.showGrid(x=True,y=True,alpha=60)
        self.plot.setMouseEnabled(x=True,y=False); self.plot.hideButtons()
        self.plot.getAxis("bottom").setLabel("日期")
        self.plot.getAxis("left").setLabel("单位净值")
        self.plot.getAxis("left").enableAutoSIPrefix(False)
        self._curve = self.plot.plot(pen=pg.mkPen("#2563eb", width=2))
        self._dd_curve = self.plot.plot(pen=pg.mkPen("#ef4444", width=2))
        self._dd_curve.hide()
        self._cmp_curve = self.plot.plot(pen=pg.mkPen(TEAL, width=2))
        self._cmp_curve.hide()
        self._rank_curve = self.plot.plot(pen=pg.mkPen("#f59e0b", width=2))
        self._rank_curve.hide()
        self._rank_curve.setClipToView(True)
        self._curve.setClipToView(True); self._curve.setDownsampling(auto=True, method='peak')
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#999",width=1,style=Qt.DashLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#999",width=1,style=Qt.DashLine))
        self._vline.hide(); self._hline.hide()
        self._dot = pg.ScatterPlotItem(size=10, brush=pg.mkBrush("#2563eb"), pen=pg.mkPen("#fff"))
        self.plot.addItem(self._vline); self.plot.addItem(self._hline); self.plot.addItem(self._dot)
        self._repair_region = pg.LinearRegionItem(values=[0,1], brush=pg.mkBrush(100,116,139,24), pen=pg.mkPen(None), movable=False)
        self._repair_region.setZValue(-1); self._repair_region.hide(); self.plot.addItem(self._repair_region)
        self._zero_line = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen("#10b981", width=1.6, style=Qt.DashLine))
        self._zero_line.hide(); self.plot.addItem(self._zero_line)
        self._zero_label = pg.TextItem("前高线 0%  回到此线=修复", color=(16,185,129), anchor=(1,0))
        self._zero_label.setFont(QFont("Microsoft YaHei",8,QFont.Bold)); self._zero_label.hide(); self.plot.addItem(self._zero_label)
        self._dd_marker = pg.ScatterPlotItem(size=18, pen=pg.mkPen("#fff",width=2), brush=pg.mkBrush("#dc2626"))
        self._dd_marker.hide(); self.plot.addItem(self._dd_marker)
        self._repair_marker = pg.ScatterPlotItem(size=15, pen=pg.mkPen("#fff",width=2), brush=pg.mkBrush("#16a34a"))
        self._repair_marker.hide(); self.plot.addItem(self._repair_marker)
        self._dd_label = pg.TextItem("", color=(220,38,38), anchor=(0.5,0)); self._dd_label.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self._dd_label.hide(); self.plot.addItem(self._dd_label)
        self._repair_label = pg.TextItem("", color=(22,163,74), anchor=(0.5,1)); self._repair_label.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self._repair_label.hide(); self.plot.addItem(self._repair_label)
        self._region_label = pg.TextItem("", color=(180,90,20), anchor=(0.5,0.5)); self._region_label.setFont(QFont("Microsoft YaHei",8,QFont.Bold)); self._region_label.hide(); self.plot.addItem(self._region_label)
        self._cost_line = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen("#7c3aed", width=1.6, style=Qt.DashLine))
        self._cost_line.hide(); self.plot.addItem(self._cost_line)
        self._cost_label = pg.TextItem("", color=(124,58,237), anchor=(1,0.5)); self._cost_label.setFont(QFont("Microsoft YaHei",8,QFont.Bold)); self._cost_label.hide(); self.plot.addItem(self._cost_label)
        self._buy_line = pg.InfiniteLine(pos=0, angle=90, movable=False, pen=pg.mkPen("#7c3aed", width=1.8, style=Qt.DashLine))
        self._buy_line.hide(); self.plot.addItem(self._buy_line)
        self._buy_dot = pg.ScatterPlotItem(size=16, pen=pg.mkPen("#fff",width=2), brush=pg.mkBrush("#7c3aed"))
        self._buy_dot.hide(); self.plot.addItem(self._buy_dot)
        self._buy_label = pg.TextItem("", color=(124,58,237), anchor=(0,1)); self._buy_label.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self._buy_label.hide(); self.plot.addItem(self._buy_label)
        self._buy_off = pg.TextItem("", color=(167,139,250), anchor=(0.5,0.5)); self._buy_off.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); self._buy_off.hide(); self.plot.addItem(self._buy_off)
        self._legend = QLabel(""); self._legend.setParent(self.plot)
        self._legend.setStyleSheet("QLabel{background:rgba(255,255,255,225);border:1px solid #ddd;border-radius:6px;padding:4px 8px;color:#333;font-size:9px;}")
        self._legend.setFont(QFont("Microsoft YaHei",9)); self._legend.hide(); self._legend.raise_()
        self._readout = QLabel("移动鼠标看每日数值"); self._readout.setParent(self.plot)
        self._readout.setStyleSheet("QLabel{background:rgba(255,255,255,220);border:1px solid #ddd;border-radius:6px;padding:5px 8px;color:#333;}")
        self._readout.setFont(QFont("Microsoft YaHei",9)); self._readout.move(10,8); self._readout.raise_()
        self.plot.scene().sigMouseMoved.connect(self._mouse_moved)
        self._loading = QLabel("⏳  加载历史净值中…"); self._loading.setFont(QFont("Microsoft YaHei",12)); self._loading.setAlignment(Qt.AlignCenter); self._loading.setStyleSheet("color:#888;")
        self._stack_chart = QStackedWidget(); self._stack_chart.addWidget(self._loading); self._stack_chart.addWidget(self.plot)
        self._stack_chart.setFixedHeight(320); cl.addWidget(self._stack_chart); lay.addWidget(chart_box,3)
        self.table = QTableWidget(0,3); self.table.setHorizontalHeaderLabels(["日期","单位净值","当日涨跌"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setAlternatingRowColors(True); self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(False)
        self.table.setStyleSheet("QTableWidget{font-size:12px;}"); lay.addWidget(self.table,2)
        self._hist_worker = None; self._idx_worker = None
        self._rank_by_ts = {}; self._rank_full = []

    def load(self, code, rec2):
        self._code = code; self.lbl_title.setText(f"{NAME_MAP.get(code,'')}  详情")
        tk = TRACK.get(code)
        if tk:
            self.lbl_track.setText(f"📌 {tk[0]} ｜ {tk[1]}"); self.lbl_track.show()
        else:
            self.lbl_track.hide()
        self.lbl_my.setText(""); self._full=[]; self._hist=[]; self._dd=[]
        self._dd_state="none"; self._dd_days=0; self._dd_progress=0.0; self._repair_idx=None
        self._buy_date = load_holdings().get(code, {}).get("buy_date", "") or ""
        self._buy_date = self._buy_date.strip()
        self._cmp_on=False; self._fund_y_by_idx={}
        self._rank_by_ts = {}; self._rank_full = []
        self._curve.setData([]); self._dd_curve.setData([]); self._cmp_curve.setData([]); self._cmp_curve.hide()
        self._dot.setData([]); self._legend.hide()
        self._hide_dd_markers(); self._hide_buy_markers()
        self._vline.hide(); self._hline.hide()
        self._stack_chart.setCurrentWidget(self._loading); self._readout.setText("移动鼠标看每日数值")
        self._my_rec2 = rec2 or {}
        self._hist_worker = HistWorker(code); self._hist_worker.done.connect(self._on_hist); self._hist_worker.fail.connect(self._on_hist_fail); self._hist_worker.start()

    def _on_hist_fail(self, code, err): self._loading.setText(f"⚠ 历史净值加载失败：{err}")
    def _on_hist(self, code, name, hist, rank_by_ts):
        if code != self._code: return
        self._full = hist
        self._rank_by_ts = rank_by_ts or {}
        self._compute_rank()
        if name: self.lbl_title.setText(f"{name} 详情")
        self._apply_range()
        self._stack_chart.setCurrentWidget(self.plot)

    def _set_view(self):
        if self._vbtns["rank"].isChecked(): self._view = "rank"
        elif self._vbtns["dd"].isChecked(): self._view = "dd"
        else: self._view = "nav"
        self._draw_curve(); self._fill_table(); self._update_dd_stats()

    def _apply_range(self):
        days = None
        for k,b in self._rbtns.items():
            if b.isChecked(): days = RANGE_DAYS[k]; break
        if not self._full: return
        if days is None: self._hist = self._full
        else:
            cutoff = (datetime.now()-timedelta(days=days)).timestamp()*1000
            self._hist = [p for p in self._full if p[0] >= cutoff] or self._full
        self._compute_dd()
        self._compute_rank()
        self._draw_curve(); self._fill_table(); self._update_my_pnl(); self._update_dd_stats()

    def _on_cmp_change(self, idx):
        secid = self._cmp_combo.currentData(); name = self._cmp_combo.currentText()
        if not secid:
            self._cmp_code=None; self._cmp_name=""; self._cmp_on=False; self._cmp_hint.setText(""); self._draw_curve(); return
        self._cmp_code=secid; self._cmp_name=name
        if self._idx_cache.get(secid):
            self._cmp_hint.setText(""); self._draw_curve(); return
        self._cmp_hint.setText(f"⏳ 加载{name}（稍候）…"); self._cmp_combo.setEnabled(False)
        self._idx_worker = IndexWorker(secid, name)
        self._idx_worker.done.connect(self._on_idx_done); self._idx_worker.fail.connect(self._on_idx_fail)
        self._idx_worker.start()

    def _on_idx_done(self, secid, name, data):
        if secid != self._cmp_code: return
        self._idx_cache[secid] = data or []; self._cmp_combo.setEnabled(True)
        if not data:
            self._cmp_hint.setText(f"⚠ {name}无数据 secid={secid}")
        else:
            self._cmp_hint.setText("")
        self._draw_curve()

    def _on_idx_fail(self, secid, name, err):
        if secid != self._cmp_code: return
        self._idx_err[secid] = err; self._idx_cache[secid] = []; self._cmp_combo.setEnabled(True)
        self._cmp_hint.setText(f"⚠ {name}失败 secid={secid}：{err[:240]}")
        self._draw_curve()

    def _aligned_for_cmp(self):
        if not self._cmp_code or not self._idx_cache.get(self._cmp_code) or not self._hist:
            return []
        idx_dict = {d: c for d, c in self._idx_cache[self._cmp_code]}
        out = []
        for i, (ts, nav, eqt) in enumerate(self._hist):
            ds = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d")
            c = idx_dict.get(ds)
            if c is not None and c > 0:
                out.append((i, nav, c))
        return out

    def _compute_dd(self):
        self._dd = []; self._dd_max = 0.0; self._dd_max_idx = 0
        self._dd_state = "none"; self._dd_days = 0; self._dd_progress = 0.0; self._repair_idx = None
        peak = -1e18; peaks = []
        for i,(ts,nav,eqt) in enumerate(self._hist):
            if nav > peak: peak = nav
            peaks.append(peak)
            dd = (nav-peak)/peak*100.0 if peak > 0 else 0.0
            self._dd.append(dd)
            if dd < self._dd_max:
                self._dd_max = dd; self._dd_max_idx = i
        if self._dd_max > -0.01:
            self._dd_state = "none"; return
        di = self._dd_max_idx; peak_at_dd = peaks[di]
        for j in range(di+1, len(self._hist)):
            if self._hist[j][1] >= peak_at_dd:
                self._dd_state = "yes"
                self._dd_days = max(int(round((self._hist[j][0]-self._hist[di][0])/86400000.0)), 0)
                self._dd_progress = 1.0; self._repair_idx = j; return
        dmax = self._dd_max; dend = self._dd[-1]
        p = (dend - dmax)/(-dmax) if dmax < 0 else 0.0
        p = max(0.0, min(1.0, p)); self._dd_progress = p; self._repair_idx = len(self._hist)-1
        self._dd_state = "fixing" if p >= REPAIR_THRESHOLD else "no"

    def _compute_rank(self):
        if not self._rank_by_ts or not self._hist:
            return
        rkeys = sorted(self._rank_by_ts.keys())
        if not rkeys:
            return
        import bisect
        DAY = 86400000
        for i, (ts, nav, eqt) in enumerate(self._hist):
            p = bisect.bisect_left(rkeys, ts)
            best = None
            for cand in (rkeys[p-1] if p > 0 else None,
                         rkeys[p]   if p < len(rkeys) else None):
                if cand is not None and abs(cand - ts) <= DAY + 3600000:
                    if best is None or abs(cand - ts) < abs(best - ts):
                        best = cand
            if best is not None:
                self._rank_full.append((i, self._rank_by_ts[best]))
    def _hide_dd_markers(self):
        for it in (self._zero_line, self._zero_label, self._repair_region, self._dd_marker, self._repair_marker,
                   self._dd_label, self._repair_label, self._region_label):
            it.hide()

    def _hide_buy_markers(self):
        for it in (self._cost_line, self._cost_label, self._buy_line, self._buy_dot, self._buy_label, self._buy_off):
            it.hide()

    def _show_dd_markers(self):
        n = len(self._hist)
        if n == 0 or not self._dd:
            self._hide_dd_markers(); return
        self._zero_line.show(); self._zero_label.setPos(n-1, 0.0); self._zero_label.show()
        st = self._dd_state
        if st == "none":
            for it in (self._repair_region, self._dd_marker, self._repair_marker, self._dd_label, self._repair_label, self._region_label):
                it.hide()
            return
        di = self._dd_max_idx; dv = self._dd[di]
        self._dd_marker.setData([di], [dv]); self._dd_marker.show()
        self._dd_label.setText(f"▼ 最大回撤 {abs(dv):.2f}%"); self._dd_label.setPos(di, dv); self._dd_label.show()
        ri = self._repair_idx if self._repair_idx is not None else n-1
        a = min(di, ri); b = max(di, ri)
        if st == "yes":
            col = (22,163,74); brush = pg.mkBrush(22,163,74,32); rtxt = f"↔ 修复期 {self._dd_days} 天"
            ebrush = "#16a34a"; etxt = "▲ 已修复"; ey = 0.0
        elif st == "fixing":
            pct = self._dd_progress*100
            col = (234,88,12); brush = pg.mkBrush(234,88,12,30); rtxt = f"↔ 修复中 已填{pct:.0f}%"
            ebrush = "#ea580c"; etxt = f"● 修复中 {pct:.0f}%"; ey = self._dd[-1]
        else:
            pct = self._dd_progress*100
            col = (100,116,139); brush = pg.mkBrush(100,116,139,26)
            rtxt = f"↔ 仍在坑中 已填{pct:.0f}%" if pct > 1 else "↔ 仍在坑中"
            ebrush = "#64748b"; etxt = "● 暂未修复"; ey = self._dd[-1]
        if b > a:
            self._repair_region.setRegion([a, b]); self._repair_region.setBrush(brush); self._repair_region.show()
            self._region_label.setText(rtxt); self._region_label.setColor(col); self._region_label.setPos((a+b)/2.0, dv*0.5); self._region_label.show()
        else:
            self._repair_region.hide(); self._region_label.hide()
        self._repair_marker.setBrush(pg.mkBrush(ebrush)); self._repair_marker.setData([ri], [ey]); self._repair_marker.show()
        self._repair_label.setColor(col); self._repair_label.setText(etxt); self._repair_label.setPos(ri, ey); self._repair_label.show()

    def _draw_buy_markers(self):
        cost = float(self._my_rec2.get("cost") or 0)
        n = len(self._hist)
        if self._view == "nav" and not self._cmp_on and cost > 0 and n > 0:
            self._cost_line.setPos(cost); self._cost_line.show()
            self._cost_label.setText(f"成本线 {cost:.4f}"); self._cost_label.setPos(n-1, cost); self._cost_label.show()
        else:
            self._cost_line.hide(); self._cost_label.hide()
        bd = self._buy_date
        if not bd or n == 0:
            self._buy_line.hide(); self._buy_dot.hide(); self._buy_label.hide(); self._buy_off.hide(); return
        try:
            buy_ts = datetime.strptime(bd, "%Y-%m-%d").timestamp()*1000
        except Exception:
            self._buy_line.hide(); self._buy_dot.hide(); self._buy_label.hide(); self._buy_off.hide(); return
        t0 = self._hist[0][0]; t1 = self._hist[-1][0]
        if buy_ts < t0 or buy_ts > t1:
            self._buy_line.hide(); self._buy_dot.hide(); self._buy_label.hide()
            vr = self.plot.viewRange(); ymid = (vr[1][0]+vr[1][1])/2.0
            self._buy_off.setText(f"📍 首笔买入 {bd} 不在当前窗口（切『全部』查看）"); self._buy_off.setPos((n-1)/2.0, ymid); self._buy_off.show()
            return
        i = None
        for k,(ts,nav,eqt) in enumerate(self._hist):
            if ts >= buy_ts: i = k; break
        if i is None:
            self._buy_line.hide(); self._buy_dot.hide(); self._buy_label.hide(); self._buy_off.hide(); return
        self._buy_line.setPos(i); self._buy_line.show()
        yv = self._fund_y_by_idx.get(i)
        txt = f"📍 首笔买入 {bd[5:]}" + (f" @{cost:.4f}" if cost > 0 else "")
        if yv is None:
            self._buy_dot.hide()
            vr = self.plot.viewRange(); self._buy_label.setPos(i, vr[1][1]); self._buy_label.setText(txt); self._buy_label.show()
        else:
            self._buy_dot.setData([i], [yv]); self._buy_dot.show()
            self._buy_label.setText(txt); self._buy_label.setPos(i, yv); self._buy_label.show()
        self._buy_off.hide()

    def _place_legend(self):
        if not self._legend.text():
            self._legend.hide(); return
        self._legend.adjustSize(); self._legend.show(); self._legend.raise_()
        w = self.plot.width()
        if w > 140:
            self._legend.move(w - self._legend.width() - 12, 10)
        else:
            self._legend.move(10, 34)

    def _draw_curve(self):
        n = len(self._hist)
        if n == 0:
            self._curve.setData([]); self._dd_curve.setData([]); self._cmp_curve.setData([]); self._cmp_curve.hide()
            self._rank_curve.setData([]); self._rank_curve.hide()
            self._hide_dd_markers(); self._hide_buy_markers(); self._legend.hide(); return
        self._vline.hide(); self._hline.hide(); self._dot.setData([])
        self._readout.setText("移动鼠标看每日数值")
        xs = np.arange(n)
        span_days = (self._hist[-1][0] - self._hist[0][0]) / 86400000.0
        fmt = "%Y-%m" if span_days > 400 else "%m-%d"
        self._date_axis.set_data(self._hist, fmt)
        self.plot.setXRange(-0.5, n - 1 + 0.5, padding=0)
        aligned = self._aligned_for_cmp() if self._cmp_code else []
        self._cmp_on = bool(self._cmp_code) and len(aligned) >= 2
        if self._cmp_code and not self._cmp_on and self._idx_cache.get(self._cmp_code):
            self._cmp_hint.setText(f"⚠ {self._cmp_name}与本基金无重叠交易日")
        if self._view == "rank":
            self._curve.hide(); self._dd_curve.hide(); self._cmp_curve.hide(); self._legend.hide()
            self._hide_dd_markers(); self._hide_buy_markers()
            self.plot.getAxis("left").setLabel("同类排名（名）")
            self._fund_y_by_idx = {}
            if not self._rank_full:
                self._rank_curve.setData([]); self._rank_curve.hide()
                self.plot.getAxis("left").setLabel("同类排名（暂无数据）")
                return
            rx = np.array([p[0] for p in self._rank_full], dtype=float)
            ry = np.array([p[1] for p in self._rank_full], dtype=float)
            self._rank_curve.show(); self._rank_curve.setData(rx, ry)
            self._fund_y_by_idx = {int(p[0]): int(p[1]) for p in self._rank_full}
            rmin = float(ry.min()); rmax = float(ry.max())
            rpad = max((rmax - rmin) * 0.12, 1.0)
            self.plot.getPlotItem().invertY(True)
            self.plot.setYRange(rmin - rpad, rmax + rpad, padding=0)
            return
        self.plot.getPlotItem().invertY(False)
        self._rank_curve.hide()
        if self._view == "nav":
            self._dd_curve.hide(); self._curve.show(); self._hide_dd_markers()
            if not self._cmp_on:
                self._cmp_curve.hide(); self._legend.hide()
                self.plot.getAxis("left").setLabel("单位净值")
                ys = np.array([p[1] for p in self._hist], dtype=float); self._curve.setData(xs, ys)
                ymin = float(ys.min()); ymax = float(ys.max())
                cost = float(self._my_rec2.get("cost") or 0)
                if cost > 0: ymin = min(ymin, cost); ymax = max(ymax, cost)
                ypad = max((ymax - ymin) * 0.12, 0.005)
                self.plot.setYRange(ymin - ypad, ymax + ypad, padding=0)
                self._fund_y_by_idx = {i: float(self._hist[i][1]) for i in range(n)}
            else:
                self._cmp_curve.show()
                self.plot.getAxis("left").setLabel("区间涨跌幅 %")
                base_nav = aligned[0][1]; base_close = aligned[0][2]
                ax=[]; fp=[]; ip=[]; self._fund_y_by_idx={}
                for (i, nav, close) in aligned:
                    fpc = (nav/base_nav - 1)*100; ipc = (close/base_close - 1)*100
                    ax.append(i); fp.append(fpc); ip.append(ipc); self._fund_y_by_idx[i] = fpc
                self._curve.setData(ax, fp); self._cmp_curve.setData(ax, ip)
                allo = fp + ip; ymin = min(allo); ymax = max(allo); ypad = max((ymax-ymin)*0.12, 0.5)
                self.plot.setYRange(ymin - ypad, ymax + ypad, padding=0)
                self._legend.setText(f'<span style="color:#2563eb">━</span> 本基金 &nbsp; <span style="color:{TEAL}">━</span> {self._cmp_name}（区间涨跌%）')
                self._draw_buy_markers()
        else:
            self._curve.hide(); self._dd_curve.show()
            self.plot.getAxis("left").setLabel("回撤 %")
            ys = np.array(self._dd, dtype=float); self._dd_curve.setData(xs, ys)
            ymin = float(ys.min()) if len(ys) else -1.0
            self._fund_y_by_idx = {i: float(self._dd[i]) for i in range(n)}
            if self._cmp_on:
                self._cmp_curve.show()
                peak = -1e18; ix=[]; ipct=[]
                for (i, nav, close) in aligned:
                    if close > peak: peak = close
                    idd = (close-peak)/peak*100.0 if peak > 0 else 0.0
                    ix.append(i); ipct.append(idd)
                self._cmp_curve.setData(ix, ipct)
                if ipct: ymin = min(ymin, min(ipct))
                self._legend.setText(f'<span style="color:#ef4444">━</span> 本基金回撤 &nbsp; <span style="color:{TEAL}">━</span> {self._cmp_name}回撤')
            else:
                self._cmp_curve.hide(); self._legend.hide()
            ymin = min(ymin, -0.5); ypad = max(abs(ymin) * 0.12, 0.3)
            self.plot.setYRange(ymin - ypad, 0.0 + ypad*0.4, padding=0)
            self._show_dd_markers()
            self._draw_buy_markers()
        self._place_legend()

    def _fill_table(self):
        rows = list(reversed(self._hist))[:200]
        dd_rows = list(reversed(self._dd))[:200] if self._dd else []
        rank_rows = list(reversed(self._rank_full))[:200] if self._rank_full else []
        self.table.setUpdatesEnabled(False)
        third = "同类排名" if self._view == "rank" else ("回撤%" if self._view == "dd" else "当日涨跌")
        self.table.setHorizontalHeaderLabels(["日期", "单位净值", third])
        self.table.setRowCount(len(rows))
        rank_by_i = {int(i): int(r) for (i, r) in self._rank_full} if self._rank_full else {}
        for r,(ts,nav,eqt) in enumerate(rows):
            self.table.setItem(r,0,QTableWidgetItem(datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d")))
            self.table.setItem(r,1,QTableWidgetItem(f"{nav:.4f}"))
            if self._view == "rank":
                idx = (len(self._hist) - 1 - r)
                rv = rank_by_i.get(idx)
                it = QTableWidgetItem(f"第 {rv} 名" if rv is not None else "—")
                it.setForeground(QColor("#b45309") if rv is not None else QColor(GRAY))
            elif self._view == "dd":
                v = dd_rows[r] if r < len(dd_rows) else 0.0
                it = QTableWidgetItem(f"{v:.2f}%")
                it.setForeground(QColor(GRAY) if v > -0.005 else QColor(RED))
            else:
                it = QTableWidgetItem(f"{eqt:+.2f}%"); it.setForeground(QColor(RED) if eqt>=0 else QColor(GREEN))
            self.table.setItem(r,2,it)
        self.table.setUpdatesEnabled(True)

    def _update_dd_stats(self):
        if self._view != "dd":
            self.dd_box.hide(); return
        self.dd_box.show()
        st = self._dd_state
        if st == "none" or not self._hist:
            self.lbl_dd_max.setText("最大回撤  暂无明显回撤"); self.lbl_dd_max.setStyleSheet("color:#16a34a;font-weight:bold;")
            self.lbl_dd_rep.setText("修复  —"); self.lbl_dd_rep.setStyleSheet("color:#888;font-weight:bold;")
        else:
            self.lbl_dd_max.setText(f"最大回撤  {abs(self._dd_max):.2f}%"); self.lbl_dd_max.setStyleSheet("color:#c0392b;font-weight:bold;")
            if st == "yes":
                self.lbl_dd_rep.setText(f"修复  已修复 {self._dd_days} 天"); self.lbl_dd_rep.setStyleSheet("color:#16a34a;font-weight:bold;")
            elif st == "fixing":
                self.lbl_dd_rep.setText(f"修复  正在修复中 · 已填{self._dd_progress*100:.0f}%"); self.lbl_dd_rep.setStyleSheet("color:#ea580c;font-weight:bold;")
            else:
                self.lbl_dd_rep.setText(f"修复  暂未修复 · 已填{self._dd_progress*100:.0f}%"); self.lbl_dd_rep.setStyleSheet("color:#64748b;font-weight:bold;")

    def _update_my_pnl(self):
        sh = float(self._my_rec2.get("shares") or 0); cost = float(self._my_rec2.get("cost") or 0)
        if not self._hist or not sh:
            self.lbl_my.setText("未填持仓（点顶栏「💼 管理持仓」填写）"); self.lbl_my.setStyleSheet("color:#999;"); return
        nav = self._hist[-1][1]; mv = nav*sh
        if cost <= 0:
            self.lbl_my.setText(f"你的持仓：{sh:.0f}份 市值{mv:,.2f}（未填成本）"); self.lbl_my.setStyleSheet("color:#999;"); return
        pnl = (nav-cost)*sh; pct = (nav-cost)/cost*100; pc = RED if pnl>=0 else GREEN
        self.lbl_my.setText(f"你的持仓：{sh:.0f}份 成本{cost:.4f} 市值{mv:,.2f}  盈亏 {pnl:+,.2f}元({pct:+.2f}%)")
        self.lbl_my.setStyleSheet(f"color:{pc};font-weight:bold;")

    def _mouse_moved(self, pos):
        if not self._hist: return
        vb = self.plot.getPlotItem().vb
        if not self.plot.sceneBoundingRect().contains(pos): return
        mp = vb.mapSceneToView(pos); idx = int(round(mp.x()))
        if idx < 0 or idx >= len(self._hist): return
        ts,nav,eqt = self._hist[idx]
        self._vline.show(); self._hline.show()
        self._vline.setPos(idx)
        d = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d")
        if self._view == "rank":
            rv = self._fund_y_by_idx.get(idx)
            if rv is None:
                self._vline.hide(); self._hline.hide(); self._dot.setData([])
                self._readout.setText(f"{d} 净值 {nav:.4f} 排名 暂无")
            else:
                self._hline.setPos(rv); self._dot.setBrush(pg.mkBrush("#f59e0b")); self._dot.setData([idx],[rv])
                self._readout.setText(f"{d} 净值 {nav:.4f} 同类排名 第 {rv} 名")
        elif self._view == "nav":
            yv = self._fund_y_by_idx.get(idx, nav)
            self._hline.setPos(yv); self._dot.setBrush(pg.mkBrush("#2563eb")); self._dot.setData([idx],[yv])
            if self._cmp_on:
                self._readout.setText(f"{d} 本基金 {yv:+.2f}% 净值 {nav:.4f}")
            else:
                self._readout.setText(f"{d} 净值 {nav:.4f} 涨跌 {eqt:+.2f}%")
        else:
            dv = self._dd[idx] if idx < len(self._dd) else 0.0
            self._hline.setPos(dv); self._dot.setBrush(pg.mkBrush("#ef4444")); self._dot.setData([idx],[dv])
            self._readout.setText(f"{d} 净值 {nav:.4f} 回撤 {dv:.2f}%")
        self._readout.adjustSize(); self._readout.raise_()


def _ro(text):
    it = QTableWidgetItem(text); it.setFlags(it.flags() & ~Qt.ItemIsEditable); return it


class PasteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("📋 粘贴持仓文字"); self.resize(560,460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("把蚂蚁/天天基金里『持有份额、持仓成本价』那段【纯文本】整段复制粘贴到下面，点解析（不是csv/表格）：\n（注：『持有金额/市值』不会被导入——本金列需另行填写或留空。）\n⚠ 只认下面表里已有的基金（即首页看板上的基金）。列表外的新基金粘了会被悄悄跳过——要加新基金，请先回首页用『➕快速添加』，加完再来粘。"))
        self.te = QTextEdit(); self.te.setPlaceholderText("例如：\n某某指数基金C\n持有份额 1234.56\n持仓成本价 1.0000\n……（多只一起粘也行；只认份额+成本价）")
        self.te.setFont(QFont("Microsoft YaHei",9)); lay.addWidget(self.te,3)
        self.lbl = QLabel(""); self.lbl.setStyleSheet("color:#555;"); self.lbl.setWordWrap(True); lay.addWidget(self.lbl)
        bar = QHBoxLayout(); bar.addStretch()
        b = QPushButton("🔍 解析"); b.clicked.connect(self._parse)
        b.setStyleSheet("QPushButton{padding:8px 16px;border-radius:8px;background:#2563eb;color:#fff;border:none;}")
        bar.addWidget(b); lay.addLayout(bar)
        self.parsed = {}
    def _parse(self):
        try:
            self.parsed = parse_holdings_text(self.te.toPlainText())
        except Exception as e:
            self.parsed = {}; self.lbl.setText(f"⚠ 解析出错：{e}"); return
        n = len(self.parsed)
        if n == 0:
            self.lbl.setText("⚠ 没认出任何一只。请确认文字里含基金全名 + 『持有份额/持仓成本价』字样。认不出的，回到表格手填即可。")
        else:
            names = "、".join(NAME_MAP.get(c, c) for c in self.parsed)
            self.lbl.setText(f"✅ 认出 {n} 只：{names}（仅份额+成本价）。点『采用』填进表格（橙底待你核对），或直接关闭。")
        self.accept()


class HoldDialog(QDialog):
    def __init__(self, resolved, corrected_codes, price_map, parent=None):
        super().__init__(parent); self.setWindowTitle("💼 管理我的持仓"); self.resize(760,560)
        self._price = price_map; self._busy = True
        lay = QVBoxLayout(self)
        tip = QLabel("  • 「持仓成本价/每份」填每份成本的小数（如 1.0000），不要填持有金额。\n"
                     "  • 「投入本金」列可选：留空即可；要填就填≈份额×成本价(如1000×1.2000≈1200.00)，\n"
                     "    不要填『持有金额/市值』（那是市值=本金+收益，不是本金）。\n"
                     "  • 「买入日期」选填，格式 2026-07-15；填写后详情页画📍首笔买入竖线+🟣成本横线，记不清可留空。\n"
                     "  • 快捷方式：「📋 粘贴导入」只自动填份额+成本价，本金/日期不改动。\n"
                     "  黄底行 = 程序判定填写的是本金，已自动 ÷份额 换算为每份成本。")
        tip.setStyleSheet("color:#555;background:#f7f9fc;border-radius:8px;padding:8px;"); lay.addWidget(tip)
        self.lbl_fix = QLabel(""); self.lbl_fix.setStyleSheet("color:#9a6b00;background:#fff7d6;border:1px solid #f0d97a;border-radius:8px;padding:6px 10px;")
        self.lbl_fix.setWordWrap(True); self.lbl_fix.hide(); lay.addWidget(self.lbl_fix)
        raw_hold = load_holdings()
        self.table = QTableWidget(len(FUNDS),6)
        self.table.setHorizontalHeaderLabels(["基金名称","代码","持有份额","持仓成本价/每份","投入本金(元,可选)","买入日期(选填)"])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False); self.table.setAlternatingRowColors(True)
        for r,(code,name) in enumerate(FUNDS):
            r2 = resolved.get(code, {})
            sh = r2.get("shares", ""); cost = r2.get("cost", ""); prin = r2.get("principal", "")
            bd = raw_hold.get(code, {}).get("buy_date", "") or ""
            self.table.setItem(r,0,_ro(name)); self.table.setItem(r,1,_ro(code))
            self.table.setItem(r,2,QTableWidgetItem(self._fmt(sh)));
            self.table.setItem(r,3,QTableWidgetItem(self._fmt(cost,4)));
            self.table.setItem(r,4,QTableWidgetItem(self._fmt(prin,2)));
            self.table.setItem(r,5,QTableWidgetItem(bd.strip()))
            if code in corrected_codes:
                for cc in range(6):
                    it = self.table.item(r,cc)
                    if it: it.setBackground(HL)
        lay.addWidget(self.table)
        if corrected_codes:
            self.lbl_fix.setText(f"⚠ 已自动换算 {len(corrected_codes)} 只：『成本价』列中填写的实为『本金』，已 ÷份额 换算为『每份成本』(黄底行)。核对无误后点保存即永久修正。")
            self.lbl_fix.show()
        self.table.itemChanged.connect(self._on_cell); self._busy = False
        bar = QHBoxLayout()
        b_paste = QPushButton("📋 粘贴填份额·成本"); b_paste.setToolTip("把支付宝/天天里『持有份额·持仓成本价』那段纯文本粘进来，一次灌进表。\n只填数字、不加新基金；新基金请先回首页『➕快速添加』。")
        b_paste.clicked.connect(self._paste_import)
        b_paste.setStyleSheet("QPushButton{padding:8px 14px;border-radius:8px;background:#fff3e0;color:#e65100;border:1px solid #ffcc80;}QPushButton:hover{background:#ffe0b2;}")
        bar.addWidget(b_paste); bar.addStretch()
        b_trade = QPushButton("📝 记一笔交易"); b_trade.clicked.connect(self._record_trade)
        b_trade.setStyleSheet("QPushButton{padding:8px 14px;border-radius:8px;background:#e8f5e9;color:#2e7d32;border:1px solid #a5d6a7;}QPushButton:hover{background:#c8e6c9;}")
        bar.addWidget(b_trade)
        b_clear = QPushButton("清空全部"); b_clear.clicked.connect(self._clear)
        b_clear.setStyleSheet("QPushButton{padding:8px 14px;border-radius:8px;background:#f0f0f0;border:none;}")
        b_cancel = QPushButton("取消"); b_cancel.clicked.connect(self.reject)
        b_cancel.setStyleSheet("QPushButton{padding:8px 14px;border-radius:8px;background:#f0f0f0;border:none;}")
        b_save = QPushButton("💾 保存"); b_save.clicked.connect(self._save)
        b_save.setStyleSheet("QPushButton{padding:8px 16px;border-radius:8px;background:#2563eb;color:#fff;border:none;}QPushButton:hover{background:#1d4ed8;}")
        bar.addWidget(b_clear); bar.addWidget(b_cancel); bar.addWidget(b_save); lay.addLayout(bar)
        self.saved = False

    def _paste_import(self):
        dlg = PasteDialog(self); dlg.exec()
        if not dlg.parsed:
            return
        self._busy = True
        try:
            code2row = {code: r for r,(code,name) in enumerate(FUNDS)}
            for code, rec in dlg.parsed.items():
                r = code2row.get(code)
                if r is None:
                    continue
                if "shares" in rec:
                    self.table.setItem(r,2,QTableWidgetItem(self._fmt(rec["shares"])))
                if "cost" in rec:
                    self.table.setItem(r,3,QTableWidgetItem(self._fmt(rec["cost"],4)))
                for cc in (0,1,2,3):
                    it = self.table.item(r,cc)
                    if it: it.setBackground(IMP)
        finally:
            self._busy = False

    def _record_trade(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            cur = self.table.currentRow()
            if cur < 0:
                QMessageBox.information(self, "记一笔", "请先在表里点一下要记账的那只基金所在行。")
                return
            r = cur
        else:
            r = rows[0].row()
        code = self.table.item(r, 1).text().strip()
        name = self.table.item(r, 0).text().strip()
        dlg = TradeDialog(code, name, self._price.get(code, 0), parent=self)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return
        act, nav, amount, shares, date = dlg.result
        # 基准取表格当前行(草稿)，不读盘不写盘
        base_sh   = self._num(r, 2)
        base_cost = self._num(r, 3)
        base_prin = self._num(r, 4)
        base_bd   = (self.table.item(r, 5).text().strip() if self.table.item(r, 5) else "")
        cur = {"shares": base_sh, "cost": base_cost, "principal": base_prin}
        if base_bd:
            cur["buy_date"] = base_bd
        draft = {code: cur}
        realized = [None]; cashflow = [None]
        try:
            snap = apply_trade(draft, code, act, nav=nav, amount=amount,
                               shares=shares, date=date,
                               realized_out=realized, cashflow_out=cashflow)
        except Exception as e:
            QMessageBox.warning(self, "记账失败", f"{e}")
            return
        # 只回写表格草稿，落盘由『保存』按钮负责
        self._busy = True
        try:
            self.table.setItem(r, 2, QTableWidgetItem(self._fmt(snap.get("shares", 0))))
            self.table.setItem(r, 3, QTableWidgetItem(self._fmt(snap.get("cost", 0), 4)))
            self.table.setItem(r, 4, QTableWidgetItem(self._fmt(snap.get("principal", 0), 2)))
            bd = (snap.get("buy_date") or "").strip()
            self.table.setItem(r, 5, QTableWidgetItem(bd))
        finally:
            self._busy = False
        extra = ""
        if realized[0] is not None: extra = f"　已实现盈亏 {realized[0]:+.4f}"
        if cashflow[0] is not None: extra = f"　现金流入 {cashflow[0]:+.2f}"
        QMessageBox.information(self, "已记入草稿",
            f"✅ {name}({code}) 已记入本窗草稿（尚未存盘）。\n"
            f"现 份额={snap.get('shares',0):.4f} 成本={snap.get('cost',0):.4f} 本金={snap.get('principal',0):.2f}{extra}\n"
            f"⚠️ 点右下角『💾保存』才会写入文件；直接关窗＝放弃本笔。")

    def _fmt(self, v, nd=0):
        if v in ("", None): return ""
        try: f = float(v)
        except Exception: return str(v)
        return (f"{f:.{nd}f}" if nd else (str(int(f)) if f == int(f) else str(f)))

    def _num(self, r, c):
        it = self.table.item(r,c)
        if not it: return 0.0
        try: return float(it.text().strip())
        except Exception: return 0.0

    def _on_cell(self, item):
        if self._busy: return
        r = item.row(); c = item.column()
        if c not in (2,3,4): return
        self._busy = True
        try:
            sh = self._num(r,2); cost = self._num(r,3); prin = self._num(r,4)
            if c == 3 and sh > 0:
                self.table.setItem(r,4,QTableWidgetItem(f"{cost*sh:.2f}" if cost else ""))
            elif c == 4 and sh > 0:
                self.table.setItem(r,3,QTableWidgetItem(f"{prin/sh:.4f}" if prin else ""))
        finally:
            self._busy = False

    def _clear(self):
        self._busy = True
        for r in range(self.table.rowCount()):
            self.table.setItem(r,2,QTableWidgetItem("")); self.table.setItem(r,3,QTableWidgetItem("")); self.table.setItem(r,4,QTableWidgetItem("")); self.table.setItem(r,5,QTableWidgetItem(""))
        self._busy = False

    def _save(self):
        d = {}
        for r,(code,name) in enumerate(FUNDS):
            sh = self._num(r,2); cost = self._num(r,3); prin = self._num(r,4)
            bd = (self.table.item(r,5).text().strip() if self.table.item(r,5) else "")
            if bd:
                try:
                    datetime.strptime(bd, "%Y-%m-%d")
                except Exception:
                    QMessageBox.warning(self,"买入日期格式",
                        f"「{name}」的买入日期『{bd}』格式不对。\n请用 YYYY-MM-DD，例如 2026-07-15；记不清就清空这一格。")
                    return
            if sh == 0 and cost == 0 and prin == 0 and not bd: continue
            if cost == 0 and prin > 0 and sh > 0: cost = prin/sh
            if prin == 0 and cost > 0 and sh > 0: prin = cost*sh
            nav = self._price.get(code,0)
            if cost > 0 and nav > 0 and (cost/nav > 3 or cost/nav < 0.33):
                ans = QMessageBox.question(self,"成本价异常",
                    f"「{name}」的每份成本 {cost:.4f} 与现价 {nav:.4f} 相差 {cost/nav:.0f} 倍。\n"
                    f"你是不是把『本金/金额』填进了『每份成本』？\n\n选『否』返回修改；选『是』仍按当前值保存。",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ans != QMessageBox.Yes: return
            d[code] = {"shares": sh, "cost": round(cost,4), "principal": round(prin,2), "buy_date": bd}
        save_holdings(d); self.saved = True; self.accept()


class TradeDialog(QDialog):
    ACTS = [("买入(加仓)", "buy"), ("赎回", "sell"),
            ("红利再投资", "dividend_reinvest"), ("现金分红", "dividend_cash")]
    def __init__(self, code, name, nav_now, parent=None):
        super().__init__(parent); self.setWindowTitle(f"📝 记一笔 · {name} {code}")
        self.resize(420, 300); self.result = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"基金：{name}　代码：{code}"))
        self.cb = QComboBox()
        for txt, _ in self.ACTS: self.cb.addItem(txt)
        lay.addWidget(self.cb); self.cb.currentIndexChanged.connect(self._sync_fields)
        self.e_nav  = QLineEdit(f"{nav_now:.4f}" if nav_now else "")
        self.e_amt  = QLineEdit()
        self.e_sh   = QLineEdit()
        self.e_date = QLineEdit(datetime.now().strftime("%Y-%m-%d"))
        for lab, w in [("成交净值：", self.e_nav),
                       ("金额(元, 买入/再投/现金分红用)：", self.e_amt),
                       ("份额(赎回用)：", self.e_sh),
                       ("日期(买入=首笔, YYYY-MM-DD)：", self.e_date)]:
            rr = QHBoxLayout(); rr.addWidget(QLabel(lab)); rr.addWidget(w); lay.addLayout(rr)
        self._sync_fields()
        bar = QHBoxLayout(); bar.addStretch()
        b_ok = QPushButton("确定"); b_ok.clicked.connect(self._ok)
        b_no = QPushButton("取消"); b_no.clicked.connect(self.reject)
        bar.addWidget(b_ok); bar.addWidget(b_no); lay.addLayout(bar)

    def _sync_fields(self):
        act = self.ACTS[self.cb.currentIndex()][1]
        need_amt = act in ("buy", "dividend_reinvest", "dividend_cash")
        need_sh  = act in ("sell",)
        need_nav = act in ("buy", "sell", "dividend_reinvest")
        need_dt  = act == "buy"
        self.e_amt.setEnabled(need_amt); self.e_sh.setEnabled(need_sh)
        self.e_nav.setEnabled(need_nav); self.e_date.setEnabled(need_dt)

    def _ok(self):
        act = self.ACTS[self.cb.currentIndex()][1]
        try:
            nav = float(self.e_nav.text()) if self.e_nav.text().strip() else 0.0
            amt = float(self.e_amt.text()) if self.e_amt.text().strip() else 0.0
            sh  = float(self.e_sh.text())  if self.e_sh.text().strip()  else 0.0
        except Exception:
            QMessageBox.warning(self, "格式", "净值/金额/份额 请填写数字。"); return
        date = self.e_date.text().strip()
        if act in ("buy", "dividend_reinvest") and (nav <= 0 or amt <= 0):
            QMessageBox.warning(self, "缺值", "买入/再投 需要 净值>0 且 金额>0。"); return
        if act == "sell" and (nav <= 0 or sh <= 0):
            QMessageBox.warning(self, "缺值", "赎回 需要 净值>0 且 份额>0。"); return
        if act == "dividend_cash" and amt <= 0:
            QMessageBox.warning(self, "缺值", "现金分红 需要 金额>0。"); return
        self.result = (act, nav, amt, sh, date)
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("我的基金助手"); self.resize(1320,900)
        s = QApplication.primaryScreen().geometry(); self.move(max(0,(s.width()-1320)//2),max(0,(s.height()-900)//2))
        pg.setConfigOptions(antialias=True)
        root = QWidget(); self.setCentralWidget(root)
        outer = QVBoxLayout(root); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.stack = QStackedWidget(); outer.addWidget(self.stack)
        self.home = self._build_home(); self.detail = DetailPage(on_back=self._go_home)
        self.stack.addWidget(self.home); self.stack.addWidget(self.detail)
        self.worker = None; self.last_results = []; self.resolved = {}; self.corrected_codes = set()
        self._cleared_codes = load_show_state()
        self._refresh_home()

    def _build_home(self):
        w = QWidget(); outer = QVBoxLayout(w); outer.setContentsMargins(18,16,18,16); outer.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel("📊  我的基金助手"); title.setFont(QFont("Microsoft YaHei",16,QFont.Bold)); top.addWidget(title); top.addStretch()
        self.lbl_time = QLabel(""); self.lbl_time.setStyleSheet("color:#999;"); top.addWidget(self.lbl_time)
        self.btn_hold = QPushButton("💼 管理持仓"); self.btn_hold.setFont(QFont("Microsoft YaHei",9))
        self.btn_hold.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#eef9f0;color:#16a34a;border:none;}QPushButton:hover{background:#dcf3e1;}")
        self.btn_hold.clicked.connect(self._open_hold); top.addWidget(self.btn_hold)
        self.btn_diag = QPushButton("🩺 诊断"); self.btn_diag.setFont(QFont("Microsoft YaHei",9))
        self.btn_diag.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#f0f0f0;border:none;}QPushButton:hover{background:#e3e3e3;}")
        self.btn_diag.clicked.connect(self._show_diag); top.addWidget(self.btn_diag)
        self.btn_snap = QPushButton("📸 最新快照"); self.btn_snap.setFont(QFont("Microsoft YaHei",9))
        self.btn_snap.setStyleSheet("QPushButton{padding:8px 12px;border-radius:8px;background:#f5f3ff;color:#7c3aed;border:none;}QPushButton:hover{background:#ede9fe;}")
        self.btn_snap.clicked.connect(self._open_snapshot); top.addWidget(self.btn_snap)
        self.btn_refresh = QPushButton("🔄  刷新数据"); self.btn_refresh.setFont(QFont("Microsoft YaHei",10))
        self.btn_refresh.setStyleSheet("QPushButton{padding:9px 16px;border-radius:8px;background:#2563eb;color:#fff;border:none;}QPushButton:hover{background:#1d4ed8;}QPushButton:disabled{background:#bbb;}")
        self.btn_refresh.clicked.connect(self._refresh_home); top.addWidget(self.btn_refresh); outer.addLayout(top)
        self.summary = QFrame(); self.summary.setStyleSheet("QFrame{background:#f7f9fc;border-radius:10px;}")
        sl = QHBoxLayout(self.summary); sl.setContentsMargins(16,12,16,12)
        self.lbl_total = QLabel("总持仓市值  —"); self.lbl_total.setFont(QFont("Microsoft YaHei",11,QFont.Bold))
        self.lbl_today = QLabel("今日盈亏  —"); self.lbl_today.setFont(QFont("Microsoft YaHei",11,QFont.Bold))
        sl.addWidget(self.lbl_total); sl.addStretch(); sl.addWidget(self.lbl_today); outer.addWidget(self.summary)
        self.lbl_fix = QLabel(""); self.lbl_fix.setStyleSheet("QLabel{color:#9a6b00;background:#fff7d6;border:1px solid #f0d97a;border-radius:8px;padding:8px 12px;}")
        self.lbl_fix.setWordWrap(True); self.lbl_fix.hide(); outer.addWidget(self.lbl_fix)
        self.lbl_alert = QLabel(""); self.lbl_alert.setStyleSheet("QLabel{color:#b3261e;background:#fdecea;border:1px solid #f5b7b1;border-radius:8px;padding:8px 12px;}")
        self.lbl_alert.setWordWrap(True); self.lbl_alert.hide(); outer.addWidget(self.lbl_alert)
        # 左右分栏: 左=涨跌统计/榜单, 右=逐只卡片明细(中间竖线可拖)
        split = QSplitter(Qt.Horizontal)
        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True); left_scroll.setMinimumWidth(380)
        left_scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        left_wrap = QWidget(); left_col = QVBoxLayout(left_wrap); left_col.setContentsMargins(0,0,0,0); left_col.setSpacing(10)
        # 快速添加 / 移除自加基金(左栏顶)
        add_box = QFrame(); add_box.setStyleSheet("QFrame{background:#fff;border:1px solid #eee;border-radius:10px;}")
        abl = QHBoxLayout(add_box); abl.setContentsMargins(12,10,12,10)
        atitle = QLabel("➕ 快速添加"); atitle.setFont(QFont("Microsoft YaHei",9,QFont.Bold)); abl.addWidget(atitle)
        self._add_input = QLineEdit(); self._add_input.setPlaceholderText("6位代码 如 000001"); self._add_input.setFont(QFont("Microsoft YaHei",9))
        self._add_input.setFixedWidth(150); self._add_input.setStyleSheet("QLineEdit{padding:6px 8px;border:1px solid #ddd;border-radius:7px;}")
        abl.addWidget(self._add_input)
        self._add_btn = QPushButton("添加"); self._add_btn.setFont(QFont("Microsoft YaHei",9))
        self._add_btn.setStyleSheet("QPushButton{padding:6px 14px;border-radius:7px;background:#2563eb;color:#fff;border:none;}QPushButton:hover{background:#1d4ed8;}QPushButton:disabled{background:#bbb;}")
        self._add_btn.clicked.connect(self._add_fund); abl.addWidget(self._add_btn)
        self._rm_btn = QPushButton("🗑 移除自加"); self._rm_btn.setFont(QFont("Microsoft YaHei",9))
        self._rm_btn.setStyleSheet("QPushButton{padding:6px 12px;border-radius:7px;background:#fdecea;color:#b3261e;border:1px solid #f5b7b1;}QPushButton:hover{background:#f8d7da;}")
        self._rm_btn.clicked.connect(self._remove_custom_fund); abl.addWidget(self._rm_btn)
        abl.addStretch()
        left_col.addWidget(add_box)
        chart_box = QFrame(); chart_box.setStyleSheet("QFrame{background:#fff;border:1px solid #eee;border-radius:10px;}")
        cl = QVBoxLayout(chart_box); cl.setContentsMargins(8,8,8,4)
        ctitle = QLabel("📈  今日涨跌一览（涨红跌绿）"); ctitle.setFont(QFont("Microsoft YaHei",10,QFont.Bold)); cl.addWidget(ctitle)
        self.chart = BarChart(); self.chart.setFixedHeight(180); cl.addWidget(self.chart); left_col.addWidget(chart_box)
        self.board = QFrame(); self.board.setStyleSheet("QFrame{background:#fff;border:1px solid #eee;border-radius:10px;}")
        bl = QHBoxLayout(self.board); bl.setContentsMargins(14,10,14,10)
        self._red_col = QVBoxLayout(); rh = QLabel("🔥 今日红榜"); rh.setFont(QFont("Microsoft YaHei",10,QFont.Bold)); rh.setStyleSheet("color:#e53935;"); self._red_col.addWidget(rh)
        self._red_rows = [QLabel("—") for _ in range(3)]
        for lb in self._red_rows: lb.setFont(QFont("Microsoft YaHei",9)); lb.setStyleSheet("color:#c0392b;"); self._red_col.addWidget(lb)
        self._green_col = QVBoxLayout(); gh = QLabel("💧 今日黑榜"); gh.setFont(QFont("Microsoft YaHei",10,QFont.Bold)); gh.setStyleSheet("color:#16a34a;"); self._green_col.addWidget(gh)
        self._green_rows = [QLabel("—") for _ in range(3)]
        for lb in self._green_rows: lb.setFont(QFont("Microsoft YaHei",9)); lb.setStyleSheet("color:#15803d;"); self._green_col.addWidget(lb)
        bl.addLayout(self._red_col); bl.addSpacing(20); bl.addLayout(self._green_col); left_col.addWidget(self.board)
        # 快照全貌区(只读, 不参与盈亏计算)
        self.lbl_snap_total = QLabel("")
        self.lbl_snap_total.setFont(QFont("Microsoft YaHei", 10, QFont.Bold))
        self.lbl_snap_total.setStyleSheet("QLabel{color:#5b21b6;background:#f5f3ff;border:1px solid #ddd6fe;border-radius:8px;padding:8px 12px;}")
        self.lbl_snap_total.setWordWrap(True); self.lbl_snap_total.hide()
        left_col.addWidget(self.lbl_snap_total)
        self.snap_box = QFrame(); self.snap_box.setStyleSheet("QFrame{background:#fff;border:1px solid #eee;border-radius:10px;}")
        _sbl = QVBoxLayout(self.snap_box); _sbl.setContentsMargins(10,8,10,8)
        _sbtitle = QLabel("📸 支付宝持有全貌（快照·只读·与上方实时卡片对照；不参与盈亏计算）")
        _sbtitle.setFont(QFont("Microsoft YaHei", 9, QFont.Bold)); _sbtitle.setStyleSheet("color:#7c3aed;")
        _sbl.addWidget(_sbtitle)
        self.snap_table = QTableWidget(0, 4)
        self.snap_table.setHorizontalHeaderLabels(["名称", "金额(元)", "今日收益", "持有收益"])
        self.snap_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.snap_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.snap_table.setAlternatingRowColors(True); self.snap_table.verticalHeader().setVisible(False)
        self.snap_table.setStyleSheet("QTableWidget{font-size:12px;}")
        self.snap_table.setFixedHeight(170)
        _sbl.addWidget(self.snap_table)
        left_col.addWidget(self.snap_box)
        self.snap_box.hide()
        left_col.addStretch()
        left_scroll.setWidget(left_wrap); split.addWidget(left_scroll)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setMinimumWidth(440)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        self.cards_wrap = QWidget(); self.cards_layout = QVBoxLayout(self.cards_wrap); self.cards_layout.setSpacing(8); self.cards_layout.addStretch()
        scroll.setWidget(self.cards_wrap); split.addWidget(scroll)
        split.setChildrenCollapsible(False); split.setStretchFactor(0, 4); split.setStretchFactor(1, 6)
        split.setSizes([520, 760])
        outer.addWidget(split, 1)
        self.cards = {}
        self._cleared_codes = getattr(self, "_cleared_codes", load_show_state())
        for code,name in FUNDS:
            c = FundCard(code); c.lbl_name.setText(name)
            c.btn_detail.clicked.connect(lambda checked, cd=code: self._open_detail(cd))
            c.btn_clear.clicked.connect(lambda checked, cd=code: self._toggle_cleared(cd))
            if code in self._cleared_codes: c.set_cleared(True)
            self.cards_layout.insertWidget(self.cards_layout.count()-1,c); self.cards[code]=c
        return w

    def _toggle_cleared(self, code):
        card = self.cards.get(code)
        if not card: return
        on = code not in self._cleared_codes
        if on: self._cleared_codes.add(code)
        else: self._cleared_codes.discard(code)
        save_show_state(self._cleared_codes)
        card.set_cleared(on)
        nm = dict(FUNDS).get(code, code)
        if on:
            QMessageBox.information(self, "已清仓",
                f"「{nm}」已盖灰章：不再计入柱状图/红黑榜/总持仓，卡片仅观察。\n持仓记录未动，点卡片上『恢复持有』可随时还原。")
        else:
            QMessageBox.information(self, "已恢复", f"「{nm}」已恢复持有，重新计入总账与榜单。")
        self._apply_results()
        for fn in (self._update_board, self._update_alert, self._update_summary):
            try:
                fn(self.last_results)
            except Exception:
                pass

    def _fade_cards(self):
        for c in self.cards.values():
            eff = QGraphicsOpacityEffect(c); c.setGraphicsEffect(eff); eff.setOpacity(0.0)
            a = QPropertyAnimation(eff, b"opacity", c); a.setDuration(260); a.setStartValue(0.0); a.setEndValue(1.0)
            c._fade_anim = a; a.start(QAbstractAnimation.DeleteWhenStopped)

    def _refresh_home(self):
        if not FUNDS:
            QMessageBox.information(self, "看板是空的",
                "当前没有任何基金可抓。\n请在左上方『➕ 快速添加』输入6位代码添加第一只基金。")
            return
        self.btn_refresh.setEnabled(False); self.btn_refresh.setText("⏳  抓取中…")
        self.lbl_time.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.worker = Worker(); self.worker.done.connect(self._on_home_done); self.worker.start()

    def _on_home_done(self, results):
        self.last_results = results
        self._cleared_codes = load_show_state()
        for _cd, _c in self.cards.items(): _c.set_cleared(_cd in self._cleared_codes)
        self._apply_results(); self._fade_cards()
        self.btn_refresh.setEnabled(True); self.btn_refresh.setText("🔄  刷新数据")
        if sum(1 for r in results if r.get("status")=="ok") == 0:
            QMessageBox.warning(self,"没抓到数据","全部抓取失败，请点「🩺 诊断」。")

    def _build_snap_index(self):
        """读最新OCR快照, 按名字对齐到code, 供『手填为空时回退显示』。
           手填优先: 只有 resolved 无份额的基金, 卡片/汇总才回退用这里的值。"""
        self._snap_by_code = {}; self._snap_total_amount = 0.0
        self._snap_nav_date = "?"; self._snap_unmatched = []
        raw = None
        doc = _normalize_snap_doc(raw) if raw is not None else None
        if not (isinstance(doc, dict) and doc.get("holdings")):
            try: raw = _load_snap()
            except Exception: raw = None
            doc = _normalize_snap_doc(raw) if raw is not None else None
        if not (isinstance(doc, dict) and doc.get("holdings")):
            return
        snap_list = doc.get("holdings", []) or []
        fund_codes = {c for c, _ in FUNDS}
        by_code = {}; used = set()
        for j, it in enumerate(snap_list):                            # 6位代码精确对齐
            cd = str(it.get("code", "")).strip()
            if cd in fund_codes and cd not in by_code:
                by_code[cd] = it; used.add(j)
        norm_snap = [(i, _norm_name(it.get("name", "")), it) for i, it in enumerate(snap_list) if isinstance(it, dict)]
        norm_funds = [(code, _norm_name(name)) for code, name in FUNDS if code not in by_code]
        for code, nf in norm_funds:                                   # 名字归一化精确
            for j, ns, it in norm_snap:
                if j in used: continue
                if nf and ns and nf == ns:
                    by_code[code] = it; used.add(j); break
        import difflib                                                # 名字模糊贪心
        rem_f = [(c, nf) for c, nf in norm_funds if c not in by_code]
        rem_s = [(j, ns, it) for j, ns, it in norm_snap if j not in used]
        pairs = []
        for c, nf in rem_f:
            for j, ns, it in rem_s:
                if nf and ns:
                    pairs.append((difflib.SequenceMatcher(None, nf, ns).ratio(), c, j, it))
        pairs.sort(key=lambda x: x[0], reverse=True)
        for r, c, j, it in pairs:
            if c in by_code or j in used: continue
            if r >= 0.6:
                by_code[c] = it; used.add(j)
        self._snap_by_code = by_code
        self._snap_unmatched = [c for c, _ in FUNDS if c not in by_code]
        tot = doc.get("totals", {}) or {}
        try: self._snap_total_amount = float(tot.get("amount", 0) or 0)
        except Exception: self._snap_total_amount = 0.0
        self._snap_nav_date = doc.get("nav_date", "?")

    def _apply_results(self):
        price_map = {d["code"]: d.get("nav",0) for d in self.last_results if d.get("status")=="ok"}
        self.resolved, self.corrected_codes = resolve_holdings(load_holdings(), price_map)
        self._build_snap_index()
        self._cleared_codes = load_show_state()
        act = [d for d in self.last_results if d["code"] not in self._cleared_codes]
        for d in act:
            c = self.cards.get(d["code"])
            if c: c.update_data(d, self.resolved, self._snap_by_code.get(d["code"]))
        self.chart.draw([(d.get("name",d["code"])[:8], d.get("chg",0)) for d in act], animate=True)
        self._update_summary(self.last_results)
        self._update_board(self.last_results)
        self._update_alert(self.last_results)
        self._fill_snapshot_panel()
        if self.corrected_codes:
            self.lbl_fix.setText(f"🔧 检测到 {len(self.corrected_codes)} 只填的是『本金』(含份额≈1 的撞车行)，已按『每份成本』显示盈亏（卡片带🔧）。点「💼 管理持仓」核对并保存，即可永久修正。")
            self.lbl_fix.show()
        else:
            self.lbl_fix.hide()

    def _update_board(self, results):
        clr = getattr(self, "_cleared_codes", set()) or set()
        results = [d for d in results if d["code"] not in clr]
        ok = [(d.get("name",d["code"]), d.get("chg",0)) for d in results if d.get("status")=="ok"]
        ok.sort(key=lambda x: x[1], reverse=True)
        up = [x for x in ok if x[1] > 0]
        dn = [x for x in reversed(ok) if x[1] < 0]
        for i,lb in enumerate(self._red_rows):
            lb.setText(f"{up[i][0][:10]}  {up[i][1]:+.2f}%  🚀" if i < len(up) else "—")
        bot = list(reversed(ok))
        for i,lb in enumerate(self._green_rows):
            lb.setText(f"{dn[i][0][:10]}  {dn[i][1]:+.2f}%  📉" if i < len(dn) else "—")

    def _update_alert(self, results):
        alerts = []
        clr = getattr(self, "_cleared_codes", set()) or set()
        for d in results:
            if d["code"] in clr: continue
            if d.get("status") != "ok": continue
            name = d.get("name",d["code"]); nav = d.get("nav",0); chg = d.get("chg",0)
            if chg <= -3:
                alerts.append(f"⚠ {name[:8]} 单日跌 {chg:.1f}%")
            r2 = self.resolved.get(d["code"])
            if r2 and r2.get("shares") and nav:
                cost = float(r2.get("cost") or 0)
                if cost > 0:
                    pct = (nav-cost)/cost*100
                    if pct <= -15:
                        alerts.append(f"🕳 {name[:8]} 已深套 {pct:.0f}%")
            else:
                it = (getattr(self, "_snap_by_code", {}) or {}).get(d["code"])
                if it and not it.get("is_cash") and d["code"] not in clr:
                    try: hpr_f = float(it.get("hold_pnl_rate_pct"))
                    except Exception: hpr_f = None
                    if hpr_f is not None and hpr_f <= -15:
                        alerts.append(f"🕳 {name[:8]} 已深套 {hpr_f:.0f}%（快照）")
        if alerts:
            self.lbl_alert.setText("  ｜  ".join(alerts[:4])); self.lbl_alert.show()
        else:
            self.lbl_alert.hide()

    def _update_summary(self, results):
        total_mv=0.0; today_pnl=0.0; has=False
        clr = getattr(self, "_cleared_codes", set()) or set()
        results = [d for d in results if d["code"] not in clr]
        for d in results:
            r2=self.resolved.get(d["code"]); nav=d.get("nav",0); chg=d.get("chg",0)
            if r2 and r2.get("shares") and nav:
                cost=float(r2.get("cost") or 0); sh=float(r2["shares"])
                if cost > 0:
                    mv=nav*sh; total_mv+=mv; today_pnl += mv*(chg/(100+chg)) if (100+chg) else 0; has=True
        if has:
            self.lbl_total.setText(f"总持仓市值  ¥{total_mv:,.2f}"); self.lbl_total.setStyleSheet("color:#222;")
            pc=RED if today_pnl>=0 else GREEN
            _nds = [r.get("nav_date","") for r in results if r.get("status")=="ok" and r.get("nav_date")]
            _nd = max(_nds) if _nds else ""
            _tag = "今日盈亏" if (_nd == datetime.now().strftime("%Y-%m-%d")) else (f"盈亏(截至{_nd[5:]})" if _nd else "今日盈亏")
            self.lbl_today.setText(f"{_tag}  {today_pnl:+,.2f}元"); self.lbl_today.setStyleSheet(f"color:{pc};")
        else:
            snap = getattr(self, "_snap_by_code", {}) or {}
            tot_amt = getattr(self, "_snap_total_amount", 0.0) or 0.0
            nav_date = getattr(self, "_snap_nav_date", "?")
            if snap and tot_amt > 0:
                est = 0.0
                for d in results:
                    if d["code"] in clr: continue
                    it = snap.get(d["code"]); chg = d.get("chg", 0)
                    if it and d.get("status") == "ok" and not it.get("is_cash"):
                        amt = float(it.get("amount", 0) or 0)
                        est += amt * (chg / (100 + chg)) if (100 + chg) else 0
                self.lbl_total.setText(f"总持仓市值  ¥{tot_amt:,.2f}（快照{nav_date}·OCR）")
                self.lbl_total.setStyleSheet("color:#222;")
                pc = RED if est >= 0 else GREEN
                self.lbl_today.setText(f"今日盈亏≈ {est:+,.2f}元（估算）"); self.lbl_today.setStyleSheet(f"color:{pc};")
            else:
                self.lbl_total.setText("总持仓市值  未填持仓"); self.lbl_total.setStyleSheet("color:#999;font-size:12px;")
                chgs=[d.get("chg",0) for d in results if d.get("status")=="ok"]
                if chgs:
                    avg=sum(chgs)/len(chgs); pc=RED if avg>=0 else GREEN
                    self.lbl_today.setText(f"今日平均涨跌  {avg:+.2f}%（{len(chgs)}只）"); self.lbl_today.setStyleSheet(f"color:{pc};")
                else:
                    self.lbl_today.setText("今日盈亏  —"); self.lbl_today.setStyleSheet("color:#999;")

    def _open_snapshot(self):
        SnapshotDialog(self).exec()

    def _fill_snapshot_panel(self):
        try:
            doc = _load_snap()
        except Exception:
            doc = None
        if not doc or not isinstance(doc, dict):
            self.lbl_snap_total.hide(); self.snap_box.hide(); return
        h = doc.get("holdings", []) or []
        tot = doc.get("totals", {}) or {}
        nav_date = doc.get("nav_date", "?")
        try:
            tot_amt = float(tot.get("amount", 0))
        except Exception:
            tot_amt = 0.0
        n_fund = tot.get("n_fund", "?"); n_cash = tot.get("n_cash", "?")
        if tot_amt > 0:
            um = getattr(self, "_snap_unmatched", []) or []
            um_txt = f"　⚠OCR未对齐{len(um)}只(回退缺这几只)" if um else ""
            self.lbl_snap_total.setText(
                f"📸 支付宝全貌（快照 {nav_date}）  ¥{tot_amt:,.2f}　｜　基金 {n_fund} + 现金 {n_cash}　｜　上方『总持仓市值』仅含你已建模并填成本的部分{um_txt}")
            self.lbl_snap_total.show()
        else:
            self.lbl_snap_total.hide()
        self.snap_box.hide()

    def _open_hold(self):
        price_map = {d["code"]: d.get("nav",0) for d in self.last_results if d.get("status")=="ok"}
        dlg = HoldDialog(self.resolved, self.corrected_codes, price_map, self); dlg.exec()
        if dlg.saved: self._apply_results()

    def _open_detail(self, code): self.detail.load(code, self.resolved.get(code, {})); self.stack.setCurrentIndex(1)
    def _go_home(self): self.stack.setCurrentIndex(0)
    def _redraw_aggregates(self):
        # 添加/删除后, 用当前 last_results 重画 柱状图/红黑榜/汇总/告警(不重抓)
        self.chart.draw([(d.get("name",d["code"])[:8], d.get("chg",0)) for d in self.last_results], animate=False)
        self._update_board(self.last_results); self._update_summary(self.last_results); self._update_alert(self.last_results)

    def _add_fund(self):
        code = self._add_input.text().strip()
        if not re.search(r"^\d{6}$", code):
            QMessageBox.warning(self,"代码格式","请输入6位基金代码，例如 000001。"); return
        if code in self.cards or code in {c for c,_ in FUNDS}:
            QMessageBox.information(self,"已存在",f"{code} 已在列表里，无需重复添加。"); return
        self._add_btn.setEnabled(False); self._add_btn.setText("⏳ 识别中…"); QApplication.processEvents()
        r = fetch_one(code)
        self._add_btn.setEnabled(True); self._add_btn.setText("添加")
        name = (r.get("name") or "").strip()
        if not name or r.get("status") != "ok":
            QMessageBox.warning(self,"识别失败",f"抓不到 {code} 的名字/净值——代码可能无效，或网络暂时不通。未添加。"); return
        extra = _load_extra()
        if code not in {it["code"] for it in extra}:
            extra.append({"code": code, "name": name}); _save_extra(extra)
        FUNDS.append((code, name)); NAME_MAP[code] = name
        card = FundCard(code); card.lbl_name.setText(name)
        card.btn_detail.clicked.connect(lambda checked, cd=code: self._open_detail(cd))
        card.btn_clear.clicked.connect(lambda checked, cd=code: self._toggle_cleared(cd))
        self.cards_layout.insertWidget(self.cards_layout.count()-1, card)
        self.cards[code] = card
        card.update_data(r, self.resolved, (getattr(self,"_snap_by_code",{}) or {}).get(code))
        self.last_results.append(r)
        self._redraw_aggregates(); self._add_input.clear()
        QMessageBox.information(self,"已添加",
            f"✅ {name}（{code}）已加入看板并保存。\n\n该基金尚未填写持仓，暂不计入盈亏；\n在「💼 管理持仓」填写份额/成本后，盈亏统计才会包含它。")

    def _remove_custom_fund(self):
        extra = _load_extra()
        if not extra:
            QMessageBox.information(self,"没有可移除的基金","当前看板为空，没有可移除的基金。\n"); return
        d = QDialog(self); d.setWindowTitle("🗑 移除自加基金"); d.resize(460,380)
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel("下面列出看板上的所有基金，点「移除」即从看板删除。\n"))
        list_box = QVBoxLayout(); lay.addLayout(list_box); lay.addStretch()
        b_close = QPushButton("关闭"); b_close.clicked.connect(d.accept)
        b_close.setStyleSheet("QPushButton{padding:8px 16px;border-radius:8px;background:#2563eb;color:#fff;border:none;}")
        lay.addWidget(b_close, 0, Qt.AlignRight)
        def rebuild():
            while list_box.count():
                it = list_box.takeAt(0); w = it.widget()
                if w: w.deleteLater()
            cur = _load_extra()
            if not cur:
                t = QLabel("（已全部移除）"); t.setStyleSheet("color:#999;"); list_box.addWidget(t); return
            holds = load_holdings()
            for _it in cur:
                c, n = _it["code"], _it["name"]
                roww = QWidget(); rl = QHBoxLayout(roww); rl.setContentsMargins(0,2,0,2)
                lab = QLabel(f"{n}  ({c})"); lab.setFont(QFont("Microsoft YaHei",9)); rl.addWidget(lab,1)
                if c in holds and ((holds[c].get("shares") or 0) or (holds[c].get("principal") or 0)):
                    warn = QLabel("⚠填过持仓"); warn.setStyleSheet("color:#b45309;font-size:8px;"); rl.addWidget(warn)
                bb = QPushButton("移除"); bb.setStyleSheet("QPushButton{padding:4px 12px;border-radius:6px;background:#fdecea;color:#b3261e;border:1px solid #f5b7b1;}")
                bb.clicked.connect(lambda checked, cc=c, nn=n: do_remove(cc, nn)); rl.addWidget(bb)
                list_box.addWidget(roww)
        def do_remove(c, n):
            holds = load_holdings()
            has_hold = c in holds and ((holds[c].get("shares") or 0) or (holds[c].get("principal") or 0))
            clear_hold = False
            if has_hold:
                ans = QMessageBox.question(d,"该只填过持仓",
                    f"「{n}」在管理表里填过份额/本金。\n移除时是否一并清空它的持仓记录？\n\n是＝连持仓一起删；否＝只从看板移除、持仓保留。",
                    QMessageBox.Yes|QMessageBox.No|QMessageBox.Cancel, QMessageBox.Cancel)
                if ans == QMessageBox.Cancel: return
                clear_hold = (ans == QMessageBox.Yes)
            _save_extra([it for it in _load_extra() if str(it.get("code","")).strip() != c])
            for i in range(len(FUNDS)-1,-1,-1):
                if FUNDS[i][0] == c: del FUNDS[i]
            NAME_MAP.pop(c, None)
            card = self.cards.pop(c, None)
            if card: self.cards_layout.removeWidget(card); card.deleteLater()
            self.last_results = [x for x in self.last_results if x.get("code") != c]
            if clear_hold:
                h = load_holdings(); h.pop(c, None); save_holdings(h)
            self._redraw_aggregates(); rebuild()
        rebuild(); d.exec()

    def _show_diag(self):
        if not FUNDS:
            QMessageBox.information(self, "诊断", "看板是空的，没有可诊断的基金。\n请先在左上方『➕ 快速添加』添加基金。")
            return
        if not self.last_results:
            QMessageBox.information(self,"诊断","还没抓过数据，先点「刷新数据」。"); return
        d = QDialog(self); d.setWindowTitle("🩺 抓取诊断"); d.resize(620,420); lay = QVBoxLayout(d)
        lay.addWidget(QLabel("检测以下数据能否顺利抓取："))
        te = QTextEdit(); te.setReadOnly(True); te.setFont(QFont("Consolas",9))
        lines = [f"时间 {datetime.now():%Y-%m-%d %H:%M:%S}", "",
                 f"成功 {sum(1 for r in self.last_results if r.get('status')=='ok')} / 共 {len(self.last_results)}", "-"*50]
        for r in self.last_results:
            if r.get("status")=="ok":
                lines.append(f"[OK]   {r['code']} {r.get('name','')[:14]:14s} 净值{r['nav']:.4f} 涨跌{r['chg']:+.2f}%  via={r.get('via')}")
            else:
                lines.append(f"[FAIL] {r['code']} {r.get('name','')[:14]:14s}  -> {r.get('err','未知')}")
        te.setPlainText("\n".join(lines)); lay.addWidget(te)
        b = QPushButton("关闭"); b.clicked.connect(d.accept); lay.addWidget(b); d.exec()


if __name__ == "__main__":
    app = QApplication(sys.argv); win = MainWindow(); win.show(); sys.exit(app.exec())