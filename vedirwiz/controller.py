import asyncio
import copy
import math
import threading
import time
import uuid
from contextlib import suppress
from datetime import datetime, timezone

from . import source
from ve_direct import AsyncVEDirect
from ve_direct.exceptions import RegisterError, TimeoutError, TransportError
from ve_direct.registers import encode_value
from ve_direct.tables import LABEL_INFO

from .catalog import COMMANDS, CUSTOM_TARGETS, FAMILIES, group_for, metadata, registers, validate_relationships, validate_value
from .demo import DemoTransport


def utc():
    return datetime.now(timezone.utc).isoformat()


class Controller:
    """One asyncio owner for the device, with copied snapshots for HTTP threads."""

    def __init__(self, timeout=2):
        self.timeout = timeout
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.dev = None
        self.readers = []
        self.task = None
        self.state = {"connection": "disconnected", "flavor": "MPPT", "demo": False, "port": "", "generation": "",
                      "values": {}, "telemetry": {}, "samples": [], "events": [], "job": None, "error": "", "frames": 0,
                      "lastFrame": None, "catalogRevision": 0}
        self.thread = threading.Thread(target=self._run, daemon=True, name="VE.Direct")
        self.thread.start()
        self.ready.wait(5)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    def call(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result(timeout=15)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def event(self, message, level="info"):
        with self.lock:
            self.state["events"].append({"time": utc(), "message": message, "level": level})
            self.state["events"] = self.state["events"][-200:]

    def cache(self, name, value=None, status="available", error="", verification="read"):
        with self.lock:
            self.state["values"][name] = {"value": value, "status": status, "error": error, "time": utc(), "verification": verification}

    def busy(self):
        return self.task is not None and not self.task.done()

    async def connect(self, flavor="MPPT", demo=False, port="", baudrate=19200):
        if flavor not in FAMILIES or not isinstance(demo, bool):
            raise ValueError("Choose a supported family and connection mode.")
        if not isinstance(port, str) or len(port) > 512 or not isinstance(baudrate, int) or baudrate not in (19200, 9600, 38400, 57600, 115200):
            raise ValueError("Choose a serial port and supported baud rate.")
        if not demo and not port.strip():
            raise ValueError("Choose a serial port, or select Demo.")
        if self.busy():
            raise ValueError("Wait for the current operation before changing connections.")
        await self.disconnect()
        with self.lock:
            self.state.update(connection="connecting", flavor=flavor, demo=demo, port="Demo" if demo else port,
                              generation=uuid.uuid4().hex, values={}, telemetry={}, samples=[], frames=0, lastFrame=None, error="", job=None)
            self.state["catalogRevision"] += 1
        try:
            if demo:
                transport = DemoTransport(flavor)
                self.dev = AsyncVEDirect(transport.read_chunk, transport.write, flavor=flavor)
            else:
                self.dev = await AsyncVEDirect.from_port(port, baudrate=baudrate, flavor=flavor)
            with self.lock:
                self.state["connection"] = "connected"
            self.readers = [asyncio.create_task(self._frames()), asyncio.create_task(self._notifications())]
            self.event(f"Connected to {'demo' if demo else port} · {FAMILIES[flavor]}")
            names = [r.name for r in registers(flavor).values() if r.access != "WRITE_ONLY" and group_for(r) != "history" and r.cls != "VOID"]
            return await self.start_read(names)
        except Exception as exc:
            with self.lock:
                self.state.update(connection="fault", error=str(exc))
            self.event(str(exc), "error")
            raise ValueError(str(exc)) from exc

    async def disconnect(self):
        if self.busy():
            raise ValueError("Wait for the current operation before disconnecting.")
        for reader in self.readers:
            reader.cancel()
        for reader in self.readers:
            with suppress(asyncio.CancelledError, Exception):
                await reader
        self.readers = []
        if self.dev:
            await self.dev.close()
            self.dev = None
        with self.lock:
            self.state["connection"] = "disconnected"

    async def _frames(self):
        try:
            async for frame in self.dev.frames():
                values = frame.to_si()
                now = utc()
                with self.lock:
                    for name, field in frame.fields.items():
                        unit = field.si_unit or ""
                        self.state["telemetry"][name] = {"value": values.get(name), "human": field.human or "", "label": LABEL_INFO.get(name, {}).get("desc", name), "unit": unit, "time": now}
                    self.state["frames"] += 1
                    self.state["lastFrame"] = time.time()
                    sample = {"time": time.time(), **{n: v["value"] for n, v in self.state["telemetry"].items() if isinstance(v["value"], (int, float))}}
                    self.state["samples"].append(sample)
                    self.state["samples"] = self.state["samples"][-900:]
        except Exception as exc:
            self._fault(exc)

    async def _notifications(self):
        try:
            async for update in self.dev.async_notifications():
                if update.get("name"):
                    if update.get("flags"):
                        self.event(f"Device notification rejected: {update['name']}", "error")
                    else:
                        self.cache(update["name"], update["value"], verification="notification")
        except Exception as exc:
            self._fault(exc)

    def _fault(self, exc):
        with self.lock:
            if self.state["connection"] != "fault":
                self.event(f"Connection needs recovery: {exc}", "error")
            self.state.update(connection="fault", error=f"{exc}. Disconnect, reset the link, and reconnect before more commands.")

    def _ensure_connected(self):
        if self.state["connection"] != "connected" or not self.dev:
            raise ValueError("Connect a device before reading or applying settings.")
        if self.busy():
            raise ValueError("Wait for the current read or Apply to finish.")

    def _new_job(self, kind, total):
        job = {"id": uuid.uuid4().hex, "kind": kind, "status": "running", "phase": "Starting", "total": total,
               "completed": 0, "results": [], "error": "", "saved": None}
        with self.lock:
            self.state["job"] = job
        return job

    async def start_read(self, names):
        self._ensure_connected()
        regs = registers(self.state["flavor"])
        if not isinstance(names, list) or not 1 <= len(names) <= 300 or any(not isinstance(n, str) or n not in regs or regs[n].access == "WRITE_ONLY" for n in names):
            raise ValueError("Choose readable values from this device's catalog.")
        job = self._new_job("read", len(set(names)))
        self.task = asyncio.create_task(self._read_job(job, list(dict.fromkeys(names))))
        return {"jobId": job["id"]}

    async def _get(self, name):
        try:
            value = await self.dev.hex_get(name, timeout=self.timeout)
            reg = registers(self.state["flavor"])[name]
            if reg.access == "READ_WRITE" and reg.cls == "STRUCT" and (not isinstance(value, dict) or set(value) == {"raw"}):
                raise RegisterError("The device returned an incomplete structured setting.")
            self.cache(name, value)
            return value
        except RegisterError as exc:
            self.cache(name, status="unsupported", error=str(exc))
            raise
        except (TimeoutError, TransportError, OSError) as exc:
            self.cache(name, status="error", error=str(exc))
            self._fault(exc)
            raise

    async def _read_job(self, job, names):
        try:
            for name in names:
                with self.lock:
                    job["phase"] = f"Reading {name}"
                try:
                    await self._get(name)
                    result = {"name": name, "status": "read"}
                except RegisterError as exc:
                    result = {"name": name, "status": "unsupported", "error": str(exc)}
                with self.lock:
                    job["results"].append(result)
                    job["completed"] += 1
            with self.lock:
                job["status"] = "complete"
            self.event(f"Read finished: {job['completed']} values, {sum(r['status'] == 'unsupported' for r in job['results'])} unavailable on this device.")
        except Exception as exc:
            with self.lock:
                job.update(status="failed", error=str(exc))
            self.event(f"Read stopped: {exc}", "error")

    def prepare_changes(self, payload):
        if payload.get("generation") != self.state["generation"]:
            raise ValueError("The connection changed. Read the current device and review the edits again.")
        changes, expected = payload.get("changes"), payload.get("expected", {})
        if not isinstance(changes, dict) or not changes or len(changes) > 150 or not isinstance(expected, dict):
            raise ValueError("Provide a nonempty settings change list.")
        regs = registers(self.state["flavor"])
        prepared = {}
        for name, value in changes.items():
            reg = regs.get(name)
            if not reg or reg.access not in ("READ_WRITE", "WRITE_ONLY"):
                raise ValueError("This change is not available for the current device family.")
            try:
                prepared[name] = validate_value(reg, value)
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"{name}: {exc}") from exc
            if reg.access == "READ_WRITE" and name not in COMMANDS:
                current = self.state["values"].get(name, {})
                if current.get("status") != "available" or name not in expected:
                    raise ValueError(f"Read {name} successfully before editing it.")
                if encode_value(reg, current["value"]) != encode_value(reg, expected[name]):
                    raise ValueError(f"{name} changed since editing began. Discard that edit and read again.")
        if COMMANDS & prepared.keys():
            if len(prepared) != 1 or payload.get("acknowledged") is not True:
                raise ValueError("Run one maintenance command at a time and acknowledge its effects in the review.")
        if any(n.endswith("BAT_TYPE") for n in prepared) and len(prepared) != 1:
            raise ValueError("Apply the battery preset separately, then read its resulting targets before editing the curve.")
        current_values = {n: v["value"] for n, v in self.state["values"].items() if v["status"] == "available"}
        self._validate_custom_targets(prepared, current_values)
        validate_relationships({**current_values, **prepared}, set(prepared))
        self._validate_device_limits(prepared)
        return prepared, expected

    def _validate_custom_targets(self, prepared, values):
        if CUSTOM_TARGETS & prepared.keys():
            presets = [n for n in registers(self.state["flavor"]) if n.endswith("BAT_TYPE")]
            if presets and any(values.get(n) != "user" for n in presets):
                raise ValueError("Select the User defined battery preset and apply it separately, then read the resulting targets before editing the curve.")

    def _validate_device_limits(self, prepared):
        values = self.state["values"]
        def limit(name):
            v = values.get(name, {})
            return v.get("value") if v.get("status") == "available" else None
        maximum_current = limit("CHARGER_MAXIMUM_CURRENT")
        if "BAT_MAX_CURRENT" in prepared and isinstance(maximum_current, (int, float)) and maximum_current > 0 and prepared["BAT_MAX_CURRENT"] > maximum_current:
            raise ValueError(f"Charge current exceeds this device's reported {maximum_current:g} A maximum.")
        for name, value in prepared.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if name == "AC_OUT_VOLTAGE_SETPOINT":
                low, high = limit("AC_OUT_VOLTAGE_SETPOINT_MIN"), limit("AC_OUT_VOLTAGE_SETPOINT_MAX")
            elif name in {"BAT_ABSORPTION_VOLTAGE", "BAT_FLOAT_VOLTAGE", "BAT_STORAGE_VOLTAGE", "BAT_EQUALISATION_VOLTAGE", "SHUTDOWN_LOW_VOLTAGE_SET", "ALARM_LOW_VOLTAGE_SET", "ALARM_LOW_VOLTAGE_CLEAR"}:
                low, high = limit("ADJUSTABLE_VOLTAGE_MIN") or limit("VOLTAGE_RANGE_MIN"), limit("ADJUSTABLE_VOLTAGE_MAX") or limit("VOLTAGE_RANGE_MAX")
            else:
                continue
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low and not low <= value <= high:
                raise ValueError(f"{name} must be inside this device's {low:g}–{high:g} V range.")

    async def start_apply(self, payload):
        self._ensure_connected()
        prepared, expected = self.prepare_changes(payload)
        job = self._new_job("apply", len(prepared))
        self.task = asyncio.create_task(self._apply_job(job, prepared, expected))
        return {"jobId": job["id"]}

    async def _apply_job(self, job, prepared, expected):
        regs = registers(self.state["flavor"])
        failed_name = None
        try:
            with self.lock:
                job["phase"] = "Checking for external changes"
            # Check the whole batch before the first write, including companion settings.
            current = {}
            relevant = set(prepared) | {n for n in regs if any(s in n for s in ("_VOLTAGE", "LOAD_SWITCH", "RELAY_", "_ALARM_")) and regs[n].access == "READ_WRITE" and self.state["values"].get(n, {}).get("status") == "available"}
            if CUSTOM_TARGETS & prepared.keys():
                relevant.update(n for n in regs if n.endswith("BAT_TYPE"))
            for name in relevant:
                if regs[name].access == "READ_WRITE" and name not in COMMANDS:
                    current[name] = await self._get(name)
                    if name in expected and encode_value(regs[name], current[name]) != encode_value(regs[name], expected[name]):
                        raise ValueError(f"{name} changed in another controller. No settings were written. Read and review again.")
            validate_relationships({**current, **prepared}, set(prepared))
            self._validate_custom_targets(prepared, current)
            for name, value in prepared.items():
                failed_name = name
                with self.lock:
                    job["phase"] = f"Applying {name}"
                await self.dev.hex_set(name, value, timeout=self.timeout)
                with self.lock:
                    result = {"name": name, "status": "accepted", "value": value}
                    job["results"].append(result)
                if name not in COMMANDS and regs[name].access != "WRITE_ONLY":
                    actual = await self._get(name)
                    if encode_value(regs[name], actual) != encode_value(regs[name], value):
                        with self.lock:
                            result.update(status="mismatch", actual=actual)
                        raise ValueError(f"{name}: the device readback differs from the requested value.")
                    with self.lock:
                        result["status"] = "verified"
                elif name not in COMMANDS:
                    self.cache(name, value, verification="accepted, write-only")
                with self.lock:
                    job["completed"] += 1
                self.event(f"Applied {name}: {result['status']}")
            failed_name = None
            if self.state["flavor"] == "INV" and not (COMMANDS & prepared.keys()):
                with self.lock:
                    job["phase"] = "Saving Phoenix settings to nonvolatile memory"
                await self.dev.hex_set("INV_NVM_COMMAND", "save", timeout=self.timeout)
                with self.lock:
                    job["saved"] = True
            if any(n.endswith("BAT_TYPE") for n in prepared) or COMMANDS & prepared.keys():
                # Presets and maintenance can change many other cached values.
                with self.lock:
                    self.state["values"] = {}
                    self.state["catalogRevision"] += 1
                self.event("Read the device again: a preset or maintenance command invalidated cached settings.")
            with self.lock:
                job["status"] = "complete"
        except Exception as exc:
            if isinstance(exc, (TimeoutError, TransportError, OSError)):
                self._fault(exc)
            with self.lock:
                if failed_name and not any(r["name"] == failed_name for r in job["results"]):
                    job["results"].append({"name": failed_name, "status": "failed", "error": str(exc)})
                if self.state["flavor"] == "INV" and job["results"] and job["saved"] is not True:
                    job["saved"] = False
                job.update(status="failed", error=str(exc))
            self.event(f"Apply stopped: {exc}. Earlier accepted writes may already affect the device.", "error")

    def export_profile(self):
        state = self.snapshot()
        settings = {r["name"]: state["values"][r["name"]]["value"] for r in metadata(state["flavor"]) if r["profile"] and state["values"].get(r["name"], {}).get("status") == "available"}
        return {"format": "vedirwiz-profile", "version": 1, "flavor": state["flavor"], "exportedAt": utc(), "demo": state["demo"], "settings": settings}

    def import_profile(self, payload):
        profile = payload.get("profile", {})
        if not isinstance(profile, dict) or profile.get("format") != "vedirwiz-profile" or profile.get("version") != 1 or profile.get("flavor") != self.state["flavor"]:
            raise ValueError("Use a VeDirWiz profile for the selected device family.")
        settings = profile.get("settings")
        allowed = {r["name"] for r in metadata(self.state["flavor"]) if r["profile"]}
        regs = registers(self.state["flavor"])
        if not isinstance(settings, dict) or not settings or len(settings) > 150 or any(n not in allowed for n in settings):
            raise ValueError("This profile contains unsupported settings or maintenance commands.")
        for n, value in settings.items():
            validate_value(regs[n], value)
        return {"settings": settings}

    async def shutdown(self):
        if self.busy():
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self.disconnect()

    def close(self):
        self.call(self.shutdown())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
