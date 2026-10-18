# Gridiron Lab

Fantasy-football analytics for the Yahoo league **Fantasy Football Coalition** (ID 893771: 12-team H2H, non-PPR, custom scoring). It forecasts weekly player distributions with point-in-time-correct NFLverse, Vegas and Yahoo data, then recommends start/sit and waiver moves. Every claim is benchmarked against Yahoo's own projections ("The Gridiron Lab Challenge").

This is a ground-up rebuild of the previous tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). That tool remains in production until Gridiron Lab beats it on the plan in [`docs/DESIGN.md`](docs/DESIGN.md).

This public repository holds the code and the GitHub Pages site. Weekly capture data belongs in the private repo `Jpidge/gridiron-lab-data` and is not committed here.

## Status

**M0 — scoring.** The canonical league scoring engine is `src/gridiron_lab/scoring.py`. Rules live in the `[scoring]` table of `gridiron_lab.toml`. Ingestion, features, and models are later milestones (M1–M7).

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

## Planned commands

Later milestones add the `gridiron-lab` CLI:

```bash
cp .env.example .env    # YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN (never committed)
gridiron-lab ingest --seasons 2018-2026
gridiron-lab train
gridiron-lab predict --week next
gridiron-lab site build && python -m http.server -d build/site
```

All configuration lives in `gridiron_lab.toml`. Secrets come only from environment variables or GitHub Actions secrets.

## Layout

```
src/gridiron_lab/     scoring engine, config, logging
tests/                deterministic scoring tests
docs/DESIGN.md        architecture, roadmap, and M0 scoring assumptions
site/                 placeholder for the GitHub Pages dashboard
gridiron_lab.toml     league and scoring table
```
