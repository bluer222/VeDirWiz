import math
import re
import struct

from . import source  # Load the selected ve.direct checkout before importing it.
from ve_direct.descriptions import HEX_DESCRIPTIONS
from ve_direct.registers import ENUM_MAPS, decode_value, encode_value, for_flavor, lookup

from .help import explanation

FAMILIES = {
    "MPPT": "Solar charger · rotary-switch models", "MPPT_SMALL": "Solar charger · 10 / 15 / 20 A models",
    "MPPT_RS": "Solar charger · MPPT RS", "ORION": "Orion XS · DC-DC charger",
    "BMV": "BMV · battery monitor", "BMV71": "BMV-712 / SmartShunt",
    "INV": "Phoenix · inverter", "CHG": "Charger · shared controls", "MULTI_RS": "Multi RS · experimental support",
}
GROUPS = [
    {"id": "charging", "label": "Charging", "description": "Battery targets, charge stages, and temperature behavior.", "icon": "charge"},
    {"id": "monitor", "label": "Battery monitor", "description": "Capacity, charge accounting, and shunt calibration.", "icon": "battery"},
    {"id": "load", "label": "Load & lighting", "description": "Load protection and a visual night-lighting schedule.", "icon": "sun"},
    {"id": "inverter", "label": "Inverter", "description": "AC output, ECO behavior, and battery cutoff.", "icon": "wave"},
    {"id": "alarms", "label": "Alarms & relays", "description": "Alarm thresholds, recovery margins, and relay behavior.", "icon": "bell"},
    {"id": "network", "label": "Connections", "description": "Bluetooth, remote inputs, and external charging control.", "icon": "network"},
    {"id": "display", "label": "Display", "description": "The device's physical display and local setup controls.", "icon": "display"},
    {"id": "history", "label": "History", "description": "Daily yield, charging cycles, and lifetime statistics.", "icon": "chart"},
    {"id": "device", "label": "Device", "description": "Identity, operating mode, capabilities, and maintenance.", "icon": "device"},
]

