# Changelog

All notable changes to this project are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

## [Unreleased]

## [0.1.0] — 2026-10-18

### Added

- M0 repository scaffold: `src/gridiron_lab`, `pyproject.toml`, `gridiron_lab.toml`, a placeholder `site/`, and GitHub Actions CI running ruff and pytest.
- Canonical Fantasy Football Coalition scoring engine in `src/gridiron_lab/scoring.py`, driven by the `[scoring]` table. Negative totals are returned as negative.
- Deterministic scoring tests for every league rule, including each points-allowed tier.
- Logging helpers and narrow exceptions (`ConfigError`, `ScoringError`). Ruff rule `BLE001` bans blind `except Exception`.
- Scoring assumptions that the league table does not spell out, recorded in `docs/DESIGN.md` §16.

## [0.0.1] — 2026-10-18

### Added

- `docs/DESIGN.md`: the ground-up rebuild design. It covers:
  - a point-in-time data layer (DuckDB over append-only Parquet, `known_as_of` on every row);
  - a single train/serve feature pipeline with leakage tests;
  - per-position GBMs with calibrated quantiles;
  - rolling-origin evaluation (2022–2025) with a pre-registered promotion rule;
  - the Yahoo benchmark ("The Gridiron Lab Challenge");
  - a static JSON dashboard on GitHub Pages;
  - the M0–M7 roadmap.
- `README.md` and this changelog.
