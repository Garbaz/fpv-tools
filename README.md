# fpv-tools

[![PyPI](https://img.shields.io/pypi/v/fpv-tools)](https://pypi.org/project/fpv-tools/)
[![Python](https://img.shields.io/pypi/pyversions/fpv-tools)](https://pypi.org/project/fpv-tools/)
[![CI](https://img.shields.io/github/actions/workflow/status/Garbaz/fpv-tools/ci.yml?branch=main&label=CI)](https://github.com/Garbaz/fpv-tools/actions/workflows/ci.yml)
[![License](https://img.shields.io/pypi/l/fpv-tools)](LICENSE)

Betaflight flight controllers and their blackbox logs from the command line.

- **The flight controller's own interfaces**: any CLI command or MSP message, with the flight controller left as
  found unless changes are saved
- **Blackbox logs as signals to query**: physical units, conditions to select moments of a flight, statistics,
  spectra and step response, and values the flight controller computed but did not log
- **Faithful to the firmware**: log decoding, MSP layouts and the flight controller's calculations follow the
  Betaflight source of each release (4.5, 2025.12 and 2026.6)

## Install

Run it with [uv](https://docs.astral.sh/uv/), no install needed:

```sh
uvx fpv-tools --help
```

Or install it as a tool:

```sh
uv tool install fpv-tools
```

## Usage

### Flight controller

```sh
uvx fpv-tools cli status 'get vbat'                               # CLI commands
uvx fpv-tools cli 'diff all' > config.txt                         # a backup,
uvx fpv-tools cli < config.txt                                    # and its restore
uvx fpv-tools msp MSP_RC --every 0.05 --changes                   # poll a message
uvx fpv-tools msp MSP2_GET_TEXT type=PILOT_NAME --json            # fields as JSON
uvx fpv-tools msp MSP2_SET_TEXT type=CRAFT_NAME text=Whoop --save # change and save
```

Changes are temporary unless saved: the flight controller reboots afterwards and comes back as it was. Use `--save`
to store them, or `--no-reboot` to keep them until power-off. Irreversible operations, such as erasing the flash,
ask for confirmation first.

### Blackbox logs

```sh
uvx fpv-tools bb pull -O logs/                                    # download from the onboard flash
uvx fpv-tools bb info logs/                                       # firmware, craft, flights and events
uvx fpv-tools bb changes logs/ -s mode -s failsafe                # every mode and failsafe change
uvx fpv-tools bb runs logs/ -s acc --when 'acc > 3'               # stretches of high acceleration
uvx fpv-tools bb stats logs/ -s vbat -s amps --when 'thr > 50'    # battery under load
uvx fpv-tools bb spectrum logs/ -s gyro_raw.roll --vs-throttle 10 # gyro noise by throttle
uvx fpv-tools bb step logs/ -s gyro.roll -r setpoint.roll         # how gyro follows setpoint
uvx fpv-tools bb export logs/ -s thr -s gyro.roll -o flight.csv   # signals as CSV
```

Some signals need settings the log does not record, such as the battery alarm state, which needs the alarm
thresholds. They come from a `dump all` export, the flight controller itself, or the command line:

```sh
uvx fpv-tools cli 'dump all' > dump.txt
uvx fpv-tools bb changes logs/ -s battery_state --config dump.txt # the battery alarm as the FC raised it
```

See `uvx fpv-tools bb --help` for all signals and the condition syntax.

## Development

Needs [uv](https://docs.astral.sh/uv/). The firmware tables and MSP codes are generated from a
[Betaflight](https://github.com/betaflight/betaflight) source checkout.

```sh
uv run scripts/gen_firmware_tables.py path/to/betaflight   # regenerate the firmware tables
uv run scripts/gen_msp_codes.py path/to/betaflight         # regenerate the MSP codes
```

```sh
uv run pytest                                              # tests
BETAFLIGHT_SOURCE=path/to/betaflight uv run pytest         # including checks against the firmware source
FPV_TEST_PORT=/dev/ttyACM0 uv run pytest -m hardware       # read-only checks on a connected, disarmed FC
```

## License

GNU General Public License v3 or later, see [LICENSE](LICENSE).