LABELS = {
    "BAT_ABSORPTION_VOLTAGE": "Absorption voltage", "BAT_FLOAT_VOLTAGE": "Float voltage",
    "BAT_STORAGE_VOLTAGE": "Storage voltage", "BAT_ABSORPTION_LIMIT": "Maximum absorption time",
    "BAT_BULK_LIMIT": "Maximum bulk time", "BAT_MAX_CURRENT": "Maximum charge current",
    "BAT_TEMPERATURE_COMPENSATION": "Temperature compensation", "BAT_LOW_TEMP_LEVEL": "Low-temperature threshold",
    "REBULK_VOLTAGE_OFFSET": "Re-bulk offset", "AUTOMATIC_EQUALISATION_MODE": "Automatic equalization interval",
    "BAT_TYPE": "Battery preset", "BAT_VOLTAGE": "Nominal battery voltage", "BAT_VOLTAGE_SETTING": "Battery voltage selection",
    "MPPT_RS_BAT_TYPE": "Battery preset", "MPPT_SMALL_BAT_TYPE": "Battery preset", "ORION_BAT_TYPE": "Battery preset",
    "INV_WAVE_SET50HZ_NOT60HZ": "AC frequency", "INV_DYN_CUTOFF_FACTOR": "Dynamic cutoff · overall factor",
    "INV_DYN_CUTOFF_FACTOR5": "Dynamic cutoff · factor 5", "INV_DYN_CUTOFF_FACTOR250": "Dynamic cutoff · factor 250",
    "INV_DYN_CUTOFF_FACTOR2000": "Dynamic cutoff · factor 2000", "INV_DYN_CUTOFF_ENABLE": "Dynamic battery cutoff",
    "INV_DYN_CUTOFF_VOLTAGE": "Effective cutoff voltage", "INV_LOOP_GET_IINV": "Inverter loop current",
    "INV_NVM_COMMAND": "Settings memory command", "BMV_ZERO_CURRENT": "Calibrate zero current",
    "BMV_SYNCHRONIZE": "Synchronize to 100%", "RESTORE_DEFAULTS": "Restore factory settings",
    "BMV_CLEAR_HISTORY": "Clear battery history", "CLEAR_HISTORY": "Clear charging history",
    "BMV_SHUNT_VOLTS": "Shunt voltage rating", "TIME_OF_DAY": "Device time of day",
    "BMV_PEUKERT_COEFFICIENT": "Peukert exponent", "BMV_TTG_DELTA_T": "Time remaining averaging period",
    "BMV_ALARM_MID_VOLTAGE": "Midpoint deviation alarm", "BMV_ALARM_MID_VOLTAGE_CLEAR": "Midpoint deviation recovery",
    "BMV_RELAY_MID_VOLTAGE": "Relay midpoint deviation trigger", "BMV_RELAY_MID_VOLTAGE_CLEAR": "Relay midpoint deviation recovery",
    "BMV_DESCRIPTION": "Battery name", "INV_DEVICE_MODE": "Operating mode", "ORION_DEVICE_MODE": "Operating mode",
    "INV_DEVICE_STATE": "Operating state", "ORION_DEVICE_STATE": "Operating state", "DEVICE_MODE": "Operating mode",
    "BATTERYSAFE_MODE": "BatterySafe", "ADAPTIVE_MODE": "Adaptive absorption time",
    "BMV_RELAY_LOW_SOC_CLEAR": "Relay low-charge recovery", "BMV_DISCHARGE_FLOOR": "Discharge floor / relay low-charge trigger",
}
COMMANDS = {"RESTORE_DEFAULTS", "CLEAR_HISTORY", "BMV_CLEAR_HISTORY", "BMV_ZERO_CURRENT", "BMV_SYNCHRONIZE", "BMV_ALARM_ACKNOWLEDGE", "INV_NVM_COMMAND", "REMOTE_COMMAND"}
INVALIDATE = {"RESTORE_DEFAULTS", "INV_NVM_COMMAND"}
NULL_LABELS = {"BAT_VOLTAGE": "Auto detect", "LOW_TEMP_CHARGE_CURRENT": "Use maximum", "TIME_OF_DAY": "Unavailable",
               "CHARGE_VOLTAGE_SETPOINT": "Unavailable", "BATTERY_IDLE_VOLTAGE": "Unavailable",
               "BATTERY_CHARGE_CURRENT": "Unavailable", "TOTAL_CHARGE_CURRENT": "Unavailable", "CHARGE_CURRENT_LIMIT": "Unavailable",
               "BATTERY_VOLTAGE_SENSE": "Unavailable", "BATTERY_TEMPERATURE_SENSE": "Unavailable", "SETTINGS_CHANGED_TIMESTAMP": "Unavailable"}
CUSTOM_TARGETS = {"BAT_ABSORPTION_VOLTAGE", "BAT_FLOAT_VOLTAGE", "BAT_EQUALISATION_VOLTAGE", "BAT_TEMPERATURE_COMPENSATION"}
PRESET_LABELS = {"type_1": "Gel · Victron long life", "type_2": "Gel · Victron deep discharge",
                 "type_4": "AGM · Victron deep discharge", "type_5": "Tubular plate · cyclic mode 1",
                 "type_6": "Tubular plate · cyclic mode 2", "type_7": "Tubular plate · cyclic mode 3",
                 "type_8": "LiFePO4", "agm_normal": "AGM · normal", "agm_high": "AGM · high voltage",
                 "lithium": "Lithium", "lifepo4": "LiFePO4", "user": "User defined"}
# 1=slow and 5=fast are documented; firmware 1.64 also reports 2–4.
CHOICE_LABELS = {**PRESET_LABELS, "slow": "Slow", "speed_2": "2", "speed_3": "3",
                 "speed_4": "4", "fast": "Fast"}
NETWORK_UNAVAILABLE = ("CHARGE_VOLTAGE_SETPOINT", "BATTERY_IDLE_VOLTAGE",
                       "BATTERY_CHARGE_CURRENT", "TOTAL_CHARGE_CURRENT", "CHARGE_CURRENT_LIMIT")


def human(name):
    if name.startswith("unknown("):
        return "Unrecognized device value " + name[8:-1]
    return name.replace("_", " ").capitalize().replace("bms", "BMS").replace("soc", "state of charge").replace("pwm", "PWM").replace("lifepo4", "LiFePO4").replace("aes", "AES")


