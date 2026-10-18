# Canonical tables

M1 stores NFLverse extracts as append-only Parquet and scores weekly lines with the M0 engine. Features read a table only through `open_db(data_dir).as_of(table, ts)`.

## Storage

- Parquet parts: `{data_dir}/raw/{table}/season={season}/*.parquet`
- Rebuildable DuckDB file: `{data_dir}/gridiron.duckdb`
- Release cache: `{data_dir}/cache/nflverse/`
- `data_dir` defaults to the parent of `[paths].raw` in `gridiron_lab.toml` (`data/`). `--data-dir` overrides it.

`players`, `player_xref`, and `identity_issues` are not one NFL season. Their `season` column is 0 and they partition under `season=0`. Every other table uses the NFL season.

Every row has `known_as_of` (UTC, `YYYY-MM-DDTHH:MM:SSZ`), `known_as_of_method`, `ingested_at`, `source` (`nflverse`), and `row_hash`. The hash covers every column except `ingested_at` and itself. A re-run appends nothing when the hash is already stored. A changed payload is a new version.

`as_of(table, ts)` keeps, per entity key, the row with the latest `known_as_of` that is **strictly before** `ts`. Ties break on `ingested_at`, then `row_hash`. It scans the Parquet parts. The DuckDB file is a rebuild of those parts, not a second source of truth.

## Clocks

| Rows | `known_as_of` | Method |
|---|---|---|
| Schedule (teams, week, kickoff; lines and scores null) | 15 May 16:00 UTC of that season | `schedule_released_may_15` |
| Pregame lines (scores null) | kickoff − 1 h | `kickoff_minus_1h` |
| Stats, PBP events, postgame score | kickoff + 6 h | `kickoff_plus_6h` |
| Snap counts | kickoff + 24 h | `kickoff_plus_24h` |
| Injury with a timestamp | that timestamp | `report_timestamp` |
| Injury date with no time | 23:59 America/New_York | `report_date_2359_et` |
| Injury with neither | team-week kickoff | `kickoff_proxy_missing_report_time` |
| Depth chart or roster with `dt` | that timestamp | `snapshot_timestamp` |
| Depth chart or roster with a team game | day before `gameday`, 23:59 Eastern | `day_before_gameday_2359_et` |
| Depth chart or roster with no team game | day before the week's earliest `gameday` | `day_before_week_anchor_2359_et` |
| Player identity | 1 September of `rookie_season` / `entry_year`, Eastern | `entry_year_sept_1_et` |
| Undated crosswalk or DST identity | 1970-01-01 UTC | `crosswalk_undated` or `dst_identity` |

Kickoff is `gameday` + `gametime` interpreted in America/New_York. The 2024 opener (2024-09-05 20:20 Eastern) is `2024-09-06T00:20:00Z`. A missing `gametime` uses 23:59 Eastern on `gameday`.

Positive `spread_line` means the home team is favored. Home implied total = total/2 + spread/2. Away = total/2 − spread/2.

## Tables

| Table | Entity key for `as_of` | What a row is |
|---|---|---|
| `games` | `game_id` | Schedule, then pregame lines, then the final score |
| `players` | `player_id` | GSIS identity and display name. DST ids are `DEF:<TEAM>` |
| `player_xref` | `source_system`, `external_id` | One unambiguous NFLverse id (`yahoo`, `espn`, `pfr`, `sleeper`) |
| `identity_issues` | `source_system`, `external_id` | An external id that maps to more than one GSIS id |
| `player_weeks` | `player_id`, `season`, `week`, `season_type` | Box score, usage, and PBP events, scored with `ffc_yahoo_v1` |
| `pbp_events` | `player_id`, `season`, `week`, `season_type`, `unit` | Play-level counts (`unit` is `player` or `dst`), including dropbacks, scrambles, and red-zone touches |
| `snap_counts` | `player_id`, `game_id` | Offense, defense, and special-teams snaps, plus `offense_pct`, `defense_pct`, and `st_pct` when the file has them |
| `injuries` | `player_id`, `season`, `week`, `season_type` | Latest report for that player-week |
| `depth_charts` | `player_id`, `team`, `position` | Latest chart publication |
| `rosters` | `player_id` | Latest weekly roster status |

`player_weeks.interceptions` is passes thrown on an offensive row and takeaways on a `DEF:` row. The two never share a stat line. `fantasy_points` can be negative. `unavailable` lists scoring fields that were not observed. `opponent_score` on a DST row is the raw opponent total; `points_allowed` is the adjusted estimate in `docs/DESIGN.md` §17.

Play-by-play is not stored play by play. `pbp_events` keeps the counts the scorer and the feature pipeline need: 40+ yard touchdowns (`yards_gained >= 40`), kickoff and punt return touchdowns, offensive fumble-return touchdowns, made field-goal yards, made extra points, team defense/special-teams events, dropbacks, scrambles, and red-zone touches (`yardline_100 <= 20`). A column that is absent from the file leaves that count null for the season.

`player_weeks` also stores `targets`, `carries`, `pass_attempts` (weekly `attempts`), `passing_air_yards`, `receiving_air_yards`, and the three PBP usage counts. Those columns are not passed to the scorer. A weekly column that is absent stays null. DST rows leave usage null.

## Sources

`HttpFetch` reads the nflverse-data release, which is the same layout `nflreadpy` uses. Seasons start at 2018.

