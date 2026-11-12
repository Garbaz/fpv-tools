# fpv-tools

Command-line tools for Betaflight flight controllers and their blackbox logs:

- `fpv cli` runs commands in the flight controller's CLI, e.g. to back up and restore its configuration
- `fpv msp` sends any MSP message and prints the reply, decoded to named fields where the layout is known
- `fpv bb` pulls blackbox logs off the onboard flash and analyses them: statistics, events, spectra, step response,
  CSV export

```sh
fpv cli 'diff all' > config.txt                  # back up
fpv cli < config.txt                             # restore (the export ends with `save`)
fpv msp MSP_RC --every 0.05 --changes            # poll, print only changes

fpv bb pull -O logs/                             # copy the onboard flash, named like Betaflight's app does
fpv bb info logs/                                # firmware, craft, flights, events
fpv bb stats LOG -s vbat -s amps --when 'thr > 5'
fpv bb spectrum LOG -s gyro_raw.roll --vs-throttle 10
fpv bb export LOG -s thr -s gyro.roll -o flight.csv
```

The flight controller is left as found: after commands that change something it reboots to drop the changes,
unless `--save` stores them or `--no-reboot` keeps them until power-off. Anything that cannot be undone, such as
erasing the flash, asks first.

Some log signals need settings the log does not record, such as the battery alarm settings. These come from a
`dump all` export (`--config`), the flight controller (`--config-from-fc`) or `--set KEY=VALUE`; there are no
built-in defaults. `fpv bb --help` lists the signals and the `--when` condition syntax.

## Install

Needs Python 3.12 or newer.

```sh
uv tool install git+https://github.com/Garbaz/fpv-tools
```

Supports Betaflight 4.5, 2025.12 and 2026.6. Developed with a BETAFPV G473 flight controller on Betaflight 2026.6.

## Development

Needs [uv](https://docs.astral.sh/uv/). The firmware tables in `src/fpv_tools/firmware/tables/` and the MSP codes
are generated from a clone of [Betaflight](https://github.com/betaflight/betaflight), which also checks the
firmware statements fpv-tools reproduces (`scripts/firmware_anchors.py`).

```sh
uv run pytest                                          # offline tests
BETAFLIGHT_SOURCE=path/to/betaflight uv run pytest     # also check the tables against the firmware
FPV_TEST_PORT=/dev/ttyACM0 uv run pytest -m hardware   # read-only checks on a disarmed FC (it cannot arm until rebooted)
uv run scripts/gen_firmware_tables.py path/to/betaflight
uv run scripts/gen_msp_codes.py path/to/betaflight
```

Licensed GPL-3.0-or-later, like Betaflight, whose log writer and source the decoder and tables follow.