def label_for(reg, flavor):
    if reg.name in LABELS:
        return LABELS[reg.name]
    if reg.name.startswith("TIMER_EVENT_"):
        return "Lighting event " + str(int(reg.name.rsplit("_", 1)[1]) + 1)
    if reg.name.startswith("DAILY_HISTORY_"):
        day = int(reg.name.rsplit("_", 1)[1])
        return "Today" if day == 0 else f"{day} days ago"
    if reg.name.startswith("CYCLE_HISTORY_"):
        return "Charge cycle " + str(int(reg.name.rsplit("_", 1)[1]) + 1)
    if reg.name.startswith("MPPT_HISTORY_"):
        return "Tracker history · day " + reg.name.rsplit("_", 1)[1]
    if reg.name.startswith("UNKNOWN_"):
        return "Undocumented measurement " + str(sorted(n for n in for_flavor(flavor) if n.startswith("UNKNOWN_")).index(reg.name) + 1)
    label = HEX_DESCRIPTIONS.get(flavor, {}).get(reg.name, human(reg.name))
    for prefix in ("BMV ", "INV ", "Orion "):
        label = label.removeprefix(prefix)
    return label.replace("(experimental)", "· experimental")


def group_for(reg):
    n = reg.name
    if n in COMMANDS or n in {"SETTINGS_CHANGED_TIMESTAMP", "BMV_DESCRIPTION"}:
        return "device"
    if any(s in n for s in ("HISTORY", "YIELD", "DEEPEST_DISCHARGE", "DEPTH_", "CUMULATIVE", "AMOUNT_", "NUMBER_", "MAXIMUM_VOLTAGE", "MINIMUM_VOLTAGE", "SECONDS_SINCE", "CYCLE_", "MAXIMUM_STARTER", "MINIMUM_STARTER", "MAXIMUM_POWER_TODAY", "MAXIMUM_POWER_YESTERDAY")):
        return "history"
    if "DISPLAY" in n or n.startswith(("BMV_SHOW_", "BMV_BACKLIGHT", "BMV_SCROLL", "BMV_SETUP_LOCK", "BMV_TEMPERATURE_UNIT")):
        return "display"
    if "RELAY" in n or "ALARM" in n or "WARNING" in n:
        return "alarms"
    if n.startswith(("LOAD_", "TIMER_EVENT_")) or n in {"MIDPOINT_SHIFT", "GRADUAL_DIM_SPEED", "PANEL_VOLTAGE_NIGHT", "PANEL_VOLTAGE_DAY", "SUNSET_DELAY", "SUNRISE_DELAY", "TIME_OF_DAY", "SOLAR_ACTIVITY"}:
        return "load"
    if n.startswith(("NETWORK_", "LINK_", "REMOTE_", "TX_", "RX_", "TWO_WIRE_")) or n in {"BLE_MODE", "CAPABILITIES_BLE", "CHARGE_ALGORITHM_VERSION", "CHARGE_VOLTAGE_SETPOINT", "BATTERY_VOLTAGE_SENSE", "BATTERY_TEMPERATURE_SENSE", "BATTERY_CHARGE_CURRENT", "TOTAL_CHARGE_CURRENT", "CHARGE_CURRENT_LIMIT", "CHARGE_CURRENT_PERCENT", "BATTERY_IDLE_VOLTAGE", "CHARGE_STATE_ELAPSED", "ABSORPTION_TIME", "TOTAL_DC_INPUT_POWER"}:
        return "network"
    if n.startswith(("INV_ECO", "INV_DYN", "INV_WAVE", "INV_LOOP", "AC_", "U_AC_")) or n in {"SHUTDOWN_LOW_VOLTAGE_SET", "VOLTAGE_RANGE_MIN", "VOLTAGE_RANGE_MAX"}:
        return "inverter"
    if n.startswith("BMV_") or n in {"SOC", "TTG", "DC_MONITOR_MODE", "DC_CHANNEL1_POWER", "DC_CHANNEL1_CURRENT", "DC_CHANNEL1_VOLTAGE", "BAT_TEMPERATURE"}:
        return "monitor"
    if n.startswith(("BAT_", "CHR_", "CHARGER_", "PANEL_", "MPPT_", "EQUALIS", "INPUT_", "OUTPUT_")) or n in {"ADAPTIVE_MODE", "BATTERYSAFE_MODE", "BMS_PRESENT", "VOLTAGE_COMPENSATION", "REBULK_VOLTAGE_OFFSET", "AUTO_EQUALISE_STOP_ON_VOLTAGE", "LOW_TEMP_CHARGE_CURRENT", "TAIL_CURRENT", "AUTOMATIC_EQUALISATION_MODE", "SHUTDOWN_VOLTAGE", "START_VOLTAGE", "DELAYED_START_VOLTAGE", "START_DELAY", "ESTIMATED_BATTERY_TEMPERATURE", "ORION_BATTERY_VOLTAGE", "ORION_BAT_TYPE", "DEVICE_FUNCTION", "ADDITIONAL_CHARGER_STATE", "ADJUSTABLE_VOLTAGE_MIN", "ADJUSTABLE_VOLTAGE_MAX"}:
        return "charging"
    return "device"


