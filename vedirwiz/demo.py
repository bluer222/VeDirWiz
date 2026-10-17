"""A continuously running demo using real Text and HEX codecs."""
import asyncio
import math
import struct
import time

from ve_direct.registers import ENUM_MAPS, encode_value
from ve_direct.transport import SimulatedTransport, make_text_block

from .catalog import registers

DEFAULTS = {
    "BAT_VOLTAGE": 12, "BAT_VOLTAGE_SETTING": "12V", "BAT_TYPE": "user", "BAT_MAX_CURRENT": 30,
    "BAT_ABSORPTION_VOLTAGE": 14.4, "BAT_FLOAT_VOLTAGE": 13.8, "BAT_STORAGE_VOLTAGE": 13.2,
    "BAT_EQUALISATION_VOLTAGE": 16.2, "BAT_ABSORPTION_LIMIT": 2, "BAT_BULK_LIMIT": 10,
    "ADAPTIVE_MODE": True, "BMS_PRESENT": False, "BATTERYSAFE_MODE": False,
    "BAT_TEMPERATURE_COMPENSATION": -16, "BAT_LOW_TEMP_LEVEL": 5, "LOW_TEMP_CHARGE_CURRENT": 0,
    "TAIL_CURRENT": 1, "REBULK_VOLTAGE_OFFSET": 0.4, "AUTOMATIC_EQUALISATION_MODE": 0,
    "EQUALISATION_DURATION": 1, "EQUALISATION_CURRENT_LEVEL": 8, "AUTO_EQUALISE_STOP_ON_VOLTAGE": True,
    "LOAD_SWITCH_LOW": 11.8, "LOAD_SWITCH_HIGH": 13, "LOAD_OUTPUT_CONTROL": {"mode": 1, "lighting_timer": True},
    "PANEL_VOLTAGE_NIGHT": 5, "PANEL_VOLTAGE_DAY": 6, "SUNSET_DELAY": 5, "SUNRISE_DELAY": 5,
    "GRADUAL_DIM_SPEED": 10, "LOAD_AES_TIMER": 30, "TIME_OF_DAY": 720,
    "DEVICE_MODE": "on", "INV_DEVICE_MODE": "on", "ORION_DEVICE_MODE": "on", "DEVICE_STATE": "bulk",
    "ORION_DEVICE_STATE": "bulk", "INV_DEVICE_STATE": "inverting", "BLE_MODE": "on",
    "RELAY_OPERATION_MODE": "alarm", "RELAY_MIN_ENABLED_TIME": 5,
    "RELAY_BAT_LOW_SET": 11.8, "RELAY_BAT_LOW_CLEAR": 12.5, "RELAY_BAT_HIGH_SET": 15, "RELAY_BAT_HIGH_CLEAR": 14.5,
    "RELAY_PANEL_HIGH_SET": 90, "RELAY_PANEL_HIGH_CLEAR": 85,
    "BMV_BATTERY_CAPACITY": 200, "BMV_CHARGED_VOLTAGE": 14.2, "BMV_TAIL_CURRENT": 4,
    "BMV_CHARGED_DETECTION_TIME": 3, "BMV_CHARGE_EFFICIENCY": 95, "BMV_PEUKERT_COEFFICIENT": 1.25,
    "BMV_CURRENT_THRESHOLD": 0.1, "BMV_DISCHARGE_FLOOR": 50, "BMV_RELAY_LOW_SOC_CLEAR": 55,
    "BMV_SHUNT_AMPS": 500, "BMV_SHUNT_VOLTS": 0.05, "BMV_DESCRIPTION": "House battery",
    "BMV_TTG_DELTA_T": 3, "BMV_TEMPERATURE_COEFFICIENT": 1, "SOC": 78.4,
    "SHUTDOWN_VOLTAGE": 13.1, "START_VOLTAGE": 14, "DELAYED_START_VOLTAGE": 13.3, "START_DELAY": 120,
    "INPUT_VOLTAGE_LOCKOUT": 12.5, "INPUT_VOLTAGE_RESTART": 13.1,
    "ADJUSTABLE_VOLTAGE_MIN": 8, "ADJUSTABLE_VOLTAGE_MAX": 17, "VOLTAGE_RANGE_MIN": 8, "VOLTAGE_RANGE_MAX": 17,
    "AC_OUT_VOLTAGE_SETPOINT_MIN": 210, "AC_OUT_VOLTAGE_SETPOINT_MAX": 245,
    "AC_OUT_VOLTAGE": 230, "AC_OUT_NOM_VOLTAGE": 230, "AC_OUT_CURRENT": 1.3,
    "AC_OUT_RATED_POWER": 800, "AC_OUT_APPARENT_POWER": 300, "INV_DYN_CUTOFF_VOLTAGE": 11.3,
    "BAT_TEMPERATURE": 298.15, "CHR_INTERNAL_TEMPERATURE": 36, "CHARGER_MAXIMUM_CURRENT": 30,
    "PANEL_MAXIMUM_VOLTAGE": 100, "PANEL_VOLTAGE": 37.2, "PANEL_CURRENT": 6, "PANEL_POWER": 223.2,
    "CHARGER_VOLTAGE": 13.6, "CHARGER_CURRENT": 15.4, "DC_CHANNEL1_VOLTAGE": 13.6,
    "DC_CHANNEL1_CURRENT": 15.4, "DC_CHANNEL1_POWER": 209, "YIELD_TODAY": 1.82, "YIELD_YESTERDAY": 2.46,
    "MAXIMUM_POWER_TODAY": 286, "MAXIMUM_POWER_YESTERDAY": 301, "USER_YIELD": 328.4, "SYSTEM_YIELD": 520.1,
    "SERIAL_NUMBER": "DEMO26090001", "MODEL_NAME": "VE.Direct demonstration", "DEVICE_UPTIME": 5400,
    "SETTINGS_CHANGED_TIMESTAMP": None, "LOAD_OUTPUT_STATE": True, "LOAD_CURRENT": 0.7,
}


