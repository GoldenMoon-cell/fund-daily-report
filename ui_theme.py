# -*- coding: utf-8 -*-
"""Theme tokens and Qt rendering helpers."""

import os
import tempfile

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


THEMES = {
    "classic": {"name": "经典", "dark": False, "win_bg": "",
        "card_bg": "#ffffff", "card_border": "#eeeeee", "card_hover": "#bbccdd", "hover_bg": "#f3f4f6",
        "cleared_bg": "#f6f6f6", "cleared_border": "#bbbbbb",
        "panel_bg": "#f7f9fc", "panel_border": "#eef0f3",
        "text": "#222222", "text_sub": "#666666", "muted": "#999999", "faint": "#bbbbbb",
        "up": "#e53935", "down": "#16a34a", "flat": "#888888", "mid_val": "#b45309",
        "accent": "#2563eb", "accent_hover": "#1d4ed8",
        "accent_soft": "#eef3ff", "accent_soft_hover": "#dbe6ff", "btn_disabled_bg": "#eeeeee"},
    "b_dark": {"name": "深空暗", "dark": True, "win_bg": "#10151c",
        "card_bg": "#161d27", "card_border": "#313d4d", "card_hover": "#334052", "hover_bg": "#1b2430",
        "cleared_bg": "#141a22", "cleared_border": "#3a4656",
        "panel_bg": "#161d27", "panel_border": "#313d4d",
        "text": "#e8edf3", "text_sub": "#c9d2dc", "muted": "#97a4b4", "faint": "#707e8e",
        "up": "#ff5d5d", "down": "#34c77b", "flat": "#8b96a3", "mid_val": "#d97706",
        "accent": "#3d7eff", "accent_hover": "#2f6ae0",
        "accent_soft": "#1b2a4a", "accent_soft_hover": "#223559", "btn_disabled_bg": "#1a212b"},
    "b_light": {"name": "晨雾浅色", "dark": False, "win_bg": "#f5f7fa",
        "card_bg": "#ffffff", "card_border": "#e4e9f0", "card_hover": "#c9d4e4", "hover_bg": "#edf0f4",
        "cleared_bg": "#f2f4f7", "cleared_border": "#d3dae3",
        "panel_bg": "#ffffff", "panel_border": "#e4e9f0",
        "text": "#1a2230", "text_sub": "#4a5568", "muted": "#6b7889", "faint": "#9aa5b3",
        "up": "#e5484d", "down": "#18a058", "flat": "#888888", "mid_val": "#b45309",
        "accent": "#2563eb", "accent_hover": "#1d4ed8",
        "accent_soft": "#e8efff", "accent_soft_hover": "#d5e2ff", "btn_disabled_bg": "#eef0f3"},
    "paper": {"name": "纸账本", "dark": False, "win_bg": "#f7f1e6",
        "card_bg": "#fffbf2", "card_border": "#e2d5b8", "card_hover": "#cdbb92", "hover_bg": "#f1e8d6",
        "cleared_bg": "#f3ecdd", "cleared_border": "#d8c9a8",
        "panel_bg": "#fffbf2", "panel_border": "#e2d5b8",
        "text": "#2b2620", "text_sub": "#5c5346", "muted": "#8a7f6d", "faint": "#b0a691",
        "up": "#c23d2e", "down": "#2f7d5c", "flat": "#8a7f6d", "mid_val": "#b45309",
        "accent": "#c23d2e", "accent_hover": "#a83325",
        "accent_soft": "#f7e8dd", "accent_soft_hover": "#f0dbc9", "btn_disabled_bg": "#efe7d8"},
}

_ARROW_FILES = {}


def card_qss_normal(tokens):
    return f"FundCard{{background:{tokens['card_bg']};border:1px solid {tokens['card_border']};border-radius:10px;}}FundCard:hover{{border:1px solid {tokens['card_hover']};}}"


def card_qss_cleared(tokens):
    return f"FundCard{{background:{tokens['cleared_bg']};border:1px dashed {tokens['cleared_border']};border-radius:10px;}}"


