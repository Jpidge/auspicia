# Gridiron Lab

Fantasy-football analytics for the Yahoo league **Fantasy Football Coalition** (ID 893771: 12-team H2H, non-PPR, custom scoring). It forecasts weekly player distributions with point-in-time-correct NFLverse, Vegas and Yahoo data, then recommends start/sit and waiver moves. Every claim is benchmarked against Yahoo's own projections ("The Gridiron Lab Challenge").

This is a ground-up rebuild of the previous tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). That tool remains in production until Gridiron Lab beats it on the plan in [`docs/DESIGN.md`](docs/DESIGN.md).

This public repository holds the code and the GitHub Pages site. Weekly capture data belongs in the private repo `Jpidge/gridiron-lab-data` and is not committed here.

## Status

**M1 — data layer.** Append-only Parquet under a configurable data directory, a rebuildable DuckDB file, and `open_db(data_dir).as_of(table, ts)`. NFLverse schedules, weekly stats, snaps, injuries, depth charts, weekly rosters, player ids, and play-by-play events load through `python -m gridiron_lab ingest`. The schema and the `known_as_of` rules are in [`docs/SCHEMA.md`](docs/SCHEMA.md). Features, models, Yahoo, and the dashboard are later milestones.

## Quick start

```bash
git clone https://github.com/Jpidge/gridiron-lab && cd gridiron-lab
python -m venv .venv && source .venv/bin/activate     # Python 3.11+
pip install -e ".[dev]"
pytest
ruff check .
```

Score a stat line:

```python
from gridiron_lab.scoring import score_offense

result = score_offense(
    {
        "passing_yards": 250,
        "passing_tds": 2,
        "interceptions": 1,
    }
)
print(result.fantasy_points)  # 20.0; negative totals are kept
```

## Ingest

```bash
python -m gridiron_lab ingest --season 2025
python -m gridiron_lab ingest --season 2018-2025 --data-dir data
```

Downloads and the DuckDB file stay in that data directory and are gitignored. Re-running a season appends nothing when the payload is unchanged.

## Later commands

```bash
cp .env.example .env    # YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN (never committed)
gridiron-lab train
gridiron-lab predict --week next
gridiron-lab site build && python -m http.server -d build/site
```

All configuration lives in `gridiron_lab.toml`. Secrets come only from environment variables or GitHub Actions secrets.

## Layout

```
src/gridiron_lab/     scoring engine, config, logging, data layer, ingest CLI
tests/                scoring tests and offline ingestion fixtures
docs/DESIGN.md        architecture, roadmap, scoring and data assumptions
docs/SCHEMA.md        canonical tables, clocks, and observed row counts
site/                 placeholder for the GitHub Pages dashboard
gridiron_lab.toml     league and scoring table
```
