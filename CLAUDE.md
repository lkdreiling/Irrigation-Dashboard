# CLAUDE Working Notes

## Project Goal
Build a workable irrigation dashboard that:
- pulls weather data from Open-Meteo,
- uses Streamlit for the browser UI,
- uses Supabase as the shared user/profile hub,
- supports a local Python/Anaconda development path for testing,
- lets end users sign up, create properties, define irrigation zones, and calculate runtime minutes for each zone.

## Current Workspace
- Main app entrypoint: `et_dashboard.py`
- Core irrigation math: `core_logic.py`
- Data persistence helpers: `data_manager.py`
- Local launch script: `run_irrigation_dashboard.bat`
- Local-only secrets (gitignored, never committed): `.streamlit/secrets.toml` — holds the Supabase API keys and the Postgres connection string. `streamlit_secrets.example.toml` is the template.

## Current State
- The cloud path is live, not just planned: the app is deployed on **Streamlit Community Cloud**, backed by a real Supabase Postgres database. Secrets on that deployment are managed separately, in Streamlit Cloud's own secrets manager (not from this repo).
- On startup, `et_dashboard.py` connects via `st.connection("postgresql", type="sql")` and auto-creates its schema with `CREATE TABLE IF NOT EXISTS`: `app_users`, `properties`, `zones`, `watering_logs`, `weather_cache`, `weather_fetch_meta`.
- If that connection isn't configured or fails, the app falls back automatically to local JSON storage (`MockConnection` in `et_dashboard.py`, helpers in `data_manager.py`). The sidebar always shows which mode is active: "☁️ Cloud sync active" vs "💾 Local-only mode (DB not connected)".
- Local runs can now also point at the same live Supabase database by populating `.streamlit/secrets.toml` locally — see README's "Running Locally Against the Shared Cloud Database" section. This is how you get a local dev session sharing real data with the production deployment.
- **Known orphaned data:** the Supabase project also contains two tables that no code in this repo reads or writes — `users` (1 row) and `zone_profiles` (60 rows). They're not created by the current `CREATE TABLE IF NOT EXISTS` block, so they're leftovers from an earlier schema iteration (probably pre-dating the `app_users`/`zones` naming). `zone_profiles` has real row data — don't drop either table without checking with the user first, in case it's recoverable data rather than pure cruft.
- **Confirmed bug (2026-07-18): `properties` and `watering_logs` cloud sync silently no-ops.** The live Supabase `properties` table predates the current code and is shaped for a different, older design — `user_id UUID` (with a lone `UNIQUE` constraint, i.e. one-property-per-user) plus a `bigint id` PK — instead of the code's `(user_id VARCHAR, property_name VARCHAR, zip_code VARCHAR)` composite-PK schema. The live `watering_logs` table is similarly UUID-keyed and uses a column named `property` instead of `property_name`. Because `CREATE TABLE IF NOT EXISTS` is a no-op against tables that already exist under those names, these two were never migrated. `st.session_state.user_id` is always a plain username string (never a UUID), so every insert/select against these two tables throws (invalid UUID input, or "column does not exist") — and every call site wraps the DB call in a bare `except Exception: pass`, so the failure is invisible. Net effect: property zip codes and watering logs have been running on the local-JSON fallback this whole time, even while the sidebar reports "☁️ Cloud sync active". `zones` is unaffected — it didn't collide with a pre-existing table, so it was created fresh with the correct schema and genuinely syncs to Supabase (verified: 9 real rows for the `admin` test account). Both `properties` and `watering_logs` had 0 rows in production at time of discovery, so no data is at risk in fixing this — the fix (rename the two legacy tables aside, let the app's own `CREATE TABLE IF NOT EXISTS` create correctly-shaped replacements) is written up and ready in a prior conversation, but was **not applied** — user chose to leave it as-is for now. Revisit before assuming cloud sync fully covers properties/logs.

## Architecture (confirmed working, not just planned)
1. **Local dev/test path:**
   - Run via Anaconda (`%USERPROFILE%\anaconda3\python.exe`, confirmed present on this machine) or `run_irrigation_dashboard.bat`.
   - No `.streamlit/secrets.toml` → local JSON fallback automatically.
   - `.streamlit/secrets.toml` populated with the Postgres URL → same shared cloud data as production, run locally.
2. **Shared cloud path (live in production):**
   - Streamlit Community Cloud hosts the deployed app. Deploying changes = push to GitHub; Streamlit Cloud rebuilds from the connected repo/branch.
   - Supabase Postgres is the single source of truth for users/properties/zones/logs/weather cache, shared across every deployment and every local run pointed at it.
   - Streamlit Cloud's free tier sleeps the app on idle and auto-wakes it (~30s) on the next visit — no local machine needs to be running for other users to reach the hosted app.
   - Per-browser Streamlit session state stays isolated per user session; only the underlying Postgres data is shared.
3. **Weather and irrigation computation:**
   - Use ZIP-code to lat/lon lookup.
   - Fetch weather history and ET information from Open-Meteo, cached server-side in `weather_cache`/`weather_fetch_meta` (shared across users/zones at the same location) as well as locally.
   - Compute allowable depletion and zone run time based on area, flow, soil, and root depth.

## Runtime Notes
- Keep the app simple and reliable for non-technical users.
- Local JSON persistence remains the safety net when Postgres isn't configured or reachable — don't remove that fallback.
- The `[supabase]` secrets block (`url`/`anon_key`/`service_role_key`) is read into `SUPABASE_CONFIG`/`SUPABASE_READY` but that flag isn't consumed anywhere else in the code today — only `[connections.postgresql]` actually drives functionality. Don't assume filling in the `[supabase]` block alone enables cloud storage.

## Versioning
- The version string is tracked in two places and must be bumped together:
  - `README.md` title (line 1)
  - Footer string in `et_dashboard.py` (`f"Irrigation Dashboard • {mode_text} • vX.XX"`, near the end of the file)
- **Shipped** (live on `main`, in production): **v0.36**. **In development** (current work branch): **v0.37**. These two numbers will usually differ — `main`'s version only advances when a version branch is merged in per the "Standard merge-to-main sequence" below.
- When bumping, grep for the *old* version number across `*.py`/`*.md` (`v0\.3[0-9]`-style pattern) and update only the two authoritative "current version" declarations (README title, `et_dashboard.py` footer) plus this line — leave historical mentions alone (e.g. the branch-model list below naming past versions like `v0.33, v0.34, v0.35...`, or dated notes about a specific past merge). Those are historical record, not the live version.

## Git / Push Workflow
- **Git is not on PATH / not separately installed on this machine.** The user pushes via **GitHub Desktop**, which bundles its own `git.exe`. Locate it with a glob rather than hardcoding a version:
  `Glob pattern: C:\Users\<user>\AppData\Local\GitHubDesktop\app-*\resources\app\git\cmd\git.exe` — pick the highest `app-x.y.z` folder. Call it directly (e.g. `& $git -C "c:\Irrigation-Dashboard" status`); there's no need to install a separate system Git unless the user asks for one.
- **Branch model:** `main` is the deploy branch — **Streamlit Community Cloud auto-redeploys from `main` on push**, so pushing to `main` is a production action. Day-to-day work happens on version branches (`v0.33`, `v0.34`, `v0.35`, `v0.36`, `v0.37`, ...). To ship: merge the current version branch into `main` and push `main`. Always confirm with the user before pushing to `main` specifically — pushing a version branch is lower-stakes, pushing `main` redeploys production.
- **Known gotcha — spurious conflicts from squash merges:** PRs into `main` on this repo are merged via GitHub's "Squash and merge," which collapses a whole version branch into a single commit on `main`. A later `git merge <version-branch>` into `main` can then show conflicts in places where the actual text is identical, because git can't tell that the squash commit and the individual commits produced the same content. Before manually reconciling a conflict, check whether it's spurious:
  `git diff <main-only-commit>:<file> <equivalent-commit-on-branch>:<file>`
  If that's empty, the version branch is a strict superset for that file — resolve with `git checkout --theirs -- <file>` instead of hand-editing conflict markers. (This is exactly what happened merging v0.36 into main on 2026-07-18: `main`'s `fc62797` and v0.36's `4adf548` were byte-identical for `et_dashboard.py`, confirming `v0.36` was safe to take wholesale.)
- **Files that should never be hand-merged, only deleted/untracked on conflict:** `.cache.sqlite` (binary Open-Meteo request cache) and per-user files under `IrrigationData/` (e.g. `Home_weather.json`). Both are regenerable runtime data, already covered by `.gitignore`, but remain tracked from before those ignore rules existed. On conflict, resolve with `git rm` / `git rm --cached`, not a content merge.
- **If `git rm .cache.sqlite` fails with `unable to unlink ... Invalid argument`:** a locally running Streamlit process has the file locked. Stop it first (stop the background task, or kill the `streamlit run` process), then retry.
- **Before finalizing any merge:** run `python -m py_compile et_dashboard.py` (via `%USERPROFILE%\anaconda3\python.exe`) to confirm the resolved file is syntactically valid, and grep the repo for leftover conflict markers (`^<<<<<<<|^=======$|^>>>>>>>`) to make sure nothing was missed, before `git commit` / `git push`.
- **Standard merge-to-main sequence (shipping a finished version branch):**
  1. `git fetch origin`; confirm local `main` matches `origin/main` (no commits either direction) before touching anything.
  2. `git checkout main`
  3. `git merge <version-branch> --no-ff -m "Merge branch '<version-branch>' into main"`
  4. Resolve conflicts per the rules above.
  5. Compile-check + conflict-marker grep.
  6. `git commit --no-edit` (or with message if not already mid-merge-commit), then `git push origin main` — always confirm with the user before this specific push, since it redeploys production.
- **Starting a new version branch (this is the standard "get ready for the next version" process, used going forward):**
  1. `git fetch origin`; confirm local `main` matches `origin/main` before branching, same check as above.
  2. `git checkout -b vX.YY main` (branch from `main`, not from the previous version branch, so it starts from what's actually shipped).
  3. `git push -u origin vX.YY` to set up tracking immediately, even before any content changes.
  4. Bump the version string per the Versioning section above (README title, `et_dashboard.py` footer, and this file's "In development" line) — grep for the old version number first to find every spot, but only touch the live "current version" declarations, not historical mentions.
  5. Compile-check (`python -m py_compile et_dashboard.py`), then commit and `git push origin vX.YY`. Pushing a version branch is low-stakes (doesn't touch `main`/production) and doesn't need the same confirmation as a `main` push.

## Known Implementation Focus Areas
- **Fix the `properties`/`watering_logs` schema mismatch** (see Current State) — rename the two legacy UUID-schema tables aside (e.g. `properties_legacy_uuid`, `watering_logs_legacy_uuid`; both empty, so this is non-destructive) and let the app's `CREATE TABLE IF NOT EXISTS` create correctly-shaped replacements matching the code. Needs to run against the live Supabase DB, so confirm with the user before executing — this exact fix was scoped and ready but deferred at the user's request on 2026-07-18.
- Decide what to do with the orphaned `users` / `zone_profiles` tables in Supabase (see Current State).
- Add clearer in-repo instructions for the local-against-cloud dev workflow beyond the README section added here (e.g. a short troubleshooting note for common `st.connection` failures).
- Consider whether `[supabase]` anon/service-role keys should actually gate any behavior, or be removed if they stay permanently unused.