class DemoTransport(SimulatedTransport):
    def __init__(self, flavor):
        self.started = time.monotonic()
        self.next_frame = 0
        self.buffer = bytearray()
        replies = {}
        for reg in registers(flavor).values():
            choices = ENUM_MAPS.get(reg.enum or "", {})
            value = DEFAULTS.get(reg.name)
            if reg.name.endswith("BAT_TYPE"):
                value = "user"
            if value is None and reg.name != "SETTINGS_CHANGED_TIMESTAMP":
                value = False if reg.cls == "BOOLEAN" else next(iter(choices.values()), 0) if reg.cls == "ENUM" else [] if reg.cls == "BITMASK" else "Demo" if reg.cls == "STRING" else 0
            if reg.cls == "ENUM" and value not in choices.values():
                value = next(iter(choices.values()), 0)
            try:
                if reg.enum == "TIMER_EVENT":
                    idx = int(reg.name.rsplit("_", 1)[1])
                    value = {"offset_min": [0, 120, -60, 0, -120, 0][idx], "anchor": [1, 1, 2, 2, 3, 3][idx], "dim_pct": [100, 70, 30, 30, 70, 0][idx]}
                raw = encode_value(reg, value)
            except (ValueError, TypeError, AssertionError, struct.error):
                raw = bytes(64) if reg.cls == "STRUCT" else bytes(4)
            if reg.enum == "HISTORY_DAY":
                idx = int(reg.name.rsplit("_", 1)[1])
                data = bytearray(34)
                struct.pack_into("<IIHH", data, 1, int((1.8 + math.sin(idx * 0.7) * 0.7) * 100), 24, 1440, 1210)
                struct.pack_into("<HHH", data, 18, 100, 120, 230)
                struct.pack_into("<IHHH", data, 24, 286, 220, 3820, 180 - idx)
                raw = bytes(data)
            elif reg.enum == "CYCLE_HISTORY":
                idx = int(reg.name.rsplit("_", 1)[1])
                data = bytearray(51)
                data[0] = 1
                struct.pack_into("<IIIIIIIIIIIHHBB", data, 1, 100000 - idx * 1000, 1800, 1200, 0, 2400, 0, 220, 140, 0, 60, 0, 1220, 1380, 1, 0)
                raw = bytes(data)
            elif reg.enum == "VOLTAGE_RANGE":
                raw = bytes([8, 17])
            elif reg.name == "CAPABILITIES":
                raw = (0xFFFFFFFF).to_bytes(4, "little")
            replies[reg.id] = raw
        super().__init__(hex_replies=replies, flavor=flavor)

    async def read_chunk(self, n):
        while True:
            if self._pending_out:
                result = bytes(self._pending_out[:n])
                del self._pending_out[:n]
                return result
            if self.buffer:
                result = bytes(self.buffer[:n])
                del self.buffer[:n]
                return result
            if time.monotonic() >= self.next_frame:
                t = time.monotonic() - self.started
                voltage = 13.6 + math.sin(t / 22) * 0.08
                current = 14 + math.sin(t / 9) * 4
                fields = {"V": str(round(voltage * 1000)), "I": str(round(current * 1000)), "FW": "164", "SER#": "DEMO26090001", "ERR": "0"}
                if self.flavor in ("BMV", "BMV71"):
                    fields.update({"PID": "0xA381", "SOC": "784", "P": str(round(voltage * current)), "CE": "-43200", "TTG": "260", "T": "25", "VS": "12780", "Alarm": "OFF", "Relay": "OFF", "AR": "0"})
                elif self.flavor == "INV":
                    fields.update({"PID": "0xA201", "CS": "9", "AC_OUT_V": "23000", "AC_OUT_I": "13", "AC_OUT_S": "300", "WARN": "0", "AR": "0", "MODE": "2"})
                elif self.flavor == "ORION":
                    fields.update({"PID": "0xA3F0", "CS": "3", "DC_IN_V": "1420", "DC_IN_I": "1700", "DC_IN_P": "240", "P": "220"})
                else:
                    fields.update({"PID": "0xA056", "VPV": str(round((37.2 + math.sin(t / 15)) * 1000)), "PPV": str(round(voltage * current * 1.05)), "CS": "3", "MPPT": "2", "LOAD": "ON", "IL": "700", "H19": "32840", "H20": "182", "H21": "286", "H22": "246", "H23": "301"})
                self.buffer.extend(make_text_block(fields))
                self.next_frame = time.monotonic() + 1
            else:
                await asyncio.sleep(0.01)
