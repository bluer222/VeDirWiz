import importlib.util
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM","offscreen")


@unittest.skipUnless(importlib.util.find_spec("PySide6"),"PySide6 is not installed")
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        cls.preferences=tempfile.TemporaryDirectory()
        cls.previous_format=QSettings.defaultFormat()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,cls.preferences.name)
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    @classmethod
    def tearDownClass(cls):
        from PySide6.QtCore import QSettings
        QSettings.setDefaultFormat(cls.previous_format)
        cls.preferences.cleanup()

    def setUp(self):
        from vedirwiz.controller import Controller
        from vedirwiz.desktop import MainWindow
        self.controller=Controller(.3)
        self.window=MainWindow(self.controller)
        self.window.resize(1460,950)
        self.window.show()
        self.controller.call(self.controller.connect(demo=True))
        self.pump(lambda:self.controller.snapshot()["job"]["status"]!="running")

    def tearDown(self):
        self.window.timer.stop()
        self.window.close_configuration_windows()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.controller.close()

    def pump(self,condition):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            self.app.processEvents()
            self.window.tick()
            if condition():
                self.window.tick()
                self.app.processEvents()
                return
            time.sleep(.01)
        self.fail("UI condition did not complete")

    def test_edit_stays_pending_and_help_is_complete(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        charging=self.window.navigate("charging")
        self.app.processEvents()
        row=next(r for r in self.window.rows if r.name=="BAT_ABSORPTION_VOLTAGE")
        row.numeric.setFocus()
        row.numeric.selectAll()
        QTest.keyClicks(row.numeric,"14.5")
        QTest.keyClick(row.numeric,Qt.Key.Key_Tab)
        self.assertEqual(self.window.pending["BAT_ABSORPTION_VOLTAGE"]["value"],14.5)
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_ABSORPTION_VOLTAGE")),14.4)
        self.window.show_help("BAT_ABSORPTION_VOLTAGE",parent=charging)
        self.assertIn("battery manufacturer's",charging.help_browser.toPlainText())
        self.assertTrue(charging.review_button.isEnabled())
        self.window.discard()
        self.assertFalse(self.window.pending)

    def test_curve_has_keyboard_controls_and_undo(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.navigate("charging")
        self.app.processEvents()
        curve=next(p for p in self.window.plots if p.kind=="charge")
        curve.repaint()
        curve.setFocus()
        QTest.keyClick(curve,Qt.Key.Key_Up)
        self.assertAlmostEqual(self.window.pending["BAT_ABSORPTION_VOLTAGE"]["value"],14.41)
        self.window.undo()
        self.assertNotIn("BAT_ABSORPTION_VOLTAGE",self.window.pending)
        self.window.redo()
        self.assertIn("BAT_ABSORPTION_VOLTAGE",self.window.pending)

    def test_native_windows_search_and_lighting_editor(self):
        self.assertEqual(len(self.window.window_actions),10)
        self.assertFalse(hasattr(self.window,"nav"))
        overview=self.window.pages["overview"]
        self.window.search.setText("absorption")
        self.window.run_search()
        self.assertIs(self.window.windows["search"].page,self.window.search_page)
        self.assertIs(self.window.pages["overview"],overview)
        self.assertTrue(overview.isVisible())
        self.assertTrue(any(self.window.search_page.isAncestorOf(r) and r.name=="BAT_ABSORPTION_VOLTAGE" for r in self.window.rows))
        self.window.navigate("load")
        self.app.processEvents()
        self.assertTrue(self.window.stage("TIMER_EVENT_0",{"anchor":1,"offset_min":30,"dim_pct":65}))
        self.assertEqual(self.window.pending["TIMER_EVENT_0"]["value"]["dim_pct"],65)
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("TIMER_EVENT_0"))["dim_pct"],100)

    def test_mouse_drag_stages_curve_target_without_writing(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        self.window.navigate("charging")
        self.app.processEvents()
        curve=next(p for p in self.window.plots if p.kind=="charge")
        curve.repaint()
        point=curve.point(1,14.4).toPoint()
        QTest.mousePress(curve,Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseMove(curve,point+QPoint(0,-12))
        QTest.mouseRelease(curve,Qt.MouseButton.LeftButton,pos=point+QPoint(0,-12))
        self.assertGreater(self.window.pending["BAT_ABSORPTION_VOLTAGE"]["value"],14.4)
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_ABSORPTION_VOLTAGE")),14.4)

    def test_invalid_numeric_input_blocks_apply_and_is_not_lost(self):
        self.window.navigate("charging")
        row=next(r for r in self.window.rows if r.name=="BAT_ABSORPTION_VOLTAGE")
        row.numeric.setText("not a number")
        row.numeric.setModified(True)
        row.commit_number()
        self.assertIn(row.name,self.window.invalid)
        self.assertFalse(self.window.windows["charging"].review_button.isEnabled())
        self.window.tick()
        self.assertEqual(row.numeric.text(),"not a number")

    def test_toolbar_opens_reusable_windows_and_close_keeps_drafts(self):
        from PySide6.QtWidgets import QMainWindow
        self.window.window_actions["charging"].trigger()
        self.app.processEvents()
        charging=self.window.windows["charging"]
        self.assertIsInstance(charging,QMainWindow)
        self.assertTrue(charging.isWindow())
        self.assertIs(charging.parentWidget(),self.window)
        self.assertFalse(self.window.window_actions["charging"].icon().isNull())
        self.assertTrue(self.window.stage("BAT_ABSORPTION_VOLTAGE",14.5))
        charging.close()
        self.assertFalse(charging.isVisible())
        self.assertIn("BAT_ABSORPTION_VOLTAGE",self.window.pending)
        self.assertIs(self.window.navigate("charging"),charging)
        load=self.window.navigate("load")
        self.app.processEvents()
        self.assertTrue(charging.isVisible())
        self.assertTrue(load.isVisible())
        self.assertTrue(self.window.pages["overview"].isVisible())
        self.assertTrue(load.review_button.isEnabled())
        self.assertIn("across all windows",load.pending_label.text())

    def test_question_mark_opens_help_in_its_own_window(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QToolButton
        charging=self.window.navigate("charging")
        self.app.processEvents()
        row=next(r for r in self.window.rows if r.name=="BAT_ABSORPTION_VOLTAGE")
        question=row.findChild(QToolButton,"question")
        QTest.mouseClick(question,Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(charging.help_dock.isVisible())
        self.assertIn("Absorption voltage",charging.help_browser.toPlainText())
        self.assertFalse(self.window.help_dock.isVisible())

    def test_apply_reviews_and_verifies_edits_from_multiple_windows(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QTableWidget
        charging=self.window.navigate("charging")
        self.assertTrue(self.window.stage("BAT_ABSORPTION_VOLTAGE",14.5))
        load=self.window.navigate("load")
        self.assertTrue(self.window.stage("TIMER_EVENT_0",{"anchor":1,"offset_min":30,"dim_pct":65}))
        self.app.processEvents()
        reviewed=[]
        def accept_review():
            dialog=self.app.activeModalWidget()
            self.assertIsInstance(dialog,QDialog)
            self.assertIs(dialog.parentWidget(),load)
            reviewed.append(dialog.findChild(QTableWidget).rowCount())
            dialog.accept()
        QTimer.singleShot(0,accept_review)
        load.review_button.click()
        self.pump(lambda:not self.window.pending and self.window.active_apply is None and self.window.future is None)
        self.assertEqual(reviewed,[2])
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_ABSORPTION_VOLTAGE")),14.5)
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("TIMER_EVENT_0"))["dim_pct"],65)
        self.assertFalse(charging.review_button.isEnabled())
        self.window.result_message.close()

    def test_open_windows_rebuild_after_family_change(self):
        charging=self.window.navigate("charging")
        old_page=charging.page
        self.window.search.setText("voltage")
        self.window.run_search()
        self.controller.call(self.controller.connect(flavor="BMV71",demo=True))
        self.pump(lambda:self.controller.snapshot()["job"]["status"]!="running")
        self.assertIs(self.window.windows["charging"],charging)
        self.assertIsNot(charging.page,old_page)
        self.assertIs(charging.page,self.window.pages["charging"])
        self.assertTrue(charging.isVisible())
        self.assertTrue(self.window.pages["overview"].isVisible())
        self.assertIn("BMV_BATTERY_CAPACITY",self.window.by_name)
        self.window.navigate("monitor")
        self.app.processEvents()
        self.assertTrue(any(r.name=="BMV_BATTERY_CAPACITY" and self.window.windows["monitor"].page.isAncestorOf(r) for r in self.window.rows))

    def test_f5_reads_the_active_window_with_search_open(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.window.focus_search()
        self.window.search.setText("absorption")
        self.window.run_search()
        history=self.window.navigate("history")
        self.app.processEvents()
        self.assertEqual(self.window.operation_window(),history)
        self.assertNotIn("DAILY_HISTORY_0",self.window.state["values"])
        QTest.keyClick(history,Qt.Key.Key_F5)
        self.pump(lambda:self.window.future is None and "DAILY_HISTORY_0" in self.window.state["values"] and self.window.state["job"]["status"]!="running")
        self.assertEqual(self.window.state["values"]["DAILY_HISTORY_0"]["status"],"available")
        self.assertTrue(self.window.windows["search"].isVisible())


if __name__=="__main__":
    unittest.main()
