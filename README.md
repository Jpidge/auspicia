# AUSPICIA

Fantasy-football analytics for the Yahoo league **Fantasy Football Coalition** (ID 893771: 12-team H2H, non-PPR, custom scoring). It forecasts weekly player distributions with point-in-time-correct NFLverse, Vegas and Yahoo data, then recommends start/sit and waiver moves. Every claim is benchmarked against Yahoo's own projections ("The AUSPICIA Challenge").

This is a ground-up rebuild of the previous tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). That tool remains in production until AUSPICIA beats it on the plan in [`docs/DESIGN.md`](docs/DESIGN.md).

This public repository holds the code and the GitHub Pages site; weekly capture data is not committed here.

## Status

**M4.1 — dashboard sparklines.** `python -m gridiron_lab export-dashboard` writes dashboard JSON version 1.1. Player rows carry `recent_points` and `snap_share`; the site draws them as inline sparklines. The contract is [`docs/DASHBOARD_JSON.md`](docs/DASHBOARD_JSON.md). `site/` is the GitHub Pages app (Team, Waivers, League, Lab). The Lab block is the published M3.1 backtest: the candidate was not promoted, and mean-head bias is still outside ±0.3 in seven position-seasons. Yahoo roster sync is M5. The feature contract is in [`docs/FEATURES.md`](docs/FEATURES.md).

## Quick start

```bash
git clone https://github.com/Jpidge/auspicia && cd auspicia
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
python -m gridiron_lab predict --season 2026 --week 8 --rosters rosters.json --data-dir data --output predictions.json
python -m gridiron_lab export-dashboard \
  --rosters examples/dashboard/rosters.json \
  --output site/dashboard.json
# A real week also passes the point-in-time store:
#   --data-dir data
python -m http.server -d site
```

Open `http://127.0.0.1:8000/`. The page loads `dashboard.json` from the same origin. Opening the HTML file directly cannot fetch that JSON.

`examples/dashboard/rosters.json` is fictional. The page shows an **Example data** badge. The live site publishes the real league (the owner's decision, 2026-10-24): real dashboard exports are committed through pull requests, while raw captures stay in `Jpidge/gridiron-lab-data`. Pass `--predictions` to override `q10`, `q50`, `q90`, `mean`, and `yahoo` from a model file, and to stamp `vegas_implied` (the implied team total `predict` computes for the target week), `opp_rank` (the opposing defense's rank against the player's position, season-to-date, 1 = toughest), and `practice` (FP/LP/DNP when the player is on the injury report) — build one with the `predict` command. The shape of both files is in [`docs/DASHBOARD_JSON.md`](docs/DASHBOARD_JSON.md).

Downloads, the DuckDB file, and the evaluation JSON stay out of git. Re-running a season appends nothing when the payload is unchanged. The features command logs a row count and does not write a file. Evaluate does not write a model file.

## Site

`site/` is static: `index.html`, `app.css`, `app.js`, and `dashboard.json`. No CDN and no third-party request. The first screen (HTML, CSS, and JS, gzipped) stays under 150 KB; `tests/test_dashboard.py` checks that budget and that the script still reads the JSON fields it renders.

The Team view lists every team in the file. The default is `league.my_team_id` (Purple Reign, Yahoo team 10). A choice is stored in `localStorage` under `gl-team`. `#team=10` selects that team for the visit and then saves it. `#waivers`, `#league`, and `#lab` open the other views. The theme is picked from the header dropdown — Rowan Light, Aqua Night, Midnight, Carbon, plus the original Light and Dark — and is stored under `gl-theme`.

The dashboard carries team names and players only. Manager names are not exported or displayed.

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys `site/` with `actions/deploy-pages` on a push to `main`. Pull request branches do not deploy. The repository's Pages source has to be **GitHub Actions**; that setting is confirmed by the owner. The workflow publishes the committed `site/` as-is; dashboard updates land through pull requests. It does not read the private data repo.

## Predict

`predict` fits `GridironPredictor` on every earlier week in the store (the backtest's training filter and feature builder, unchanged) and writes one week's predictions for the players in a roster file, keyed by the roster file's `player_id`s for `export-dashboard --predictions`. Roster players are matched to store identities by normalized name and position, with team as tiebreak; anyone who cannot be resolved uniquely is listed under `unmatched`, never guessed. There is no persisted model artifact by design — each run fits fresh through the target week's Thursday capture, so a prediction can never come from a stale fit. Two honest limits: the backtest's conformal interval calibration is not applied yet (the output says `"conformal_adjusted": false`; q50 and mean are unaffected), and waivers are not predicted yet.

Still planned with Yahoo integration — **not yet implemented**: a standalone `train` command and live Yahoo capture.

```bash
cp .env.example .env    # YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET / YAHOO_REFRESH_TOKEN (never committed)
gridiron-lab train
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
gridiron_lab.toml     league, lineup, and scoring
```
