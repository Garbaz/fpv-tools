# Changelog

All notable changes to fpv-tools. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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

[Unreleased]: https://github.com/Garbaz/fpv-tools/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/Garbaz/fpv-tools/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Garbaz/fpv-tools/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Garbaz/fpv-tools/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Garbaz/fpv-tools/releases/tag/v0.1.0