def panel_qss(tokens):
    return f"QFrame{{background:{tokens['panel_bg']};border-radius:10px;}}"


def board_qss(tokens):
    return f"QFrame{{background:{tokens['card_bg']};border:1px solid {tokens['card_border']};border-radius:10px;}}"


def ghost_btn_qss(tokens, pad="7px 12px"):
    return (f"QPushButton{{padding:{pad};border:1px solid {tokens['card_border']};border-radius:6px;"
            f"background:transparent;color:{tokens['text_sub']};}}"
            f"QPushButton:hover{{background:{tokens['hover_bg']};color:{tokens['text']};}}")


def primary_btn_qss(tokens):
    return (f"QPushButton{{padding:8px 14px;border-radius:6px;background:{tokens['accent']};color:#ffffff;border:none;}}"
            f"QPushButton:hover{{background:{tokens['accent_hover']};}}"
            f"QPushButton:disabled{{background:{tokens['btn_disabled_bg']};color:{tokens['flat']};}}")


def soft_btn_qss(tokens):
    return (f"QPushButton{{padding:6px 12px;border-radius:6px;background:{tokens['accent_soft']};color:{tokens['accent']};border:none;}}"
            f"QPushButton:hover{{background:{tokens['accent_soft_hover']};}}")


def danger_btn_qss(tokens, pad="8px 14px"):
    return (f"QPushButton{{padding:{pad};border-radius:6px;background:transparent;color:{tokens['up']};border:1px solid {tokens['card_border']};}}"
            f"QPushButton:hover{{background:{tokens['hover_bg']};border-color:{tokens['up']};}}")


def panel_label_qss(tokens, kind="tip"):
    if kind == "warn":
        return f"QLabel{{color:{tokens['mid_val']};background:{tokens['hover_bg']};border:1px solid {tokens['card_border']};border-radius:8px;padding:6px 10px;}}"
    if kind == "ok":
        return f"QLabel{{color:{tokens['down']};background:{tokens['hover_bg']};border:1px solid {tokens['card_border']};border-radius:8px;padding:6px 10px;}}"
    return f"QLabel{{color:{tokens['text_sub']};background:{tokens['panel_bg']};border:1px solid {tokens['panel_border']};border-radius:8px;padding:8px;}}"


def table_qss(tokens, extra=""):
    return (f"QTableWidget{{background:{tokens['card_bg']};alternate-background-color:{tokens['panel_bg']};color:{tokens['text']};"
            f"gridline-color:{tokens['card_border']};border:1px solid {tokens['card_border']};{extra}}}"
            f"QTableWidget::item:selected{{background:{tokens['accent_soft']};color:{tokens['text']};}}"
            f"QHeaderView::section{{background:{tokens['panel_bg']};color:{tokens['text_sub']};border:none;padding:5px;font-weight:bold;}}")


def arrow_png(kind, color):
    key = (kind, color)
    path = _ARROW_FILES.get(key)
    if path and os.path.exists(path):
        return path
    pixmap = QPixmap(20, 12)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    if kind == "down":
        painter.drawPolyline([QPointF(3, 3), QPointF(10, 9.5), QPointF(17, 3)])
    else:
        painter.drawPolyline([QPointF(3, 9.5), QPointF(10, 3), QPointF(17, 9.5)])
    painter.end()
    path = os.path.join(tempfile.gettempdir(), f"fund_arrow_{kind}_{color[1:]}.png").replace("\\", "/")
    pixmap.save(path, "PNG")
    _ARROW_FILES[key] = path
    return path


def combo_qss(tokens):
    arrow = arrow_png("down", tokens["muted"])
    return (f"QComboBox{{padding:6px 12px;border:1px solid {tokens['card_border']};border-radius:6px;"
            f"background:{tokens['card_bg']};color:{tokens['text']};}}"
            f"QComboBox:hover{{border-color:{tokens['muted']};}}"
            f"QComboBox:focus{{border-color:{tokens['accent']};}}"
            f"QComboBox::drop-down{{border:none;width:26px;}}"
            f"QComboBox::down-arrow{{image:url({arrow});width:12px;height:8px;margin-right:8px;}}")


