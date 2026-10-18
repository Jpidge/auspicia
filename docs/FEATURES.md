# Feature contract

`build_features(season, week, as_of_policy, data_dir=...)` in `src/gridiron_lab/features.py` is the only feature path. Training (`simulated_capture`) and serving (`capture`) call it. There is no model in this milestone. `feature_version` is `v2.0`.

A Week W row uses inputs with `known_as_of` strictly before that player's cutoff, and it drops every Week W `player_weeks` and `snap_counts` row even when the stamp is early. Reads go through `open_db(data_dir).as_of`. Game scores are not features.

## Cutoff

- **`simulated_capture`:** Thursday 20:00 UTC of the NFL week (Thursday through Wednesday, America/New_York) that contains the earliest kickoff.
- **`capture`:** `now`, then clamped to the player's kickoff when that kickoff is earlier.
- A bye has no kickoff, so the cutoff stays at the policy time.

`as_of` on the feature row is that cutoff. `as_of_policy` records which entry point was used. The same cutoff produces the same values either way.

## Who is included

Regular season only. Skill positions `QB`, `RB`, `WR`, `TE`, and `K` come from the latest roster known at the cutoff, with a depth-chart fill when that player has no roster row. `DEF:<TEAM>` is added for every team on that season's schedule. A team with no game that week is a bye.

## Missing values and the zero gate

Missing is `None` plus a `*_missing` flag of 1. Zero is stored only when the value was observed as zero. `force_zero` is 1 on a bye or when the target-week injury status is `OUT` (or `O`). Multiplying a projection by that flag is a later milestone; this pipeline does not emit a prediction.

Rolling windows skip missing games. `last` is the most recent observed game. `roll3` and `roll5` are means of up to that many observed games. EWMA uses alpha 0.3 from oldest to newest. `points_slope4` and `opportunity_slope4` are least-squares slopes on up to four observed games (`opportunity` is targets + carries). Fewer than two games leaves the slope missing.

History is regular-season player-weeks before Week W, including prior seasons. Postseason weeks are excluded. Week 1 uses that same history; it is not a separate code path.

## Groups and where each value was knowable

| Group | Columns | `known_as_of` source |
|---|---|---|
| Schedule context | `opponent`, `is_home`, `game_id`, `rest_days` | `games` `record_kind=schedule`, stamped 15 May 16:00 UTC (`schedule_released_may_15`). Kickoff, teams, and week only. |
| Usage and production | `points_*`, `targets_*`, `carries_*`, `air_yards_*`, `redzone_touches_*`, `pass_attempts_*`, `dropbacks_*`, `designed_rushes_*`, both slopes | `player_weeks` at kickoff + 6 h. Counts come from the weekly file (`targets`, `carries`, `attempts`, air yards). Dropbacks, scrambles, and red-zone touches are copied from `pbp_events` at ingest. |
| Snap share and opportunity | `snap_share_*`, `target_share_*`, `carry_share_*`, `air_yards_share_*`, `redzone_share_*` | Prior `snap_counts` (kickoff + 24 h) and prior `player_weeks`. Snap share prefers `offense_pct`, otherwise offense snaps over the team's max offense snaps that game. Other shares are the player's count over the team's count in that game. |
| Vegas | `implied_team_total`, `team_spread`, `game_total`, `vegas_missing` | `games` lines. The closing line is stamped kickoff − 1 h, so a Thursday capture usually has `vegas_missing=1`. A line stamped before the cutoff is used. Positive `team_spread` means the player's team is favored. |
| Opponent defense | `opp_points_allowed_pos_roll5` | League-scored `fantasy_points` of that position against the opponent, regular-season games before Week W whose stats were knowable at the cutoff. Mean of up to five such games. The opponent is the target-week schedule, and it is never the player's own team. |
| Injury | `injury_status`, `practice_status`, `teammates_out_same_position` | Target-week `injuries` row known before the cutoff. Teammates out are other players on that team and week with status `OUT` and the same position. |
| Depth | `depth_rank`, `depth_position` | Latest `depth_charts` row for that player and team known before the cutoff, preferring the forecast position. |
| Roster context | `team`, `position`, `carryover_weight`, `team_changed`, `prior_season_ppg`, `games_played`, `is_bye`, `force_zero` | Latest `rosters` row before the cutoff for team and position. `prior_season_ppg` is the mean of the previous regular season. `carryover_weight` is `0.5 ** (week - 1)` when that season's last team matches, otherwise 0. |