def registers(flavor):
    if flavor not in FAMILIES:
        raise ValueError("Choose a supported device family.")
    # for_flavor includes generic aliases; resolve each address before presenting it.
    resolved = {r.id: lookup(r.id, flavor=flavor) for r in for_flavor(flavor).values()}
    return {r.name: r for r in resolved.values()}


def metadata(flavor):
    result = []
    for reg in registers(flavor).values():
        label, group = label_for(reg, flavor), group_for(reg)
        unit = "°C" if reg.unit in ("K", "C") else "mV" if reg.name == "BMV_SHUNT_VOLTS" else reg.unit.replace("mV/K", "mV/°C").replace("%CAP/degC", "%/°C")
        offset, factor = (-273.15, 1) if reg.unit == "K" else (0, 1000) if reg.name == "BMV_SHUNT_VOLTS" else (0, 1)
        bounds = numeric_bounds(reg)
        item = {"name": reg.name, "label": label, "group": group, "kind": reg.cls, "access": reg.access,
                "writable": reg.access in ("READ_WRITE", "WRITE_ONLY"), "command": reg.name in COMMANDS,
                "unit": unit, "step": reg.scale * factor, "offset": offset, "factor": factor,
                "choices": [{"value": v, "label": CHOICE_LABELS.get(v, human(v))} for v in ENUM_MAPS.get(reg.enum or "", {}).values()],
                "requiresUserPreset": reg.name in CUSTOM_TARGETS,
                "nullable": NULL_LABELS.get(reg.name, "Disabled" if reg.unit == "K" and reg.access == "READ_WRITE" else None),
                "maxLength": reg.max_length - 1 if reg.max_length else None,
                "help": explanation(reg, label, group), "profile": reg.access in ("READ_WRITE", "WRITE_ONLY") and reg.name not in COMMANDS and group != "network" and reg.name != "SETTINGS_CHANGED_TIMESTAMP",
                "experimental": reg.name.startswith(("UNKNOWN_", "U_")),
                "min": bounds[0] * factor + offset if bounds else None, "max": bounds[1] * factor + offset if bounds else None}
        result.append(item)
    return sorted(result, key=lambda r: (r["command"], not r["writable"], r["label"].lower()))


def numeric_bounds(reg):
    match = re.fullmatch(r"(u?)int(8|16|24|32)_t", reg.dtype)
    if reg.cls != "NUMERIC" or not match:
        return None
    unsigned, bits = match.group(1), int(match.group(2))
    return ((0 if unsigned else -(2 ** (bits - 1))) * reg.scale,
            (2 ** (bits if unsigned else bits - 1) - 1) * reg.scale)


