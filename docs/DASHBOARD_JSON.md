# Dashboard JSON

Version **1.1** (`version` is the string `"1.1"`). The static site in `site/` reads this document and nothing else. A schema change has to update `site/app.js` and `tests/test_dashboard.py` together.

1.1 adds optional `recent_points` and `snap_share` on player rows, and uses `snap_share` on waiver rows. Older 1.0 files still load: a missing or one-point series is drawn as "—".

The exporter does not call Yahoo. League-wide rosters arrive in M5 from the private `Jpidge/gridiron-lab-data` repo. Until then the input is a roster file you pass on the command line.

## Export

```bash
python -m gridiron_lab export-dashboard \
  --rosters examples/dashboard/rosters.json \
  --output site/dashboard.json
```

Optional `--predictions path.json` overrides `q10`, `q50`, `q90`, `mean`, and `yahoo` by `player_id`, and stamps `vegas_implied`, `opp_rank`, and `practice` when the prediction carries them (the `predict` command writes all three: the target week's implied team total, the opposing defense's rank against the player's position — season-to-date points allowed per game, 1 = fewest allowed — and the player's latest practice code, FP/LP/DNP, when he is on the injury report). Optional `--config` defaults to `gridiron_lab.toml`.

Optional `--data-dir` fills `recent_points` and `snap_share` from the point-in-time store. Omit it to copy those lists from the roster file. The example export omits it, because the example players are not in the store. A real export passes the store and does not keep a roster-file series for an id the store does not know.

The shipped dashboard carries the real league (owner decision, 2026-10-24: the live site shows real team names and players). The roster file in `examples/` stays fictional (`"example": true`); the site shows an **Example data** badge when a dashboard's flag is true. Secrets never go in this repository. Whether a published export carries the real league is the owner's call; the shipped example stays fictional.

## Manager names

There are none. The export carries team names and players only: manager names are not read from the roster file, not written to the JSON, and not shown on the page (owner decision, 2026-10-24).

## Lineup

Slots are read from `[lineup]` in `gridiron_lab.toml`. The shipped table is the league's real roster: 1 QB, 2 RB, 2 WR, 1 TE, 1 W/R/T flex (RB, WR, or TE), 1 K, 1 DEF (stored as DST), 6 bench, 1 IR. `DEF` on a player row is treated as DST.

The objective is **mean** (expected points); the median (q50) is the headline on the card but does not drive selection. Required slots are filled first, then flex takes the best remaining RB, WR, or TE. Ties break on `player_id`. A player with `force_zero`, `bye`, or injury `OUT`, `O`, `IR`, `PUP`, or `SUS` is not started when someone else can fill the slot. An empty slot stays empty.

A starter is `monitor` when a legal bench swap is within `close_call_margin` (1.0 q50 point). That bench player is `monitor` too. Other starters are `start`. Other eligible players are `sit`. Up to three calls are returned, smallest margin first.

`DESIGN.md` still describes lineup expected value on the mean head. This export uses q50 so the number on the card is the number that was maximized. That choice is recorded here for the owner to confirm.

## Win probability

When the opponent's projected total is known:

`0.5 * (1 + erf((my_total - opp_total) / (sd * sqrt(2))))`

with `win_probability_sd` = **25**. That spread is an assumption, not a fit on test data. The field is `null` when the opponent total is missing.

## Roster input

```json
{
  "example": true,
  "season": 2026,
  "week": 7,
  "kickoff_label": "Sun 1:00 PM",
  "countdown_label": "2d 18h 24m",
  "teams": [
    {
      "team_id": 10,
      "name": "Purple Reign",
      "wins": 4,
      "losses": 2,
      "opponent_id": 11,
      "players": []
    }
  ],
  "waivers": []
}
```

Each player needs `player_id`, `name`, `position`, and should include `nfl_team`, `opponent`, `home`, `kickoff`, `injury`, `force_zero`, `bye`, `q10`, `q50`, `q90`, `mean`, `yahoo`, `notes`, `opp_rank`, `vegas_implied`, `practice`. `mean` drives the lineup. Missing `mean` falls back to `q50`, then 0.

`recent_points` and `snap_share`, when present on the roster file, are copied only if `--data-dir` is omitted. Each is the last six numbers. A non-list or an empty list becomes an empty `recent_points` and a null `snap_share`. DST and DEF snap lists on the file are dropped.

Each waiver needs `player_id`, `name`, `position`, and may include `nfl_team`, `q50_next` (a list), `ros`, `snap_share` (a list of percentages), `note`, and `drop_player_id`. The exporter fills `drop_name` and `drop_position` from the roster catalog. Waiver `snap_share` follows the same copy-or-lookup rule, and is null for DST and DEF.

Predictions, when passed, look like:

```json
{ "players": [{ "player_id": "ex-vellwood", "q10": 12.1, "q50": 18.4, "q90": 24.0, "mean": 18.6, "yahoo": 17.1 }] }
```

