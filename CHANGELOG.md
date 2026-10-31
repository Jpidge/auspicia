# Changelog

All notable changes to this project are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow SemVer.

## [Unreleased]

## [0.5.27] — 2026-10-31

### Changed

- Header brand spacing (owner call, 2026-10-31): the gap between the emblem and the page title widens from 8px to 30px — roughly four to five spaces of air. Applies to both the dashboard and Control Center headers (one shared `.brand` rule).
- The Control Center's back-link to the dashboard now reads "AUSPICIA" instead of "Dashboard →" (owner call, 2026-10-31). (This entry also carries the header-gap change, which the owner directed to ride along with the next edit rather than ship as its own PR — the standalone PR #45 was closed unmerged.)
- The Control Center's Promotion gates card now explains what is being promoted (owner call, 2026-10-31): the model itself, from "in use" to "proven" — a title, not a usage switch; the dashboard already runs the model's projections. Definitions gain "Promotion gate" and "Promoted" entries saying the same in glossary form.
- Definitions gain a "LightGBM" entry (what the ML is and how gradient boosting works), and defined terms throughout the Control Center now link down to their definition — LightGBM, mean, median, the range, win probability, Vegas implied, gates, promoted (owner call, 2026-10-31).
- "How the engine works" drops its step 6 (owner call, 2026-10-31): the promotion-gates bullet was a definition sitting in a process list — steps 1–5 explain the process; the gate is defined in Definitions and detailed in its own card.
- Definitions gain an "MAE" entry (mean absolute error) — it was used in the gates list and the benchmark table but defined nowhere (owner catch, 2026-10-31); its mentions now link to the definition like the other terms.
- Definitions gain a "Bias" entry (the model's systematic lean, vs MAE's scatter), linked from the gates card (owner call, 2026-10-31).

## [0.5.26] — 2026-10-31

### Added

- **Control Center** (`site/control.html`): the third part of the three-part architecture (owner direction, 2026-10-31) — a separate, read-only page behind the dashboard that serves as the engine's user manual. It carries the system workflow diagram, the pipeline explained step by step, the promotion-gate status (not promoted; fails on bias only, with the seven failing position-seasons listed), the 2022–2025 backtest benchmarks (MAE by season, interval coverage by position, lineup gains), the milestone list, and the plain-language definitions. It reads engine outputs and changes nothing; there is no write path back. The dashboard footer links to it — it is a page, not a fifth tab.
- `site/workflow.svg`: the workflow diagram recoloured from the retired navy chrome to the charcoal scheme (geometry and labels unchanged; the mint improvement loop and amber gates keep their data emphasis).

## [0.5.25] — 2026-10-31

### Changed

