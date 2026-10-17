"""Independent desktop windows sharing the main window's device session and drafts."""

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDockWidget, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QTextBrowser, QToolBar, QVBoxLayout, QWidget)


def section_icon(key):
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QApplication.palette().buttonText().color(), 1.8))
    if key in ("overview", "display"):
        painter.drawRect(QRectF(3, 5, 26, 20))
        painter.drawLine(11, 29, 21, 29)
        painter.drawLine(16, 25, 16, 29)
        if key == "overview":
            path = QPainterPath(QPointF(6, 19))
            for x, y in ((10, 19), (13, 10), (17, 21), (21, 14), (26, 14)):
                path.lineTo(x, y)
            painter.drawPath(path)
        else:
            for y in (11, 16, 21):
                painter.drawLine(8, y, 23, y)
    elif key in ("monitor", "charging"):
        painter.drawRect(QRectF(3, 8, 24, 16))
        painter.drawRect(QRectF(27, 13, 3, 6))
        if key == "charging":
            path = QPainterPath(QPointF(17, 10))
            for x, y in ((11, 17), (17, 17), (13, 23)):
                path.lineTo(x, y)
            painter.drawPath(path)
        else:
            painter.drawLine(7, 16, 13, 16)
            painter.drawLine(10, 13, 10, 19)
            painter.drawLine(18, 16, 23, 16)
    elif key == "load":
        painter.drawEllipse(QRectF(9, 9, 14, 14))
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            painter.drawLine(QPointF(16+10*math.cos(a), 16+10*math.sin(a)),
                             QPointF(16+14*math.cos(a), 16+14*math.sin(a)))
    elif key == "inverter":
        painter.drawRect(QRectF(3, 5, 26, 23))
        path = QPainterPath(QPointF(6, 16))
        for x in range(7, 27):
            path.lineTo(x, 16-7*math.sin((x-6)*math.pi/10))
        painter.drawPath(path)
    elif key == "alarms":
        path = QPainterPath(QPointF(6, 24))
        path.lineTo(9, 20)
        path.lineTo(9, 12)
        path.cubicTo(9, 3, 23, 3, 23, 12)
        path.lineTo(23, 20)
        path.lineTo(26, 24)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(13, 23, 6, 6), 180*16, 180*16)
    elif key == "network":
        painter.drawLine(5, 16, 27, 16)
        for x, y in ((4, 4), (22, 4), (13, 23)):
            painter.drawRect(QRectF(x, y, 6, 6))
            painter.drawLine(x+3, y+6 if y < 16 else y, x+3, 16)
    elif key == "history":
        painter.drawLine(4, 3, 4, 28)
        painter.drawLine(4, 28, 29, 28)
        for x, height in ((8, 10), (16, 21), (24, 16)):
            painter.drawRect(QRectF(x, 28-height, 4, height))
    else:
        for y, x in ((7, 11), (16, 22), (25, 15)):
            painter.drawLine(3, y, 29, y)
            painter.drawRect(QRectF(x-2, y-3, 4, 6))
    painter.end()
    return QIcon(pixmap)


