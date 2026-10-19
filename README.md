# Gridiron Lab

Fantasy-football analytics for the Yahoo league **Fantasy Football Coalition** (ID 893771: 12-team H2H, non-PPR, custom scoring). It forecasts weekly player distributions with point-in-time-correct NFLverse, Vegas and Yahoo data, then recommends start/sit and waiver moves. Every claim is benchmarked against Yahoo's own projections ("The Gridiron Lab Challenge").

This is a ground-up rebuild of the previous tool ([Jpidge/Fandromeda2](https://github.com/Jpidge/Fandromeda2)). That tool remains in production until Gridiron Lab beats it on the plan in [`docs/DESIGN.md`](docs/DESIGN.md).

This public repository holds the code and the GitHub Pages site. Weekly capture data belongs in the private repo `Jpidge/gridiron-lab-data` and is not committed here.

## Status

**M5a — Yahoo client, no live credentials yet.** `python -m gridiron_lab yahoo-auth` stores the first refresh token locally. `python -m gridiron_lab yahoo-capture` reads league 893771 (settings, teams, weekly rosters, projections, scoreboard, transactions) and can replay fictional fixtures with `--fixture-dir`. The Thursday workflow is [`.github/workflows/yahoo-capture.yml`](.github/workflows/yahoo-capture.yml). It is a no-op until `YAHOO_CLIENT_ID` exists, and it commits snapshots only to the private repo `Jpidge/gridiron-lab-data`. The dashboard is still JSON 1.1. The feature contract is in [`docs/FEATURES.md`](docs/FEATURES.md).

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

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) deploys `site/` with `actions/deploy-pages` on a push to `main`. Pull request branches do not deploy. The repository's Pages source has to be **GitHub Actions**; that setting is confirmed by the owner. The workflow re-exports JSON before upload. It uses the latest `roster_for_dashboard.json` from `Jpidge/gridiron-lab-data` only when `DATA_REPO_TOKEN` or `DATA_REPO_DEPLOY_KEY` is set on this repo. Otherwise it keeps the fictional example. The private file is an Actions checkout, not a commit to this public repo.

## Yahoo

Secrets are environment variables or GitHub Actions secrets. Names:

| Name | Where it goes |
|---|---|
| `YAHOO_CLIENT_ID` | Local `.env` and the `Jpidge/gridiron-lab` Actions secret |
| `YAHOO_CLIENT_SECRET` | Same |
| `YAHOO_REFRESH_TOKEN` | Same. The helper writes the first value to a file; you paste it into the secret |
| `YAHOO_TOKEN_FILE` | Optional local path. Default is `yahoo_refresh_token`, which is gitignored |
| `YAHOO_SECRETS_ADMIN_TOKEN` | Optional. Actions secret that may update `YAHOO_REFRESH_TOKEN` when Yahoo rotates it |
| `DATA_REPO_TOKEN` | Optional. Contents write on `Jpidge/gridiron-lab-data` |
| `DATA_REPO_DEPLOY_KEY` | Optional write deploy key for that private repo |

Nothing in that list is committed. `.env.example` has empty values. Logs must not contain tokens, and the capture writes the refresh token only to the token file (mode 0600).

Once the Yahoo app codes exist:

1. Create a Yahoo developer app with Fantasy Sports read access. Set the redirect URI to `http://127.0.0.1:8085/callback`.
2. Copy `.env.example` to `.env`. Fill in `YAHOO_CLIENT_ID` and `YAHOO_CLIENT_SECRET`. Leave `YAHOO_REFRESH_TOKEN` empty until the next step.
3. Run `python -m gridiron_lab yahoo-auth`. Open the printed URL, approve access, and let the local callback finish. The command writes the refresh token to `yahoo_refresh_token` (or `YAHOO_TOKEN_FILE`) and prints that path. It does not print the token.
4. On `Jpidge/gridiron-lab`, add Actions secrets `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, and `YAHOO_REFRESH_TOKEN` (the file from step 3).
5. Add `DATA_REPO_TOKEN` (contents write on `Jpidge/gridiron-lab-data`) or `DATA_REPO_DEPLOY_KEY` (a write deploy key for that repo). Without one of those, a live capture runs and then logs that it could not store the snapshot.
6. Optional: add `YAHOO_SECRETS_ADMIN_TOKEN` if Yahoo returns a new refresh token and Actions should write it back with `gh secret set YAHOO_REFRESH_TOKEN --body-file`. Without it, the workflow logs that the secret was not updated and does not print the new token.

The Thursday workflow (20:00 UTC, plus `workflow_dispatch`) stays installed and exits 0 while `YAHOO_CLIENT_ID` is empty. Replay a capture without credentials:

```bash
python -m gridiron_lab yahoo-capture \
  --fixture-dir tests/fixtures/yahoo \
  --output /tmp/yahoo-capture \
  --data-dir /tmp/yahoo-data
```

Those fixtures are fictional. They are not a copy of league 893771. The first live run writes three reconciliation reports: DST points-allowed tiers versus `gridiron_lab.toml` (the exclusion question stays unanswered until a played game can be compared), whether week W−1 projections came back, and which Yahoo stat modifiers match the toml. Betting lines are not captured. See `docs/DESIGN.md` section 21.

## Later commands

```bash
gridiron-lab train
gridiron-lab predict --week next
```

All configuration lives in `gridiron_lab.toml`. Secrets come only from environment variables or GitHub Actions secrets.

## Layout

```
src/gridiron_lab/     scoring engine, config, logging, data layer, features, models, evaluation, lineup, dashboard, Yahoo client, CLI
tests/                scoring, ingestion, leakage, model, lineup, and dashboard tests
docs/DESIGN.md        architecture, roadmap, scoring and data assumptions
docs/SCHEMA.md        canonical tables, clocks, and observed row counts
docs/FEATURES.md      feature contract and column list
docs/DASHBOARD_JSON.md  dashboard JSON version 1.1 and the roster input
examples/dashboard/   fictional roster file for the site
site/                 GitHub Pages dashboard (HTML, CSS, JS, dashboard.json)
gridiron_lab.toml     league, lineup, scoring, and the manager-name flag
```
