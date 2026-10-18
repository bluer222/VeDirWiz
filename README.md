> This project is primarily AI-generated code.

# VeDirWiz

A Qt desktop app for monitoring and configuring Victron VE.Direct devices,
built on [ve.direct](https://github.com/bluer222/ve.direct). It includes live
telemetry, settings editors, profiles, and a demo that needs no hardware.

## Quick start

Requires Python 3.10 or newer and Git. Installation fetches the latest
`ve.direct` library from its `main` branch.

```sh
git clone https://github.com/bluer222/VeDirWiz.git
cd VeDirWiz
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[serial]'
vedirwiz --demo
```

Connect to a device with `vedirwiz --port /dev/ttyUSB0 --family MPPT`, or run
`vedirwiz` and use Connect. Settings stay pending until Review and apply.

See [usage, updates, device coverage, and checks](docs/USAGE.md) and
[desktop design notes](docs/DESIGN.md) for details.

Licensed under [GNU Affero GPL v3](LICENCE), `AGPL-3.0-only`.
