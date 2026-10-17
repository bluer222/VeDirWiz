import asyncio
import time
import unittest

from vedirwiz.catalog import FAMILIES, NETWORK_UNAVAILABLE, metadata, registers, validate_value
from vedirwiz.controller import Controller
from ve_direct.exceptions import RegisterError


def wait_job(controller):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        job = controller.snapshot()["job"]
        if job["status"] != "running":
            return job
        time.sleep(.01)
    raise AssertionError("Device operation did not finish")


class CatalogTests(unittest.TestCase):
    def test_every_family_has_unique_controls_and_full_help(self):
        for flavor in FAMILIES:
            with self.subTest(flavor=flavor):
                rows = metadata(flavor)
                regs = registers(flavor)
                self.assertEqual(len(rows), len({r.id for r in regs.values()}))
                self.assertEqual({r["name"] for r in rows}, set(regs))
                self.assertTrue(all(len(r["help"]["what"]) > 200 for r in rows))
                self.assertTrue(all(r["help"]["source"].startswith("https://www.victronenergy.com/") for r in rows))

    def test_validation_catches_bad_types_ranges_and_structures(self):
        regs = registers("MPPT")
        for name,value in (("BAT_MAX_CURRENT",float("nan")),("BAT_MAX_CURRENT",-1),("BAT_MAX_CURRENT",1.03),("ADAPTIVE_MODE","false"),("DEVICE_MODE","ON"),("TIMER_EVENT_0",{"offset_min":0,"anchor":0,"dim_pct":50}),("TIMER_EVENT_0",{"offset_min":0,"anchor":1,"dim_pct":101})):
            with self.subTest(name=name,value=value):
                with self.assertRaises(ValueError):
                    validate_value(regs[name],value)
        timer = validate_value(regs["TIMER_EVENT_0"],{"offset_min":-20,"anchor":1,"dim_pct":70})
        self.assertEqual(validate_value(regs["TIMER_EVENT_0"],timer),timer)
        load = validate_value(regs["LOAD_OUTPUT_CONTROL"],{"mode":5,"lighting_timer":True})
        self.assertEqual(validate_value(regs["LOAD_OUTPUT_CONTROL"],load),load)
        bmv = registers("BMV")
        self.assertIsNone(validate_value(bmv["BMV_ALARM_LOW_TEMPERATURE"],None))
        with self.assertRaises(ValueError):
            validate_value(bmv["BMV_DESCRIPTION"],"Non-ASCII é")

    def test_network_sentinels_are_unavailable_and_scroll_speed_includes_three(self):
        rows = {r["name"]: r for r in metadata("MPPT")}
        regs = registers("MPPT")
        for name in NETWORK_UNAVAILABLE:
            with self.subTest(name=name):
                self.assertEqual(rows[name]["nullable"], "Unavailable")
                self.assertIsNone(validate_value(regs[name], None))
        self.assertEqual({c["value"] for c in rows["DISPLAY_SCROLL_SPEED"]["choices"]},
                         {"slow", "speed_2", "speed_3", "speed_4", "fast"})
        self.assertEqual(next(c["label"] for c in rows["DISPLAY_SCROLL_SPEED"]["choices"] if c["value"]=="speed_3"), "3")
        self.assertEqual(validate_value(regs["DISPLAY_SCROLL_SPEED"], "speed_3"), "speed_3")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(timeout=.3)
        self.controller.call(self.controller.connect(demo=True))
        self.assertEqual(wait_job(self.controller)["status"],"complete")

    def tearDown(self):
        self.controller.close()

    def payload(self, changes):
        state = self.controller.snapshot()
        return {"generation":state["generation"],"changes":changes,"expected":{n:state["values"][n]["value"] for n in changes if n in state["values"]}}

    def apply(self, changes):
        self.controller.call(self.controller.start_apply(self.payload(changes)))
        return wait_job(self.controller)

    def test_apply_verifies_values_and_structured_curve_events(self):
        changes = {"BAT_MAX_CURRENT":20,"BAT_ABSORPTION_VOLTAGE":14.5,"TIMER_EVENT_0":{"offset_min":30,"anchor":1,"dim_pct":60},"LOAD_OUTPUT_CONTROL":{"mode":5,"lighting_timer":True}}
        job = self.apply(changes)
        self.assertEqual(job["status"],"complete",job["error"])
        self.assertTrue(all(r["status"]=="verified" for r in job["results"]))
        state = self.controller.snapshot()
        self.assertEqual(state["values"]["BAT_MAX_CURRENT"]["value"],20)
        self.assertEqual(state["values"]["TIMER_EVENT_0"]["value"]["offset_min"],30)

    def test_external_change_aborts_entire_batch(self):
        payload = self.payload({"BAT_MAX_CURRENT":20,"BAT_FLOAT_VOLTAGE":13.7})
        self.controller.call(self.controller.dev.hex_set("BAT_MAX_CURRENT",22))
        self.controller.call(self.controller.start_apply(payload))
        job = wait_job(self.controller)
        self.assertEqual(job["status"],"failed")
        self.assertEqual(job["results"],[])
        self.assertIn("another controller",job["error"])
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_FLOAT_VOLTAGE")),13.8)

    def test_partial_failure_preserves_accepted_result_and_stops(self):
        original = self.controller.dev.hex_set
        async def rejecting_set(name,value,**kwargs):
            if name=="BAT_FLOAT_VOLTAGE":
                raise RegisterError("Simulated parameter rejection")
            return await original(name,value,**kwargs)
        self.controller.dev.hex_set = rejecting_set
        job = self.apply({"BAT_MAX_CURRENT":20,"BAT_FLOAT_VOLTAGE":13.7,"BAT_ABSORPTION_LIMIT":3})
        self.assertEqual(job["status"],"failed")
        self.assertEqual([(r["name"],r["status"]) for r in job["results"]],[("BAT_MAX_CURRENT","verified"),("BAT_FLOAT_VOLTAGE","failed")])
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_MAX_CURRENT")),20)
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_ABSORPTION_LIMIT")),2)

    def test_stale_generation_and_invalid_relationships_are_rejected(self):
        payload=self.payload({"BAT_FLOAT_VOLTAGE":15})
        with self.assertRaises(ValueError):
            self.controller.call(self.controller.start_apply(payload))
        payload=self.payload({"BAT_MAX_CURRENT":20})
        payload["generation"]="previous-device"
        with self.assertRaises(ValueError):
            self.controller.call(self.controller.start_apply(payload))
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_MAX_CURRENT")),30)

    def test_custom_targets_require_user_preset_and_check_external_preset_change(self):
        payload=self.payload({"BAT_ABSORPTION_VOLTAGE":14.5})
        self.controller.call(self.controller.dev.hex_set("BAT_TYPE","type_2"))
        self.controller.call(self.controller.start_apply(payload))
        job=wait_job(self.controller)
        self.assertEqual(job["status"],"failed")
        self.assertEqual(job["results"],[])
        self.assertIn("User defined",job["error"])
        self.assertEqual(self.controller.call(self.controller.dev.hex_get("BAT_ABSORPTION_VOLTAGE")),14.4)

    def test_maintenance_requires_acknowledgement_and_runs_alone(self):
        state=self.controller.snapshot()
        payload={"generation":state["generation"],"changes":{"RESTORE_DEFAULTS":""},"expected":{}}
        with self.assertRaises(ValueError):
            self.controller.call(self.controller.start_apply(payload))
        payload["acknowledged"]=True
        self.controller.call(self.controller.start_apply(payload))
        self.assertEqual(wait_job(self.controller)["status"],"complete")
        self.assertEqual(self.controller.snapshot()["values"],{})

    def test_profile_roundtrip_has_no_commands_or_external_measurements(self):
        profile=self.controller.export_profile()
        imported=self.controller.import_profile({"profile":profile})
        self.assertEqual(imported["settings"],profile["settings"])
        self.assertNotIn("RESTORE_DEFAULTS",profile["settings"])
        self.assertNotIn("BATTERY_VOLTAGE_SENSE",profile["settings"])
        profile["flavor"]="INV"
        with self.assertRaises(ValueError):
            self.controller.import_profile({"profile":profile})

    def test_phoenix_write_only_targets_are_accepted_and_saved(self):
        self.controller.call(self.controller.connect(flavor="INV",demo=True))
        wait_job(self.controller)
        job=self.apply({"AC_OUT_VOLTAGE_SETPOINT":230,"INV_ECO_MODE_RETRY_TIME":2})
        self.assertEqual(job["status"],"complete",job["error"])
        self.assertTrue(job["saved"])
        self.assertTrue(all(r["status"]=="accepted" for r in job["results"]))

    def test_all_demo_families_read_through_actual_protocol(self):
        for flavor in FAMILIES:
            with self.subTest(flavor=flavor):
                self.controller.call(self.controller.connect(flavor=flavor,demo=True))
                job=wait_job(self.controller)
                self.assertEqual(job["status"],"complete",job["error"])
                self.assertGreater(len(self.controller.snapshot()["values"]),20)

    def test_demo_mppt_reports_standalone_network_inputs_as_none(self):
        state=self.controller.snapshot()
        for name in NETWORK_UNAVAILABLE:
            with self.subTest(name=name):
                record=state["values"][name]
                self.assertEqual(record["status"],"available")
                self.assertIsNone(record["value"])
        self.assertEqual(state["values"]["DISPLAY_SCROLL_SPEED"]["value"],"speed_3")
        self.assertIsNone(state["values"]["BATTERY_VOLTAGE_SENSE"]["value"])
        self.assertNotIn("BATTERY_VOLTAGE_SENSE",self.controller.export_profile()["settings"])


if __name__=="__main__":
    unittest.main()
