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
- Current version: **v0.36**.

## Known Implementation Focus Areas
- Decide what to do with the orphaned `users` / `zone_profiles` tables in Supabase (see Current State).
- Add clearer in-repo instructions for the local-against-cloud dev workflow beyond the README section added here (e.g. a short troubleshooting note for common `st.connection` failures).
- Consider whether `[supabase]` anon/service-role keys should actually gate any behavior, or be removed if they stay permanently unused.