- **Single charcoal colour scheme** (owner decision, 2026-10-31, Phase 3 of the AUSPICIA transition): the site's chrome — backgrounds, cards, borders, text — is now a neutral charcoal/gray scale based on the former Carbon palette. Colour is reserved for the emblem and for data: verdict colours, position colours, charts, and trendlines are byte-for-byte unchanged.
- `theme-color` follows the new chrome (#0d0e0f).
- One deliberate deviation from the old Carbon tokens: `--faint` is lifted #777c80 → #8a9096. Carbon's faint managed only 3.5–3.9:1 on card surfaces (below AA for the small text it carries); the new value passes AA on every surface (4.58:1 or better) while staying visibly fainter than `--muted`.

### Removed

- The theme system: the header theme dropdown, the five `[data-theme]` palettes (Rowan Light, Aqua Night, Midnight, Carbon, Light, and the navy Dark default), the `gl-theme` localStorage preference, and their wiring in `app.js`. The Carbon Start-pill override (owner call, 2026-10-28) is folded into the base rule — the Start pill keeps its verdict green.


## [0.5.24] — 2026-10-31

### Added

- Splash screen: the stacked AUSPICIA lockup (emblem over wordmark) flashes on the charcoal brand background while the dashboard loads, then fades.
- `site/emblem.svg` (header mark, cropped from the production emblem) and `site/auspicia-stacked.svg` (splash lockup), built from the verified Blue-Amber-Coral asset suite.

### Changed

- The app is now **AUSPICIA** (repository renamed from `gridiron-lab`; the Python package and CLI keep their internal names). Page title, browser-tab title, and the matchup projection labels ("Auspicia proj.") follow.
- Header: the "GL" placeholder box and "Gridiron Lab" wordmark are replaced by the eye emblem plus the current page title (Team / Waivers / League / Lab), which updates as views change — the pattern modern app UI uses.
- Favicon set and apple-touch-icon replaced with the Blue-Amber-Coral emblem assets; `theme-color` meta added.
- README: new name, new clone URL, and the stale `gridiron-lab-data` reference removed (that repo was deleted; it was empty and never used).


## [0.5.23] — 2026-10-29

### Changed

- Card-to-card spacing is now a consistent 24px: the Lab and waiver grids move up from 16px, and the standalone "How the engine works" card gets the same 24px above it instead of sitting directly against the Notes card.

### Fixed

- Definition tooltips were clipped by card edges: the per-label `::after` tooltip is absolutely positioned, and the player row's `overflow: hidden` (plus the screen edge for right-hand stats like Boom/bust) cut it off mid-sentence. The tooltips now render in a single shared `#def-tip` element with `position: fixed`, placed by script above the label (below it when there is no room) and clamped to the viewport, so no card edge can clip them. Labels also no longer let the ⓘ wrap onto its own line. Behavior is otherwise unchanged: hover, tap, or keyboard focus shows the same `DEFS` text, and the Definitions card is untouched.

## [0.5.22] — 2026-10-29

### Added

- **Definitions, in place and in one place.** A single `DEFS` table in `site/app.js` now feeds both the label tooltips and a new **Definitions** card (replacing "How to read a row") on the Team view, so the two can never drift apart. Every player-drawer stat label (Last 6 weeks, 10th–90th, Median, Mean, Boom/bust, Opp rank, Vegas implied, Snap share, Practice) carries an ⓘ whose definition appears on hover, tap, or keyboard focus; the matchup card's projected totals and win probability are defined the same way, and the row chips (verdict, Yahoo delta), the median bar, the standings Proj header, and the share-of-leader bar carry `title` definitions. The Boom/bust entry states the rule in words: within-position percentile of the 10th–90th width — Steady under 34, Balanced 34–66, Boom/bust 67 and over — and that a wide range means more swing, not more points.

### Fixed

- The Toughest calls card labeled its projections "median"; they have been means since the 0.5.20 objective change, and now say so. The old legend card's claim that "the lineup maximizes median points" (stale since 0.5.20) is corrected in the new Definitions card.

## [0.5.21] — 2026-10-29

### Added

- The player drawer now carries a **Boom/bust** tag. The exporter stamps `volatility` on each predicted player: the within-position percentile of his `q90 - q10` interval width among the week's predicted players (definition in `docs/DASHBOARD_JSON.md`). The drawer reads it as Steady (under 34), Balanced (34–66), or Boom/bust (67+) — informational only, in no verdict colors, beside the Median/Mean/10th–90th stats that were already there. No prediction, verdict, or total changes; the Week 8 re-export in this PR differs from 0.5.20 only by the new field. A display-only first slice of the boom/bust idea; a graded boom/bust index is still unbuilt.

## [0.5.20] — 2026-10-29

### Changed

- The lineup optimizer's objective is now the **mean** (expected points), not q50 (owner decision 2026-10-29, superseding 2026-10-24, recorded in `docs/DESIGN.md`). `lineup.projection()` ranks on the mean with a q50 fallback, so starters, close calls, and team projected totals (now sums of starter means, and with them the win probabilities) all use expected points. The median stays the headline number on the player card. On the 2022–2025 backtest the mean-ranked lineup gained +1,591.28 points against the baseline lineup vs +1,472.10 for q50 ranking, with lower regret (10,550.36 vs 10,669.54). The promotion gate now grades the mean-head lineup figure (`graded_on: "mean"`); the q50-head figure is still computed and reported alongside. This does not change the current promotion verdict, which fails on the bias gates under either figure.

## [0.5.19] — 2026-10-28

### Added

- `evaluate --residuals-out` writes the backtest's row-level scored predictions (actual, mean/q50/interval heads, baseline, and the segmentation features) alongside the aggregate report. The report itself is unchanged — same predictions, same digest — but a bias can now be taken apart by segment to find its mechanism before anyone reaches for a fix. First use: the promotion gate's bias failures (QB, K, DST 2025).

## [0.5.18] — 2026-10-28

### Added

- Opponent rank and practice status reach the dashboard the same way the Vegas total did. `predict` now writes `opp_rank` — the opposing defense's rank against the player's position by fantasy points allowed per game, season-to-date before the target week in the league's own scoring, **1 = fewest allowed (toughest)**, ties sharing a rank — for QB/RB/WR/TE/K (defenses are not ranked against an offense). It also writes `practice` (FP/LP/DNP) from the target week's injury report when the player is on it; unreported players stay blank rather than being assumed full-go. `export-dashboard --predictions` stamps both onto the player row.

## [0.5.17] — 2026-10-28

### Added

- The Vegas implied team total reaches the dashboard. `predict` now writes each predicted player's `vegas_implied` (the implied team total its feature row already computed from the week's spread and total), and `export-dashboard --predictions` stamps it onto the player row, so the drawer's "Vegas implied" stat shows the number the model actually used. Players without a prediction (byes, unresolved identities) keep `null` — no game, no line, no invented number.