def input_qss(tokens):
    return f"QLineEdit{{padding:6px 8px;border:1px solid {tokens['card_border']};border-radius:6px;background:{tokens['card_bg']};color:{tokens['text']};}}"


def highlight_background(tokens):
    return QColor("#3d3216") if tokens.get("dark") else QColor("#fff7d6")


def import_background(tokens):
    return QColor("#4a3419") if tokens.get("dark") else QColor("#ffe0b2")


def blend_color(background, foreground, alpha):
    bg = QColor(background)
    fg = QColor(foreground)
    return QColor(
        int(bg.red() * (1 - alpha) + fg.red() * alpha),
        int(bg.green() * (1 - alpha) + fg.green() * alpha),
        int(bg.blue() * (1 - alpha) + fg.blue() * alpha),
    ).name()


def global_qss(tokens):
    win_bg = tokens["win_bg"] or "#ffffff"
    return (f"QDialog,QMessageBox{{background:{tokens['card_bg']};}}"
            f"QDialog QLabel,QMessageBox QLabel{{color:{tokens['text']};background:transparent;}}"
            f"QDialog QPushButton,QMessageBox QPushButton{{padding:6px 14px;border:1px solid {tokens['card_border']};border-radius:6px;background:{tokens['card_bg']};color:{tokens['text']};}}"
            f"QDialog QPushButton:hover,QMessageBox QPushButton:hover{{background:{tokens['hover_bg']};}}"
            f"QDialog QComboBox,QMessageBox QComboBox{{padding:6px 12px;border:1px solid {tokens['card_border']};border-radius:6px;background:{tokens['card_bg']};color:{tokens['text']};}}"
            f"QDialog QComboBox::drop-down,QMessageBox QComboBox::drop-down{{border:none;width:26px;}}"
            f"QDialog QComboBox::down-arrow,QMessageBox QComboBox::down-arrow{{image:url({arrow_png('down', tokens['muted'])});width:12px;height:8px;margin-right:8px;}}"
            f"QDialog QDateEdit::up-button,QDialog QDateEdit::down-button{{border:none;width:16px;background:transparent;}}"
            f"QDialog QDateEdit::up-arrow{{image:url({arrow_png('up', tokens['muted'])});width:10px;height:7px;}}"
            f"QDialog QDateEdit::down-arrow{{image:url({arrow_png('down', tokens['muted'])});width:10px;height:7px;}}"
            f"QComboBox QAbstractItemView{{background:{tokens['card_bg']};color:{tokens['text']};border:1px solid {tokens['card_border']};selection-background-color:{tokens['accent_soft']};selection-color:{tokens['accent']};}}"
            f"QDialog QTextEdit,QDialog QLineEdit,QDialog QDateEdit{{background:{win_bg};color:{tokens['text']};border:1px solid {tokens['card_border']};border-radius:6px;padding:4px;}}"
            f"QDialog QCheckBox,QDialog QRadioButton{{color:{tokens['text']};}}"
            f"QDialog QTableWidget{{background:{tokens['card_bg']};alternate-background-color:{tokens['panel_bg']};color:{tokens['text']};gridline-color:{tokens['card_border']};border:1px solid {tokens['card_border']};}}"
            f"QDialog QHeaderView::section{{background:{tokens['panel_bg']};color:{tokens['text_sub']};border:none;padding:4px;}}"
            f"QCalendarWidget{{background:{tokens['card_bg']};color:{tokens['text']};}}"
            f"QCalendarWidget QToolButton{{color:{tokens['text']};}}"
            f"QScrollArea{{border:none;background:transparent;}}"
            f"QToolTip{{background:{tokens['card_bg']};color:{tokens['text']};border:1px solid {tokens['card_border']};}}"
            f"QScrollBar:vertical{{background:transparent;width:10px;}}"
            f"QScrollBar::handle:vertical{{background:{tokens['card_border']};border-radius:5px;min-height:24px;}}")


