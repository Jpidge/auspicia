# Gridiron Lab — Design

**Status:** M0 scoring · **Date:** 2026-10-18 · **Owner:** John Page

Ground-up rebuild of the previous analytics tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). Public code and the GitHub Pages site live in `Jpidge/gridiron-lab`. Weekly capture data lives in the private repo `Jpidge/gridiron-lab-data`. One job: make better weekly decisions in **Fantasy Football Coalition** than Yahoo's projections would, and prove it with measured evidence.

## 1. Why rebuild

| Previous-tool finding | Gridiron Lab response |
|---|---|
| Corrected 2025 holdout: only ~5% MAE gain over baseline (QB 8.23→7.84, RB 4.24→4.01, WR 3.43→3.26, TE 2.83→2.67) | Add the missing signals. Keep the baseline as a permanent benchmark. |
| Ridge and XGBoost near tied | One regularized GBM per position, plus one baseline |
| Single holdout season, no uncertainty | Four rolling-origin folds with bootstrap CIs |
| Never compared to Yahoo | Automated pre-kickoff Yahoo capture ("The Gridiron Lab Challenge") |
| QB error large | Dedicated QB work (M7) |
| No Vegas or injury/practice features | First-class, timestamped feature groups |
| Point error only | Start/sit decision metrics |
| ± range = 1.15 × volatility, uncalibrated | Calibrated quantiles |
| 6,438-line `gptindex.py`, 15 blind `except Exception` | Package, narrow exceptions, logging |
| 1.5 MB inlined dashboard; heavy process docs | Compact JSON site; README/CHANGELOG/DESIGN only |

Code findings that matter for the port:

- **Baseline weights.** The legacy baseline's "15% season average" term reuses `fantasy_points_ewma`. The effective blend is therefore 60% EWMA (α=0.45) / 25% roll3 / 15% roll5, plus a clipped momentum adjustment and a TD-dependency penalty. The output is floored at 0, even though the league allows negative scores.
- **Volatility.** It is the standard deviation of five summary statistics (last, roll2, roll3, roll5, EWMA), not of game outcomes. That is why the range can't be calibrated.
- **Week 1 skew.** Walk-forward training skips target Week 1, but live forecasts serve Week 1 from carry-over. That is train/serve skew.
- **Matchup bug.** The earlier matchup experiment joined the player's own team defense instead of the opponent's.

## 2. Goals and non-goals

**Goals:**

- Data that is correct as of any point in time
- One feature pipeline shared by training and serving
- Calibrated weekly projections for QB/RB/WR/TE/K/DST
- Start/sit and waiver recommendations
- An honest Yahoo benchmark
- A fast mobile dashboard

**Non-goals (v2.0):** trades, DFS, multiple leagues, play-level simulation, paid data, writing lineups via the API.

## 3. League configuration (authoritative)

Yahoo league **Fantasy Football Coalition**, ID **893771**: 12 teams, H2H, **non-PPR**, fractional and negative scoring. Lineup: QB, 2 RB, 2 WR, TE, W/R/T, K, DEF, 6 BN, 1 IR. My team: Purple Reign (team ID 10).

| Category | Rule |
|---|---|
| Passing | 1 pt / 25 yd; TD 6; INT −2; 40+ yd TD +1 |
| Rushing | 1 pt / 10 yd; TD 6; 40+ yd TD +1 |
| Receiving | 1 pt / 10 yd; TD 6; reception 0; 40+ yd TD +1 |
| Misc offense | Return yd 1/25; return TD 6; 2-pt 2; fumble lost −2; offensive fumble-return TD 6 |
| Kicker | PAT 1; FG = total made FG yards / 10 |
| DST | Sack 1; INT 2; fumble rec 2; TD 6; safety 2; blocked kick 2; return yd 1/25; KR/PR TD 6; extra-point return 2 |
| Points allowed | 0 → 10; 1–6 → 7; 7–13 → 4; 14–20 → 1; 21–27 → 0; 28–34 → −1; 35+ → −4 |

- **Where the rules live.** They sit in the `[scoring]` table of `gridiron_lab.toml`. Boundary readings that the table does not spell out are in §16. The engine (ported `scoring.py`) is generic over that table.
- **Version stamp.** Every target, model and snapshot carries `scoring_version = ffc_yahoo_v1` plus the table's hash. A model with a different hash refuses to load.
- **Event-level rules.** Long-TD bonuses, returns and K/DST events come from play-by-play aggregation (ported `pbp_scoring.py`).
- **Offensive fumble-return TDs** stay explicitly *unavailable*, never silently zero.

## 4. Architecture

```
NFLverse ─┐                 ┌─> features(as_of) ─> models ─> predictions ─┐
Vegas  ───┼─> ingest ─> DB ─┤                                             ├─> eval, snapshots ─> site JSON ─> Pages
Yahoo  ───┘                 └─> scoring engine ─> targets ────────────────┘
```

There is one CLI, `gridiron-lab {ingest, build-features, train, predict, evaluate, yahoo capture, site build}`. Stages talk only through the DB.

## 5. Data layer

**Storage.**