## [0.5.16] — 2026-10-28

### Fixed

- The Start verdict pill is back on mobile. A rule inherited from the original site build hid `.v-start` below 720px while Sit and Monitor stayed visible, which made "no pill" ambiguous; all three verdicts now render at every width, as they do on desktop.
- On the Carbon theme the Start pill is green instead of white: Carbon's accent is near-white, so the accent-filled pill read as a white lozenge; it now fills with the theme's verdict green.

## [0.5.15] — 2026-10-28

### Fixed

- The Lab's interval-coverage card is readable now: the old 0-to-1 bars compressed every value into the same last sliver against a hairline target stripe. Coverage is now a dot plot on a zoomed 0.70–0.90 scale with the 0.77–0.83 target as a shaded band — in or out reads at a glance — plus the exact percentage and, when a position misses, how many points low or high.

## [0.5.14] — 2026-10-28

### Changed

- The live dashboard moves to **Week 8**: model projections trained on every week in the store through 2026-W07 headline, with Yahoo's numbers kept as the comparison. This week's league capture was taken fully pre-kickoff, so the Week 8 scorecard can grade model and Yahoo against the same frozen moment. The model's one lineup disagreement with the manager's Yahoo starters: Zach Charbonnet (7.10) flexed over Parker Washington (6.78).

## [0.5.13] — 2026-10-25

### Changed

- The live Week 7 dashboard now carries **the model's own projections** in the headline — generated by the new `predict` command, trained on every week in the store through 2026-W06 — with Yahoo's number kept as the comparison figure instead of the headline. Intervals are the model's raw ranges; the conformal calibration remains a later slice, as noted in 0.5.12.

## [0.5.12] — 2026-10-25

### Added