def icon_pixmap(kind, color, size=24, stroke=1.6):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    scale = size / 24.0

    def point(x, y):
        return QPointF(x * scale, y * scale)

    def line_path(*points):
        path = QPainterPath(point(*points[0]))
        for item in points[1:]:
            path.lineTo(point(*item))
        return path

    if kind == "logo":
        painter.drawRoundedRect(QRectF(2.5 * scale, 2.5 * scale, 19 * scale, 19 * scale), 5 * scale, 5 * scale)
        painter.drawPath(line_path((6.8, 15.6), (10.2, 11.5), (13.1, 14.0), (17.0, 8.8)))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(point(17.4, 8.2), 1.4 * scale, 1.4 * scale)
    elif kind == "refresh":
        path = QPainterPath()
        path.arcTo(QRectF(4.5 * scale, 4.5 * scale, 15 * scale, 15 * scale), 65, 255)
        painter.drawPath(path)
        end = path.currentPosition()
        painter.drawLine(end, QPointF(end.x() + 3.0 * scale, end.y() - 1.2 * scale))
        painter.drawLine(end, QPointF(end.x() + 0.8 * scale, end.y() - 3.2 * scale))
    elif kind == "export":
        painter.drawLine(point(12, 4), point(12, 13))
        painter.drawPath(line_path((8.6, 9.8), (12, 13.2), (15.4, 9.8)))
        painter.drawPath(line_path((5, 16.5), (5, 19), (19, 19), (19, 16.5)))
    elif kind == "backup":
        painter.drawRoundedRect(QRectF(4 * scale, 4.5 * scale, 16 * scale, 4.5 * scale), 1.2 * scale, 1.2 * scale)
        painter.drawPath(line_path((6, 9), (6, 17.8), (18, 17.8), (18, 9)))
        painter.drawLine(point(10, 12.9), point(14, 12.9))
    elif kind == "about":
        painter.drawEllipse(point(12, 12), 8.5 * scale, 8.5 * scale)
        painter.drawLine(point(12, 11.2), point(12, 16.4))
        painter.drawLine(point(12, 7.7), point(12, 8.2))
    elif kind == "hold":
        painter.drawRoundedRect(QRectF(3.5 * scale, 7.5 * scale, 17 * scale, 12 * scale), 2 * scale, 2 * scale)
        painter.drawPath(line_path((9, 7.5), (9, 5.6), (15, 5.6), (15, 7.5)))
        painter.drawLine(point(3.5, 12.6), point(20.5, 12.6))
    elif kind == "pnl":
        painter.drawLine(point(4.5, 19), point(19.5, 19))
        painter.drawLine(point(8, 19), point(8, 12.5))
        painter.drawLine(point(12, 19), point(12, 7.5))
        painter.drawLine(point(16, 19), point(16, 14.5))
    elif kind == "trades":
        painter.drawRoundedRect(QRectF(5 * scale, 3.5 * scale, 14 * scale, 17 * scale), 2 * scale, 2 * scale)
        painter.drawLine(point(8, 8.2), point(16, 8.2))
        painter.drawLine(point(8, 12), point(16, 12))
        painter.drawLine(point(8, 15.8), point(12.5, 15.8))
    elif kind == "diag":
        painter.drawPath(line_path((3, 12.5), (7, 12.5), (9.4, 7), (12.6, 17), (15, 12.5), (21, 12.5)))
    elif kind == "settings":
        painter.drawLine(point(4, 8), point(20, 8))
        painter.drawEllipse(point(9.5, 8), 2 * scale, 2 * scale)
        painter.drawLine(point(4, 16), point(20, 16))
        painter.drawEllipse(point(14.5, 16), 2 * scale, 2 * scale)
    painter.end()
    return pixmap