- **Source of truth:** append-only Parquet partitions (`data/raw/<source>/<season>/`, `data/snapshots/`).
- **Query layer:** DuckDB on top. It can always be rebuilt from the Parquet files.
- **What can't be rebuilt:** snapshots and Yahoo captures. They are never rewritten, and they are not stored in this public repo. Weekly capture data belongs in `Jpidge/gridiron-lab-data`.

**Temporal columns.** Every row carries:

- `known_as_of` (UTC): the earliest time the value was knowable
- `ingested_at`
- `source`

Live ingests set `known_as_of = ingested_at`. Historical backfill uses conservative, documented proxies:

- Stats and play-by-play: kickoff + 6 h
- Snaps: kickoff + 24 h
- Vegas lines: kickoff − 1 h (assumed close line)
- Injury reports: report timestamp, or the report date at 23:59 local if there is no time
- Depth charts and rosters: snapshot date

**Sources.**

- **NFLverse** (`nflreadpy`): weekly stats, play-by-play, schedules, snap counts, injuries, depth charts, rosters, IDs.
- **Vegas:** `spread_line` and `total_line` from NFLverse schedules (free, already a dependency). Implied team total = total/2 ± spread/2, with the sign convention pinned by a unit test.
- **Yahoo API:** league settings, weekly rosters and lineup slots, player status, projections.
- **Manual fallback:** pasted Yahoo roster text, read by the ported parser.

**Identity.**

- The canonical `player_id` is the NFLverse `gsis_id`.
- `player_xref` maps Yahoo keys, names and teams to it, with `match_method` and `match_confidence`.
- Ambiguous matches are surfaced, never auto-assigned.
- DST uses `DEF:<team>`.

**Point-in-time access** goes through one function: `db.as_of(table, ts)` returns, per key, the latest row with `known_as_of < ts`. A test fails if `features/` reads a table any other way.

## 6. Feature pipeline

**Contract.** For player *p* in week *W*, `as_of ≤ kickoff(p, W)`, and every input has `known_as_of < as_of`. Training and serving call the same `build_features(season, week, as_of_policy)`. The column list and the clock for each group are in [`docs/FEATURES.md`](FEATURES.md).

There are two policies:

- **`capture` (serving):** as_of = now.
- **`simulated_capture` (training and evaluation):** as_of = the scheduled capture time for that week, defaulting to Thursday 20:00 UTC.

Training and serving see the same information state. A Sunday-morning policy would be a separate model variant.

**Feature groups (v2.0):**