- A `predict` command: fits `GridironPredictor` on every earlier week in the store — the backtest's feature builder, training filter, and Thursday-capture policy, unchanged — and writes one week's predictions JSON for `export-dashboard --predictions`, keyed by the roster file's player ids. Roster players resolve to store identities by normalized name and position with team as tiebreak; unresolvable players are listed under `unmatched`, never guessed. Bye and force-zero players are skipped so a prediction cannot override the exporter's display for them. The output discloses `"conformal_adjusted": false`: the backtest's interval calibration is not applied yet, so q10/q90 are the model's raw quantiles (q50 and mean are unaffected). Waivers are not predicted yet.
- A "How the engine works" card in the Lab view: a plain-language list of what the model looks at for each player (form, opportunity, matchup, game environment, availability) and how it is graded, with no code terms.

### Changed

- Trendlines now take direction colors everywhere (owner rule, 2026-10-24): green `#22C55E` when a series ends higher than it started, red `#EF4444` when lower, gray when the move is under half a point. Previously the sparkline wore the position color.
- On phones, the players (lineup) card now sits above the Toughest calls card; the side column's order was matchup, toughest calls, lineup, legend and is now matchup, lineup, toughest calls, legend.

## [0.5.11] — 2026-10-24

### Fixed

- The Pages deploy no longer regenerates the example dashboard over the committed one. The workflow's "Export the example dashboard" step rebuilt `site/dashboard.json` from `examples/dashboard/rosters.json` on every deploy, so the live site could never show the real league shipped in 0.5.10. The deploy now publishes the committed `site/` directory as-is; dashboard updates land through pull requests.

## [0.5.10] — 2026-10-24

### Changed

- The shipped `site/dashboard.json` is now the real league (owner decision): Week 7 of the Fantasy Football Coalition, captured from Yahoo on 2026-10-24 — all 12 teams, 187 rostered players, top-25 available players, Yahoo projections standing in for the model's until the predict path lands, and the Lab block generated from the 2026-10-24 evaluation report. The fictional example remains under `examples/` as the sample and fixture. The shipped-data contract test now pins the real file.

## [0.5.9] — 2026-10-24

### Removed

- Manager names, completely (owner decision): the exporter no longer reads or writes them, the dashboard JSON drops `manager_display` and the top-level `anonymize_managers` field, the `[dashboard]` config section and its flag are gone, and the page no longer renders a manager name anywhere. Team names and players are unaffected. This also retires the anonymize path wholesale — including the known gap where waiver exports ignored the flag.

## [0.5.8] — 2026-10-24

### Added

- Interval coverage rows in the Lab view now draw a bar like the MAE and improvement rows, scaled 0 to 1, with the 0.77–0.83 target band shaded on the track so "in band" is visible at a glance.

## [0.5.7] — 2026-10-24

### Changed

- The theme dropdown loses its visible "Theme" label; the select keeps an `aria-label` for assistive tech, and its styles move onto the select itself.

## [0.5.6] — 2026-10-24

### Changed

- Theme dropdown, corrected. The Day & Night entry (the built-in pair following the system preference) is replaced by the original Light and Dark themes as plain selections, as the owner asked. The mode wiring is now a direct map from selection to `data-theme`; the system-following and legacy-pin logic is gone. Default is Dark, as before.

## [0.5.5] — 2026-10-24

### Changed

- Site theme picker. The day/night button is now a dropdown: Rowan Light, Aqua Night, Midnight, and Carbon palettes from the theme preview (exact preview hexes mapped onto the site's tokens), plus Day & Night — the built-in light/dark pair, which follows the system preference once chosen. A saved legacy `dark`/`light` choice is honored as a Day & Night pin, and the default first visit is unchanged (dark). Midnight returns to the set at the owner's request.

## [0.5.4] — 2026-10-24

### Added

- Dashboard Lab block from an evaluation report. `export-dashboard --lab-report results.json` generates the Lab block from a real `evaluate` report — per-season rows from its folds, pooled coverage from its calibration block, criteria prose from its promotion checklist, and the q50-graded lineup total as `points_gained_vs_baseline` — instead of shipping the hand-copied M3.1 constants. Without the flag, the published M3.1 backtest ships unchanged. A report that cannot support the block (no folds, no promotion verdict, no q50 lineup measurement) fails the export.

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
