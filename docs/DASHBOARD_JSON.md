# Dashboard JSON

Version **1** (`version` is the string `"1"`). The static site in `site/` reads this document and nothing else. A schema change has to update `site/app.js` and `tests/test_dashboard.py` together.

The exporter does not call Yahoo. League-wide rosters arrive in M5 from the private `Jpidge/gridiron-lab-data` repo. Until then the input is a roster file you pass on the command line.

## Export

```bash
python -m gridiron_lab export-dashboard \
  --rosters examples/dashboard/rosters.json \
  --output site/dashboard.json
```

Optional `--predictions path.json` overrides `q10`, `q50`, `q90`, `mean`, and `yahoo` by `player_id`. Optional `--config` defaults to `gridiron_lab.toml`.

The shipped roster file is fictional (`"example": true`). The site shows an **Example data** badge when that flag is true. Do not commit a real league roster or any secret to this repository.

## Manager names

Display names come from each team's `manager` field in the roster file. The page reads `manager_display` and does not hardcode names.

`[dashboard] anonymize_managers` in `gridiron_lab.toml` is the one switch:

- `false` (default): `manager_display` is the roster file's `manager` string.
- `true`: `manager_display` is `Manager {team_id}`, and the export fails if a roster `manager` string still appears in the JSON. Team names stay.

## Lineup

Slots are read from `[lineup]` in `gridiron_lab.toml`. The shipped table is the league's real roster: 1 QB, 2 RB, 2 WR, 1 TE, 1 W/R/T flex (RB, WR, or TE), 1 K, 1 DEF (stored as DST), 6 bench, 1 IR. `DEF` on a player row is treated as DST.

The objective is **q50** (the median on the card). Required slots are filled first, then flex takes the best remaining RB, WR, or TE. Ties break on `player_id`. A player with `force_zero`, `bye`, or injury `OUT`, `O`, `IR`, `PUP`, or `SUS` is not started when someone else can fill the slot. An empty slot stays empty.

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
      "manager": "Avery Quinn",
      "wins": 4,
      "losses": 2,
      "opponent_id": 11,
      "players": []
    }
  ],
  "waivers": []
}
```

Each player needs `player_id`, `name`, `position`, and should include `nfl_team`, `opponent`, `home`, `kickoff`, `injury`, `force_zero`, `bye`, `q10`, `q50`, `q90`, `mean`, `yahoo`, `notes`, `opp_rank`, `vegas_implied`, `practice`. `q50` drives the lineup. Missing `q50` falls back to `mean`, then 0.

Each waiver needs `player_id`, `name`, `position`, and may include `nfl_team`, `q50_next` (a list), `ros`, `note`, and `drop_player_id`. The exporter fills `drop_name` and `drop_position` from the roster catalog.

Predictions, when passed, look like:

```json
{ "players": [{ "player_id": "ex-vellwood", "q10": 12.1, "q50": 18.4, "q90": 24.0, "mean": 18.6, "yahoo": 17.1 }] }
```

## Output

Top-level fields:

| Field | Meaning |
|---|---|
| `version` | `"1"` |
| `example` | `true` when the roster file says so |
| `disclaimer` | Footer sentence. Example exports say the players are fictional. |
| `anonymize_managers` | The config flag that produced this file |
| `league` | `name`, `yahoo_id`, `teams`, `format`, `ppr`, `scoring_version` from config |
| `lineup` | Slot counts plus `flex_positions` |
| `week` | `season`, `week`, `kickoff_label`, `countdown_label`, `countdown_is_live` (always `false`; the label is static) |
| `default_team_id` | `league.my_team_id` (10, Purple Reign). The page remembers a different choice in `localStorage` and honors `#team=10`. |
| `objective` | `"q50"` |
| `close_call_margin` | `1.0` |
| `win_probability_sd` | `25` |
| `teams` | One object per fantasy team, below |
| `waivers` | Pickup rows |
| `standings` | Same teams sorted by projected total, with `rank` and `share_of_leader` |
| `lab` | Published M3.1 backtest, below |

Each team includes `team_id`, `name`, `manager_display`, `wins`, `losses`, `record` (en dash), `opponent_id`, `opponent_name`, `projected_total`, `opponent_projected_total`, `win_probability`, `starters`, `bench`, `inactive`, and `calls`.

Each player row includes `player_id`, `name`, `position`, `nfl_team`, `opponent`, `home`, `kickoff`, `injury`, `force_zero`, `bye`, `q10`, `q50`, `q90`, `mean`, `yahoo`, `yahoo_delta` (`q50 - yahoo` when both exist), `slot` (`QB`, `RB`, `WR`, `TE`, `FLEX`, `K`, `DST`, or `null` on the bench), `verdict` (`start`, `monitor`, `sit`), `notes`, `sparkline`, `opp_rank`, `vegas_implied`, `snap_share`, and `practice`.

The page labels FLEX as W/R/T and DST as DEF.

## Lab

`lab` is the published M3.1 backtest (commit `33d5bbc`), not a number computed at export time and not a Yahoo comparison. Yahoo capture is M5.

- `promoted` is `false`. The M3 candidate was not promoted.
- `primary_mae` is `q50`.
- `positions[].seasons[]` has `mae`, `mae_baseline`, `bias` (mean head), and `coverage` (adjusted 10/90).
- `pooled_coverage` and `mae_diff_by_season` (`model MAE − baseline MAE`; negative means the model was closer).
- `points_gained_vs_baseline` is `1591.3` lineup points versus the legacy baseline over 72 weeks. It is not a Yahoo result.
- `criteria` records the promotion rule. Bias fails: mean bias is outside ±0.3 for QB 2022, QB 2023, QB 2025, WR 2022, K 2024, K 2025, and DEF 2025. Coverage fails because TE pooled coverage is 0.846, above 0.83. The page says DEF wherever this prose names the defense slot.
- The page draws `mae_diff_by_season` as improvement over the baseline (the negation). Positive means the model was closer. The JSON field stays model MAE minus baseline MAE.
- `notes` repeats those limits in plain language. The Lab view shows them.
