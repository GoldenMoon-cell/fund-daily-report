# -*- coding: utf-8 -*-
"""只读 OCR 快照视图。读 ocr_hold 产出的 snapshots/{date}.json 展示，与持仓表并存、互不污染。
   独立模块、自包含：不 import app 的任何符号 → 写错也不连累 app 主体。
   语义铁律：OCR 快照只有 市值/占比/收益，【没有份额和成本】→ 不参与盈亏计算、不替代『我的持仓.json』。
   它是一面『和 app 自算值对照的镜子』；目录无文件 = 还没跑过 OCR，弹窗提示，不崩溃。"""
import json, glob, os
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QPushButton)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

RED, GREEN, GRAY = "#e53935", "#16a34a", "#888888"
SNAP_DIR = "snapshots"   # 相对 cwd；app 已 chdir 到 _BASE，ocr_hold 也对齐到同目录 → 一致

def load_latest_snapshot():
    """读 SNAP_DIR 下日期最新的快照；目录不存在/无文件/全坏 → None。文件名=YYYY-MM-DD，字典序=时间序。"""
    files = sorted(glob.glob(os.path.join(SNAP_DIR, "*.json")))
    for f in reversed(files):
        try:
            doc = json.loads(Path(f).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("holdings"), list):
                return doc
        except Exception:
            continue
    return None

def _money(v):
    try: return f"{float(v):,.2f}"
    except Exception: return "—"
def _pct(v):
    try: return f"{float(v):.2f}%"
    except Exception: return "—"
def _signed(v):
    try:
        f = float(v); return f"{f:+,.2f}"
    except Exception: return "—"
def _color_signed(v):
    try:
        f = float(v); return QColor(RED if f > 0 else (GREEN if f < 0 else GRAY))
    except Exception: return QColor(GRAY)

class SnapshotDialog(QDialog):
    COLS = ["名称", "分类", "金额(元)", "占比%", "日收益", "持有收益", "持有收益率%", "累计收益"]
    def __init__(self, parent=None):
        super().__init__(parent); self.setWindowTitle("📸 最新 OCR 快照（只读）"); self.resize(940, 560)
        lay = QVBoxLayout(self)
        tip = QLabel("📖 这是 ocr_hold 从 app 截图识别出的『持有列表快照』，【只读展示】。\n"
                     "它只有 市值/占比/收益，【没有份额和成本】，所以不参与盈亏计算、也不能替你填『管理持仓』。\n"
                     "用途：和 app 自己算的市值/盈亏并排对照（一面镜子）。范围可能比 app 卡片多几只（如余额宝/未建模基金）。")
        tip.setStyleSheet("color:#555;background:#f7f9fc;border-radius:8px;padding:8px;"); tip.setWordWrap(True)
        lay.addWidget(tip)
        doc = load_latest_snapshot()
        if not doc:
            m = QLabel("⚠ 没读到任何快照。请先在命令行跑一次 ocr_hold.py（确保 screenshots/ 里有 hold_list_*.jpg），再点本按钮。")
            m.setStyleSheet("color:#9a6b00;background:#fff7d6;border:1px solid #f0d97a;border-radius:8px;padding:8px;"); m.setWordWrap(True)
            lay.addWidget(m)
            b = QPushButton("关闭"); b.clicked.connect(self.accept); lay.addWidget(b); return
        h = doc.get("holdings", []); tot = doc.get("totals", {})
        meta = QLabel(
            f"📅 录入日 recorded_at={doc.get('recorded_at','?')}　净值日 nav_date={doc.get('nav_date','?')}（{doc.get('nav_date_certainty','?')}）　"
            f"来源 {doc.get('source','?')}　|　合计金额 {tot.get('amount','?')} 元　占比和 {tot.get('ratio_pct_sum','?')}%　"
            f"基金 {tot.get('n_fund','?')} + 现金 {tot.get('n_cash','?')}　共 {len(h)} 行")
        meta.setStyleSheet("color:#333;background:#f5f3ff;border:1px solid #ddd6fe;border-radius:8px;padding:8px;"); meta.setWordWrap(True)
        lay.addWidget(meta)
        self.table = QTableWidget(len(h), len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers); self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False); self.table.setStyleSheet("QTableWidget{font-size:12px;}")
        for r, x in enumerate(h):
            row_vals = [
                (str(x.get("name", "")),            None),
                (str(x.get("asset_class", "")),     None),
                (_money(x.get("amount")),           None),
                (_pct(x.get("ratio_pct")),          None),
                (_signed(x.get("daily_pnl")),       x.get("daily_pnl")),
                (_signed(x.get("hold_pnl")),        x.get("hold_pnl")),
                (_pct(x.get("hold_pnl_rate_pct")),  x.get("hold_pnl_rate_pct")),
                (_signed(x.get("cum_pnl")),         x.get("cum_pnl")),
            ]
            for c, (text, raw) in enumerate(row_vals):
                it = QTableWidgetItem(text)
                if raw is not None:
                    it.setForeground(_color_signed(raw))      # 收益类涨红跌绿
                if c >= 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
        lay.addWidget(self.table)
        bar = QHBoxLayout(); bar.addStretch()
        b = QPushButton("关闭"); b.clicked.connect(self.accept)
        b.setStyleSheet("QPushButton{padding:8px 16px;border-radius:8px;background:#2563eb;color:#fff;border:none;}")
        bar.addWidget(b); lay.addLayout(bar)