| Source | Release path |
|---|---|
| schedules | `schedules/games.parquet` |
| player_stats | `stats_player/stats_player_week_{season}.parquet` |
| snap_counts | `snap_counts/snap_counts_{season}.parquet` |
| injuries | `injuries/injuries_{season}.parquet` |
| depth_charts | `depth_charts/depth_charts_{season}.parquet` |
| rosters | `weekly_rosters/roster_weekly_{season}.parquet` |
| players | `players/players.parquet` |
| pbp | `pbp/play_by_play_{season}.parquet` |

```bash
python -m gridiron_lab ingest --season 2025
python -m gridiron_lab ingest --season 2018-2025 --data-dir data
```

Weekly `passing_40` is ignored. It is not a 40-yard touchdown count. `fg_made_distance` is the sum of made field-goal yards and is preferred when the weekly file has that column. Fumbles lost sum `sack_fumbles_lost`, `rushing_fumbles_lost`, and `receiving_fumbles_lost`.

Snap rows have no GSIS id. They join through `pfr_player_id` and the players crosswalk. Yahoo ids are on weekly rosters, not on `players.parquet`.

## Row counts observed on the release files

Counted from the GitHub release parquet while building M1. They are expectations for a full ingest, not fixture sizes. Play-by-play files were not downloaded here, so `pbp_events` and `player_weeks` row counts are not listed.

| Release file | Rows |
|---|---|
| `schedules/games.parquet`, all game types, 2018 and 2019 | 267 |
| same, 2020 | 269 |
| same, 2021, 2023, 2024, 2025 | 285 |
| same, 2022 | 284 |
| regular season 2018–2020 | 256 |
| regular season 2021, 2023, 2024, 2025 | 272 |
| regular season 2022 | 271 |
| 2024 postseason | WC 6, DIV 4, CON 2, SB 1 |
| 2026 schedule (regular season only in the file) | 272 games; 179 missing `spread_line` and 179 missing `total_line`; kickoff times are present |
| `stats_player_week_2024.parquet` | 18,983 rows, 2,001 players, weeks 1–22 |
| `injuries_2018.parquet` | 5,133 |
| `injuries_2020.parquet` | 5,661 |
| `injuries_2024.parquet` | 6,215 |
| `injuries_2025.parquet` | 6,068 |
| `depth_charts_2024.parquet` | 37,312 rows, 22 weeks |
| `depth_charts_2025.parquet` | 554,215 rows, 221 `dt` values |
| `roster_weekly_2024.parquet` | 46,579 rows, weeks 1–22, 1,841 distinct `yahoo_id` values, no Yahoo id pointed at two GSIS ids |
| `snap_counts_2024.parquet` | 26,615 rows, 2,184 players, weeks 1–22 |
| `players.parquet` | 24,854 rows |

## Stored rows, 2023–2025

Counted in this repo's ingest, not committed. The first load and the later re-ingest both keep every version, so tables whose payload changed grew. `as_of` key counts are the distinct entities, taken at 2030-01-01 UTC.

| Table | Stored after the usage re-ingest | `as_of` keys at 2030-01-01 |
|---|---|---|
| `games` | 2,565 (855 of them are the May schedule version; 285 games × 3 seasons) | 855 |
| `players` | 24,886 | 24,886 |
| `player_xref` | 44,400 | 44,400 |
| `identity_issues` | 9 | 9 |
| `player_weeks` | 117,384 | 58,692 |
| `snap_counts` | 159,262 | 79,631 |
| `injuries` | 17,882 | 17,880 |
| `depth_charts` | 622,445 | 8,692 |
| `rosters` | 139,042 | 4,565 |
| `pbp_events` | 16,727 | 12,649 |

`player_weeks` and `snap_counts` doubled because usage and snap-percent columns changed the row hash. `pbp_events` grew because dropbacks, scrambles, and red-zone touches add player rows that the scoring-only extract skipped. Before that re-ingest the stored counts were games 1,710, player_weeks 58,692, snap_counts 79,631, depth_charts 615,353, pbp_events 4,078.

## Data-quality gaps

- **2018, 2020, and 2024 injuries** have `date_modified` on every row, with a time of day. The span checked for 2018 is `2018-09-03 13:32:37+00` through `2019-02-01 13:02:39+00`.
- **2025 injuries dropped `date_modified`.** The file has report and practice status, team, and week, and no clock. Those rows are stamped at kickoff so they stay invisible before the game.
- **Depth charts changed shape in 2025.** Through 2024 the file is one row per player-week. From 2025 it is a `dt` timestamp (`2025-08-03T10:09:07Z` through `2026-03-14T07:32:09Z` in the file that was counted) plus `pos_rank` / `pos_abb`. Both shapes are accepted.
- **Weekly rosters and pre-2025 depth charts have no snapshot clock.** The day-before-gameday proxy keeps a Thursday 20:00 UTC read from seeing a Sunday chart. It also hides a chart that was actually published earlier in the week.
- **The players file is a current crosswalk.** `rookie_season` is the only date on it. Display names can move backward to that September. `latest_team` and status are not stored. Yahoo ids live on the weekly roster file.
- **2022 has 271 regular-season games, not 272.** 2026 lines are mostly empty. Implied totals are null on those games.
- **DST points allowed is an estimate** until it is checked against Yahoo (DESIGN §15 question 5). Safeties count. Six points are removed for each opponent return or defensive touchdown. The kick after that touchdown is left in.
- **Weekly stats files have 22 rows with a null `player_id` each season** (2023–2025). Those rows are skipped. They are not a failed game join.
- **Depth charts tagged SBBYE have no week.** They stay skipped. A week that does not match that team's game, including week-19 regular-season chart rows, uses the week's earliest kickoff as the anchor.
- **Ambiguous PFR, ESPN, and Yahoo ids stay in `identity_issues`.** Snap rows that only have one of those PFR ids are dropped on purpose.
