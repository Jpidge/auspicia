# Changelog

All notable changes to this project are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

## [Unreleased]

## [0.4.2] — 2026-10-18

### Added

- QB, K, and DST get a trailing residual-mean offset on the same 8 weeks used for conformal calibration. The offset is added to the mean and to q10, q50, and q90. Fewer than 30 residuals leaves it at 0. The rule is the 2026-10-18 M3.2 amendment in `docs/DESIGN.md`, written after the M3.1 backtest and before this refit.
- Leave-one-season-out stability. The results JSON and the evaluate log carry a flag that passes only when the bias stays inside ±0.3, or shrinks by more than 0.05, in every dropped-season variant.

## [0.4.1] — 2026-10-18

### Changed

- Promotion bias uses the mean head. q50 bias is reported and does not gate promotion. The change is the 2026-10-18 amendment in `docs/DESIGN.md`, decided after the M3 backtest.
- The 10/90 interval is shifted with split conformalized quantile regression on the last 8 weeks before each test week. Raw and adjusted coverage are both reported. LightGBM hyperparameters are unchanged.

## [0.4.0] — 2026-10-18

### Added

- One LightGBM model per skill position (`lgbm_skill_v1`) with a mean head and q10/q50/q90 heads. K and DST use a small OLS model that fits rows with a line and rows without a line separately. `force_zero` rows predict 0 and are not training rows.
- `legacy_baseline_v1`, the previous tool's projection blend on the v2.0 feature row.
- Rolling-origin evaluation for 2022–2025: MAE, RMSE, bias, 80% interval coverage, a week bootstrap of the MAE difference, synthetic-roster lineup points gained, and `promotion_rule`. `python -m gridiron_lab evaluate` writes a JSON results file. Conformal calibration is left to M7. Model files are not written.

## [0.3.0] — 2026-10-18

### Added

- `build_features` for training (`simulated_capture`, Thursday 20:00 UTC) and serving (`capture`). Both read only through `as_of`. The contract and column list are in `docs/FEATURES.md`.
- Feature groups for rolling and EWMA production and usage, snap and opportunity shares, Vegas lines, opponent points allowed by position, injury and depth, and home, rest, and prior-season carry-over.
- Leakage tests: an absurd Week W stat line leaves every feature row unchanged; each group respects the cutoff; train and serve match at the same Thursday; repeats match.
- A schedule version of each game at 15 May 16:00 UTC so Thursday features can see the opponent without seeing the closing line or the score.
- Weekly usage columns and play-level dropbacks, scrambles, and red-zone touches. Snap rows keep `offense_pct`, `defense_pct`, and `st_pct`. Depth charts with no team game use the week anchor.

## [0.2.0] — 2026-10-18

### Added

- Append-only Parquet storage and a rebuildable DuckDB file. `open_db(data_dir).as_of(table, ts)` returns, per entity key, the latest row whose `known_as_of` is strictly before the cutoff.
- NFLverse ingestion for schedules, weekly player stats, snap counts, injuries, depth charts, weekly rosters, player ids, and the play-by-play events the scorer needs. `python -m gridiron_lab ingest --season 2025` is idempotent. Network access stays in `HttpFetch`; tests use CSV fixtures.
- Canonical tables documented in `docs/SCHEMA.md`. `player_weeks` are scored with the M0 engine (`ffc_yahoo_v1`).
- M1 clock and identity assumptions in `docs/DESIGN.md` §17, including the home-favorite spread sign and the DST points-allowed estimate.

### Fixed

- `.gitignore` anchors `data/` at the repository root so the `gridiron_lab.data` package is not ignored.

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
