# Changelog

All notable changes to fpv-tools. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `fpv-tools msp` shows and takes known fields as the firmware means them. Modes, RC and aux channels, VTX device
  types, `vtx_low_power_disarm` and dataflash flags appear as names. Quantities appear with their unit, each field
  in one fixed unit: voltage and current (`16.12V`, `3.42A`), charge, attitude, mode ranges and channel values
  (`1700us`), VTX frequencies (`5733MHz`) and flash sizes (`B`). Output can be given back as input: lists are
  comma-separated, and records print one per line as `NAME=VALUE` fields. JSON replies list the units.
- Quantities given as input need their unit and take any SI prefix (`1.7ms` is `1700us`). A bare number, a value
  between the firmware's steps, or one outside its range (mode ranges 900us..2100us) is refused, never rounded.
- A layout for `MSP_SET_MODE_RANGE`, e.g. `mode=ANGLE aux_channel=AUX2 start=1700us end=2100us`.

### Fixed

- fpv-tools keeps other programs off the FC's port while it uses it. On Linux and macOS another program (e.g.
  the Betaflight app) could open it meanwhile and talk to the FC in between, which once left the FC in its CLI.
- If entering the CLI isn't confirmed, `fpv-tools cli` sends `exit` before giving up, so the FC isn't left in its
  CLI, where it ignores MSP.
- `fpv-tools msp` says when the FC is in its CLI (it echoes the request back) instead of only timing out.

### Changed

- `fpv-tools msp --raw` now shows and takes known fields as sent (numbers). Hex output is now `--hex`.
- MSP field names follow their meaning: `MSP_MODE_RANGES` has `mode`, `start` and `end` (were `permanent_id`,
  `start_step` and `end_step`), and `MSP_BOXIDS` has `modes` (was `permanent_ids`). `MSP_RC` channels are
  named (`ROLL=1500,...`).

## [0.2.3] - 2026-11-12

- Adjusted examples

## [0.2.2] - 2026-11-12

### Fixed

- The README's links on PyPI, and a link to the repository.

## [0.2.1] - 2026-11-12

### Fixed

- The PyPI page: current description, links to the repository, changelog and issues, and the supported Python
  versions.

## [0.2.0] - 2026-11-12

### Changed

- The command is now `fpv-tools` instead of `fpv`, so `uvx fpv-tools` runs it without installing.

## [0.1.0] - 2026-11-12

First release.

### Added

- `fpv cli`: run commands in the flight controller's CLI, from arguments or stdin.
- `fpv msp`: send any MSP message, with named fields for known layouts; changes are undone by a reboot unless
  saved or kept, and irreversible messages ask first.
- `fpv bb`: pull, erase, inspect and analyse blackbox logs (`info`, `stats`, `runs`, `changes`, `spectrum`, `step`,
  `export`), with a decoder that keeps all log events.
- Support for Betaflight 4.5, 2025.12 and 2026.6, with tables generated from the firmware source.

[Unreleased]: https://github.com/Garbaz/fpv-tools/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/Garbaz/fpv-tools/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/Garbaz/fpv-tools/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Garbaz/fpv-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Garbaz/fpv-tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Garbaz/fpv-tools/releases/tag/v0.1.0