## Output

Top-level fields:

| Field | Meaning |
|---|---|
| `version` | `"1.1"` |
| `example` | `true` when the roster file says so |
| `disclaimer` | Footer sentence. Example exports say the players are fictional. |
| `league` | `name`, `yahoo_id`, `teams`, `format`, `ppr`, `scoring_version` from config |
| `lineup` | Slot counts plus `flex_positions` |
| `week` | `season`, `week`, `kickoff_label`, `countdown_label`, `countdown_is_live` (always `false`; the label is static) |
| `default_team_id` | `league.my_team_id` (10, Purple Reign). The page remembers a different choice in `localStorage` and honors `#team=10`. |
| `objective` | `"mean"` |
| `close_call_margin` | `1.0` |
| `win_probability_sd` | `25` |
| `teams` | One object per fantasy team, below |
| `waivers` | Pickup rows |
| `standings` | Same teams sorted by projected total, with `rank` and `share_of_leader` |
| `lab` | Published M3.1 backtest, below, or a block generated from an evaluation report (`--lab-report`) |

Each team includes `team_id`, `name`, `wins`, `losses`, `record` (en dash), `opponent_id`, `opponent_name`, `projected_total`, `opponent_projected_total`, `win_probability`, `starters`, `bench`, `inactive`, and `calls`.

Each player row includes `player_id`, `name`, `position`, `nfl_team`, `opponent`, `home`, `kickoff`, `injury`, `force_zero`, `bye`, `q10`, `q50`, `q90`, `mean`, `yahoo`, `yahoo_delta` (`q50 - yahoo` when both exist), `slot` (`QB`, `RB`, `WR`, `TE`, `FLEX`, `K`, `DST`, or `null` on the bench), `verdict` (`start`, `monitor`, `sit`), `notes`, `sparkline`, `recent_points`, `opp_rank`, `vegas_implied`, `snap_share`, and `practice`.

`recent_points` is the last up to six weekly fantasy-point totals, oldest first. With `--data-dir` those totals are `fantasy_points` on `player_weeks`, which ingestion already scored with `ffc_yahoo_v1`. The lookup is `recent_series`: the same Thursday 20:00 UTC simulated capture and the same history filter as `build_features`. A week is included only when its `known_as_of` is before that capture, and the target week is excluded even when it was stamped early. Weeks with no observed total are skipped. Fewer than six weeks is a shorter list, not a padded one. An id with no history row is `[]`.

`snap_share` is the last up to six offensive snap percentages (0–100), oldest first, or `null`. The feature pipeline stores snap share as a 0–1 ratio (`offense_pct`, otherwise offense snaps over that team's max offense snaps). The dashboard multiplies by 100 and rounds to one decimal. Weeks with no snap observation are left out, so this list is not guaranteed to line up with `recent_points`. It is `null` when the store has no offensive snap observation, and it is always `null` for DST and DEF. The exporter does not invent a series.

The page draws an inline sparkline when a series has at least two points, and prints the numbers as text. Fewer than two points, or a missing field, is "—". DST waiver cards say "Snap trend n/a".

The page labels FLEX as W/R/T and DST as DEF.

## Lab

`lab` is the published M3.1 backtest (commit `33d5bbc`), not a number computed at export time and not a Yahoo comparison. Yahoo capture is M5.

With `--lab-report <report.json>` (the JSON written by `evaluate`), the block is generated from that report at export time instead, and `source` is `"evaluation_report"`. The per-season rows come from the report's folds (the primary head's MAE, selected by the report), pooled coverage from its calibration block, the criteria prose is generated from its promotion checklist, and `points_gained_vs_baseline` is the report's q50-graded lineup total (`lineup.points_gained_q50_total`). Nothing is recomputed at export time. A report with no folds, no promotion verdict, or no q50 lineup measurement fails the export instead of publishing a partial block. Without the flag, the published M3.1 block below ships unchanged.

- `promoted` is `false`. The M3 candidate was not promoted.
- `primary_mae` is `q50`.
- `positions[].seasons[]` has `mae`, `mae_baseline`, `bias` (mean head), and `coverage` (adjusted 10/90).
- `pooled_coverage` and `mae_diff_by_season` (`model MAE − baseline MAE`; negative means the model was closer).
- `points_gained_vs_baseline` is `1591.3` lineup points versus the legacy baseline over 72 weeks. It is not a Yahoo result.
- `criteria` records the promotion rule. Bias fails: mean bias is outside ±0.3 for QB 2022, QB 2023, QB 2025, WR 2022, K 2024, K 2025, and DEF 2025. Coverage fails because TE pooled coverage is 0.846, above 0.83. The page says DEF wherever this prose names the defense slot.
- The page draws `mae_diff_by_season` as improvement over the baseline (the negation). Positive means the model was closer. The JSON field stays model MAE minus baseline MAE.
- `notes` repeats those limits in plain language. The Lab view shows them.
