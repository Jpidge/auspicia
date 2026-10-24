# Gridiron Lab

Fantasy-football analytics for the Yahoo league **Fantasy Football Coalition** (ID 893771: 12-team H2H, non-PPR, custom scoring). It forecasts weekly player distributions with point-in-time-correct NFLverse, Vegas and Yahoo data, then recommends start/sit and waiver moves. Every claim is benchmarked against Yahoo's own projections ("The Gridiron Lab Challenge").

This is a ground-up rebuild of the previous tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). That tool remains in production until Gridiron Lab beats it on the plan in [`docs/DESIGN.md`](docs/DESIGN.md).

This public repository holds the code and the GitHub Pages site. Weekly capture data belongs in the private repo `Jpidge/gridiron-lab-data` and is not committed here.

## Status

**M4.1 — dashboard sparklines.** `python -m gridiron_lab export-dashboard` writes dashboard JSON version 1.1. Player rows carry `recent_points` and `snap_share`; the site draws them as inline sparklines. The contract is [`docs/DASHBOARD_JSON.md`](docs/DASHBOARD_JSON.md). `site/` is the GitHub Pages app (Team, Waivers, League, Lab). The Lab block is the published M3.1 backtest: the candidate was not promoted, and mean-head bias is still outside ±0.3 in seven position-seasons. Yahoo roster sync is M5. The feature contract is in [`docs/FEATURES.md`](docs/FEATURES.md).

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
python -m gridiron_lab features --season 2024 --week 1 --policy simulated_capture --data-dir data
python -m gridiron_lab evaluate --season 2018-2025 --test-season 2022-2025 --data-dir data --output /tmp/eval.json
python -m gridiron_lab export-dashboard \
  --rosters examples/dashboard/rosters.json \
  --output site/dashboard.json
# A real week also passes the point-in-time store:
#   --data-dir data
python -m http.server -d site
```

Open `http://127.0.0.1:8000/`. The page loads `dashboard.json` from the same origin. Opening the HTML file directly cannot fetch that JSON.

`examples/dashboard/rosters.json` is fictional. The page shows an **Example data** badge. Real rosters stay in `Jpidge/gridiron-lab-data` and are not committed here. Pass `--predictions` to override `q10`, `q50`, `q90`, `mean`, and `yahoo` from a model file. The shape of both files is in [`docs/DASHBOARD_JSON.md`](docs/DASHBOARD_JSON.md).

Downloads, the DuckDB file, and the evaluation JSON stay out of git. Re-running a season appends nothing when the payload is unchanged. The features command logs a row count and does not write a file. Evaluate does not write a model file.

## Site

`site/` is static: `index.html`, `app.css`, `app.js`, and `dashboard.json`. No CDN and no third-party request. The first screen (HTML, CSS, and JS, gzipped) stays under 150 KB; `tests/test_dashboard.py` checks that budget and that the script still reads the JSON fields it renders.

The Team view lists every team in the file. The default is `league.my_team_id` (Purple Reign, Yahoo team 10). A choice is stored in `localStorage` under `gl-team`. `#team=10` selects that team for the visit and then saves it. `#waivers`, `#league`, and `#lab` open the other views. The theme is stored under `gl-theme`.

Manager names are the roster file's `manager` field, written through as `manager_display`. Set `[dashboard] anonymize_managers = true` in `gridiron_lab.toml` and re-export to replace every name with `Manager {team_id}`. Team names stay. The page has no hardcoded manager list.

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys `site/` with `actions/deploy-pages` on a push to `main`. Pull request branches do not deploy. The repository's Pages source has to be **GitHub Actions**; that setting is confirmed by the owner. The workflow re-exports the example JSON before upload, so the deployed file matches the exporter. It does not read the private data repo.

## Later commands

Planned with Yahoo integration — **not yet implemented**. The CLI today provides `ingest`, `features`, `evaluate`, and `export-dashboard` only.

```bash
cp .env.example .env    # YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN (never committed)
gridiron-lab train
gridiron-lab predict --week next
```

All configuration lives in `gridiron_lab.toml`. Secrets come only from environment variables or GitHub Actions secrets.

## Layout

```
src/gridiron_lab/     scoring engine, config, logging, data layer, features, models, evaluation, lineup, dashboard, CLI
tests/                scoring, ingestion, leakage, model, lineup, and dashboard tests
docs/DESIGN.md        architecture, roadmap, scoring and data assumptions
docs/SCHEMA.md        canonical tables, clocks, and observed row counts
docs/FEATURES.md      feature contract and column list
docs/DASHBOARD_JSON.md  dashboard JSON version 1.1 and the roster input
examples/dashboard/   fictional roster file for the site
site/                 GitHub Pages dashboard (HTML, CSS, JS, dashboard.json)
gridiron_lab.toml     league, lineup, scoring, and the manager-name flag
```
