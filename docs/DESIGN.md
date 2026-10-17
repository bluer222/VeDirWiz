# Desktop design

VeDirWiz uses a monitoring `QMainWindow` and independent configuration
`QMainWindow` instances. The main window always displays live instruments,
the time plot, measurement table, and connection state. Configuration windows
can sit beside it, move to another monitor, or close while monitoring continues.

This follows the desktop window pattern in the sibling SevCANWiz application
as inspected on October 9, 2026. VeDirWiz shares the design approach, with its
own VE.Direct controls and session logic. It has no runtime dependency on
SevCANWiz and does not track that project's ongoing changes.

The design was checked against the Qt company's
[Qt UI Design skill](https://github.com/theqtcompanyrnd/agent-skills/blob/main/skills/qt-ui-design/SKILL.md).
The defaults are a rectangular Linux desktop window, mouse and keyboard
input, English labels, scalable system fonts, and Fusion controls.
The help and activity panels can be resized, detached, or hidden through View.
Qt's [dock-widget example](https://doc.qt.io/qtforpython-6/examples/example_widgets_mainwindows_dockwidgets.html)
provided the desktop window pattern.

The top window toolbar has a labeled icon for every configuration section.
Each icon opens or raises one reusable window. The Window menu lists the same
sections, so they are available by keyboard as well as mouse. There is no
left navigation list. Related controls share named tabs inside their section's
window. Ctrl+F opens a separate search window that searches labels and help.
Read and connection commands remain on the command toolbar. Each configuration
window includes Read, Apply, Discard, session status, and its own explanation
dock. The monitoring window's help and activity docks start hidden.

All windows share one connection and one set of pending changes. Closing a
configuration window hides it without losing drafts. Apply always reviews
the entire set, including changes in closed windows. Reconnecting clears
drafts and rebuilds open windows for the selected device family. F5 reads
the active window's settings; in monitoring it reads current measurements
and device information. Automatic measurement reads cover open configuration
windows without repeatedly downloading history. Main and configuration-window
geometry persist independently. Connection and fault status stay visible,
with text as well as color.

Microsoft's [desktop toolbar guidance](https://learn.microsoft.com/en-us/windows/win32/uxguide/cmd-toolbars)
informed the short labels, fixed command positions, and menu equivalents.
The [Qt main-window framework](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QMainWindow.html)
provides the native menus, toolbars, status bars, and movable docks.

Neutral backgrounds, distinct control boundaries, and restrained status
colors follow the approach described in Siemens'
[HMI Template Suite](https://www.siemens.com/th-th/products/simatic-hmi/hmi-template-suite/).
This is desktop configuration software, not a certified machine safety HMI.

Curve controls have exact numeric equivalents and keyboard operation.
Measurements keep a number, unit, and timestamp alongside plots. Unread or
unavailable data stays empty. A graph's scale does not imply a device rating.

Question-mark help opens beside the setting, leaving the control visible.
The authored help explains what the setting changes, how to choose it,
which other settings matter, and when Apply sends it. Technical references
are links in the help dock. Ordinary flows use names and units rather than
register addresses.
