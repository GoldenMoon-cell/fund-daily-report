# ocr_hold.py —— 读图→规整→补正→分类→写 snapshots/{日期}.json→重读自校验
# clone 后跑一次即落盘，纯标准库。
# 前提：本地 ollama 运行中，且已 ollama pull qwen2.5vl:7b
import os, re, json, glob, time, base64, datetime, urllib.request, urllib.error
from pathlib import Path

# ============ 配置（按需改） ============
OLLAMA_URL = "http://localhost:11434"
MODEL      = "qwen2.5vl:7b"
NUM_CTX    = 8192
TEMP       = 0.0
REQ_TIMEOUT= 600
SCREENSHOT_DIR_NAME = "screenshots"
SNAPSHOT_DIR_NAME   = "snapshots"     # 账本快照目录（相对脚本目录），脚本自建
import sys as _sys
# 路径基准与 app.py 对齐：源码=脚本目录；打包exe=exe所在目录
if getattr(_sys, "frozen", False):
    _BASE = Path(_sys.executable).resolve().parent
else:
    _BASE = Path(__file__).resolve().parent
IMG_DIR    = _BASE / SCREENSHOT_DIR_NAME
SNAP_DIR   = _BASE / SNAPSHOT_DIR_NAME
LIST_GLOB  = "hold_list_*"
# asset_class 派生规则（按顺序匹配，先命中先生效；名称关键词->类别）：
CLASS_RULES = [
    ("余额宝",        "cash"),
    ("黄金",          "commodity_gold"),
    ("债",            "bond"),
    ("纳斯达克",      "us_equity"),
    ("恒生",          "hk_equity"),
    ("红利",          "a_equity_dividend"),
    ("低波",          "a_equity_dividend"),
]
CLASS_DEFAULT = "a_equity_sector"
CLASS_RULE_TEXT = ("按名称关键词顺序匹配: 余额宝->cash; 含'黄金'->commodity_gold; 含'债'->bond; "
                   "含'纳斯达克'->us_equity; 含'恒生'->hk_equity; 含'红利'或'低波'->a_equity_dividend; "
                   "其余->a_equity_sector。asset_class 为派生标签，可改本脚本 CLASS_RULES。")
# =======================================

LIST_PROMPT = """你是 OCR 助手，只从这张"基金持有列表"截图里提取每一行，输出严格 JSON，不要任何解释、不要 markdown 围栏。

页面视觉结构（每行一只基金/或余额宝）：
- 第1行：名称（完整抄录，含括号与 C/A 后缀，如"易方达恒生科技ETF联接(QDII)C"）
- 名称下一行的大数字 = 金额 amount
- 金额下方"占比 xx%" 里的 xx = ratio（保留%号，如"18.30%"）
- "日收益"列的值 = daily_pnl（如"+1.24" / "-0.15" / "0.00"）
- "持有收益"列上方的值 = hold_pnl（如"+3.39" / "0.00"）
- "持有收益"列下方带%的值 = hold_pnl_rate（如"+2.42%"）
- "累计收益"列的值 = cum_pnl（如"+3.39"）

关于 null 的铁律：0.00、0.00%、+0.00、-0.00 都是【有效值】，必须照读成数字/带%串，绝不可填 null。
只有当某个格子在视觉上【完全空白、什么都没有】时才填 null。
特殊行"余额宝"（带"灵活取用"标签）：name="余额宝"，is_cash=true；它的 hold_pnl 与 hold_pnl_rate 视觉上是空的，填 null；其余照读。
普通基金行 is_cash=false。

必须排除（不要当成基金行）：推广卡片文字、提示文字（如"以上按照持有收益排序"）、标签文字（如"今日收益更新""灵活取用""超额收益"等）、表头、顶部统计区、被截断而只有名称没有金额的残行。
看不清且非空的字段才填 null，绝不编造。按图片从上到下的视觉顺序输出。
只输出如下结构：
{"list":[ {"name":..,"amount":..,"ratio":..,"daily_pnl":..,"hold_pnl":..,"hold_pnl_rate":..,"cum_pnl":..,"is_cash":..}, ... ]}"""

FIELDS = ["name","amount","ratio","daily_pnl","hold_pnl","hold_pnl_rate","cum_pnl"]