1. **Usage and production:** last, roll3, roll5 and EWMA of league-scored points, targets, carries, air yards, red-zone touches, pass attempts, dropbacks and designed QB rushes. Plus four-game slopes (v1's opportunity momentum).
2. **Snap share and opportunity:** snap %, plus target, carry, air-yard and red-zone shares of team totals.
3. **Vegas:** implied team total, spread, game total.
4. **Opponent defensive strength:** league-scored points allowed to the position, using only games completed before as_of. The opponent comes from the *target-week schedule*.
5. **Injury and practice:** game status, daily DNP/LP/FP, and same-position teammates ruled out.
6. **Context:** home/away, rest days, games played, prior-season carry-over that decays by week and resets when a player changes teams.

**Missing values and forced zeros.**

- Missing stays NaN with a `*_missing` flag. Zero is used only when it is true.
- After the model runs, byes and Out status force the projection to 0.

**Required tests:**

- W-1 stats present, W stats absent.
- **Adversarial test:** set one player's Week W stats to 1e6, rebuild, and assert that the features and predictions are byte-identical.
- An injury or line row stamped as_of + 1 s is excluded.
- Opponent ≠ own team.
- Bye → 0, while the same player in a scheduled week gets a non-zero projection.
- **Train/serve parity:** the training row equals the serving row built at the same as_of.

## 7. Models

**QB/RB/WR/TE.** One regularized GBM per position: XGBoost ≥ 2.0 or LightGBM, decided in M3. Shallow trees, L2 regularization, early stopping on an inner temporal split. Two heads:

- **Mean** (squared error): a prediction head, trained on squared error. The dashboard lineup optimizer maximizes the mean, and the evaluation grades the mean-head lineup figure (see the owner decisions below); the q50 figure is reported alongside. The median stays the headline number on the player card, but it is not the selection objective. See `docs/DASHBOARD_JSON.md`.
- **Quantile** (q10, q50, q90): used for ranges and MAE. Quantile crossing is fixed by sorting.

**Owner decision (2026-10-24) — the promotion gate grades the shipped selection rule.** The dashboard lineup optimizer maximizes q50, so lineup points gained is graded on starters chosen by q50. The mean-head lineup figure is still computed and reported alongside, so the two selection rules can be compared on the same backtests. This decision was recorded before the next evaluation run; it does not change the current verdict, which fails on the bias gates under either figure. **Superseded 2026-10-29 (below): the shipped selection rule is now the mean.**

**Owner decision (2026-10-29) — the shipped selection rule is the mean; the gate grades it.** The dashboard lineup optimizer maximizes the mean (expected points), so lineup points gained is graded on starters chosen by mean. The trigger was a live Week 8 flex call the q50 objective got backwards (a skewed WR with the higher mean benched for a steadier RB with the higher median), and the backtest comparison the 2026-10-24 decision kept alongside settles it: on 2022–2025 the mean-ranked lineup gained +1,591.28 points against the baseline lineup vs +1,472.10 for q50 ranking, with lower regret (10,550.36 vs 10,669.54). The q50 lineup figure is still computed and reported alongside. Team projected totals on the dashboard are now sums of starter means, so win probabilities use expected totals. This decision was recorded before the next evaluation run; it does not change the current promotion verdict, which fails on the bias gates under either figure.

**K.** Ridge or Poisson GLM on implied team total, FG attempt rate, kick-distance history and dome.

**DST.** Expected points-allowed tier value from an empirical distribution of points allowed, conditioned on the opponent's implied total. Sacks and turnovers come from rolling rates for both teams. TDs and returns use league-average priors.

**Baseline.** `legacy_baseline_v1`, ported verbatim with its quirks, so old numbers can be reproduced. It is never retired.

**Calibration.** Conformalized quantile regression, fitted on the latest fold before the test season.

- 10–90 interval: 80% ± 3 pp coverage per position
- Each tail: 10% ± 2 pp

The ridge and XGBoost layers and the hand-tuned blend are dropped. Every prediction carries `model_version`, `feature_version`, `scoring_version` and `trained_through`.

## 8. Evaluation

**Folds.** Train ≤ S−1, test S, for S = 2022–2025:

2022–2024 are development folds, 2025 is confirmation-only (once per frozen candidate), and live 2026+ is the true out-of-sample test.

**Metrics** are reported per position and for pooled offense. Each has a 95% CI from a bootstrap that resamples whole weeks, because errors are correlated within a week.

- MAE, RMSE, bias, median absolute error
- Spearman rank correlation within each position-week
- Top-12 hit rate
- 10–90 interval coverage
- **Decision metrics:** each week, build the optimal lineup by model, by baseline and (once captured) by Yahoo, then score them with actuals. Report **points gained per week** and **regret** against the hindsight optimum.
  - Greedy fill (required slots, then best remaining RB/WR/TE at flex) is optimal because each player has one position.
  - Rosters for 2026+ are the real league rosters. Earlier seasons use seeded synthetic 15-player rosters drawn by season-to-date points.

**Promotion rule (pre-registered, checked in code).** A candidate replaces the incumbent only if all of these hold:

1. Pooled-offense MAE improves in ≥ 3 of 4 folds, and the paired CI of the MAE difference excludes 0.
2. No position worsens by > 0.05 MAE in more than one fold.
3. |bias| ≤ 0.3 per position.
4. Coverage stays on target.
5. Pooled points gained vs the incumbent is ≥ 0.

**Amendment (2026-10-18, M3.1).** Written after the M3 backtest, not before it. M3 applied criterion 3 to q50. Those medians sat about 0.8 to 1.8 points below the actual mean for QB, RB, WR, and TE. Weekly fantasy scores are right-skewed, so a median forecast lies below the mean even when the mean head is centered. From this date, criterion 3 is the mean (squared-error) head: the average of `mean − actual` in each position-season has absolute value ≤ 0.3. q50 bias is still computed and reported, and it does not gate promotion. Criteria 1, 2, 4, and 5 are unchanged. A verdict under this wording is an amended rule, applied post-hoc to seasons M3 had already shown. The M3 q50-bias failures are not re-labeled as passes under the old wording.

Every run is logged to an `experiments` table: id, git SHA, folds, features, params, metrics.

## 9. Yahoo benchmark — The Gridiron Lab Challenge

- **Capture.** A GitHub Actions cron runs Thursday 20:00 UTC, about 4 h before TNF. The margin absorbs Actions schedule delay. The job pulls Yahoo projections for every rostered player plus top waiver targets, and runs `predict` in the same job. Both sides are stamped `captured_at_utc`. Optional captures: Sunday 15:00 UTC, and before Saturday or international games.
- **Validation.** A row is eligible only if `captured_at_utc < kickoff(player)`. The canonical pair is the latest eligible Gridiron Lab row and Yahoo row, captured ≤ 15 min apart.
- **Actuals.** After Tuesday finalization, join canonical-engine actuals. Yahoo's reported points are stored as a cross-check; a difference > 0.1 fails the week loudly.
- **Publishing.** A week is published only when every row is comparable. The report covers:
  - MAE and RMSE for both systems
  - Value added (|err_yahoo| − |err_ours|)
  - Head-to-head win rate
  - Disagreement buckets (<1, 1–2, 2–3, 3–5, >5)
  - Lineup points vs Yahoo
- **Secrets.**
  - The OAuth2 client ID, secret and refresh token live in GitHub secrets, or in a git-ignored `.env` locally, behind a pre-commit secret scan.
  - Access tokens (~1 h) are refreshed per job and never written to disk in CI.
- **Timing.** Capture starts the day the access codes arrive; uncaptured weeks are lost.

## 10. Dashboard

**Hosting.** A static site on GitHub Pages, deployed from an Actions artifact (`actions/deploy-pages`). Generated files are never committed.

**Data.** `gridiron-lab site build` emits:

- `meta.json`
- `roster.json`
- `startsit.json`: lineup, swaps, points gained
- `projections.json`: columnar mean/p10/p50/p90 and the Yahoo delta
- `waivers.json`
- lazy-loaded `players/<id>.json`
- `lab.json`

**Front end.**

- Size budget: < 150 KB gzipped for the first screen.
- Vanilla JS modules, one CSS file, no build step.
- Mobile-first cards with ≥ 44 px tap targets.

**Lab.** `lab.html` is a separate page (folds, CIs, calibration, Challenge results), not in the main nav.

## 11. Repo and engineering standards

```
src/gridiron_lab/{data,features,models,eval,yahoo,scoring,site}.py
tests/   site/   docs/DESIGN.md   gridiron_lab.toml   pyproject.toml   README.md   CHANGELOG.md
.github/workflows/{ci,weekly,yahoo-capture,pages}.yml
```

M0 lands `scoring.py`, `config.py`, and `logging.py` under `src/gridiron_lab/`. The other modules arrive in the milestone that needs them.

- **Tooling:** Python 3.11+, locked dependencies, ruff (lint + format), pytest, mypy on `scoring/` and `features/`.
- **Exceptions:** caught only by specific type. Ruff `BLE001` bans blind `except Exception`.
- **Failure modes:** optional sources (injuries, Vegas) degrade to flagged-missing. A core-stats failure aborts the run.
- **Logging:** stage summaries at INFO, e.g. `Matched 183/186 · ambiguous 2 · unmatched 1`.
- **Config:** everything lives in `gridiron_lab.toml`: league, scoring, seasons, schedule, model params, paths. Secrets come only from env vars.

## 12. Migration

1. **Port with tests first.** Port `scoring.py`, `pbp_scoring.py`, the Yahoo roster parser, name normalization and their tests without changing behavior, then refactor under those tests.
2. **Port the temporal tests** (W-1 present / W absent, carry-over, opponent join) onto `as_of`.
3. **Keep the previous tool running.** It stays production until Gridiron Lab beats it on both:
   - the rolling-origin eval under the promotion rule (the previous tool's ML projections are the incumbent where reproducible, the legacy baseline otherwise);
   - ≥ 4 weeks of the Challenge.
4. **No private data in this repo.** Nothing from the old repo's private roster files, snapshots or Yahoo exports is committed. Old snapshots may be imported into `Jpidge/gridiron-lab-data` tagged `source = legacy`.

## 13. Roadmap

| Milestone | Deliverable | Acceptance criteria |
|---|---|---|
| **M0** Repo + CI + scoring | Skeleton, pyproject, ruff, pytest, CI, scoring port | Ported scoring tests pass. One hand-checked stat line per rule category. CI green. |
| **M1** Data layer | DuckDB/Parquet, NFLverse + Vegas ingestion, `as_of()`, `player_xref` | 2018–2025 backfill loads. Every table has `known_as_of`. Re-ingest is idempotent. Row counts match NFLverse. |
| **M2** Features | `build_features`, all groups, both policies | All §6 tests pass, including adversarial and parity. |
| **M3** Models + eval | GBMs, K/DST, legacy baseline, fold harness, bootstrap, experiments | `legacy_baseline_v1` reproduces the previous tool's 2025 baseline MAEs (8.23 / 4.24 / 3.43 / 2.83) within ±0.10, or the gap is explained. GBM vs baseline reported with CIs on four folds. |
| **M4** Site | JSON emitters, front end, Pages workflow | < 150 KB first screen; usable on a phone; Lab renders. Contract: `docs/DASHBOARD_JSON.md`. |
| **M5** Yahoo | OAuth refresh, capture, roster sync, xref | Three consecutive scheduled captures pass kickoff validation. ≥ 98% of rostered players matched, the rest surfaced. Manual fallback works. |
| **M6** Decisions + Challenge | Lineup simulator, decision metrics, report | Points gained per week with CI on historical folds. First fully comparable Challenge week published. |
| **M7** Intervals + QB | Conformal calibration, QB features | Coverage on target in ≥ 3 of 4 folds per position. QB changes pass the promotion rule, or are recorded as rejected. |

**Order:** M0 → M4 in sequence. The M5 capture job starts in parallel with M1 the day the Yahoo codes arrive.

## 14. Risks

- **Yahoo history:** past projections may be unavailable, so the Challenge may start from zero.
- **Cron jitter:** Actions schedules can fire late or be skipped. Mitigation: hours of margin, manual dispatch backup, failure alerts.
- **CI storage:** the Actions cache is evictable. Captures need the private repo `Jpidge/gridiron-lab-data`, outside this public repository.
- **Vegas timing:** NFLverse lines are probably near-close values. Using them at a Thursday as_of is mild leakage.
- **Injury data:** NFLverse injury timestamps may be coarse, and recent-season coverage needs to be verified.
- **Sample size:** four folds and ~17 weeks per season give wide CIs. A ~5% gain may be indistinguishable from noise.
- **Privacy:** a public Pages site exposes manager names and rosters.

## 15. Open questions

1. Does the Yahoo API serve past-week projections? Test with the first token.
2. Injury/practice source: are NFLverse injuries enough, or should we add timestamped Yahoo status captures?
3. Vegas: accept NFLverse lines with a "close" assumption, or capture lines ourselves each Thursday?
4. K/DST: keep them out of the Challenge until they beat a naive rolling mean?
5. Does Yahoo's DST points-allowed exclude points scored by the opponent's defense or special teams? Verify against Yahoo-reported DST points.
6. Do Yahoo refresh tokens rotate? If so, CI must write the new token back to secrets.
7. Anonymize every manager except Purple Reign on the public site? Decided for M4: the page shows `manager_display` from the roster file. Set `[dashboard] anonymize_managers = true` to write `Manager {team_id}` instead. The owner will honor later privacy requests one at a time. **Superseded 2026-10-24:** the owner removed manager names from the export and the page entirely — unneeded. Team names and players stay real on the public site.
8. XGBoost or LightGBM? Decided in M3: LightGBM 4.6.0. See §19.

## 16. M0 scoring assumptions

The point values in §3 are the league rules, stored in `gridiron_lab.toml`. `src/gridiron_lab/scoring.py` multiplies supplied counts by those rates. It does not round, and it does not floor a total at zero. These are the readings §3 does not spell out. Tests use hand-derived literals for each one.

1. **Points-allowed tiers are inclusive integer ranges.** 0 scores 10; 1 through 6 score 7; 7 through 13 score 4; 14 through 20 score 1; 21 through 27 score 0; 28 through 34 score −1; 35 and every larger integer score −4. A non-integer or a negative `points_allowed` raises `ScoringError`. The table does not define those inputs, so the engine does not invent a tier for them. The previous engine treated every `points_allowed <= 0` as a shutout, which also covered negatives; that reading is not kept. Whether Yahoo's points-allowed figure excludes points scored by the opponent's defense or special teams is still open (§15 question 5). The engine scores the number it is given.

2. **A missing `points_allowed` is not a shutout.** The omitted field is reported as unavailable and the +10 tier is not applied. Explicit 0 is a shutout.

3. **A 40+ touchdown bonus includes a 40-yard touchdown and stacks on the 6-point score.** The engine scores a supplied bonus count. It rejects a bonus count larger than the matching touchdown count, because the bonus is extra, not a substitute for the touchdown. The previous unit test awarded rushing and receiving bonuses with no base touchdown; that input is now an error. Play-by-play reconstruction is not part of M0. The previous aggregator treated the threshold as `yards_gained >= 40`. "40+" is read here as 40 or more.

4. **There is no 40+ bonus for return touchdowns or defensive touchdowns.** The rules list that bonus only on passing, rushing, and receiving touchdowns.

5. **Return yards and return touchdowns stack.** Yards score 1 per 25; the touchdown scores 6. Kickoff and punt returns share one `return_yards` field and one `return_tds` field. Offense and defense have separate touchdown rates in the table (`return_touchdown` and `defense_return_touchdown`); both are 6 in this league. The caller chooses `score_offense` or `score_defense`. The same name `interceptions` is −2 for a pass thrown and +2 for a takeaway.

6. **Made field-goal yards score `yards / 10`.** Missed field goals are not an input, and the rules give them no penalty. Made PATs score 1 each. Missed PATs are not an input. Negative field-goal yards are rejected.

7. **Each two-point conversion on the stat line scores 2.** The canonical field is `two_point`. The engine does not split pass, rush, and receive, and it does not award one conversion to two players. A two-point conversion is not counted inside `touchdown_points`.

8. **Categories stack. The engine does not infer one from another.** A fumble lost (−2) and an offensive fumble-return touchdown (6) both score when both counts are present. A sack is not implied by a safety, and a fumble recovery does not imply a touchdown. The caller attributes each event to one category so a kick-return touchdown is `return_tds`, not also `defense_tds`.

9. **Omitted core box-score counts are zero. Omitted play-level events are unavailable, not an observed zero.** Core counts are passing, rushing, and receiving yards and touchdowns, interceptions, receptions, fumbles lost, two-point conversions, and the defensive counts sacks, interceptions, fumble recoveries, touchdowns, and safeties. Unavailable when omitted: 40+ touchdown counts, return yards, return touchdowns, offensive fumble-return touchdowns, made PATs, made field-goal yards, blocked kicks, extra-point returns, and `points_allowed`. `None` and NaN are unavailable on every field. Unknown keys are ignored so an identifier column can ride along. M0 does not sum split source columns. The previous ingestion path kept the first matching alias, so a later ingester should sum rushing and receiving fumbles lost rather than copy that behavior.

10. **Negative yardage scores negative points.** The per-yard rate applies below zero for passing, rushing, receiving, and return yards. Counting stats cannot be negative.

11. **Fractional scoring has no separate rounding step.** A config with `fractional = false` is rejected because the rules do not define a rounding mode. `ppr` must agree with the reception rate (0 in this league). A table that contains a negative rate while `negative_scoring` is false is rejected, because the engine will not hide that rate behind a zero floor.

12. **The scoring-table hash covers rates and tiers only.** `scoring_version` stays `ffc_yahoo_v1`. `require_scoring_hash` refuses a mismatch. League name and roster slots are not part of the hash.

## 17. M1 data assumptions

The column list, entity keys, and the release files that were counted are in [`docs/SCHEMA.md`](SCHEMA.md). These are the readings §5 does not pin down.

1. **One game row is visible at a time.** The schedule (teams, week, kickoff; lines and scores null) is knowable at 15 May 16:00 UTC. Pregame lines use `known_as_of = kickoff − 1 h` and null scores. The final score is a later version of the same `game_id` at `kickoff + 6 h`. `as_of` keeps the latest version only after that instant, so a Thursday capture sees the opponent and not the closing line or the result.

2. **Positive `spread_line` means the home team is favored.** Home implied total = `total_line / 2 + spread_line / 2`. Away = `total_line / 2 − spread_line / 2`. A missing line leaves both implied totals null. The 2024 opener (home KC, spread 3, total 46) is the unit-test case: home 24.5, away 21.5.

3. **Kickoff is `gameday` plus `gametime` in America/New_York**, then UTC. A date with no clock uses 23:59 Eastern. Stats, play-event counts, and the postgame score use kickoff + 6 h. Snaps use kickoff + 24 h.

4. **Injury reports.** A `date_modified` value with a time of day is that timestamp. A calendar date with no time is 23:59 America/New_York. The 2025 injuries file dropped `date_modified` and has no report date. Those rows use the team-week kickoff (`kickoff_proxy_missing_report_time`), so a read taken before kickoff cannot see them.

5. **Depth charts and weekly rosters.** A `dt` timestamp is the snapshot clock. A week with no clock uses 23:59 America/New_York on the day before that team's `gameday`. A depth or roster week with no team game uses the earliest `gameday` in that season-week. A depth row with no week (the SBBYE tag) is skipped. Season-level rosters are not ingested; weekly rosters keep only the latest week. Depth-chart `as_of` keys are `(player_id, team, position)`, and roster keys are `(player_id,)`, so the latest publication before the cutoff wins.

6. **Identity.** `player_id` is `gsis_id`. DST is `DEF:<TEAM>`. The players file has `pfr_id`, `espn_id`, and `rookie_season` (used as the entry-year proxy: 1 September, America/New_York). It does not carry `yahoo_id`. Yahoo and Sleeper ids are read from weekly rosters when those columns are present. An external id that points at two GSIS ids is written to `identity_issues` and is not assigned. Current team and status are not copied onto the player dimension.

7. **Weekly `passing_40` is not a 40-yard touchdown count.** Long touchdowns, return touchdowns, offensive fumble-return touchdowns, and DST events come from play-by-play. The same file supplies dropbacks (`qb_dropback` on the passer), scrambles (`qb_scramble`), and red-zone touches (`yardline_100 <= 20`). A missing PBP column leaves that event null. Weekly `targets`, `carries`, `attempts`, and air yards are stored on `player_weeks` and are not scoring inputs. Fumbles lost sum the sack, rushing, and receiving columns. `fg_made_distance` is the sum of made field-goal yards. NFLverse fantasy-point columns are ignored.

8. **DST points allowed is still an open Yahoo question (§15 item 5).** The stored estimate is the opponent's final score minus 6 for each opponent kickoff/punt return touchdown and each opponent defensive touchdown. Safeties stay in the total. The extra point after an excluded touchdown is not removed. A negative result, or a game with no play-by-play touchdown columns, leaves `points_allowed` null rather than using the raw score. `opponent_score` is stored beside it.

9. **Downloads and the DuckDB file stay out of git.** `HttpFetch` is the only module that opens a release URL. Tests use `FixtureFetch` and CSV files. Output goes under the data directory (the parent of `paths.raw`, or `--data-dir`).

## 18. M2 feature assumptions

The column list is in [`docs/FEATURES.md`](FEATURES.md). These are the readings §6 does not pin down.

1. **Thursday capture** is 20:00 UTC on the Thursday of the NFL week (Thursday through Wednesday, America/New_York) that contains the earliest kickoff. Each player is then clamped to their own kickoff. A bye keeps the policy time.
2. **Week W player-weeks and snaps are dropped** even if they were stamped before the cutoff. Opponent strength uses the same rule, so a Thursday-night result is not a feature for anyone else that week.
3. **The May 15 schedule version** is what makes opponent, home/away, and rest knowable on Thursday. Flex kickoffs in the release file are the played time, so the stamp can be earlier than the real announcement. Closing lines stay at kickoff − 1 h, and a Thursday row usually has `vegas_missing=1`.
4. **Rolling history is regular season only** and crosses seasons. `carryover_weight` is `0.5 ** (week - 1)` when the previous season's last team matches, else 0. Personal rolls still include games from the old team.
5. **EWMA alpha is 0.3.** Designed QB rushes are carries minus scrambles. Other positions store a true 0 on games they played. Snap share prefers `offense_pct`.
6. **`force_zero` is the bye and Out gate.** No model multiplies it yet. The adversarial test compares feature rows, not predictions.
7. **Week-1 Thursday capture** often misses Sunday rosters, because those rows are stamped the evening before Sunday. Depth charts with a real `dt` (2025) can fill some of that gap. Earlier seasons cannot, without backdating a chart that was not knowable yet.

## 19. M3 model assumptions

1. **LightGBM 4.6.0** is the booster. Early stopping uses the last 20% of training weeks, then the model is refit on every pre-test row for the chosen number of rounds. Depth 3, 8 leaves, L2 of 1, learning rate 0.05, at most 80 rounds, patience 20. No row or column subsampling, one thread, deterministic mode. `min_data_in_leaf` is `min(40, max(5, n // 10))`. These values were fixed before the 2022–2025 run and were not searched on those seasons. A position with fewer than 40 usable rows falls back to the legacy baseline.

2. **M3 scored MAE, bias, and coverage on q50.** Lineup expected value uses the mean head. Quantile crossing is fixed by sorting. Intervals in that run were the raw q10/q90. The 2026-10-18 amendment in §8 moves promotion bias onto the mean head. §20 adds the trailing-window interval adjustment. The M3 numbers themselves were raw q50.

3. **`legacy_baseline_v1` uses the old blend on v2.0 columns.** EWMA here is alpha 0.3 and crosses seasons; the old EWMA was alpha 0.45 inside one season. The season term reuses EWMA. `opportunity_momentum` and `td_dependency` are not on the feature row, so those adjustments are 0. Volatility has no `points_roll2`. The projection is still floored at 0. The 2025 MAE gap against 8.23 / 4.24 / 3.43 / 2.83 is this difference, plus the Thursday universe, not a retune.

4. **K and DST** are OLS, not the full kick-distance or points-allowed tier models in §7. With a line, K uses implied team total and DST uses the opponent implied total, plus points EWMA. Without a line, both use points EWMA and the 3-game mean. Means are not floored. Thursday `simulated_capture` has `vegas_missing=1` on essentially every row, so the backtest reports the no-line regime. The with-line regime is fit only on rows that actually have a line and is covered by unit tests.

5. **`force_zero` predicts 0** and is excluded from training and from MAE, RMSE, bias, and coverage. Missing actuals are excluded from metrics.

6. **Each test week is fit only on earlier weeks.** The inner validation split is inside that history. Labels are `player_weeks.fantasy_points` read after the fact. They are not features.

7. **A season counts for the MAE rule only when model MAE is lower and the week-bootstrap 95% interval of (model − baseline) is entirely below 0.** No position may be worse by more than 0.05 MAE in more than one season. M3 required |bias| ≤ 0.3 on q50 in every scored position-season. The 2026-10-18 amendment moves that check to the mean head; q50 bias stays in the report and does not gate promotion. Coverage must sit in [0.77, 0.83] per position. Pooled lineup points gained must be ≥ 0. Historical lineups are seeded 15-player synthetic rosters (12 teams), because Yahoo rosters are not stored.

8. **The experiments record is the JSON file**, plus `experiments.jsonl` under the data directory. No model artifact is written.

## 20. M3.1 bias and calibration assumptions

Decided before the M3.1 refit, and after the M3 results were known. Tree depth, leaves, L2, learning rate, round cap, and patience are the M3 values. They are not searched again.

1. **The mean head already exists** (squared error). M3.1 reports its bias, MAE, and RMSE beside the q50 numbers. Promotion bias uses the mean head only.
2. **Primary accuracy stays q50 MAE** for criteria 1 and 2, unless the mean head has a lower pooled-offense MAE in at least 3 of the 4 test seasons. That comparison is the only switch, and it is fixed here before the refit. Either way the report says which head was primary.
3. **Intervals are adjusted with split conformalized quantile regression.** For each test week and each position, the last 8 earlier weeks are a calibration window. The model is fit only on weeks before that window. Both the fit and the window are strictly before the test week. The score is `max(q10 − y, y − q90)`. The finite-sample quantile is the `ceil((n+1) × 0.8)`-th ordered score, and it needs at least 30 scores or the raw interval is kept. The adjusted interval is `[q10 − q, q90 + q]`, clipped so q50 stays inside. Criterion 4 is unchanged and is applied to this adjusted interval, pooled across seasons. Raw coverage is reported next to it. Tail rates are reported and do not gate promotion.
4. **A negative score shrinks a too-wide interval. A positive score widens a too-narrow one.** Whether QB, TE, and DST land inside 80% ± 3 pp is an outcome of this rule, not a second pass over the window length. The TE outcome is taken up in §21.

## 21. M3.3 tight-end interval calibration

Decided 2026-10-19, after the M3.1 backtest and before any change to the interval rule. Tree depth, leaves, L2, learning rate, round cap, patience, the 8-week window, the 30-score floor, and the finite-sample index stay at the §20 values. Nothing in this section was picked by searching 2022–2025 coverage.

The run behind these figures is seed 0, `simulated_capture`, fit history from 2018, test weeks in 2022–2025. Adjusted TE coverage equals raw TE coverage: **0.846** pooled (n = 4914). By season: 2022 **0.837** (n = 1210), 2023 **0.846** (n = 1191), 2024 **0.858** (n = 1231), 2025 **0.844** (n = 1282). All four seasons are above 0.83. Pooled adjusted coverage for the other positions is inside [0.77, 0.83]: QB 0.802, RB 0.808, WR 0.825, K 0.798, DST 0.804.

1. **The average TE band is narrower than the residual spread, not wider.** Mean interval width is 7.32 points. On the same rows, the 10th and 90th percentiles of `y − q50` are −1.80 and +6.16, a central-80 width of 7.96. Over-coverage is not a band that simply contains the marginal residual distribution.

2. **The trailing window is not too short for the weekly TE sample.** All 72 test weeks produce a TE conformal score. A window holds 505 to 583 scores (median 540), well above the floor of 30. `ceil((n+1) × 0.8)` targets about 0.802. The extra coverage is not the `(n+1)` index.

3. **The additive shift is zero, and scaling by width does not move it.** The TE conformal delta is 0.0 on every test week, so adjusted and raw coverage match in every season. The width-normalized score `max(q10 − y, y − q90) / (q90 − q10)` has quantile 0.0 on those same 72 weeks. A zero stays zero under any positive scale, so a locally weighted version of the same score cannot shrink TE.

4. **That zero is a point mass on the interval boundary.** The two-sided score is negative when `y` is strictly inside `(q10, q90)`, zero when `y` equals `q10` or `q90`, and positive outside. Of the TE calibration scores, 0.668 are strictly negative, 0.178 are exactly zero, and 0.154 are positive. Closed calibration coverage is 0.846, the same as the test set. By season the calibration mix is 2022 open 0.670 / atom 0.168 / closed 0.838, 2023 0.654 / 0.184 / 0.838, 2024 0.679 / 0.178 / 0.857, 2025 0.667 / 0.183 / 0.850. The 80% order statistic sits between 51 and 97 ranks inside the zero atom (median 71), so it returns 0. QB, K, and DST have no exact-zero scores; their deltas move (QB mean +0.48, DST mean −0.62, which is what brings DST from raw 0.836 to 0.804). RB and WR atoms are smaller (0.038 and 0.077) and those pooled coverages are already inside the band.

5. **No additive delta puts this atom inside the promotion band.** A zero shift leaves closed coverage, above 0.83. Any shrink uncovers every exact-boundary row at once and leaves only the open coverage, 0.668, which is below 0.77. A widen goes further above 0.83. There is no intermediate coverage between the open and closed figures, because the scores do not take values between the last negative score and zero.

6. **The replacement interval is the equal-tailed residual quantile around the same q50.** Use it on a position-week only when that week's calibration scores straddle the existing promotion band: the share of scores `< 0` is below 0.77 and the share of scores `≤ 0` is above 0.83. Those cuts are the promotion band, not new parameters. They describe the case in which every additive shift misses [0.77, 0.83]. On the M3.1 scores this holds for 59 of 72 TE weeks (15 in 2022, 14 in 2023, 18 in 2024, 12 in 2025) and for 12 WR weeks (2, 3, 4, and 3). It holds for no QB, RB, K, or DST week. The other 13 TE weeks already have closed calibration coverage at or below 0.83, so they keep the additive shift of zero.

7. **The residual formula does not introduce a tuned width.** On the calibration rows, `L = q50 − y` and `U = y − q50`. Each tail uses `conformal_quantile` at `alpha = 0.1`, half of the two-sided `alpha = 0.2`, which is the same `ceil((n+1) × 0.9)` index. The interval is `[q50 − Q(L), q50 + Q(U)]`, sorted so q50 stays inside. One pair `(Q(L), Q(U))` is used for every player at that position in that week, added to that player's own q50. The tree's q10 and q90 are not used as a scale: those endpoints are what pinned the two-sided score at zero. Fewer than 30 scores leaves the raw interval, as in §20.

8. **q50, the mean head, and the training rows do not change.** The 8 weeks are still held out of the fit. MAE, mean-head bias, and lineup points gained stay the M3.1 numbers. Only q10 and q90 can move, and only on weeks the band-straddle condition fires.

**Acceptance, fixed before the refit.** One rolling-origin run. No second choice of window, index, trigger, or formula.

1. Pooled TE adjusted coverage on 2022–2025 is in [0.77, 0.83].
2. For each season dropped from {2022, 2023, 2024, 2025}, TE coverage pooled over the other three is in [0.77, 0.83]. The fit does not depend on which test season is in the metric, so this is that one run with the dropped season's rows removed.
3. Pooled adjusted coverage for QB, RB, WR, K, and DST stays in [0.77, 0.83].
4. The MAE promotion checks still pass: q50 stays primary unless the mean head wins at least three seasons, the week-bootstrap interval still clears zero in at least three seasons, and no position is worse by more than 0.05 MAE in more than one season.

Mean-head bias is not an acceptance check here. It already fails, and this rule does not touch the mean. If any of 1–4 fails, report the per-season and leave-one-season tables and do not retune.

