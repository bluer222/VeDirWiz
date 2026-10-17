"""Native Qt configuration workspace. The GUI never owns the serial connection."""
import argparse
import asyncio
import copy
import csv
import glob
import html
import json
import math
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .catalog import COMMANDS, FAMILIES, GROUPS, human, metadata, registers, validate_value
from .controller import Controller, utc
from .help import CONTROL_HELP

try:
    from PySide6.QtCore import QLocale, QSettings, QSize, Qt, QTimer
    from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
        QDockWidget, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
        QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStyle,
        QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QToolBar, QToolButton,
        QVBoxLayout, QWidget)
except ImportError as exc:
    raise SystemExit("PySide6 is required. Run nix-shell, or install this project in a virtual environment with pip install -e '.[serial]'.") from exc

from .plots import Meter, Plot
from .widgets import SettingRow
from .windows import ConfigurationWindow, section_icon


def serial_ports():
    try:
        from serial.tools.list_ports import comports
        return [(p.device, p.description) for p in comports()]
    except ImportError:
        return [(p, "Serial port") for p in sorted(set(glob.glob("/dev/serial/by-id/*") + glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")))]


class ConnectionDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("Device connection")
        self.resize(550, 320)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.family = QComboBox()
        for key, label in FAMILIES.items():
            self.family.addItem(label, key)
        self.family.setCurrentIndex(self.family.findData(window.state["flavor"]))
        self.demo = QCheckBox("Offline demonstration, no hardware access")
        self.demo.setChecked(window.state["demo"])
        self.port = QComboBox()
        self.port.setEditable(True)
        for path, label in serial_ports():
            self.port.addItem(f"{path} · {label}", path)
        if window.state["port"] and not window.state["demo"]:
            self.port.setEditText(window.state["port"])
        self.baud = QComboBox()
        for n in (19200, 9600, 38400, 57600, 115200):
            self.baud.addItem(f"{n}" + (" · VE.Direct default" if n == 19200 else ""), n)
        for label, widget in (("Device family", self.family), ("Serial port", self.port), ("Baud rate", self.baud)):
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(widget, 1)
            hl.addWidget(window.help_button("connection", control=True))
            form.addRow(label, row)
        layout.addLayout(form)
        layout.addWidget(self.demo)
        note = QLabel("Select the correct family before connecting. Small MPPT, RS, Orion, and Phoenix use different preset or control definitions. Connecting reads settings; it does not change them.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.demo.toggled.connect(lambda checked: (self.port.setDisabled(checked), self.baud.setDisabled(checked)))
        self.port.setDisabled(self.demo.isChecked())
        self.baud.setDisabled(self.demo.isChecked())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Connect")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def parameters(self):
        text = self.port.currentText()
        port = self.port.currentData() if self.port.currentIndex() >= 0 and text == self.port.itemText(self.port.currentIndex()) else text
        return {"flavor": self.family.currentData(), "demo": self.demo.isChecked(), "port": port or "", "baudrate": self.baud.currentData()}


class MainWindow(QMainWindow):
    MISSING = object()

    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.state = controller.snapshot()
        self.pending, self.invalid = {}, {}
        self.catalog = metadata(self.state["flavor"])
        self.by_name = {r["name"]:r for r in self.catalog}
        self.rows, self.plots = [], []
        self.future, self.future_done = None, None
        self.active_apply, self.completed_job = None, None
        self.trace_key, self.trace_seconds = "power", 300
        self.cutoff_samples, self.last_cutoff_time = [], None
        self.hex_history, self.hex_times = {}, {}
        self.trace_paused = False
        self.auto_read = False
        self.last_auto_read = 0
        self.windows, self.pages = {}, {}
        self.search_query = ""
        self.undo_history, self.redo_history = [], []
        self.settings = QSettings("VeDirWiz", "VeDirWiz")
        self.setWindowTitle("VeDirWiz · Live monitoring")
        self.resize(1280, 900)
        self.setMinimumSize(980, 640)
        self.build_shell()
        self.load_pages()
        geometry = self.settings.value("monitor/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        dock_state = self.settings.value("monitor/windowState")
        if dock_state:
            self.restoreState(dock_state)
        self.theme(self.settings.value("theme", "light"))
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.update_actions()

    def build_shell(self):
        file_menu = self.menuBar().addMenu("&File")
        device_menu = self.menuBar().addMenu("&Device")
        edit_menu = self.menuBar().addMenu("&Edit")
        view_menu = self.menuBar().addMenu("&View")
        windows_menu = self.menuBar().addMenu("&Window")
        help_menu = self.menuBar().addMenu("&Help")
        def action(label, callback, shortcut=None, menu=None):
            a = QAction(label, self)
            a.triggered.connect(callback)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if menu:
                menu.addAction(a)
            return a
        self.connect_action = action("Connect…", self.open_connection, "Ctrl+K", device_menu)
        self.disconnect_action = action("Disconnect", self.disconnect, menu=device_menu)
        self.read_action = action("Read values", self.read_page, "F5", device_menu)
        self.read_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.connect_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon))
        self.read_all_action = action("Read all available values", lambda:self.read_page(all_values=True), "Ctrl+F5", device_menu)
        self.import_action = action("Import settings…", self.import_profile, "Ctrl+O", file_menu)
        self.export_action = action("Export settings…", self.export_profile, "Ctrl+S", file_menu)
        action("Export full snapshot…", self.export_snapshot, menu=file_menu)
        action("Export telemetry CSV…", self.export_csv, menu=file_menu)
        file_menu.addSeparator()
        self.quit_action = action("Quit", self.close, "Ctrl+Q", file_menu)
        self.undo_action = action("Undo pending edit", self.undo, "Ctrl+Z", edit_menu)
        self.redo_action = action("Redo pending edit", self.redo, "Ctrl+Shift+Z", edit_menu)
        self.discard_action = action("Discard all pending edits", self.discard, menu=edit_menu)
        self.apply_action = action("Review and apply…", self.review_apply, "Ctrl+Return", edit_menu)
        self.apply_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.window_actions = {}
        short_names = {"overview":"Monitor", "charging":"Charge", "monitor":"Battery",
                       "load":"Load", "inverter":"Inverter", "alarms":"Alarms",
                       "network":"Links", "display":"Display", "history":"History", "device":"Device"}
        for page, label in [("overview", "Live overview")] + [(g["id"], g["label"]) for g in GROUPS]:
            a = action(label, lambda checked=False, page=page:self.navigate(page), menu=windows_menu)
            a.setIcon(section_icon(page))
            a.setIconText(short_names[page])
            a.setToolTip("Activate the live monitoring window" if page == "overview" else "Open the " + label.lower() + " window")
            self.window_actions[page] = a
        windows_menu.addSeparator()
        action("Close configuration windows", self.close_configuration_windows, menu=windows_menu)
        action("Light theme", lambda:self.theme("light"), menu=view_menu)
        action("Dark theme", lambda:self.theme("dark"), menu=view_menu)
        action("How Apply works", lambda:self.show_control_help("apply"), "F1", help_menu)
        action("Connection guide", lambda:self.show_control_help("connection"), menu=help_menu)
        action("Profile guide", lambda:self.show_control_help("profiles"), menu=help_menu)
        action("About", self.about, menu=help_menu)
        toolbar = QToolBar("Device commands")
        toolbar.setObjectName("deviceToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)
        toolbar.addAction(self.connect_action)
        toolbar.addAction(self.disconnect_action)
        toolbar.addSeparator()
        toolbar.addAction(self.read_action)
        self.auto_read_action = action("Auto-read measurements", lambda checked:setattr(self,"auto_read",checked))
        self.auto_read_action.setCheckable(True)
        self.auto_read_action.setToolTip("Read live HEX measurements in open configuration windows every two seconds. Telemetry is always received independently.")
        toolbar.addAction(self.auto_read_action)
        toolbar.addSeparator()
        toolbar.addAction(self.apply_action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a setting  ·  Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(300)
        self.search.setAccessibleName("Find a setting")
        self.find_action = action("Find a setting", self.focus_search, "Ctrl+F", edit_menu)
        self.find_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView))
        toolbar.addAction(self.find_action)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search.textChanged.connect(lambda:self.search_timer.start())
        self.search_timer.timeout.connect(self.run_search)
        self.addToolBarBreak()
        self.windows_toolbar = QToolBar("Monitoring and configuration windows", self)
        self.windows_toolbar.setObjectName("configurationWindows")
        self.windows_toolbar.setMovable(False)
        self.windows_toolbar.setIconSize(QSize(28, 28))
        self.windows_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.windows_toolbar.addActions(list(self.window_actions.values()))
        self.addToolBar(self.windows_toolbar)
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        self.connection_label = QLabel("Disconnected. Connect hardware or start a demonstration.")
        self.connection_label.setObjectName("connectionState")
        self.connection_label.setWordWrap(True)
        layout.addWidget(self.connection_label)
        self.warning = QLabel()
        self.warning.setObjectName("persistentWarning")
        self.warning.setWordWrap(True)
        self.warning.hide()
        layout.addWidget(self.warning)
        self.monitor_body = QVBoxLayout()
        layout.addLayout(self.monitor_body, 1)
        self.pending_label = QLabel("No pending changes")
        self.setCentralWidget(central)
        self.help_dock = QDockWidget("Setting explanation", self)
        self.help_dock.setObjectName("settingHelpDock")
        self.help_dock.setMinimumWidth(300)
        self.help_browser = QTextBrowser()
        self.help_browser.setOpenExternalLinks(True)
        self.help_browser.setObjectName("settingExplanation")
        self.help_dock.setWidget(self.help_browser)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.help_dock)
        self.resizeDocks([self.help_dock], [340], Qt.Orientation.Horizontal)
        self.help_dock.hide()
        self.log_dock = QDockWidget("Activity and device responses", self)
        self.log_dock.setObjectName("activityDock")
        self.log = QTableWidget(0, 3)
        self.log.setHorizontalHeaderLabels(["Time", "Status", "Message"])
        self.log.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.log.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.log.verticalHeader().hide()
        self.log.setMinimumHeight(90)
        self.log_dock.setWidget(self.log)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_dock)
        self.resizeDocks([self.log_dock], [135], Qt.Orientation.Vertical)
        self.log_dock.hide()
        view_menu.addAction(self.help_dock.toggleViewAction())
        view_menu.addAction(self.log_dock.toggleViewAction())
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(230)
        self.progress.setTextVisible(True)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.status_label = QLabel("Not connected")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.pending_label)

    def theme(self, name):
        app = QApplication.instance()
        palette = QPalette()
        dark = name == "dark"
        colors = {
            QPalette.ColorRole.Window: "#292d32" if dark else "#eceef0",
            QPalette.ColorRole.Base: "#202429" if dark else "#ffffff",
            QPalette.ColorRole.AlternateBase: "#2c3238" if dark else "#f3f5f7",
            QPalette.ColorRole.Text: "#e5e9ee" if dark else "#202830",
            QPalette.ColorRole.WindowText: "#e5e9ee" if dark else "#202830",
            QPalette.ColorRole.Button: "#343b43" if dark else "#e5e9ed",
            QPalette.ColorRole.ButtonText: "#e5e9ee" if dark else "#202830",
            QPalette.ColorRole.Highlight: "#356d9f", QPalette.ColorRole.HighlightedText: "#ffffff",
            QPalette.ColorRole.PlaceholderText: "#aab5c0" if dark else "#5e6b77",
            QPalette.ColorRole.Mid: "#59636d" if dark else "#b9c2ca",
            QPalette.ColorRole.Link: "#79b8ea" if dark else "#286493",
        }
        for role, color in colors.items():
            palette.setColor(role, QColor(color))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8e98a3" if dark else "#727d87"))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8e98a3" if dark else "#727d87"))
        app.setPalette(palette)
        app.setStyleSheet("QToolBar {spacing: 5px; padding: 5px;} QGroupBox {margin-top: 14px; padding-top: 10px;} QGroupBox::title {subcontrol-origin: margin; left: 9px;} QLabel#fieldNote {font-size: 11px;} QLabel#connectionState {padding: 7px; border: 1px solid palette(mid);} QLabel#persistentWarning {padding: 8px; border: 2px solid #ba7c23;} QTextBrowser#settingExplanation {padding: 10px;} QLineEdit, QComboBox {min-height: 25px;} QPushButton {padding: 6px 10px;} QToolButton#question {padding: 2px;}")
        for key, action in self.window_actions.items():
            action.setIcon(section_icon(key))
        self.settings.setValue("theme", name)
        for plot in self.plots:
            plot.update()

    def help_button(self, key, control=False):
        button = QToolButton()
        button.setText("?")
        button.setObjectName("question")
        button.setMinimumSize(24, 24)
        label = self.by_name.get(key, {}).get("label", human(key))
        button.setAccessibleName("Explain " + label)
        button.setToolTip("Open the full explanation for " + label)
        button.clicked.connect(lambda:self.show_control_help(key, parent=button.window()) if control else self.show_help(key, parent=button.window()))
        return button

    def operation_window(self):
        active = QApplication.activeWindow()
        while active is not None:
            if active is self or isinstance(active, ConfigurationWindow) and active.owner is self:
                return active
            active = active.parentWidget()
        return self

    def help_target(self, parent):
        target = parent or self.operation_window()
        return target if isinstance(target, ConfigurationWindow) and target.owner is self else self

    def show_help(self, name, parent=None):
        r = self.by_name.get(name)
        if not r:
            return
        esc = html.escape
        help_text = r["help"]
        related = next((g["description"] for g in GROUPS if g["id"] == r["group"]), "")
        choices = "".join(f"<li>{esc(c['label'])}</li>" for c in r["choices"])
        target = self.help_target(parent)
        target.help_browser.setHtml(f"<h2>{esc(r['label'])}</h2><p>{esc(help_text['what'])}</p><h3>When this takes effect</h3><p>{esc(help_text['workflow'])}</p>" + (f"<h3>Units and precision</h3><p>{esc(r['unit'])}. Smallest step {self.number(r['step'],6)}.</p>" if r["unit"] else "") + (f"<h3>Available choices</h3><ul>{choices}</ul>" if choices else "") + f"<h3>Related controls</h3><p>{esc(related)}</p><p><a href='{esc(help_text['source'])}'>Official protocol reference</a></p>")
        target.help_dock.show()

    def show_control_help(self, key, parent=None):
        target = self.help_target(parent)
        target.help_browser.setHtml(f"<h2>{html.escape({'apply':'How Apply works','profiles':'Configuration profiles','connection':'Device connection','chargecurve':'Charge curve','temperature':'Temperature compensation','lighting':'Lighting schedule','peukert':'Discharge-rate model','dynamic':'Dynamic cutoff'}.get(key,human(key)))}</h2><p>{html.escape(CONTROL_HELP[key])}</p>")
        target.help_dock.show()

    def value(self, name):
        if name in self.pending:
            return self.pending[name]["value"]
        record = self.state["values"].get(name, {})
        return record.get("value") if record.get("status") == "available" else self.MISSING

    def connected(self):
        return self.state["connection"] == "connected"

    def busy(self):
        return self.future is not None or (self.state.get("job") or {}).get("status") == "running"

    def can_edit(self, name):
        r = self.by_name.get(name)
        if r and r["kind"] == "BITMASK":
            value = self.state["values"].get(name,{}).get("value")
            known = {c["value"] for c in r["choices"]}
            if isinstance(value,list) and any(flag not in known for flag in value):
                return False
        if r and r["requiresUserPreset"]:
            presets = [r["name"] for r in self.catalog if r["name"].endswith("BAT_TYPE")]
            if presets and any(self.state["values"].get(n,{}).get("value")!="user" for n in presets):
                return False
        return bool(r and r["writable"] and self.connected() and not self.busy() and (r["access"] == "WRITE_ONLY" or r["command"] or self.state["values"].get(name, {}).get("status") == "available"))

    def number(self, value, decimals=2):
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            return "—"
        locale = QLocale()
        locale.setNumberOptions(QLocale.NumberOption.OmitGroupSeparator)
        text = locale.toString(float(value), "f", decimals)
        decimal = locale.decimalPoint()
        return text.rstrip("0").rstrip(decimal) if decimals else text

    def format_value(self, r, value):
        if value is self.MISSING:
            record = self.state["values"].get(r["name"], {})
            return "Unavailable on this device" if record.get("status") == "unsupported" else "Read failed" if record.get("status") == "error" else "Not read"
        if value is None:
            return r["nullable"] or "Unavailable"
        if r["command"] and r["kind"] == "VOID":
            return "Run once"
        if isinstance(value, bool):
            return "Enabled" if value else "Disabled"
        if isinstance(value, (int, float)):
            return self.number(value * r["factor"] + r["offset"], 3) + (" " + r["unit"] if r["unit"] else "")
        if isinstance(value, list):
            return ", ".join(human(v) for v in value) or "No active flags"
        if isinstance(value, dict):
            if r["name"].startswith("TIMER_EVENT_"):
                offset=value.get("offset_min")
                timing=f"{offset:+} min" if isinstance(offset,(int,float)) else "timing unavailable"
                return f"{value.get('dim_pct', '—')}% · { {1:'sunset',2:'midnight',3:'sunrise'}.get(value.get('anchor'),'unknown')} {timing}"
            if r["name"] == "LOAD_OUTPUT_CONTROL":
                return human(value.get("mode_name", str(value.get("mode")))) + (" · lighting timer" if value.get("lighting_timer") else "")
            return "\n".join(f"{human(k)}: {self.number(v,3) if isinstance(v,(int,float)) else v if v is not None else 'unavailable'}" for k,v in value.items() if k not in ("reserved", "raw")) or "No decoded fields"
        return next((c["label"] for c in r["choices"] if c["value"] == value), human(value))

    def stage(self, name, value):
        if not self.can_edit(name):
            return False
        r = self.by_name[name]
        if r["command"] and any(n != name for n in self.pending) or not r["command"] and any(self.by_name[n]["command"] for n in self.pending):
            self.statusBar().showMessage("Stage maintenance commands separately. Finish or discard the existing edits first.", 6000)
            return False
        try:
            value = validate_value(registers(self.state["flavor"])[name], value)
        except (ValueError, TypeError, KeyError) as exc:
            self.invalid[name] = str(exc)
            self.update_actions()
            return False
        self.undo_history.append(copy.deepcopy(self.pending))
        self.undo_history = self.undo_history[-100:]
        self.redo_history.clear()
        old = self.pending.get(name, {}).get("expected", self.state["values"].get(name, {}).get("value", self.MISSING))
        original = self.state["values"].get(name, {}).get("value", self.MISSING)
        same = False
        if original is not self.MISSING and not r["command"]:
            from ve_direct.registers import encode_value
            device_reg = registers(self.state["flavor"])[name]
            same = encode_value(device_reg, original) == encode_value(device_reg, value)
        if same:
            self.pending.pop(name, None)
        else:
            self.pending[name] = {"value":value}
            if old is not self.MISSING:
                self.pending[name]["expected"] = old
        self.invalid.pop(name, None)
        self.update_actions()
        for plot in self.plots:
            plot.update()
        return True

    def undo(self):
        if self.undo_history and not self.busy():
            self.redo_history.append(copy.deepcopy(self.pending))
            self.pending = self.undo_history.pop()
            self.invalid.clear()
            self.refresh_fields()
            self.update_actions()

    def redo(self):
        if self.redo_history and not self.busy():
            self.undo_history.append(copy.deepcopy(self.pending))
            self.pending = self.redo_history.pop()
            self.invalid.clear()
            self.refresh_fields()
            self.update_actions()

    def discard(self):
        if self.busy():
            return
        self.pending.clear()
        self.invalid.clear()
        self.undo_history.clear()
        self.redo_history.clear()
        self.refresh_fields()
        self.update_actions()

    def groupbox(self, title, rows):
        box = QGroupBox(title)
        layout = QVBoxLayout(box)
        layout.setSpacing(0)
        for r in rows:
            row = SettingRow(self, r)
            self.rows.append(row)
            layout.addWidget(row)
        return box

    def scroll(self, content):
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(content)
        return area

    def form_page(self, title, rows, plot_kind=None, note=""):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 8, 10, 10)
        if plot_kind:
            row = QHBoxLayout()
            row.addWidget(QLabel(title), 1)
            row.addWidget(self.help_button({"charge":"chargecurve","temperature":"temperature","lighting":"lighting","peukert":"peukert","dynamic":"dynamic"}[plot_kind], control=True))
            layout.addLayout(row)
            plot = Plot(self, plot_kind)
            plot.setMinimumHeight(250)
            layout.addWidget(plot)
            self.plots.append(plot)
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            layout.addWidget(label)
        if rows:
            layout.addWidget(self.groupbox(title if not plot_kind else "Exact settings", rows))
        layout.addStretch()
        return self.scroll(container)

    def load_pages(self):
        while self.monitor_body.count():
            self.monitor_body.takeAt(0)
        for widget in self.pages.values():
            widget.hide()
            widget.deleteLater()
        self.rows, self.plots, self.pages = [], [], {}
        self.pages["overview"] = self.build_overview()
        self.monitor_body.addWidget(self.pages["overview"])
        for group in GROUPS:
            rows = [r for r in self.catalog if r["group"] == group["id"]]
            tabs = QTabWidget()
            tabs.setDocumentMode(True)
            self.pages[group["id"]] = tabs
            writable = [r for r in rows if r["writable"]]
            readouts = [r for r in rows if not r["writable"]]
            if not rows:
                empty = QLabel("This device family does not expose these controls.\nChoose another configuration window for its available functions.")
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                tabs.addTab(empty, group["label"])
                continue
            used = set()
            def section(label, selected, plot=None, note=""):
                selected = list(selected)
                if not selected and not plot:
                    return
                used.update(r["name"] for r in selected)
                tabs.addTab(self.form_page(label, selected, plot, note), label)
            g = group["id"]
            if g == "charging":
                stages = {"BAT_ABSORPTION_VOLTAGE","BAT_FLOAT_VOLTAGE","BAT_STORAGE_VOLTAGE","BAT_ABSORPTION_LIMIT","BAT_BULK_LIMIT","TAIL_CURRENT","ADAPTIVE_MODE","REBULK_VOLTAGE_OFFSET"}
                section("Charge stages", (r for r in writable if r["name"] in stages), "charge", "Drag points to edit targets and maximum absorption time. Bulk duration is illustrative. Exact values remain available below.")
                temperature = {"BAT_TEMPERATURE_COMPENSATION","BAT_LOW_TEMP_LEVEL","LOW_TEMP_CHARGE_CURRENT"}
                section("Temperature", (r for r in writable if r["name"] in temperature), "temperature")
                section("Equalization", (r for r in writable if "EQUALIS" in r["name"] or "EQUALISE" in r["name"]), note="Use equalization only when the battery manufacturer explicitly permits it.")
                section("Engine & input", (r for r in writable if r["name"] in {"SHUTDOWN_VOLTAGE","START_VOLTAGE","DELAYED_START_VOLTAGE","START_DELAY","INPUT_VOLTAGE_LOCKOUT","INPUT_VOLTAGE_RESTART"}))
                section("Battery & limits", (r for r in writable if r["name"] not in used), note="Apply a changed battery preset separately. Read its resulting targets before editing the charge curve.")
            elif g == "monitor":
                section("Battery model", (r for r in writable if r["name"] in {"BMV_BATTERY_CAPACITY","BMV_PEUKERT_COEFFICIENT","BMV_CHARGE_EFFICIENCY","BMV_DISCHARGE_FLOOR","BMV_TTG_DELTA_T"}), "peukert")
                section("Full-charge detection", (r for r in writable if r["name"] in {"BMV_CHARGED_VOLTAGE","BMV_TAIL_CURRENT","BMV_CHARGED_DETECTION_TIME","BMV_START_SYNCHRONIZED"}))
                section("Measurement & shunt", (r for r in writable if r["name"] not in used))
            elif g == "load":
                section("Lighting schedule", (r for r in writable if r["name"].startswith("TIMER_EVENT_")), "lighting", "Preview uses sunset 18:00 and sunrise 06:00. The controller detects its own night. Negative offsets occur before the event anchor.")
                section("Load output", (r for r in writable if r["name"].startswith("LOAD_")), note="Disconnect and reconnect boundaries are separate to prevent rapid switching near one voltage.")
                section("Day & night detection", (r for r in writable if r["name"] not in used))
            elif g == "inverter":
                section("Dynamic cutoff", (r for r in writable if r["name"].startswith("INV_DYN_")), "dynamic", "The protocol does not publish a coefficient-to-curve equation. The graph plots measured cutoff versus load current. Use model-specific calibration values.")
                section("AC output & ECO", (r for r in writable if r["name"] not in used), note="These targets are mostly write-only. Normal Phoenix Apply also saves settings to nonvolatile memory.")
            elif g == "alarms":
                section("Relay control", (r for r in writable if r["kind"] in ("BOOLEAN","ENUM") or "TIME" in r["name"]))
                section("Voltage thresholds", (r for r in writable if r["unit"] == "V"))
                section("Charge & temperature", (r for r in writable if r["name"] not in used))
            elif g == "network":
                section("Ports & remote inputs", (r for r in writable if r["name"] in {"BLE_MODE","TX_PORT_MODE","RX_PORT_MODE","REMOTE_CONTROL_USED","REMOTE_INPUT_MODE_CONFIG"}))
                section("External charge control", (r for r in writable if r["name"] not in used), note="External measurements need an active controller that maintains freshness. A manually entered value does not replace a sensor.")
            elif g == "device":
                section("Operation", (r for r in writable if not r["command"]))
                section("Maintenance", (r for r in writable if r["command"]), note="Stage one command at a time. The review shows its full explanation and requires acknowledgement before Apply.")
            elif g == "history":
                summary = QWidget()
                vl = QVBoxLayout(summary)
                chart = Plot(self, "history")
                vl.addWidget(chart, 1)
                self.plots.append(chart)
                label = QLabel("Read values retrieves history. Missing records stay empty. Export a full snapshot before clearing device history.")
                label.setWordWrap(True)
                vl.addWidget(label)
                tabs.addTab(summary, "Yield / cycles")
                section("Records", (r for r in readouts if r["name"].startswith(("DAILY_HISTORY_","CYCLE_HISTORY_","MPPT_HISTORY_"))))
                section("Statistics", (r for r in readouts if r["name"] not in used))
            else:
                section("Display settings", writable)
            if g != "history":
                section("Readouts", readouts)
        self.search_page = self.build_search_page(self.search_query)
        self.pages["search"] = self.search_page
        self.telemetry_frame = None
        for key, window in self.windows.items():
            window.set_page(self.pages[key])
            window.help_browser.clear()
            window.help_dock.hide()

    def build_overview(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 8, 10, 8)
        instruments = QHBoxLayout()
        self.meters = []
        monitor = self.state["flavor"] in ("BMV", "BMV71")
        inverter = self.state["flavor"] == "INV"
        for title, key, unit in (("Battery voltage","V","V"),("Battery current","I","A"),("State of charge" if monitor else "AC output" if inverter else "Input power","SOC" if monitor else "AC_OUT_S" if inverter else "power","%" if monitor else "VA" if inverter else "W")):
            meter = Meter(title, unit)
            box = QGroupBox()
            v = QVBoxLayout(box)
            v.setContentsMargins(4, 2, 4, 2)
            v.addWidget(meter)
            help_button = QToolButton()
            help_button.setText("?")
            help_button.setAccessibleName("Explain " + title)
            help_button.clicked.connect(lambda checked=False,key=key:self.show_telemetry_help(key))
            v.addWidget(help_button,0,Qt.AlignmentFlag.AlignRight)
            instruments.addWidget(box)
            self.meters.append((meter,key))
        layout.addLayout(instruments)
        controls = QHBoxLayout()
        self.trace_combo = QComboBox()
        for key, label in (("power","Power"),("V","Battery voltage"),("I","Battery current"),("VPV","Panel voltage"),("SOC","State of charge"),("TTG","Time remaining"),("AC_OUT_V","AC voltage"),("AC_OUT_I","AC current"),("DC_IN_V","Source voltage")):
            self.trace_combo.addItem(label,key)
        self.trace_combo.setCurrentIndex(max(0,self.trace_combo.findData(self.trace_key)))
        self.trace_combo.currentIndexChanged.connect(lambda:setattr(self,"trace_key",self.trace_combo.currentData()))
        controls.addWidget(QLabel("Trace"))
        controls.addWidget(self.trace_combo)
        controls.addWidget(self.help_button("live",control=True))
        range_combo = QComboBox()
        for seconds, label in ((60,"1 minute"),(300,"5 minutes"),(900,"15 minutes")):
            range_combo.addItem(label,seconds)
        range_combo.setCurrentIndex(1)
        range_combo.currentIndexChanged.connect(lambda:setattr(self,"trace_seconds",range_combo.currentData()))
        controls.addWidget(range_combo)
        pause = QCheckBox("Pause plot")
        pause.setChecked(self.trace_paused)
        pause.toggled.connect(lambda enabled:setattr(self,"trace_paused",enabled))
        controls.addWidget(pause)
        controls.addStretch()
        export = QPushButton("Export CSV…")
        export.clicked.connect(self.export_csv)
        controls.addWidget(export)
        layout.addLayout(controls)
        chart = Plot(self,"live")
        self.plots.append(chart)
        layout.addWidget(chart, 1)
        self.operating_label = QLabel("Waiting for device state")
        self.operating_label.setWordWrap(True)
        layout.addWidget(self.operating_label)
        self.telemetry_table = QTableWidget(0, 4)
        self.telemetry_table.setHorizontalHeaderLabels(["Measurement", "Value", "Received", "Help"])
        self.telemetry_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.telemetry_table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.ResizeToContents)
        self.telemetry_table.verticalHeader().hide()
        self.telemetry_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.telemetry_table.cellDoubleClicked.connect(self.telemetry_help)
        layout.addWidget(self.telemetry_table, 1)
        return page

    def navigate(self, page):
        if page == "overview":
            window = self
        else:
            if page not in self.windows:
                title = "Find a setting" if page == "search" else next(g["label"] for g in GROUPS if g["id"] == page)
                self.windows[page] = ConfigurationWindow(self, page, title)
                self.windows[page].set_page(self.pages[page])
            window = self.windows[page]
        window.show()
        window.raise_()
        window.activateWindow()
        self.refresh_fields()
        self.update_actions()
        return window

    def close_configuration_windows(self):
        for window in self.windows.values():
            window.close()

    def focus_search(self):
        self.navigate("search")
        self.search.setFocus()
        self.search.selectAll()

    def build_search_page(self, query):
        page = QWidget()
        layout = QVBoxLayout(page)
        matches = [r for r in self.catalog if query and query in (r["label"]+" "+r["help"]["what"]).lower()]
        layout.addWidget(QLabel(f"{len(matches)} results for '{query}'. Search uses setting names and explanations." if query else "Enter a setting name or a word from its explanation above."))
        content = QWidget()
        vl = QVBoxLayout(content)
        for group in GROUPS:
            found = [r for r in matches if r["group"] == group["id"]]
            if found:
                vl.addWidget(self.groupbox(group["label"], found))
        vl.addStretch()
        layout.addWidget(self.scroll(content))
        return page

    def run_search(self):
        query = self.search.text().strip().lower()
        self.search_query = query
        old = self.search_page
        self.rows = [r for r in self.rows if not old.isAncestorOf(r)]
        self.search_page = self.build_search_page(query)
        self.pages["search"] = self.search_page
        if "search" in self.windows:
            self.windows["search"].set_page(self.search_page)
        old.deleteLater()
        if query and ("search" not in self.windows or not self.windows["search"].isVisible()):
            self.navigate("search")

    def dispatch(self, coroutine, done=None):
        if self.future:
            coroutine.close()
            return
        self.future = asyncio.run_coroutine_threadsafe(coroutine,self.controller.loop)
        self.future_done = done
        self.update_actions()

    def open_connection(self):
        if self.busy():
            return
        dialog = ConnectionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and self.confirm_discard():
            self.dispatch(self.controller.connect(**dialog.parameters()))

    def confirm_discard(self):
        if not self.pending and not self.invalid:
            return True
        result = QMessageBox.question(self,"Pending edits","Changing the connection discards pending edits. Discard them and continue?",QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        return result == QMessageBox.StandardButton.Discard

    def disconnect(self):
        if not self.busy() and self.confirm_discard():
            self.discard()
            self.dispatch(self.controller.disconnect())

    def read_page(self, all_values=False, page=None):
        if not self.connected() or self.busy():
            return
        window = self.operation_window()
        page = page or (window.key if isinstance(window, ConfigurationWindow) else "overview")
        if all_values:
            selected = self.catalog
        elif page == "overview":
            selected = [r for r in self.catalog if r["access"] == "READ_ONLY" and r["group"] != "history"]
        elif page == "search":
            selected = [r for r in self.catalog if self.search_query in (r["label"]+" "+r["help"]["what"]).lower()]
        else:
            selected = [r for r in self.catalog if r["group"] == page]
        names = [r["name"] for r in selected if r["access"] != "WRITE_ONLY" and r["kind"] != "VOID"]
        if names:
            self.dispatch(self.controller.start_read(names))

    def review_apply(self):
        # Finish any numeric input before taking the review snapshot.
        focus = QApplication.focusWidget()
        if focus:
            focus.clearFocus()
        if not self.pending or self.invalid or self.busy():
            return
        parent = self.operation_window()
        dialog = QDialog(parent)
        dialog.setWindowTitle("Review pending changes")
        dialog.resize(780, 460)
        layout = QVBoxLayout(dialog)
        note = QLabel(f"{len(self.pending)} changes for {'the offline demo' if self.state['demo'] else self.state['port']}. Settings are written sequentially; a failure can leave earlier changes in place.")
        note.setWordWrap(True)
        layout.addWidget(note)
        table = QTableWidget(len(self.pending),3)
        table.setHorizontalHeaderLabels(["Setting","Device value","Pending value"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i,(name,change) in enumerate(self.pending.items()):
            r=self.by_name[name]
            for j,text in enumerate((r["label"],"One-time command" if r["command"] else self.format_value(r,change.get("expected",self.MISSING)),self.format_value(r,change["value"]))):
                table.setItem(i,j,QTableWidgetItem(text))
        layout.addWidget(table,1)
        command = next((n for n in self.pending if self.by_name[n]["command"]),None)
        ack = QCheckBox("I have read the explanation and understand this command's effects.")
        ack.setVisible(bool(command))
        if command:
            browser=QTextBrowser()
            browser.setPlainText(self.by_name[command]["help"]["what"])
            layout.addWidget(browser,1)
            self.show_help(command, parent=parent)
        layout.addWidget(ack)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        apply_button=buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setText("Apply to demo" if self.state["demo"] else "Apply to device")
        apply_button.setEnabled(not command)
        ack.toggled.connect(apply_button.setEnabled)
        apply_button.clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            payload={"generation":self.state["generation"],"changes":{n:p["value"] for n,p in self.pending.items()},"expected":{n:p["expected"] for n,p in self.pending.items() if "expected" in p},"acknowledged":ack.isChecked()}
            names=list(self.pending)
            self.dispatch(self.controller.start_apply(payload),lambda result:setattr(self,"active_apply",{"id":result["jobId"],"names":names,"window":parent}))

    def finish_apply(self, job):
        parent = self.active_apply["window"]
        requested=self.active_apply["names"]
        attempted=set()
        statuses=[]
        for result in job["results"]:
            name=result["name"]
            attempted.add(name)
            r=self.by_name[name]
            statuses.append(f"{r['label']}: { {'verified':'readback verified','accepted':'command accepted','mismatch':'readback differs','failed':'failed'}.get(result['status'],result['status'])}")
            if result["status"]=="verified" or result["status"]=="accepted" and (r["access"]=="WRITE_ONLY" or r["command"]):
                self.pending.pop(name,None)
            elif name in self.pending and self.state["values"].get(name,{}).get("status")=="available":
                self.pending[name]["expected"]=self.state["values"][name]["value"]
        statuses.extend(f"{self.by_name[n]['label']}: not attempted" for n in requested if n not in attempted)
        self.undo_history.clear()
        self.redo_history.clear()
        self.active_apply=None
        self.completed_job=job["id"]
        self.refresh_fields()
        self.update_actions()
        message=QMessageBox(parent)
        message.setWindowTitle("Apply stopped" if job["status"]=="failed" else "Changes applied")
        message.setIcon(QMessageBox.Icon.Warning if job["status"]=="failed" else QMessageBox.Icon.Information)
        message.setText(job["error"] if job["status"]=="failed" else "The device accepted your changes. Readable targets were verified.")
        extra="Earlier accepted writes can already affect the device. Remaining edits are kept." if job["status"]=="failed" else ""
        if job["saved"] is False:
            extra+="\nPhoenix settings were not saved. Accepted settings can be lost after reset. Recover the connection if needed, then use the Save memory command in Device."
        if job["saved"] is True:
            extra+="\nPhoenix user settings were saved to nonvolatile memory."
        if not self.state["values"]:
            extra+="\nThis command invalidated cached settings. Read the device again before editing."
        message.setInformativeText("\n".join(statuses)+( "\n\n"+extra if extra else ""))
        message.setStandardButtons(QMessageBox.StandardButton.Ok)
        message.open()
        self.result_message=message

    def refresh_fields(self):
        for row in self.rows:
            row.refresh()
        for plot in self.plots:
            if plot.kind!="live" or not self.trace_paused:
                plot.update()

    def update_actions(self):
        busy, connected = self.busy(), self.connected()
        self.connect_action.setEnabled(not busy)
        self.disconnect_action.setEnabled(not busy and self.state["connection"]!="disconnected")
        self.read_action.setEnabled(not busy and connected)
        self.read_all_action.setEnabled(not busy and connected)
        self.import_action.setEnabled(not busy and connected)
        self.apply_action.setEnabled(bool(self.pending) and not self.invalid and not busy and connected)
        self.discard_action.setEnabled(bool(self.pending or self.invalid) and not busy)
        self.undo_action.setEnabled(bool(self.undo_history) and not busy)
        self.redo_action.setEnabled(bool(self.redo_history) and not busy)
        count=len(self.pending)
        self.pending_label.setText(f"{count} pending {'change' if count==1 else 'changes'} across all windows" if self.pending else "No pending changes")
        if self.invalid:
            self.pending_label.setText(f"{len(self.invalid)} invalid inputs · finish or discard them before Apply")
        for page, action in self.window_actions.items():
            label="Live overview" if page=="overview" else next(g["label"] for g in GROUPS if g["id"]==page)
            count=sum(self.by_name[n]["group"]==page for n in self.pending)
            action.setText(label+(f"  [{count}]" if count else ""))
        for window in self.windows.values():
            window.update_status()

    def tick(self):
        if self.future is not None and self.future.done():
            future, done = self.future, self.future_done
            self.future, self.future_done = None, None
            try:
                result=future.result()
                if done:
                    done(result)
            except Exception as exc:
                QMessageBox.warning(self,"Device operation",str(exc))
        old=self.state
        self.state=self.controller.snapshot()
        if old["generation"]!=self.state["generation"]:
            self.pending.clear()
            self.invalid.clear()
            self.undo_history.clear()
            self.redo_history.clear()
            self.catalog=metadata(self.state["flavor"])
            self.by_name={r["name"]:r for r in self.catalog}
            self.cutoff_samples=[]
            self.hex_history, self.hex_times = {}, {}
            self.load_pages()
        job=self.state.get("job")
        if self.active_apply and job and job["id"]==self.active_apply["id"] and job["status"]!="running" and job["id"]!=self.completed_job:
            self.finish_apply(job)
        self.update_actions()
        for name,entry in self.state["values"].items():
            if entry["status"]=="available" and isinstance(entry["value"],(int,float)) and not isinstance(entry["value"],bool) and entry["time"]!=self.hex_times.get(name):
                self.hex_history.setdefault(name,[]).append(entry["value"])
                self.hex_history[name]=self.hex_history[name][-60:]
                self.hex_times[name]=entry["time"]
        self.refresh_fields()
        self.refresh_telemetry()
        flavor=FAMILIES[self.state["flavor"]]
        t=self.state["telemetry"]
        model=t.get("PID",{}).get("human","")
        self.connection_label.setText(f"{'DEMONSTRATION · ' if self.state['demo'] else ''}{human(self.state['connection'])} · {model or flavor} · {self.state['port'] or 'no port selected'}")
        if self.state["connection"]=="fault":
            warning=self.state["error"]
        elif self.connected() and self.state["lastFrame"] and time.time()-self.state["lastFrame"]>8:
            warning="Telemetry is stale. Cached values do not confirm current device operation. Check the cable and device."
        elif self.state["flavor"]=="MULTI_RS":
            warning="Multi RS contains experimental, reverse-engineered measurements. Device support and meanings are not guaranteed."
        else:
            warning=""
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning))
        if job and job["status"]=="running":
            self.progress.show()
            self.progress.setRange(0,job["total"])
            self.progress.setValue(job["completed"])
            phase=job["phase"]
            for name,r in self.by_name.items():
                if phase.endswith(name):
                    phase=phase[: -len(name)]+r["label"]
                    break
            self.status_label.setText(phase)
        else:
            self.progress.hide()
            self.status_label.setText(f"{self.state['frames']} validated frames · {len(self.state['values'])} values read · {human(self.state['connection'])}")
        if old["events"]!=self.state["events"]:
            events=self.state["events"]
            self.log.setRowCount(len(events))
            for i,event in enumerate(events):
                for j,text in enumerate((datetime.fromisoformat(event["time"]).astimezone().strftime("%X"),event["level"],self.friendly_message(event["message"]))):
                    self.log.setItem(i,j,QTableWidgetItem(text))
            self.log.scrollToBottom()
        cutoff=self.state["values"].get("INV_DYN_CUTOFF_VOLTAGE",{})
        amps=self.state["values"].get("AC_OUT_CURRENT",{})
        if cutoff.get("status")=="available" and amps.get("status")=="available" and cutoff["time"]!=self.last_cutoff_time:
            self.cutoff_samples.append((abs(amps["value"]),cutoff["value"]))
            self.cutoff_samples=self.cutoff_samples[-200:]
            self.last_cutoff_time=cutoff["time"]
        for window in self.windows.values():
            window.update_status()
        if self.auto_read and self.connected() and not self.busy() and time.monotonic()-self.last_auto_read>=2:
            visible = {key for key, window in self.windows.items() if window.isVisible() and key not in ("search", "history")}
            names=[r["name"] for r in self.catalog if r["group"] in visible and r["access"]=="READ_ONLY" and r["kind"]!="VOID"]
            if names:
                self.last_auto_read=time.monotonic()
                self.dispatch(self.controller.start_read(names))

    def friendly_message(self, message):
        for name in sorted(self.by_name,key=len,reverse=True):
            message=message.replace(name,self.by_name[name]["label"])
        return message

    def trace_label(self,key):
        return {"V":"Battery voltage","I":"Battery current","VPV":"Panel voltage","PPV":"Solar power","SOC":"State of charge","TTG":"Time remaining","AC_OUT_V":"AC voltage","AC_OUT_I":"AC current","DC_IN_V":"Source voltage","power":"Power"}.get(key,self.state["telemetry"].get(key,{}).get("label",key).split(" (")[0])

    def trace_unit(self,key):
        return "VA" if key=="power" and self.state["flavor"]=="INV" else "W" if key=="power" else self.state["telemetry"].get(key,{}).get("unit","")

    def sample_value(self,sample,key):
        if key=="power":
            return sample.get("PPV",sample.get("P",sample.get("AC_OUT_S",sample.get("DC_IN_P",sample["V"]*sample["I"] if isinstance(sample.get("V"),(int,float)) and isinstance(sample.get("I"),(int,float)) else None))))
        return sample.get(key)

    def refresh_telemetry(self):
        t=self.state["telemetry"]
        sample={k:v["value"] for k,v in t.items()}
        for meter,key in self.meters:
            value=self.sample_value(sample,key)
            if key=="V":
                nominal=self.value("BAT_VOLTAGE")
                nominal=nominal if isinstance(nominal,(int,float)) and nominal else 12 if not isinstance(value,(int,float)) or value<18 else 24 if value<36 else 48
                low,high=nominal*.75,nominal*1.4
            elif key=="SOC":
                low,high=0,100
            elif key=="I":
                rated=self.value("CHARGER_MAXIMUM_CURRENT")
                high=max(10,rated if isinstance(rated,(int,float)) else 0,abs(value)*1.2 if isinstance(value,(int,float)) else 0)
                low=-high
            else:
                rated=self.value("AC_OUT_RATED_POWER")
                low,high=0,max(100,rated if isinstance(rated,(int,float)) else 0,value*1.2 if isinstance(value,(int,float)) else 0)
            fresh=self.connected() and self.state["lastFrame"] and time.time()-self.state["lastFrame"]<=8
            meter.set_value(value,low,high,f"Scale {low:g} to {high:g} {meter.unit}" if fresh else "Cached / waiting")
        state=t.get("CS",{}).get("human","Not reported")
        alarms=[v["human"] or str(v["value"]) for k,v in t.items() if k in ("ERR","AR","WARN") and isinstance(v["value"],(int,float)) and v["value"]>0]
        self.operating_label.setText("Operating state: "+state+("    ⚠ "+" · ".join(alarms) if alarms else "    No alarm reported" if any(k in t for k in ("ERR","AR","WARN","Alarm")) else "    Alarm status not received"))
        fields=list(t.items())
        # Rebuild only when a new frame arrives, keeping selection while polling.
        if getattr(self,"telemetry_frame",None)==self.state["frames"]:
            return
        self.telemetry_frame=self.state["frames"]
        self.telemetry_table.setRowCount(len(fields))
        for i,(key,value) in enumerate(fields):
            text=value["human"] or (self.number(value["value"],3) if isinstance(value["value"],(int,float)) else str(value["value"] if value["value"] is not None else "Unavailable"))
            label=QTableWidgetItem(self.trace_label(key))
            label.setData(Qt.ItemDataRole.UserRole,key)
            self.telemetry_table.setItem(i,0,label)
            self.telemetry_table.setItem(i,1,QTableWidgetItem(text+(" "+value["unit"] if value["unit"] else "")))
            self.telemetry_table.setItem(i,2,QTableWidgetItem(datetime.fromisoformat(value["time"]).astimezone().strftime("%X")))
            button=QToolButton()
            button.setText("?")
            button.setAccessibleName("Explain "+self.trace_label(key))
            button.clicked.connect(lambda checked=False,key=key:self.show_telemetry_help(key))
            self.telemetry_table.setCellWidget(i,3,button)
            if isinstance(value["value"],(int,float)) and value["unit"] and self.trace_combo.findData(key)<0:
                self.trace_combo.addItem(self.trace_label(key),key)

    def telemetry_help(self,row,column):
        item=self.telemetry_table.item(row,0)
        if item:
            key=item.data(Qt.ItemDataRole.UserRole)
            self.show_telemetry_help(key)
            if self.trace_combo.findData(key)>=0:
                self.trace_combo.setCurrentIndex(self.trace_combo.findData(key))

    def show_telemetry_help(self,key):
        value=self.state["telemetry"].get(key,{})
        self.help_browser.setHtml(f"<h2>{html.escape(self.trace_label(key))}</h2><p>{html.escape(value.get('label',self.trace_label(key)))}. This field comes from checksum-validated VE.Direct telemetry. Protocol scaling is already applied.</p><p>{'Positive battery current means charging; negative means discharge. Other loads and charging sources can contribute to net current.' if key=='I' else 'The monitor estimates state of charge from current, capacity, efficiency, and the discharge-rate model. It is not a direct measurement of chemical charge.' if key=='SOC' else 'Time remaining depends on averaged current and the configured discharge floor. An unavailable time can mean that the bank is not discharging.' if key=='TTG' else 'Check the timestamp and live connection state before treating a cached value as current.'}</p>")
        self.help_dock.show()

    def write_json(self, data, title, default):
        path,_=QFileDialog.getSaveFileName(self,title,default,"JSON files (*.json)")
        if not path:
            return
        try:
            destination=Path(path)
            serialized=json.dumps(data,indent=2,allow_nan=False)+"\n"
            with tempfile.NamedTemporaryFile(mode="w",dir=destination.parent,prefix=destination.name+".",suffix=".tmp",delete=False) as output:
                temporary=Path(output.name)
                output.write(serialized)
            temporary.replace(destination)
            self.statusBar().showMessage("Saved "+path,6000)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,"Export failed",str(exc))

    def export_profile(self):
        self.write_json(self.controller.export_profile(),"Export settings","vedirwiz-settings.json")

    def export_snapshot(self):
        self.write_json({"format":"vedirwiz-snapshot","exportedAt":utc(),**self.controller.snapshot()},"Export device snapshot","vedirwiz-snapshot.json")

    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,"Export recorded telemetry","vedirwiz-telemetry.csv","CSV files (*.csv)")
        if not path:
            return
        try:
            samples=self.state["samples"]
            fields=["time"]+sorted(set().union(*(set(s)-{"time"} for s in samples)))
            with open(path,"w",newline="") as output:
                writer=csv.DictWriter(output,fieldnames=fields)
                writer.writeheader()
                writer.writerows(samples)
        except OSError as exc:
            QMessageBox.warning(self,"Export failed",str(exc))

    def import_profile(self):
        path,_=QFileDialog.getOpenFileName(self,"Import settings as pending changes","","JSON files (*.json)")
        if not path:
            return
        try:
            if Path(path).stat().st_size>262144:
                raise ValueError("Use a profile smaller than 256 KiB.")
            settings=self.controller.import_profile({"profile":json.loads(Path(path).read_text())})["settings"]
            unread=[n for n in settings if self.by_name[n]["access"]!="WRITE_ONLY" and self.state["values"].get(n,{}).get("status")!="available"]
            if unread:
                raise ValueError("Read the device successfully before importing. Some profile controls have not been read on this model.")
            if any(self.by_name[n]["command"] for n in self.pending):
                raise ValueError("Discard the maintenance command before importing a profile.")
            presets=[n for n in settings if n.endswith("BAT_TYPE") and settings[n]!=self.value(n)]
            if presets:
                for n in presets:
                    self.stage(n,settings[n])
                QMessageBox.information(self,"Battery preset changed","Apply the preset separately, read the resulting targets, then import this profile again.")
            else:
                for name,value in settings.items():
                    self.stage(name,value)
            self.refresh_fields()
        except (OSError,ValueError,TypeError,KeyError) as exc:
            QMessageBox.warning(self,"Import failed",str(exc))

    def about(self):
        QMessageBox.about(self,"About VeDirWiz","VeDirWiz 0.1\nNative Qt VE.Direct configuration and monitoring.\nUses the ve.direct Python protocol library.\nAGPL-3.0-only.\n\nA supported family catalog does not guarantee that every model implements every listed control. Unsupported reads remain visible with their reason.")

    def closeEvent(self,event):
        if self.busy():
            QMessageBox.information(self,"Operation in progress","Wait for the read or Apply to finish before closing the device connection.")
            event.ignore()
            return
        if self.pending or self.invalid:
            result=QMessageBox.question(self,"Pending edits","Discard pending edits and close VeDirWiz?",QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
            if result!=QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        self.settings.setValue("monitor/geometry",self.saveGeometry())
        self.settings.setValue("monitor/windowState",self.saveState())
        self.close_configuration_windows()
        self.timer.stop()
        event.accept()


def main():
    parser=argparse.ArgumentParser(description="Native Qt VE.Direct configuration application")
    parser.add_argument("--demo",action="store_true",help="Connect the offline protocol demonstration")
    parser.add_argument("--port",help="VE.Direct serial port")
    parser.add_argument("--family",choices=FAMILIES,default="MPPT")
    parser.add_argument("--baudrate",type=int,default=19200)
    parser.add_argument("--timeout",type=float,default=2)
    args=parser.parse_args()
    if not 0<args.timeout<=30:
        parser.error("--timeout must be between 0 and 30 seconds")
    app=QApplication([sys.argv[0]])
    app.setApplicationName("VeDirWiz")
    app.setOrganizationName("VeDirWiz")
    app.setStyle("Fusion")
    font=app.font()
    font.setPointSizeF(max(11.5,font.pointSizeF()))
    app.setFont(font)
    controller=Controller(args.timeout)
    window=MainWindow(controller)
    window.show()
    if args.demo or args.port:
        QTimer.singleShot(0,lambda:window.dispatch(controller.connect(args.family,args.demo,args.port or "",args.baudrate)))
    try:
        return app.exec()
    finally:
        controller.close()


if __name__=="__main__":
    main()
