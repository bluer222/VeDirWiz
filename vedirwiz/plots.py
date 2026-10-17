"""Qt-painted plots and direct-manipulation editors, with numeric equivalents."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class Plot(QWidget):
    def __init__(self, window, kind):
        super().__init__()
        self.window, self.kind = window, kind
        self.setMinimumSize(300, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName({"charge": "Charge curve editor", "temperature": "Temperature compensation editor", "lighting": "Lighting schedule editor", "peukert": "Discharge rate model", "live": "Live measurement graph", "history": "History graph", "dynamic": "Measured dynamic cutoff graph"}[kind])
        self.setToolTip("Select a point with Left/Right. Up/Down adjusts its value. Ctrl+Left/Right changes lighting timing. Exact values can also be edited in the fields below.")
        self.handles, self.selected, self.drag = [], 0, None
        self.bounds = (0, 1, 0, 1)
        self.frozen_bounds = None

    def contents(self):
        w, kind = self.window, self.kind
        curves, handles, labels, bars = [], [], [], []
        val = w.value
        if kind == "charge":
            a, f, h = val("BAT_ABSORPTION_VOLTAGE"), val("BAT_FLOAT_VOLTAGE"), val("BAT_ABSORPTION_LIMIT")
            if not all(isinstance(v, (int, float)) for v in (a, f, h)):
                return None
            nominal = val("BAT_VOLTAGE")
            if not isinstance(nominal, (int, float)) or nominal <= 0:
                nominal = min(a, f) / 1.15
            self.bounds = (0, max(6, h + 3), min(a, f) * .78, max(a, f) * 1.08)
            curves = [([(0, max(nominal, self.bounds[2])), (1, a), (1 + h, a), (1 + h, f), (self.bounds[1], f)], "Voltage target")]
            handles = [("BAT_ABSORPTION_VOLTAGE", 1, a, "Absorption"), ("BAT_ABSORPTION_LIMIT", 1 + h, a, "Absorption time"), ("BAT_FLOAT_VOLTAGE", self.bounds[1] - .3, f, "Float")]
            labels = [(1.15, a, "Absorption"), (self.bounds[1] - 1.3, f, "Float")]
            units = ("Illustrative charging time · hours", "V")
        elif kind == "temperature":
            a, f, c = val("BAT_ABSORPTION_VOLTAGE"), val("BAT_FLOAT_VOLTAGE"), val("BAT_TEMPERATURE_COMPENSATION")
            if not all(isinstance(v, (int, float)) for v in (a, f, c)):
                return None
            points = [[(t, v + c / 1000 * (t - 25)) for t in (-10, 25, 50)] for v in (a, f)]
            self.bounds = (-10, 50, min(p[1] for pts in points for p in pts) - .4, max(p[1] for pts in points for p in pts) + .4)
            curves = [(points[0], "Absorption"), (points[1], "Float")]
            handles = [("BAT_TEMPERATURE_COMPENSATION", 50, points[0][-1][1], "Compensation")]
            units = ("Battery temperature · °C", "V")
        elif kind == "lighting":
            for r in w.catalog:
                if r["name"].startswith("TIMER_EVENT_"):
                    v = val(r["name"])
                    if isinstance(v, dict) and v.get("anchor") in (1, 2, 3):
                        handles.append((r["name"], {1:18, 2:24, 3:30}[v["anchor"]] + v["offset_min"] / 60, v["dim_pct"], r["label"]))
            if not handles:
                return None
            pts = sorted((x, y) for _, x, y, _ in handles)
            stepped = []
            for x, y in pts:
                if stepped:
                    stepped.append((x, stepped[-1][1]))
                stepped.append((x, y))
            self.bounds = (min(16, min(p[0] for p in pts) - .5), max(32, max(p[0] for p in pts) + .5), 0, 100)
            curves = [(stepped, "Brightness")]
            labels = [(18, 90, "Sunset 18:00"), (24, 90, "Midnight"), (30, 90, "Sunrise 06:00")]
            units = ("Preview clock · actual events follow solar detection", "%")
        elif kind == "peukert":
            capacity, exponent = val("BMV_BATTERY_CAPACITY"), val("BMV_PEUKERT_COEFFICIENT")
            if not all(isinstance(v, (int, float)) and v > 0 for v in (capacity, exponent)):
                return None
            rating = capacity / 20
            self.bounds = (rating / 2, rating * 8, 0, 45)
            points = [(rating / 2 + rating * 7.5 * i / 99, 20 * (rating / (rating / 2 + rating * 7.5 * i / 99)) ** exponent) for i in range(100)]
            curves = [(points, f"Exponent {exponent:g}")]
            handles = [("BMV_PEUKERT_COEFFICIENT", points[-1][0], points[-1][1], "Peukert exponent")]
            units = ("Discharge current · A, assumes a 20-hour capacity rating", "hours")
        elif kind == "live":
            samples = w.state["samples"]
            key, seconds = w.trace_key, w.trace_seconds
            if not samples:
                return None
            end = samples[-1]["time"]
            pts = [(s["time"] - end, w.sample_value(s, key)) for s in samples if end - s["time"] <= seconds and isinstance(w.sample_value(s, key), (int, float))]
            if len(pts) < 2:
                return None
            mn, mx = min(p[1] for p in pts), max(p[1] for p in pts)
            pad = max(abs(mx) * .05, (mx - mn) * .1, .05)
            span = min(seconds, max(5, end - samples[0]["time"]))
            self.bounds = (-span, 0, mn - pad, mx + pad)
            curves = [(pts, w.trace_label(key))]
            units = ("Time relative to newest sample · seconds", w.trace_unit(key))
        elif kind == "dynamic":
            pts = w.cutoff_samples
            if not pts:
                return None
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            self.bounds = (0, max(5, max(xs) * 1.1), max(0, min(ys) - .5), max(ys) + .5)
            curves = [(pts, "Measured effective cutoff")]
            units = ("Measured AC output current · A", "V")
        else:
            records = sorted((r for r in w.catalog if r["name"].startswith(("DAILY_HISTORY_", "CYCLE_HISTORY_"))), key=lambda r:int(r["name"].rsplit("_", 1)[1]), reverse=True)
            for i, r in enumerate(records):
                value = w.value(r["name"])
                y = value.get("yield_kwh") if isinstance(value, dict) else None
                if isinstance(value, dict) and r["name"].startswith("CYCLE_HISTORY_") and isinstance(value.get("ah_bulk"), (int, float)):
                    y = sum(value.get(k) or 0 for k in ("ah_bulk", "ah_absorption", "ah_recondition", "ah_float", "ah_storage"))
                bars.append((i, y, r["label"]))
            if not any(isinstance(y, (int, float)) for _, y, _ in bars):
                return None
            self.bounds = (-.5, max(1, len(bars) - .5), 0, max(y for _, y, _ in bars if isinstance(y, (int, float))) * 1.15 or 1)
            units = ("Oldest record → most recent record", "kWh" if records[0]["name"].startswith("DAILY") else "Ah")
        if self.frozen_bounds:
            self.bounds = self.frozen_bounds
        return curves, handles, labels, bars, units

    def plot_rect(self):
        return QRectF(58, 28, max(1, self.width() - 82), max(1, self.height() - 80))

    def point(self, x, y):
        a, b, c, d = self.bounds
        rect = self.plot_rect()
        return QPointF(rect.left() + (x - a) / max(1e-9, b - a) * rect.width(), rect.bottom() - (y - c) / max(1e-9, d - c) * rect.height())

    def coordinates(self, p):
        a, b, c, d = self.bounds
        rect = self.plot_rect()
        x = a + (p.x() - rect.left()) / rect.width() * (b - a)
        y = d - (p.y() - rect.top()) / rect.height() * (d - c)
        return max(a, min(b, x)), max(c, min(d, y))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.palette().base())
        data = self.contents()
        color = self.palette().text().color()
        muted = self.palette().placeholderText().color()
        p.setPen(color)
        if not data:
            text = "Waiting for live measurements…" if self.kind == "live" else "Read this page to load the graph.\nOnly values reported by this device are plotted."
            p.drawText(self.rect().adjusted(15, 15, -15, -15), Qt.AlignmentFlag.AlignCenter, text)
            self.handles = []
            return
        curves, self.handles, labels, bars, units = data
        rect = self.plot_rect()
        a, b, c, d = self.bounds
        for i in range(5):
            f = i / 4
            y = rect.top() + f * rect.height()
            p.setPen(QPen(self.palette().mid().color(), .5))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(color)
            p.drawText(QRectF(0, y - 10, 50, 20), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.window.number(d - f * (d - c), 1))
            x = rect.left() + f * rect.width()
            p.setPen(QPen(self.palette().mid().color(), .5))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            p.setPen(color)
            v = a + f * (b - a)
            label = f"{int(v)%24:02d}:{int((v%1)*60):02d}" if self.kind == "lighting" else self.window.number(v, 1)
            p.drawText(QRectF(x - 35, rect.bottom() + 8, 70, 20), Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(muted)
        p.drawText(QRectF(rect.left(), 2, rect.width(), 22), Qt.AlignmentFlag.AlignLeft, units[1])
        p.drawText(QRectF(rect.left(), self.height() - 24, rect.width(), 20), Qt.AlignmentFlag.AlignCenter, units[0])
        colors = [QColor("#356d9f"), QColor("#667989")]
        p.save()
        p.setClipRect(rect.adjusted(-2, -2, 2, 2))
        for i, (points, legend) in enumerate(curves):
            path = QPainterPath()
            for j, (x, y) in enumerate(points):
                pos = self.point(x, y)
                path.moveTo(pos) if j == 0 else path.lineTo(pos)
            p.setPen(QPen(colors[i % 2], 2))
            p.drawPath(path)
            if self.kind == "dynamic":
                for x, y in points:
                    p.setBrush(colors[0])
                    p.drawEllipse(self.point(x, y), 4, 4)
            if self.kind != "lighting":
                p.setPen(colors[i % 2])
                p.drawText(QRectF(rect.left() + 12 + i * 160, rect.top() + 8, 155, 22), legend)
        for x, y, _ in bars:
            if y is not None:
                pos = self.point(x, y)
                bar_width = max(2, rect.width() / (b - a) * .7)
                p.fillRect(QRectF(pos.x() - bar_width / 2, pos.y(), bar_width, rect.bottom() - pos.y()), colors[0])
        p.restore()
        for x, y, label in labels:
            pos = self.point(x, y)
            p.setPen(muted)
            p.drawText(QRectF(pos.x() - 55, pos.y() - 23, 110, 20), Qt.AlignmentFlag.AlignCenter, label)
        for i, (name, x, y, label) in enumerate(self.handles):
            pos = self.point(x, y)
            editable = self.window.can_edit(name)
            p.setPen(QPen(colors[0] if editable else muted, 2))
            p.setBrush(self.palette().base())
            p.drawEllipse(pos, 6, 6)
            if self.hasFocus() and self.selected == i:
                p.drawEllipse(pos, 10, 10)
            if self.kind == "lighting":
                p.setPen(color)
                p.drawText(QRectF(pos.x() - 13, pos.y() + 9, 26, 18), Qt.AlignmentFlag.AlignCenter, str(int(name.rsplit("_", 1)[1]) + 1))

    def mousePressEvent(self, event):
        for i, (name, x, y, _) in enumerate(self.handles):
            if self.window.can_edit(name) and (self.point(x, y) - event.position()).manhattanLength() <= 18:
                self.selected, self.drag = i, name
                self.frozen_bounds = self.bounds
                self.setFocus()
                self.window.show_help(name)
                self.update()
                return

    def mouseMoveEvent(self, event):
        if not self.drag:
            return
        x, y = self.coordinates(event.position())
        value = y
        if self.kind == "charge" and self.drag == "BAT_ABSORPTION_LIMIT":
            value = max(0, x - 1)
        elif self.kind == "temperature":
            value = (y - self.window.value("BAT_ABSORPTION_VOLTAGE")) / 25 * 1000
        elif self.kind == "peukert":
            rating = self.window.value("BMV_BATTERY_CAPACITY") / 20
            value = max(1, min(1.5, math.log(max(.1, y) / 20) / math.log(rating / (rating * 8))))
        elif self.kind == "lighting":
            old = self.window.value(self.drag)
            value = {"anchor": old["anchor"], "offset_min": round((x - {1:18, 2:24, 3:30}[old["anchor"]]) * 60), "dim_pct": round(y)}
        self.window.stage(self.drag, self.round_value(self.drag, value))
        self.window.refresh_fields()
        self.update()

    def round_value(self, name, value):
        if isinstance(value, (int, float)):
            r = self.window.by_name[name]
            step = r["step"] / r["factor"]
            return round(round(value / step) * step, 8)
        return value

    def mouseReleaseEvent(self, event):
        self.drag, self.frozen_bounds = None, None
        self.update()

    def keyPressEvent(self, event):
        if not self.handles:
            return super().keyPressEvent(event)
        key = event.key()
        name = self.handles[self.selected % len(self.handles)][0]
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.selected = (self.selected + (1 if key == Qt.Key.Key_Right else -1)) % len(self.handles)
            self.window.show_help(self.handles[self.selected][0])
            self.update()
        elif self.window.can_edit(name) and key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            value = self.window.value(name)
            amount = 1 if key in (Qt.Key.Key_Up, Qt.Key.Key_Right) else -1
            if self.kind == "lighting":
                field = "offset_min" if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) else "dim_pct"
                value = {k: value[k] for k in ("offset_min", "anchor", "dim_pct")}
                value[field] = max(-1440 if field == "offset_min" else 0, min(1440 if field == "offset_min" else 100, value[field] + amount))
            else:
                r = self.window.by_name[name]
                value += amount * r["step"] / r["factor"]
            self.window.stage(name, self.round_value(name, value))
            self.window.refresh_fields()
            self.update()
        else:
            super().keyPressEvent(event)


class Meter(QWidget):
    def __init__(self, title, unit, parent=None):
        super().__init__(parent)
        self.title, self.unit, self.value, self.minimum, self.maximum = title, unit, None, 0, 1
        self.setMinimumSize(150, 125)
        self.setAccessibleName(title)

    def set_value(self, value, minimum, maximum, caption):
        self.value, self.minimum, self.maximum, self.caption = value, minimum, maximum, caption
        self.setAccessibleDescription(f"{self.title}: {value if value is not None else 'unavailable'} {self.unit}. {caption}")
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().text().color()
        p.setPen(color)
        p.drawText(QRectF(5, 2, self.width() - 10, 22), Qt.AlignmentFlag.AlignCenter, self.title)
        radius = min(self.width() / 2 - 26, 56)
        center = QPointF(self.width() / 2, radius + 32)
        rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        p.setPen(QPen(self.palette().mid().color(), 6))
        p.drawArc(rect, 20 * 16, 140 * 16)
        if isinstance(self.value, (int, float)):
            ratio = max(0, min(1, (self.value - self.minimum) / max(1e-9, self.maximum - self.minimum)))
            angle = math.radians(160 - ratio * 140)
            end = center + QPointF(math.cos(angle) * (radius - 7), -math.sin(angle) * (radius - 7))
            p.setPen(QPen(QColor("#356d9f"), 2.5))
            p.drawLine(center, end)
            p.setBrush(QColor("#356d9f"))
            p.drawEllipse(center, 3, 3)
        p.setPen(color)
        p.drawText(QRectF(6, center.y() + 1, self.width() - 12, 28), Qt.AlignmentFlag.AlignCenter, f"{self.value:.2f} {self.unit}" if isinstance(self.value, (int, float)) else "—")
        p.setPen(self.palette().placeholderText().color())
        p.drawText(QRectF(5, self.height() - 23, self.width() - 10, 22), Qt.AlignmentFlag.AlignCenter, getattr(self, "caption", "Waiting for data"))


class MiniTrace(QWidget):
    def __init__(self, window, name):
        super().__init__()
        self.window, self.name = window, name
        self.setFixedHeight(28)
        self.setMinimumWidth(110)
        self.setMaximumWidth(180)
        self.setAccessibleName("Recent read trend for " + window.by_name[name]["label"])

    def paintEvent(self, event):
        values = self.window.hex_history.get(self.name, [])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(self.palette().mid().color(), 1))
        p.drawLine(3, self.height() // 2, self.width() - 3, self.height() // 2)
        if not values:
            return
        low, high = min(values), max(values)
        pad = max(abs(high) * .02, .01)
        low, high = low - pad, high + pad
        pts = [QPointF(4 + i / max(1, len(values) - 1) * (self.width() - 8), 24 - (v - low) / (high - low) * 20) for i, v in enumerate(values)]
        path = QPainterPath()
        path.moveTo(pts[0])
        for point in pts[1:]:
            path.lineTo(point)
        p.setPen(QPen(QColor("#356d9f"), 1.5))
        p.drawPath(path)
        p.setBrush(QColor("#356d9f"))
        p.drawEllipse(pts[-1], 2.5, 2.5)