Air yards are `passing_air_yards` for a QB and `receiving_air_yards` otherwise. Designed rushes are carries minus scrambles for a QB, and a true 0 for every other position that has a player-week. Non-QB rows do not invent a scramble count.

## Column list

```
player_id
season
week
season_type
team
opponent
position
game_id
as_of
as_of_policy
feature_version
is_home
is_home_missing
rest_days
rest_days_missing
games_played
prior_season_ppg
prior_season_ppg_missing
carryover_weight
team_changed
team_changed_missing
is_bye
force_zero
points_last
points_last_missing
points_roll3
points_roll3_missing
points_roll5
points_roll5_missing
points_ewma
points_ewma_missing
targets_last
targets_last_missing
targets_roll3
targets_roll3_missing
targets_roll5
targets_roll5_missing
targets_ewma
targets_ewma_missing
carries_last
carries_last_missing
carries_roll3
carries_roll3_missing
carries_roll5
carries_roll5_missing
carries_ewma
carries_ewma_missing
air_yards_last
air_yards_last_missing
air_yards_roll3
air_yards_roll3_missing
air_yards_roll5
air_yards_roll5_missing
air_yards_ewma
air_yards_ewma_missing
redzone_touches_last
redzone_touches_last_missing
redzone_touches_roll3
redzone_touches_roll3_missing
redzone_touches_roll5
redzone_touches_roll5_missing
redzone_touches_ewma
redzone_touches_ewma_missing
pass_attempts_last
pass_attempts_last_missing
pass_attempts_roll3
pass_attempts_roll3_missing
pass_attempts_roll5
pass_attempts_roll5_missing
pass_attempts_ewma
pass_attempts_ewma_missing
dropbacks_last
dropbacks_last_missing
dropbacks_roll3
dropbacks_roll3_missing
dropbacks_roll5
dropbacks_roll5_missing
dropbacks_ewma
dropbacks_ewma_missing
designed_rushes_last
designed_rushes_last_missing
designed_rushes_roll3
designed_rushes_roll3_missing
designed_rushes_roll5
designed_rushes_roll5_missing
designed_rushes_ewma
designed_rushes_ewma_missing
points_slope4
points_slope4_missing
opportunity_slope4
opportunity_slope4_missing
snap_share_last
snap_share_last_missing
snap_share_roll3
snap_share_roll3_missing
snap_share_roll5
snap_share_roll5_missing
snap_share_ewma
snap_share_ewma_missing
target_share_last
target_share_last_missing
target_share_roll3
target_share_roll3_missing
target_share_roll5
target_share_roll5_missing
target_share_ewma
target_share_ewma_missing
carry_share_last
carry_share_last_missing
carry_share_roll3
carry_share_roll3_missing
carry_share_roll5
carry_share_roll5_missing
carry_share_ewma
carry_share_ewma_missing
air_yards_share_last
air_yards_share_last_missing
air_yards_share_roll3
air_yards_share_roll3_missing
air_yards_share_roll5
air_yards_share_roll5_missing
air_yards_share_ewma
air_yards_share_ewma_missing
redzone_share_last
redzone_share_last_missing
redzone_share_roll3
redzone_share_roll3_missing
redzone_share_roll5
redzone_share_roll5_missing
redzone_share_ewma
redzone_share_ewma_missing
implied_team_total
team_spread
game_total
vegas_missing
opp_points_allowed_pos_roll5
opp_points_allowed_pos_roll5_missing
injury_status
practice_status
injury_missing
teammates_out_same_position
teammates_out_same_position_missing
depth_rank
depth_rank_missing
depth_position
depth_missing
```

## Checks

- Week W−1 stats are present and Week W stats are absent, including a Week W row stamped before the cutoff.
- Setting one player's Week W stats to 1e6 leaves every feature row for that week unchanged. There is no prediction to compare yet.
- An injury or a line stamped one second after the cutoff is excluded.
- Opponent is never the player's team.
- A bye sets `force_zero` to 1. The same player in a scheduled week keeps a non-zero `points_last` and `force_zero` of 0.
- `simulated_capture` and `capture` at the same Thursday match on every column except `as_of_policy`.
- Two builds on the same store match.

```bash
python -m gridiron_lab features --season 2024 --week 1 --policy simulated_capture --data-dir data
```

That command logs the row count and writes nothing.