class ConfigurationWindow(QMainWindow):
    def __init__(self, owner, key, title):
        super().__init__(owner, Qt.WindowType.Window)
        self.owner, self.key = owner, key
        self.page = None
        self.setObjectName("configurationWindow_" + key)
        self.setWindowTitle("VeDirWiz · " + title)
        self.resize(1160, 850)
        self.setMinimumSize(820, 600)
        toolbar = QToolBar("Configuration commands", self)
        toolbar.setObjectName("configurationCommands")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setIconSize(QSize(20, 20))
        toolbar.addAction(owner.read_action)
        toolbar.addAction(owner.auto_read_action)
        toolbar.addSeparator()
        toolbar.addAction(owner.apply_action)
        toolbar.addAction(owner.discard_action)
        self.addToolBar(toolbar)
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addActions([owner.import_action, owner.export_action])
        close_action = QAction("Close window", self)
        close_action.setShortcut(QKeySequence("Ctrl+W"))
        close_action.triggered.connect(self.close)
        file_menu.addSeparator()
        file_menu.addAction(close_action)
        file_menu.addAction(owner.quit_action)
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addActions([owner.undo_action, owner.redo_action, owner.find_action])
        edit_menu.addSeparator()
        edit_menu.addActions([owner.apply_action, owner.discard_action])
        device_menu = self.menuBar().addMenu("&Device")
        device_menu.addActions([owner.connect_action, owner.disconnect_action,
                                owner.read_action, owner.read_all_action])
        self.menuBar().addMenu("&Window").addActions(list(owner.window_actions.values()))
        view_menu = self.menuBar().addMenu("&View")
        central = QWidget()
        self.body = QVBoxLayout(central)
        self.body.setContentsMargins(8, 8, 8, 8)
        self.connection_label = QLabel()
        self.connection_label.setObjectName("connectionState")
        self.connection_label.setWordWrap(True)
        self.body.addWidget(self.connection_label)
        self.warning = QLabel()
        self.warning.setObjectName("persistentWarning")
        self.warning.setWordWrap(True)
        self.warning.hide()
        self.body.addWidget(self.warning)
        if key == "search":
            self.body.addWidget(owner.search)
        footer = QWidget()
        hl = QHBoxLayout(footer)
        hl.setContentsMargins(4, 4, 4, 0)
        self.pending_label = QLabel()
        self.pending_label.setWordWrap(True)
        hl.addWidget(self.pending_label, 1)
        hl.addWidget(owner.help_button("apply", control=True))
        self.discard_button = QPushButton("Discard all")
        self.discard_button.clicked.connect(owner.discard)
        hl.addWidget(self.discard_button)
        self.review_button = QPushButton("Review and apply…")
        self.review_button.clicked.connect(owner.review_apply)
        hl.addWidget(self.review_button)
        self.body.addWidget(footer)
        self.setCentralWidget(central)
        self.help_dock = QDockWidget("Setting explanation", self)
        self.help_dock.setObjectName("settingHelpDock")
        self.help_dock.setMinimumWidth(300)
        self.help_browser = QTextBrowser()
        self.help_browser.setObjectName("settingExplanation")
        self.help_browser.setOpenExternalLinks(True)
        self.help_browser.setAccessibleName("Complete setting explanation")
        self.help_dock.setWidget(self.help_browser)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.help_dock)
        self.resizeDocks([self.help_dock], [340], Qt.Orientation.Horizontal)
        self.help_dock.hide()
        view_menu.addAction(self.help_dock.toggleViewAction())
        help_menu = self.menuBar().addMenu("&Help")
        guide = QAction("How Apply works", self)
        guide.setShortcut(QKeySequence("F1"))
        guide.triggered.connect(lambda: owner.show_control_help("apply", parent=self))
        help_menu.addAction(guide)
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)
        geometry = owner.settings.value("windows/" + key + "/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self.update_status()

    def set_page(self, page):
        if self.page is not None:
            self.body.removeWidget(self.page)
        self.page = page
        self.body.insertWidget(self.body.count()-1, page, 1)
        page.show()

    def update_status(self):
        owner = self.owner
        self.connection_label.setText(owner.connection_label.text())
        self.warning.setText(owner.warning.text())
        self.warning.setVisible(bool(owner.warning.text()))
        self.pending_label.setText(owner.pending_label.text())
        self.review_button.setEnabled(owner.apply_action.isEnabled())
        self.discard_button.setEnabled(owner.discard_action.isEnabled())
        self.status_label.setText(owner.status_label.text())

    def closeEvent(self, event):
        # Closing a tool window preserves its drafts in the shared session.
        self.owner.settings.setValue("windows/" + self.key + "/geometry", self.saveGeometry())
        event.accept()