def ask_vision(img_path: Path, prompt: str) -> str:
    b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "images": [b64], "stream": False,
        "options": {"num_ctx": NUM_CTX, "temperature": TEMP}
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8")).get("response", "")

def extract_json(text: str):
    text = text.strip()
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1 or j <= i:
        raise ValueError("模型返回里找不到 JSON 对象")
    return json.loads(text[i:j+1])

def is_empty(v):
    return v is None or (isinstance(v, str) and v.strip() in ("", "null", "None", "-"))

def to_float(s):
    if is_empty(s): return None
    t = str(s).strip().replace(",", "").replace("%", "").replace("+", "")
    try: return float(t)
    except ValueError: return None

def completeness(row):
    return sum(0 if is_empty(row.get(f)) else 1 for f in FIELDS)

def img_sort_key(p: Path):
    m = re.search(r"(\d+)", p.stem)
    return (int(m.group(1)) if m else 10**9, p.name)

# ---------- 确定性后处理：规整 + 补正 + 分类 ----------
def classify(name: str) -> str:
    for kw, cls in CLASS_RULES:
        if kw in name:
            return cls
    return CLASS_DEFAULT

def normalize_row(r: dict) -> dict:
    name = str(r.get("name")).strip()
    amount   = to_float(r.get("amount"))
    ratio    = to_float(r.get("ratio"))
    daily    = to_float(r.get("daily_pnl"))
    hold_pnl = to_float(r.get("hold_pnl"))
    rate     = to_float(r.get("hold_pnl_rate"))
    cum      = to_float(r.get("cum_pnl"))
    # 派生兜底：持有收益数值为 0 但率被读漏(null) -> 率补 0.0
    if rate is None and hold_pnl is not None and hold_pnl == 0.0:
        rate = 0.0
    return {
        "name": name,
        "asset_class": classify(name),
        "amount": amount,
        "ratio_pct": ratio,
        "daily_pnl": daily,
        "hold_pnl": hold_pnl,
        "hold_pnl_rate_pct": rate,
        "cum_pnl": cum,
        "is_cash": bool(r.get("is_cash")),
    }

def heuristic_nav_date(recorded: datetime.date) -> datetime.date:
    # 仅跳过周末的启发式：从录入日往前找最近 weekday（不查节假日表，保持零依赖/可移植）
    d = recorded
    for _ in range(4):
        if d.weekday() < 5:
            return d
        d -= datetime.timedelta(days=1)
    return recorded

# ---------- 写盘 + 幂等 + 重读自校验 ----------
def write_snapshot(rows, recorded: datetime.date, nav: datetime.date):
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    amt_sum   = round(sum(x["amount"]    or 0 for x in rows), 2)
    ratio_sum = round(sum(x["ratio_pct"] or 0 for x in rows), 2)
    n_cash    = sum(1 for x in rows if x["is_cash"])
    doc = {
        "schema_version": 1,
        "recorded_at": recorded.isoformat(),
        "nav_date": nav.isoformat(),
        "nav_date_certainty": "heuristic_weekday_only",
        "nav_date_note": ("nav_date 为仅跳过周末的启发式（未查节假日表，零依赖/可移植）；"
                          "QDII 因时差与确认 lag 可能再滞后 1-2 个交易日，仅供参考。"
                          "recorded_at=脚本运行日，为硬事实。"),
        "source": "ocr_hold",
        "order": "app_hold_pnl_desc_cash_last",
        "classification_rule": CLASS_RULE_TEXT,
        "totals": {"amount": amt_sum, "ratio_pct_sum": ratio_sum,
                   "n_fund": len(rows) - n_cash, "n_cash": n_cash},
        "holdings": rows,
    }
    target = SNAP_DIR / f"{recorded.isoformat()}.json"
    new_blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)

    # 幂等：旧文件存在且 holdings 完全一致 -> 跳过；空文件/坏json/内容变化 -> 覆盖
    action = "write"
    if target.exists():
        try:
            old = json.loads(target.read_text(encoding="utf-8"))
            old_blob = json.dumps(old.get("holdings", []), ensure_ascii=False, sort_keys=True)
            if old_blob == new_blob:
                action = "skip"
        except Exception:
            action = "overwrite_invalid"

    if action == "skip":
        print(f"[快照] 已存在且内容一致，未重写: {target}")
    else:
        if action == "overwrite_invalid":
            print(f"[快照] 检测到旧文件无效(空/损坏)，已覆盖: {target}")
        elif target.exists():
            print(f"[快照] 警告: 同日文件内容已变化，已覆盖(封账后请勿重跑历史日): {target}")
        target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[快照] 已写入: {target}")

    # 重读自校验：重算加和/行数与内存比对
    re_doc = json.loads(target.read_text(encoding="utf-8"))
    re_h   = re_doc["holdings"]
    re_amt = round(sum(x["amount"]    or 0 for x in re_h), 2)
    re_rat = round(sum(x["ratio_pct"] or 0 for x in re_h), 2)
    ok = (re_amt == amt_sum and re_rat == ratio_sum and len(re_h) == len(rows))
    print(f"[自校验] 重读 加和={re_amt}/{re_rat} 行数={len(re_h)} | 内存={amt_sum}/{ratio_sum}/{len(rows)} -> "
          + ("通过" if ok else "不一致!!"))
    return ok

