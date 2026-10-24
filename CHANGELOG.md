# Changelog

All notable changes to this project are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

## [Unreleased]

## [0.5.3] — 2026-10-24

### Changed

- Lineup grading. The promotion gate now grades lineup points gained on starters chosen by q50, the shipped dashboard objective (owner decision 2026-10-24, `docs/DESIGN.md` §7). The evaluation still computes and reports the mean-head lineup figure alongside: `points_gained_total` in the report, `points_gained_mean_head` in the promotion checklist; the graded figure is `points_gained_q50_total`.

## [0.5.2] — 2026-10-19

### Changed

- Tight-end intervals. When a position-week's conformal scores sit on the interval boundary and straddle [0.77, 0.83], q10 and q90 are the equal-tailed residual quantiles around q50. q50, the mean head, and the 8-week window are unchanged. The diagnosis and the acceptance rule are in `docs/DESIGN.md` §21 (2026-10-19).

## [0.5.1] — 2026-10-19

### Added

- Dashboard JSON 1.1. Player rows include `recent_points` and `snap_share` (percent, or null). `export-dashboard --data-dir` fills them from the point-in-time store through `recent_series`, using the Thursday simulated capture. The target week and any row known after that capture stay out. DST snap share is always null. The site draws inline SVG sparklines in the player drawer and on waiver cards.

## [0.5.0] — 2026-10-19

### Added

- Dashboard JSON version 1 and `python -m gridiron_lab export-dashboard`. The contract is in `docs/DASHBOARD_JSON.md`. Rosters come from a JSON file. `examples/dashboard/rosters.json` is a fictional league so the site runs before the Yahoo connection.
- A lineup recommender that fills the `[lineup]` slots by q50, leaves bye and OUT players off the field, and marks a start/sit within 1.0 point as a close call.
- A static site in `site/` (vanilla JS, no third-party requests). Team, Waivers, League, and Lab. Dark and light themes are saved. The Team view can show any team; `#team=10` and `localStorage` remember the choice. The default is Purple Reign.
- `[dashboard] anonymize_managers`. When true, the export writes `Manager {team_id}` and refuses to emit the roster file's manager string.
- A GitHub Pages workflow that deploys `site/` on push to `main` only, using `actions/deploy-pages`.
- The Lab block is the published M3.1 backtest. The candidate was not promoted, and mean-head bias is still outside ±0.3 in seven position-seasons. The site draws that gap as improvement over the baseline and labels the defense slot DEF.

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
