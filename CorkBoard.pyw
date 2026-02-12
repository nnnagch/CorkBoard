import sys
import unicodedata
import copy
import os
import unittest

from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QMainWindow,
    QTabWidget,
    QVBoxLayout,
    QRadioButton,
    QButtonGroup,
    QMenu,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtGui import (
    QPainter,
    QColor,
    QFont,
    QInputMethodEvent,
    QPaintEvent,
    QKeyEvent,
    QAction,
    QCloseEvent,
)
from PySide6.QtCore import Qt, QRect, QTimer, QPointF, Signal


class QFontMetricsCompat:
    """既存のコードを崩さず、最低限必要なメトリクスだけ提供"""

    def __init__(self, font: QFont):
        from PySide6.QtGui import QFontMetrics

        self._fm = QFontMetrics(font)

    def h_advance(self, s: str) -> int:
        return self._fm.horizontalAdvance(s)

    def line_spacing(self) -> int:
        return self._fm.lineSpacing()


class FookMemoWidget(QWidget):
    dirtyChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)

        # ===== ボード設定（論理座標） =====
        self.BOARD_W = 800
        self.BOARD_H = 800

        # ===== フォント =====
        self.FONT = QFont("Consolas", 14)
        fm = QFontMetricsCompat(self.FONT)
        self.SLOT_W = fm.h_advance("x")
        self.LINE_H = fm.line_spacing() + 2
        fm.h_advance("あ")  # warm-up

        # ===== グリッド（ボード内） =====
        self.COLS = max(1, self.BOARD_W // self.SLOT_W)
        self.ROWS = max(1, self.BOARD_H // self.LINE_H)

        # ===== 内部モデル =====
        self.model = [["" for _ in range(self.COLS)] for _ in range(self.ROWS)]
        self.caret_row = 0
        self.caret_col = 0
        self.caret_visible = True

        self.anchor_row = 0
        self.anchor_col = 0

        self.hover_row = 0
        self.hover_col = 0
        self.hover_visible = True

        # IME
        self.preedit_text = ""

        # ===== 表示（パン/ズーム） =====
        self.zoom = 1.0
        self.min_zoom = 0.25
        self.max_zoom = 4.0
        self.view_offset = QPointF(40.0, 40.0)

        # 右ドラッグでパン（右クリックメニュー抑制）
        self._rc_pressed = False
        self._rc_dragged = False
        self._rc_press_pos = QPointF(0, 0)
        self._pan_start_mouse = QPointF(0, 0)
        self._pan_start_offset = QPointF(0, 0)

        # ===== Dirty / Undo/Redo =====
        self._dirty = False
        self._undo_stack = []
        self._redo_stack = []
        self._undo_limit = 200

        # Blink
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.blink)
        self.timer.start(200)

        # Theme
        self.set_theme("light")

        # ===== ショートカット =====
        self._act_undo = QAction(self)
        self._act_undo.setShortcut(Qt.CTRL | Qt.Key_Z)
        self._act_undo.triggered.connect(self.undo)

        self._act_redo = QAction(self)
        self._act_redo.setShortcut(Qt.CTRL | Qt.Key_Y)
        self._act_redo.triggered.connect(self.redo)

        self._act_cut = QAction(self)
        self._act_cut.setShortcut(Qt.CTRL | Qt.Key_X)
        self._act_cut.triggered.connect(self.cut_selection)

        self._act_copy = QAction(self)
        self._act_copy.setShortcut(Qt.CTRL | Qt.Key_C)
        self._act_copy.triggered.connect(self.copy_selection)

        self._act_paste = QAction(self)
        self._act_paste.setShortcut(Qt.CTRL | Qt.Key_V)
        self._act_paste.triggered.connect(self.paste_selection)

        self.addAction(self._act_undo)
        self.addAction(self._act_redo)
        self.addAction(self._act_cut)
        self.addAction(self._act_copy)
        self.addAction(self._act_paste)

    # ===== Dirty =====
    def is_dirty(self) -> bool:
        return self._dirty

    def set_dirty(self, v: bool):
        if self._dirty != v:
            self._dirty = v
            self.dirtyChanged.emit(v)

    # ===== 文字幅判定 =====
    def get_char_width(self, char: str) -> int:
        return 2 if unicodedata.east_asian_width(char) in ("F", "W", "A") else 1

    # ===== キャレット点滅 =====
    def blink(self):
        self.caret_visible = not self.caret_visible
        self.update()

    # ===== テーマ =====
    def set_theme(self, mode: str):
        if mode == "computer":
            # 「黒基調に調整」: エディタ側のみ黒系。ウィンドウ全体は MainWindow 側で制御。
            self.theme = {
                "ui_bg": QColor("#1b1b1b"),
                "bg": QColor("#1b1b1b"),
                "board_bg": QColor("#0f0f0f"),
                "board_border": QColor(255, 255, 255, 24),
                "text": QColor("#e8e8e8"),
                "caret": QColor("#39ff14"),
                "hover_bg": QColor(255, 255, 255, 20),
                "hover_border": QColor(255, 255, 255, 40),
                "space": QColor(255, 255, 255, 35),
                "zen_space": QColor(255, 255, 255, 55),
                "preedit_bg": QColor(255, 255, 255, 28),
                "preedit_text": QColor("#ffffff"),
                "selection": QColor(0, 120, 215, 140),
            }
        else:
            ui_bg = QColor("#C6B4A5")
            self.theme = {
                "ui_bg": ui_bg,
                "bg": ui_bg,
                "board_bg": QColor("#FFFFFF"),
                "board_border": QColor(0, 0, 0, 30),
                "text": QColor("#212427"),
                "caret": QColor("#212427"),
                "hover_bg": QColor("#EAEAE0"),
                "hover_border": QColor("#D0D0C8"),
                "space": QColor(200, 200, 200, 128),
                "zen_space": QColor(180, 180, 180, 128),
                "preedit_bg": QColor("#E0E0E0"),
                "preedit_text": QColor("#000"),
                "selection": QColor(0, 120, 215, 64),
            }
        self.update()

    # ===== Undo/Redo スナップショット =====
    def _snapshot(self):
        return {
            "model": copy.deepcopy(self.model),
            "caret": (self.caret_row, self.caret_col),
            "anchor": (self.anchor_row, self.anchor_col),
            "board": (self.BOARD_W, self.BOARD_H, self.COLS, self.ROWS),
        }

    def _restore(self, snap):
        self.BOARD_W, self.BOARD_H, self.COLS, self.ROWS = snap["board"]
        self.model = copy.deepcopy(snap["model"])
        self.caret_row, self.caret_col = snap["caret"]
        self.anchor_row, self.anchor_col = snap["anchor"]
        self._snap_caret_off_none()
        self.update()

    def push_undo(self):
        self._undo_stack.append(self._snapshot())
        if len(self._undo_stack) > self._undo_limit:
            self._undo_stack.pop(0)
        self._redo_stack.clear()
        self.set_dirty(True)

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._snapshot())
        snap = self._undo_stack.pop()
        self._restore(snap)
        self.set_dirty(True)

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._snapshot())
        snap = self._redo_stack.pop()
        self._restore(snap)
        self.set_dirty(True)

    # ===== 座標変換 =====
    def _screen_to_content(self, p: QPointF) -> QPointF:
        return (p - self.view_offset) / self.zoom

    def _content_to_screen(self, p: QPointF) -> QPointF:
        return p * self.zoom + self.view_offset

    # ===== ボード内判定（スクリーン座標） =====
    def is_in_board_screen(self, p: QPointF) -> bool:
        rect = self._board_rect_screen()
        return rect.contains(int(p.x()), int(p.y()))

    # ===== セル整合性（全角片割れ修復） =====
    def _normalize_row(self, r: int):
        row = self.model[r]

        for c in range(self.COLS):
            if row[c] is None:
                prev = row[c - 1] if c - 1 >= 0 else ""
                if not (isinstance(prev, str) and prev != "" and self.get_char_width(prev) == 2):
                    row[c] = ""

        c = 0
        while c < self.COLS:
            ch = row[c]
            if isinstance(ch, str) and ch != "" and ch is not None:
                if self.get_char_width(ch) == 2:
                    if c + 1 >= self.COLS:
                        row[c] = ""
                        break
                    row[c + 1] = None
                    c += 2
                    continue
            c += 1

        for c in range(self.COLS - 1):
            if row[c] == "" and row[c + 1] is None:
                row[c + 1] = ""

    def _normalize_rows(self, rows):
        for r in rows:
            if 0 <= r < self.ROWS:
                self._normalize_row(r)

    # ===== キャレット補正 =====
    def _snap_caret_off_none(self):
        if 0 <= self.caret_row < self.ROWS and 0 <= self.caret_col < self.COLS:
            if self.model[self.caret_row][self.caret_col] is None and self.caret_col > 0:
                self.caret_col -= 1

    # ===== ボード拡張 =====
    def _expand_cols_to(self, min_cols: int):
        if min_cols <= self.COLS:
            return
        add = min_cols - self.COLS
        self.COLS = min_cols
        self.BOARD_W = self.COLS * self.SLOT_W
        for r in range(self.ROWS):
            self.model[r].extend([""] * add)

    def _expand_rows_to(self, min_rows: int):
        if min_rows <= self.ROWS:
            return
        add = min_rows - self.ROWS
        self.ROWS = min_rows
        self.BOARD_H = self.ROWS * self.LINE_H
        for _ in range(add):
            self.model.append(["" for _ in range(self.COLS)])

    # ===== グリッド位置計算（スクリーン座標→row,col） =====
    def slot_from_screen_if_in_board(self, sx: float, sy: float):
        p = QPointF(sx, sy)
        if not self.is_in_board_screen(p):
            return None
        content = self._screen_to_content(p)
        x = max(0.0, min(float(self.BOARD_W - 1), float(content.x())))
        y = max(0.0, min(float(self.BOARD_H - 1), float(content.y())))
        col = max(0, min(self.COLS - 1, int(x // self.SLOT_W)))
        row = max(0, min(self.ROWS - 1, int(y // self.LINE_H)))
        return row, col

    # ===== ボード矩形（スクリーン座標） =====
    def _board_rect_screen(self) -> QRect:
        return QRect(
            int(self.view_offset.x()),
            int(self.view_offset.y()),
            int(self.BOARD_W * self.zoom),
            int(self.BOARD_H * self.zoom),
        )

    # ===== 影（Windows 11風に、邪魔にならない薄い影） =====
    def _draw_soft_shadow(self, painter: QPainter, rect: QRect):
        for i in range(1, 9):
            alpha = max(0, 26 - i * 3)
            col = QColor(0, 0, 0, alpha)
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            r = QRect(rect)
            r.adjust(-i, -i, i, i)
            r.translate(i // 2, i)
            painter.drawRoundedRect(r, 8, 8)

    # ===== 描画 =====
    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        painter.fillRect(self.rect(), self.theme["bg"])

        board_rect_s = self._board_rect_screen()
        self._draw_soft_shadow(painter, board_rect_s)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.theme["board_bg"])
        painter.drawRect(board_rect_s)

        painter.setPen(self.theme["board_border"])
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(board_rect_s)

        painter.save()
        painter.setClipRect(board_rect_s)
        painter.setFont(self.FONT)

        top_left_c = self._screen_to_content(QPointF(0, 0))
        bot_right_c = self._screen_to_content(QPointF(self.width(), self.height()))
        c0 = max(0, int(min(top_left_c.x(), bot_right_c.x()) // self.SLOT_W) - 1)
        r0 = max(0, int(min(top_left_c.y(), bot_right_c.y()) // self.LINE_H) - 1)
        c1 = min(self.COLS - 1, int(max(top_left_c.x(), bot_right_c.x()) // self.SLOT_W) + 1)
        r1 = min(self.ROWS - 1, int(max(top_left_c.y(), bot_right_c.y()) // self.LINE_H) + 1)

        # 選択
        if (self.caret_row, self.caret_col) != (self.anchor_row, self.anchor_col):
            start = min((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))
            end = max((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))

            painter.setPen(Qt.NoPen)
            painter.setBrush(self.theme["selection"])

            for r in range(max(start[0], r0), min(end[0], r1) + 1):
                c_start = start[1] if r == start[0] else 0
                c_end = end[1] if r == end[0] else self.COLS
                c_start = max(c_start, c0)
                c_end = min(c_end, c1 + 1)
                if c_end > c_start:
                    x = c_start * self.SLOT_W
                    y = r * self.LINE_H
                    w = (c_end - c_start) * self.SLOT_W
                    rect_s = QRect(
                        int(x * self.zoom + self.view_offset.x()),
                        int(y * self.zoom + self.view_offset.y()),
                        int(w * self.zoom),
                        int(self.LINE_H * self.zoom),
                    )
                    painter.drawRect(rect_s)

        # ホバー（範囲外なら非表示）
        if self.hover_visible:
            hx = self.hover_col * self.SLOT_W
            hy = self.hover_row * self.LINE_H
            hover_s = QRect(
                int(hx * self.zoom + self.view_offset.x()),
                int(hy * self.zoom + self.view_offset.y()),
                int(self.SLOT_W * self.zoom),
                int(self.LINE_H * self.zoom),
            )
            painter.fillRect(hover_s, self.theme["hover_bg"])
            painter.setPen(self.theme["hover_border"])
            painter.drawRect(hover_s)

        # 文字
        painter.setPen(self.theme["text"])
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                ch = self.model[r][c]
                if ch is None:
                    continue

                x = c * self.SLOT_W
                y = r * self.LINE_H

                if ch == " ":
                    rect_s = QRect(
                        int((x + 2) * self.zoom + self.view_offset.x()),
                        int((y + 2) * self.zoom + self.view_offset.y()),
                        max(0, int((self.SLOT_W - 4) * self.zoom)),
                        max(0, int((self.LINE_H - 4) * self.zoom)),
                    )
                    painter.fillRect(rect_s, self.theme["space"])
                elif ch == "\u3000":
                    w_px = self.SLOT_W
                    if c + 1 < self.COLS and self.model[r][c + 1] is None:
                        w_px = self.SLOT_W * 2
                    rect_s = QRect(
                        int((x + 2) * self.zoom + self.view_offset.x()),
                        int((y + 2) * self.zoom + self.view_offset.y()),
                        max(0, int((w_px - 4) * self.zoom)),
                        max(0, int((self.LINE_H - 4) * self.zoom)),
                    )
                    painter.fillRect(rect_s, self.theme["zen_space"])

                if ch and isinstance(ch, str) and ch.isprintable() and ch not in (" ", "\u3000"):
                    w_px = self.SLOT_W
                    if c + 1 < self.COLS and self.model[r][c + 1] is None:
                        w_px = self.SLOT_W * 2

                    rect_s = QRect(
                        int(x * self.zoom + self.view_offset.x()),
                        int(y * self.zoom + self.view_offset.y()),
                        int(w_px * self.zoom),
                        int(self.LINE_H * self.zoom),
                    )
                    painter.drawText(rect_s, Qt.AlignCenter, ch)

        # IME Preedit
        if self.preedit_text:
            curr_c = self.caret_col
            for char in self.preedit_text:
                w = self.get_char_width(char)
                w_px = w * self.SLOT_W
                x = curr_c * self.SLOT_W
                y = self.caret_row * self.LINE_H

                rect_s = QRect(
                    int(x * self.zoom + self.view_offset.x()),
                    int(y * self.zoom + self.view_offset.y()),
                    int(w_px * self.zoom),
                    int(self.LINE_H * self.zoom),
                )
                painter.fillRect(rect_s, self.theme["preedit_bg"])
                painter.setPen(self.theme["preedit_text"])
                painter.drawText(rect_s, Qt.AlignCenter, char)
                painter.setPen(self.theme["text"])
                curr_c += w

        # キャレット
        if self.caret_visible:
            offset_c = 0
            if self.preedit_text:
                for ch in self.preedit_text:
                    offset_c += self.get_char_width(ch)

            x = (self.caret_col + offset_c) * self.SLOT_W
            y = self.caret_row * self.LINE_H
            sx = x * self.zoom + self.view_offset.x()
            sy = y * self.zoom + self.view_offset.y()
            painter.setPen(self.theme["caret"])
            painter.drawLine(int(sx), int(sy + 4 * self.zoom), int(sx), int(sy + (self.LINE_H - 4) * self.zoom))

        painter.restore()

    # ===== 右クリックメニュー（Undo/Redo無し） =====
    def _show_context_menu(self, global_pos):
        menu = QMenu(self)
        # 文字が背景に同化しないように明示
        menu.setStyleSheet(
            "QMenu { background: #ffffff; color: #111111; border: 1px solid rgba(0,0,0,40); padding: 6px; }"
            "QMenu::item { color: #111111; padding: 6px 24px; border-radius: 6px; }"
            "QMenu::item:disabled { color: rgba(0,0,0,80); }"
            "QMenu::item:selected { background: rgba(42,120,215,28); }"
            "QMenu::item:pressed { background: rgba(42,120,215,60); }"
        )

        act_cut = menu.addAction("切り取り")
        act_copy = menu.addAction("コピー")
        act_paste = menu.addAction("貼り付け")

        act_cut.setEnabled(self.has_selection())
        act_copy.setEnabled(self.has_selection())
        act_paste.setEnabled(bool(QApplication.clipboard().text()))

        chosen = menu.exec(global_pos)
        if chosen == act_cut:
            self.cut_selection()
        elif chosen == act_copy:
            self.copy_selection()
        elif chosen == act_paste:
            self.paste_selection()

    # ===== ホイール：マウス位置中心にズーム =====
    def wheelEvent(self, event):
        mouse_s = event.position()
        before_c = self._screen_to_content(mouse_s)

        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = 1.1 if delta > 0 else 1 / 1.1
        new_zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-6:
            return

        self.zoom = new_zoom
        self.view_offset = mouse_s - before_c * self.zoom
        self.update()

    # ===== マウス =====
    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self._rc_pressed = True
            self._rc_dragged = False
            self._rc_press_pos = event.position()
            self._pan_start_mouse = event.position()
            self._pan_start_offset = QPointF(self.view_offset)
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() == Qt.LeftButton:
            slot = self.slot_from_screen_if_in_board(event.position().x(), event.position().y())
            if slot is None:
                return
            self.caret_row, self.caret_col = slot
            if self.caret_col > 0 and self.model[self.caret_row][self.caret_col] is None:
                self.caret_col -= 1
            self.anchor_row, self.anchor_col = self.caret_row, self.caret_col
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            self.setCursor(Qt.ArrowCursor)
            if self._rc_pressed and not self._rc_dragged:
                # 範囲外はメニューを出さない
                if self.is_in_board_screen(event.position()):
                    self._show_context_menu(event.globalPos())
            self._rc_pressed = False
            self._rc_dragged = False
            return

    def mouseMoveEvent(self, event):
        if self._rc_pressed and (event.buttons() & Qt.RightButton):
            if (event.position() - self._rc_press_pos).manhattanLength() > 3:
                self._rc_dragged = True
            delta = event.position() - self._pan_start_mouse
            self.view_offset = self._pan_start_offset + delta
            self.update()
            return

        slot = self.slot_from_screen_if_in_board(event.position().x(), event.position().y())
        if slot is None:
            self.hover_visible = False
            self.update()
            return

        self.hover_visible = True
        self.hover_row, self.hover_col = slot
        if self.hover_col > 0 and self.model[self.hover_row][self.hover_col] is None:
            self.hover_col -= 1

        if event.buttons() & Qt.LeftButton:
            self.caret_row = self.hover_row
            self.caret_col = self.hover_col
            self._snap_caret_off_none()

        self.update()

    # ===== IME =====
    def inputMethodEvent(self, event: QInputMethodEvent):
        commit = event.commitString()
        preedit = event.preeditString()

        if commit:
            self.push_undo()
            if self.has_selection():
                self.delete_selection()
            self.insert_text(commit)
            self.anchor_row, self.anchor_col = self.caret_row, self.caret_col
            self.preedit_text = ""
        else:
            self.preedit_text = preedit

        self.update()
        event.accept()

    def inputMethodQuery(self, query):
        if query == Qt.ImCursorRectangle:
            x = self.caret_col * self.SLOT_W
            y = self.caret_row * self.LINE_H
            rect_s = QRect(
                int(x * self.zoom + self.view_offset.x()),
                int(y * self.zoom + self.view_offset.y()),
                int(self.SLOT_W * self.zoom),
                int(self.LINE_H * self.zoom),
            )
            return rect_s
        elif query == Qt.ImFont:
            return self.FONT
        return super().inputMethodQuery(query)

    # ===== キー入力 =====
    def keyPressEvent(self, event: QKeyEvent):
        key = event.key()
        text = event.text()
        modifiers = event.modifiers()

        if modifiers & Qt.ControlModifier:
            if key == Qt.Key_W:
                self.window().close()
                return

        if key == Qt.Key_Left:
            self.caret_col = max(0, self.caret_col - 1)
            self._snap_caret_off_none()
            if not (modifiers & Qt.ShiftModifier):
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif key == Qt.Key_Right:
            self.caret_col = min(self.COLS - 1, self.caret_col + 1)
            if self.model[self.caret_row][self.caret_col] is None:
                self.caret_col = min(self.COLS - 1, self.caret_col + 1)
                self._snap_caret_off_none()
            if not (modifiers & Qt.ShiftModifier):
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif key == Qt.Key_Up:
            self.caret_row = max(0, self.caret_row - 1)
            self._snap_caret_off_none()
            if not (modifiers & Qt.ShiftModifier):
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif key == Qt.Key_Down:
            self.caret_row = min(self.ROWS - 1, self.caret_row + 1)
            self._snap_caret_off_none()
            if not (modifiers & Qt.ShiftModifier):
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif key == Qt.Key_Backspace:
            if self.has_selection():
                self.push_undo()
                self.delete_selection()
            elif self.caret_col > 0:
                self.push_undo()
                del_w = 2 if self.model[self.caret_row][self.caret_col - 1] is None else 1
                self.caret_col = max(0, self.caret_col - del_w)
                self.model[self.caret_row][self.caret_col] = ""
                if del_w == 2 and self.caret_col + 1 < self.COLS:
                    self.model[self.caret_row][self.caret_col + 1] = ""
                self._normalize_rows([self.caret_row])
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.push_undo()
            if self.has_selection():
                self.delete_selection()
            # 「最下段で改行→下に拡大」
            if self.caret_row >= self.ROWS - 1:
                self._expand_rows_to(self.ROWS + 1)
            self.caret_row += 1
            self.caret_row = min(self.ROWS - 1, self.caret_row)
            self._snap_caret_off_none()
            if not (modifiers & Qt.ShiftModifier):
                self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        elif text and not self.preedit_text and not (modifiers & Qt.ControlModifier):
            if text == "\t":
                text = "    "
            self.push_undo()
            if self.has_selection():
                self.delete_selection()
            self.insert_text(text)
            self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

        self.update()

    # ===== 挿入 =====
    def insert_text(self, text: str):
        for char in text:
            if char == "\n":
                # 「まっすぐ落ちる」：col維持
                if self.caret_row >= self.ROWS - 1:
                    self._expand_rows_to(self.ROWS + 1)
                self.caret_row += 1
                self.caret_row = min(self.ROWS - 1, self.caret_row)
                self._snap_caret_off_none()
                continue

            self._snap_caret_off_none()

            w = self.get_char_width(char)
            # 「最右端で入力→右に拡大」
            if self.caret_col + w > self.COLS:
                self._expand_cols_to(self.caret_col + w)

            if self.model[self.caret_row][self.caret_col] != "":
                end_col = self.caret_col
                while end_col + 1 < self.COLS and self.model[self.caret_row][end_col + 1] != "":
                    end_col += 1

                # 右へシフト（必要ならさらに拡張）
                if end_col + w >= self.COLS:
                    self._expand_cols_to(end_col + w + 1)

                for c in range(end_col, self.caret_col - 1, -1):
                    self.model[self.caret_row][c + w] = self.model[self.caret_row][c]
                    self.model[self.caret_row][c] = ""

            self.model[self.caret_row][self.caret_col] = char
            if w == 2:
                if self.caret_col + 1 >= self.COLS:
                    self._expand_cols_to(self.caret_col + 2)
                self.model[self.caret_row][self.caret_col + 1] = None

            self.caret_col = min(self.COLS - 1, self.caret_col + w)
            self._normalize_rows([self.caret_row])

    # ===== 選択範囲 =====
    def has_selection(self):
        return (self.caret_row, self.caret_col) != (self.anchor_row, self.anchor_col)

    def delete_selection(self):
        if not self.has_selection():
            return

        start = min((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))
        end = max((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))

        touched_rows = set()
        for r in range(start[0], end[0] + 1):
            touched_rows.add(r)
            c_start = start[1] if r == start[0] else 0
            c_end = end[1] if r == end[0] else self.COLS
            for c in range(c_start, c_end):
                self.model[r][c] = ""

        self._normalize_rows(touched_rows)

        self.caret_row, self.caret_col = start
        self._snap_caret_off_none()
        self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

    def get_selected_text(self):
        if not self.has_selection():
            return ""

        start = min((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))
        end = max((self.anchor_row, self.anchor_col), (self.caret_row, self.caret_col))

        out = []
        for r in range(start[0], end[0] + 1):
            c_start = start[1] if r == start[0] else 0
            c_end = end[1] if r == end[0] else self.COLS
            line = ""
            for c in range(c_start, c_end):
                ch = self.model[r][c]
                if ch is not None:
                    line += ch
            out.append(line)
        return "\n".join(out)

    def copy_selection(self):
        text = self.get_selected_text()
        if text:
            QApplication.clipboard().setText(text)

    def cut_selection(self):
        if not self.has_selection():
            return
        self.push_undo()
        self.copy_selection()
        self.delete_selection()

    def paste_selection(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        self.push_undo()
        if self.has_selection():
            self.delete_selection()
        self.insert_text(text)
        self.anchor_row, self.anchor_col = self.caret_row, self.caret_col

    # ===== 保存用シリアライズ（最小） =====
    def to_plain_text(self) -> str:
        lines = []
        for r in range(self.ROWS):
            line = ""
            for c in range(self.COLS):
                ch = self.model[r][c]
                if ch is None:
                    continue
                line += ch
            lines.append(line.rstrip())
        return "\n".join(lines).rstrip("\n") + "\n"

    def load_plain_text(self, text: str):
        # 初期ボードに収まらないならロード時にも拡張
        lines = text.splitlines()
        needed_rows = max(1, len(lines))
        max_len = max([len(l) for l in lines], default=0)

        # ざっくり「文字数=セル数」として必要列数を確保
        self._expand_rows_to(max(self.ROWS, needed_rows))
        self._expand_cols_to(max(self.COLS, max_len + 2))

        self.model = [["" for _ in range(self.COLS)] for _ in range(self.ROWS)]

        for r, line in enumerate(lines[: self.ROWS]):
            self.caret_row = r
            self.caret_col = 0
            self.insert_text(line)
            self.caret_col = 0

        self.caret_row = 0
        self.caret_col = 0
        self.anchor_row = 0
        self.anchor_col = 0
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.set_dirty(False)
        self.update()


class SettingsWidget(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        layout = QVBoxLayout()

        self.rb_light = QRadioButton("ライトモード")
        self.rb_comp = QRadioButton("コンピュータモード")
        self.rb_light.setChecked(True)

        # 背景と同化しない indicator
        self.setStyleSheet(
            "QWidget { background: transparent; }"
            "QRadioButton { padding: 8px; }"
            "QRadioButton::indicator { width: 16px; height: 16px; }"
            "QRadioButton::indicator:unchecked { border: 2px solid #405060; background: #ffffff; border-radius: 8px; }"
            "QRadioButton::indicator:checked { border: 2px solid #405060; background: #2a78d7; border-radius: 8px; }"
        )

        self.bg = QButtonGroup()
        self.bg.addButton(self.rb_light)
        self.bg.addButton(self.rb_comp)
        self.bg.buttonClicked.connect(self.on_change)

        layout.addWidget(self.rb_light)
        layout.addWidget(self.rb_comp)
        layout.addStretch()
        self.setLayout(layout)

    def on_change(self, btn):
        if btn == self.rb_comp:
            self.main_window.apply_theme("computer")
        else:
            self.main_window.apply_theme("light")


class MainWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self._path = None
        self._mode = "light"

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.editor = FookMemoWidget()
        self.editor.dirtyChanged.connect(self._on_dirty_changed)
        self.tabs.addTab(self.editor, "メモ")

        self.settings = SettingsWidget(self)
        self.tabs.addTab(self.settings, "設定")

        # Save
        self._act_save = QAction(self)
        self._act_save.setShortcut(Qt.CTRL | Qt.Key_S)
        self._act_save.triggered.connect(self.save)
        self.addAction(self._act_save)

        self.apply_theme("light")

        self.resize(980, 760)
        screen = QApplication.primaryScreen().geometry()
        self.move((screen.width() - self.width()) // 2, (screen.height() - self.height()) // 2)

        if initial_path:
            self.open_file(initial_path)
        else:
            self._update_title()

    def _base_title(self):
        if self._path:
            return os.path.basename(self._path)
        return "無題.cork"

    def _update_title(self):
        prefix = "*" if self.editor.is_dirty() else ""
        self.setWindowTitle(prefix + self._base_title())

    def _on_dirty_changed(self, _dirty: bool):
        self._update_title()

    def apply_theme(self, mode: str):
        self._mode = mode
        self.editor.set_theme(mode)

        if mode == "computer":
            # 「黒基調」：指定色は固定しない（背景色を強制しない）
            self.setStyleSheet(
                "QTabWidget::pane { border: none; }"
                "QTabBar::tab { padding: 8px 14px; }"
            )
        else:
            # 「全体的に #C6B4A5 を調整した色」
            self.setStyleSheet(
                "QMainWindow { background-color: #C6B4A5; }"
                "QWidget { background-color: rgba(198,180,165,180); }"
                "QTabWidget::pane { border: none; background: rgba(198,180,165,140); }"
                "QTabBar::tab { background: rgba(255,255,255,120); padding: 8px 14px; border-radius: 8px; margin: 4px; }"
                "QTabBar::tab:selected { background: rgba(255,255,255,190); }"
            )

        self.tabs.update()
        self.update()

    def open_file(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            self._path = path
            self.editor.load_plain_text(text)
            self._update_title()
        except Exception:
            self._path = None
            self.editor.load_plain_text("")
            self._update_title()

    def save(self):
        if not self._path:
            return self.save_as()
        try:
            text = self.editor.to_plain_text()
            with open(self._path, "w", encoding="utf-8") as f:
                f.write(text)
            self.editor.set_dirty(False)
            self._update_title()
            return True
        except Exception:
            return False

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "名前を付けて保存",
            "",
            "Cork Files (*.cork)",
        )
        if not path:
            return False
        if not path.lower().endswith(".cork"):
            path += ".cork"
        self._path = path
        return self.save()

    def closeEvent(self, event: QCloseEvent):
        if not self.editor.is_dirty():
            event.accept()
            return

        # 未保存確認ダイアログ
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("未保存")
        box.setText("変更が保存されていません。保存しますか？")
        btn_save = box.addButton("保存", QMessageBox.AcceptRole)
        btn_discard = box.addButton("破棄", QMessageBox.DestructiveRole)
        btn_cancel = box.addButton("キャンセル", QMessageBox.RejectRole)
        box.setDefaultButton(btn_save)
        box.exec()

        clicked = box.clickedButton()
        if clicked == btn_save:
            ok = self.save()
            if ok:
                event.accept()
            else:
                event.ignore()
        elif clicked == btn_discard:
            event.accept()
        else:
            event.ignore()


# ===== テスト =====
class _EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_insert_text_newline_keeps_column(self):
        w = FookMemoWidget()
        w.caret_row = 0
        w.caret_col = 3
        w.insert_text("A\nB")
        self.assertEqual(w.model[0][3], "A")
        self.assertEqual(w.model[1][3], "B")

    def test_expand_right_on_input(self):
        w = FookMemoWidget()
        w.caret_row = 0
        w.caret_col = w.COLS - 1
        before_cols = w.COLS
        w.insert_text("Z")
        self.assertGreaterEqual(w.COLS, before_cols)
        self.assertEqual(w.model[0][before_cols - 1], "Z")

    def test_expand_down_on_newline_at_bottom(self):
        w = FookMemoWidget()
        w.caret_row = w.ROWS - 1
        w.caret_col = 2
        before_rows = w.ROWS
        w.insert_text("\n")
        self.assertEqual(w.ROWS, before_rows + 1)


def _run_tests():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(_EditorTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    if "--test" in sys.argv:
        _run_tests()

    app = QApplication.instance() or QApplication(sys.argv)

    initial = None
    if len(sys.argv) >= 2 and sys.argv[1].lower().endswith(".cork"):
        initial = sys.argv[1]

    window = MainWindow(initial)
    window.show()
    sys.exit(app.exec())