# ---------- 主流程 ----------
def main():
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    imgs = sorted((Path(g) for g in glob.glob(str(IMG_DIR / LIST_GLOB))), key=img_sort_key)
    if not imgs:
        print(f"[错误] 在 {IMG_DIR} 没找到匹配 {LIST_GLOB} 的文件。"
              f"请把分页截图命名为 hold_list_1.jpg / hold_list_2.jpg / ... 放进该目录。")
        return
    print(f"[找到列表图 {len(imgs)} 张] " + ", ".join(p.name for p in imgs))

    all_rows, failed = [], []
    for idx, p in enumerate(imgs, 1):
        t0 = time.time()
        try:
            obj = extract_json(ask_vision(p, LIST_PROMPT))
            rows = obj.get("list", []) if isinstance(obj, dict) else []
            rows = [r for r in rows if isinstance(r, dict) and not is_empty(r.get("name"))]
            print(f"[图{idx} {p.name}] 耗时 {time.time()-t0:.1f}s 读到 {len(rows)} 行 -> "
                  + ", ".join(str(r.get("name")) for r in rows))
            all_rows.extend(rows)
        except urllib.error.URLError as e:
            failed.append((p.name, f"ollama 连接失败: {e}")); print(f"[图{idx} {p.name}] 失败: {failed[-1][1]}")
        except Exception as e:
            failed.append((p.name, f"{type(e).__name__}: {e}")); print(f"[图{idx} {p.name}] 失败: {failed[-1][1]}")

    # 分页重叠行去重，保留更完整者
    merged = {}
    for r in all_rows:
        k = str(r.get("name")).strip()
        if k not in merged or completeness(r) > completeness(merged[k]):
            merged[k] = r

    rows = [normalize_row(r) for r in merged.values()]
    print("\n----- 本次将写入的 holdings（规整后） -----")
    print(json.dumps(rows, ensure_ascii=False, indent=2))

    amt_sum   = round(sum(x["amount"]    or 0 for x in rows), 2)
    ratio_sum = round(sum(x["ratio_pct"] or 0 for x in rows), 2)
    n_cash    = sum(1 for x in rows if x["is_cash"])
    print("\n===== 汇总 =====")
    print(f"去重后行数 = {len(rows)}  （基金 {len(rows)-n_cash} + 现金/余额宝 {n_cash}）")
    print(f"金额加和   = {amt_sum:.2f}   占比加和 = {ratio_sum:.2f}%")
    if failed:
        print(f"[警告] 读取失败未计入: " + "; ".join(f"{n}:{m}" for n, m in failed))

    recorded = datetime.date.today()
    nav      = heuristic_nav_date(recorded)
    print(f"\n[日期] recorded_at={recorded.isoformat()} (硬事实)  nav_date={nav.isoformat()} (启发式,仅跳周末)")

    ok = write_snapshot(rows, recorded, nav)
    print("\n[完成]" + (" 账本快照已落盘且自校验通过。" if ok else " 自校验未通过，请检查上方输出。"))

if __name__ == "__main__":
    main()