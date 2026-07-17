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

## Current State
- The app already has a basic Streamlit login/signup flow.
- There is a local JSON fallback for users and property data.
- Weather fetch logic is integrated with Open-Meteo and cached in local JSON.
- The app currently defaults to local file storage unless a Postgres/Supabase connection is configured.

## likely Architecture to Target
1. Local dev/test path:
   - Run from Anaconda or a local Python environment.
   - Use the local JSON files for quick development and testing.
2. Shared cloud path:
   - Supabase stores users, profiles, and shared property data.
   - Streamlit can run in a hosted environment or locally.
3. Weather and irrigation computation:
   - Use ZIP-code to lat/lon lookup.
   - Fetch weather history and ET information from Open-Meteo.
   - Compute allowable depletion and zone run time based on area, flow, soil, and root depth.

## Runtime Notes
- Keep the app simple and reliable for non-technical users.
- Prefer local JSON persistence during development so the app can still run when external services are not configured.
- Treat Supabase as a future/pluggable user data layer rather than the only source of truth during early setup.

## Immediate Verification Tasks
- Confirm the Python files compile without syntax issues.
- Launch the Streamlit app locally.
- Check whether any startup exceptions occur before the UI loads.

## Known Implementation Focus Areas
- Replace or harden the current ad hoc database connection behavior.
- Finalize the user/property/zone storage model to support multi-user login and shared property ownership.
- Add a simple zone runtime evaluation for each zone based on computed ET demand and flow rate.
- Add clear setup instructions for non-technical local use.