def validate_value(reg, value):
    if reg.cls == "NUMERIC" and value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Enter a finite number.")
        bounds = numeric_bounds(reg)
        if bounds and not bounds[0] <= value <= bounds[1]:
            raise ValueError("The number is outside this setting's supported representation.")
        if reg.unit == "%" and not 0 <= value <= 100:
            raise ValueError("Enter a percentage between 0 and 100.")
        if reg.unit == "K" and reg.access == "READ_WRITE" and not 174 <= value <= 372:
            raise ValueError("This temperature threshold must be between 174 and 372 kelvin, or Disabled.")
        if not math.isclose(value / reg.scale, round(value / reg.scale), abs_tol=1e-6, rel_tol=0):
            raise ValueError(f"Use steps of {reg.scale:g} in the device's units.")
    elif reg.cls == "BOOLEAN" and not isinstance(value, bool):
        raise ValueError("Choose on or off.")
    elif reg.cls == "STRING":
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("Enter text without NUL characters.")
    elif reg.cls == "ENUM" and value not in ENUM_MAPS.get(reg.enum or "", {}).values():
        raise ValueError("Choose a supported option.")
    elif reg.cls == "BITMASK" and (not isinstance(value, list) or any(v not in ENUM_MAPS.get(reg.enum or "", {}).values() for v in value)):
        raise ValueError("Choose supported named flags.")
    elif reg.cls == "STRUCT":
        if not isinstance(value, dict):
            raise ValueError("Use the graphical controls for this setting.")
        if reg.enum == "TIMER_EVENT":
            if not {"offset_min", "anchor", "dim_pct"} <= set(value) or not set(value) <= {"offset_min", "anchor", "dim_pct", "anchor_name"} or value["anchor"] not in (1, 2, 3) or not isinstance(value["dim_pct"], (int, float)) or not isinstance(value["offset_min"], (int, float)) or not 0 <= value["dim_pct"] <= 100 or not -1440 <= value["offset_min"] <= 1440:
                raise ValueError("Choose a sunset, midnight, or sunrise event within 24 hours and 0–100% brightness.")
            value = {k: value[k] for k in ("offset_min", "anchor", "dim_pct")}
        if reg.enum == "LOAD_CONTROL" and (not {"mode", "lighting_timer"} <= set(value) or not set(value) <= {"mode", "lighting_timer", "mode_name", "raw"} or value["mode"] not in range(8) or not isinstance(value["lighting_timer"], bool)):
            raise ValueError("Choose a load mode and lighting-timer state.")
        if reg.enum == "LOAD_CONTROL":
            value = {k: value[k] for k in ("mode", "lighting_timer")}
    elif reg.cls == "VOID" and value != "":
        raise ValueError("A command must use its named action.")
    try:
        raw = encode_value(reg, value)
    except (ValueError, TypeError, OverflowError, AssertionError, struct.error) as exc:
        raise ValueError(str(exc)) from exc
    return decode_value(reg, raw) if reg.cls != "VOID" else ""


def validate_relationships(values, changed):
    pairs = [("BAT_FLOAT_VOLTAGE", "BAT_ABSORPTION_VOLTAGE"), ("BAT_STORAGE_VOLTAGE", "BAT_FLOAT_VOLTAGE"),
             ("LOAD_SWITCH_LOW", "LOAD_SWITCH_HIGH"), ("PANEL_VOLTAGE_NIGHT", "PANEL_VOLTAGE_DAY"),
             ("INPUT_VOLTAGE_LOCKOUT", "INPUT_VOLTAGE_RESTART"), ("ALARM_LOW_VOLTAGE_SET", "ALARM_LOW_VOLTAGE_CLEAR")]
    for n in values:
        if "LOW" in n and n + "_CLEAR" in values:
            pairs.append((n, n + "_CLEAR"))
        elif "HIGH" in n and n + "_CLEAR" in values:
            pairs.append((n + "_CLEAR", n))
        elif n.endswith("_LOW_SET") and n.replace("_SET", "_CLEAR") in values:
            pairs.append((n, n.replace("_SET", "_CLEAR")))
        elif n.endswith("_HIGH_SET") and n.replace("_SET", "_CLEAR") in values:
            pairs.append((n.replace("_SET", "_CLEAR"), n))
    for low, high in pairs:
        a, b = values.get(low), values.get(high)
        if {low, high} & changed and isinstance(a, (int, float)) and isinstance(b, (int, float)) and a != 0 and b != 0 and a > b:
            raise ValueError(f"{human(low)} must be at or below {human(high)}.")
