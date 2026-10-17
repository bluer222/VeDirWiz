import math

from PySide6.QtCore import QLocale, QSignalBlocker, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from .plots import MiniTrace


class SettingRow(QWidget):
    def __init__(self, window, metadata):
        super().__init__()
        self.window, self.meta = window, metadata
        self.name = metadata["name"]
        self.controls = []
        self.setObjectName("row-" + self.name)
        grid = QGridLayout(self)
        grid.setContentsMargins(4, 5, 4, 5)
        grid.setHorizontalSpacing(8)
        self.label = QLabel(metadata["label"])
        self.label.setWordWrap(True)
        self.label.setMinimumWidth(160)
        grid.addWidget(self.label, 0, 0)
        grid.setColumnStretch(0, 1)
        help_button = window.help_button(self.name)
        grid.addWidget(help_button, 0, 1)
        self.input_area = QWidget()
        self.inputs = QHBoxLayout(self.input_area)
        self.inputs.setContentsMargins(0, 0, 0, 0)
        self.inputs.setSpacing(6)
        self.inputs.setAlignment(Qt.AlignmentFlag.AlignRight)
        grid.addWidget(self.input_area, 0, 2)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.note.setObjectName("fieldNote")
        grid.addWidget(self.note, 1, 2)
        self.numeric, self.mode, self.combo, self.checkbox, self.text = None, None, None, None, None
        self.flags, self.parts = {}, {}
        self.command_button = None
        self.mini_trace = None
        if not metadata["writable"]:
            self.readout = QLabel("Not read")
            self.readout.setWordWrap(True)
            self.readout.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.readout.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.readout.setMaximumWidth(450)
            self.inputs.addWidget(self.readout)
            if metadata["kind"] == "NUMERIC":
                self.mini_trace = MiniTrace(window, self.name)
                self.inputs.addWidget(self.mini_trace)
        elif metadata["command"] and metadata["kind"] == "VOID":
            self.command_button = QPushButton("Stage action")
            self.command_button.clicked.connect(lambda: window.stage(self.name, ""))
            self.inputs.addWidget(self.command_button)
        elif metadata["kind"] == "NUMERIC":
            self.numeric = QLineEdit()
            self.numeric.setObjectName("input-" + self.name)
            self.numeric.setMaximumWidth(135)
            self.numeric.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.numeric.setAccessibleName(metadata["label"])
            self.numeric.setPlaceholderText("Enter target" if metadata["access"] == "WRITE_ONLY" else "Not read")
            self.numeric.textEdited.connect(self.number_changed)
            self.numeric.editingFinished.connect(self.commit_number)
            self.inputs.addWidget(self.numeric)
            self.unit_label = QLabel(metadata["unit"])
            self.inputs.addWidget(self.unit_label)
            self.controls.append(self.numeric)
            if metadata["nullable"]:
                self.mode = QComboBox()
                self.mode.addItem("Set value", "number")
                self.mode.addItem(metadata["nullable"], "null")
                self.mode.setAccessibleName(metadata["label"] + " value mode")
                self.mode.currentIndexChanged.connect(self.change_mode)
                self.inputs.addWidget(self.mode)
                self.controls.append(self.mode)
        elif metadata["kind"] == "BOOLEAN":
            self.checkbox = QCheckBox("Enabled")
            self.checkbox.setAccessibleName(metadata["label"])
            self.checkbox.toggled.connect(lambda enabled: window.stage(self.name, enabled))
            self.inputs.addWidget(self.checkbox)
            self.controls.append(self.checkbox)
        elif metadata["kind"] == "ENUM":
            self.combo = QComboBox()
            self.combo.setMaximumWidth(240)
            self.combo.addItem("Choose a target" if metadata["access"] == "WRITE_ONLY" else "Not read", None)
            for choice in metadata["choices"]:
                self.combo.addItem(choice["label"], choice["value"])
            self.combo.currentIndexChanged.connect(lambda _: self.window.stage(self.name, self.combo.currentData()) if self.combo.currentData() is not None else None)
            self.inputs.addWidget(self.combo)
            self.controls.append(self.combo)
        elif metadata["kind"] == "BITMASK":
            container = QWidget()
            layout = QGridLayout(container)
            layout.setContentsMargins(0, 0, 0, 0)
            for i, choice in enumerate(metadata["choices"]):
                check = QCheckBox(choice["label"])
                check.setAccessibleName(metadata["label"] + ": " + choice["label"])
                check.toggled.connect(lambda _: window.stage(self.name, [n for n, c in self.flags.items() if c.isChecked()]))
                self.flags[choice["value"]] = check
                self.controls.append(check)
                layout.addWidget(check, i // 2, i % 2)
            self.inputs.addWidget(container)
        elif metadata["kind"] == "STRING":
            self.text = QLineEdit()
            self.text.setAccessibleName(metadata["label"])
            if metadata["maxLength"]:
                self.text.setMaxLength(metadata["maxLength"])
            self.text.textEdited.connect(lambda text: window.stage(self.name, text))
            self.inputs.addWidget(self.text)
            self.controls.append(self.text)
        elif self.name == "LOAD_OUTPUT_CONTROL":
            self.combo = QComboBox()
            for i, label in enumerate(("Off", "BatteryLife / auto", "Alternative 1", "Alternative 2", "On", "User mode 1", "User mode 2", "Energy saving")):
                self.combo.addItem(label, i)
            self.checkbox = QCheckBox("Lighting timer")
            self.combo.currentIndexChanged.connect(self.commit_load)
            self.checkbox.toggled.connect(self.commit_load)
            self.inputs.addWidget(self.combo)
            self.inputs.addWidget(self.checkbox)
            self.controls.extend([self.combo, self.checkbox])
        elif self.name.startswith("TIMER_EVENT_"):
            self.anchor = QComboBox()
            for i, label in enumerate(("Sunset", "Midnight", "Sunrise"), 1):
                self.anchor.addItem(label, i)
            self.inputs.addWidget(self.anchor)
            self.controls.append(self.anchor)
            self.anchor.setAccessibleName(metadata["label"] + " anchor")
            self.anchor.currentIndexChanged.connect(self.commit_timer)
            for key, label in (("offset_min", "Offset · min"), ("dim_pct", "Level · %")):
                column = QWidget()
                layout = QVBoxLayout(column)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.setSpacing(3)
                line = QLineEdit()
                line.setMaximumWidth(95)
                line.setAccessibleName(metadata["label"] + " " + label)
                line.setAlignment(Qt.AlignmentFlag.AlignRight)
                heading = QLabel(label)
                layout.addWidget(heading)
                layout.addWidget(line)
                self.inputs.addWidget(column)
                self.parts[key] = line
                self.controls.append(line)
                line.textEdited.connect(self.commit_timer)
                line.editingFinished.connect(self.commit_timer)
        for control in self.controls:
            if not control.accessibleName():
                control.setAccessibleName(metadata["label"])
        self.refresh()

    def check_number(self, *_):
        text = self.numeric.text().strip()
        if self.name == "TIME_OF_DAY":
            parts = text.split(":")
            valid = len(parts) == 2 and all(p.isdigit() for p in parts) and 0 <= int(parts[0]) <= 23 and 0 <= int(parts[1]) <= 59
        else:
            value, valid = QLocale().toDouble(text)
            valid = valid and math.isfinite(value)
        if valid:
            self.window.invalid.pop(self.name, None)
        else:
            self.window.invalid[self.name] = "Enter a valid number" if self.name != "TIME_OF_DAY" else "Enter a time as HH:MM"
        self.window.update_actions()

    def number_changed(self, *_):
        self.numeric.setModified(True)
        self.commit_number()

    def commit_number(self):
        if not self.numeric or not self.numeric.isEnabled() or not self.numeric.isModified():
            return
        self.check_number()
        if self.name in self.window.invalid:
            self.refresh()
            return
        if self.name == "TIME_OF_DAY":
            h, m = map(int, self.numeric.text().split(":"))
            value = h * 60 + m
        else:
            value = (QLocale().toDouble(self.numeric.text())[0] - self.meta["offset"]) / self.meta["factor"]
            value = round(value, 8)
        if self.window.stage(self.name, value):
            self.numeric.setModified(False)
        self.refresh()

    def change_mode(self):
        if self.mode.currentData() == "null":
            self.window.stage(self.name, None)
        else:
            old = self.window.state["values"].get(self.name, {}).get("value")
            if isinstance(old, (int, float)):
                self.window.stage(self.name, old)
            else:
                self.window.invalid[self.name] = "Enter a value before applying"
                self.numeric.show()
                self.unit_label.show()
                self.numeric.clear()
                self.numeric.setFocus()
        self.window.update_actions()
        if self.name not in self.window.invalid:
            self.refresh()

    def commit_load(self, *_):
        self.window.stage(self.name, {"mode": self.combo.currentData(), "lighting_timer": self.checkbox.isChecked()})

    def check_timer(self, *_):
        valid = all(line.text().lstrip("-").isdigit() for line in self.parts.values())
        if valid:
            self.window.invalid.pop(self.name, None)
        else:
            self.window.invalid[self.name] = "Enter integer event offsets and brightness"
        self.window.update_actions()

    def commit_timer(self, *_):
        if not self.window.can_edit(self.name):
            return
        self.check_timer()
        if self.name in self.window.invalid:
            return
        self.window.stage(self.name, {"anchor": self.anchor.currentData(), **{k:int(line.text()) for k, line in self.parts.items()}})

    def refresh(self):
        w, r = self.window, self.meta
        value = w.value(self.name)
        record = w.state["values"].get(self.name, {})
        editable = w.can_edit(self.name)
        for control in self.controls:
            control.setEnabled(editable)
        self.setProperty("pending", self.name in w.pending)
        self.label.setText(("● " if self.name in w.pending else "") + r["label"])
        self.label.setToolTip("Pending change" if self.name in w.pending else "")
        if self.name in w.invalid:
            self.note.setText(w.invalid[self.name])
        elif r["access"] == "WRITE_ONLY":
            self.note.setText("Write-only · accepted target" if value is not w.MISSING else "Write-only · enter a target")
        elif record.get("status") == "unsupported":
            self.note.setText("Unavailable on this model or firmware")
            self.note.setToolTip(record.get("error", ""))
        elif record.get("status") == "error":
            self.note.setText("Read failed · connection needs recovery")
            self.note.setToolTip(record.get("error", ""))
        elif r["requiresUserPreset"] and not editable and w.connected() and not w.busy():
            self.note.setText("Apply the User defined battery preset first")
        elif r["kind"] == "BITMASK" and not editable and isinstance(value,list) and any(flag not in {c["value"] for c in r["choices"]} for flag in value):
            self.note.setText("Unrecognized device flags · editing is locked to preserve them")
        elif r["writable"] and value is w.MISSING and not r["command"]:
            self.note.setText("Read the device to enable editing")
        else:
            self.note.setText("")
        self.note.setVisible(bool(self.note.text()))
        if not r["writable"]:
            self.readout.setText(w.format_value(r, value))
            if self.mini_trace:
                self.mini_trace.update()
            return
        if self.command_button:
            self.command_button.setEnabled(w.connected() and not w.busy())
            self.command_button.setText("Action staged" if self.name in w.pending else "Stage action")
        blockers = [QSignalBlocker(c) for c in self.controls]
        if self.numeric and not self.numeric.hasFocus() and self.name not in w.invalid:
            text = "" if value is None or value is w.MISSING else f"{int(value)//60:02d}:{int(value)%60:02d}" if self.name == "TIME_OF_DAY" else w.number(value * r["factor"] + r["offset"], 6)
            self.numeric.setText(text)
            self.numeric.setModified(False)
            self.numeric.setVisible(value is not None)
            self.unit_label.setVisible(value is not None)
        if self.mode and self.name not in w.invalid:
            self.mode.setCurrentIndex(1 if value is None else 0)
        if r["kind"] == "ENUM" and self.combo and not self.combo.hasFocus():
            index = self.combo.findData(value)
            self.combo.setCurrentIndex(max(0, index))
            if value is not w.MISSING and index < 0:
                self.note.setText("Device reports " + str(value))
        if self.checkbox and r["kind"] == "BOOLEAN":
            self.checkbox.setChecked(value is True)
        if self.text and not self.text.hasFocus():
            self.text.setText(value if isinstance(value, str) else "")
        for name, check in self.flags.items():
            check.setChecked(isinstance(value, list) and name in value)
        if self.name == "LOAD_OUTPUT_CONTROL" and isinstance(value, dict):
            self.combo.setCurrentIndex(self.combo.findData(value.get("mode")))
            self.checkbox.setChecked(value.get("lighting_timer", False))
        if self.parts and isinstance(value, dict):
            self.anchor.setCurrentIndex(max(0, self.anchor.findData(value.get("anchor"))))
            for key, line in self.parts.items():
                if not line.hasFocus() and self.name not in w.invalid:
                    line.setText(str(value.get(key, "")))
        del blockers
