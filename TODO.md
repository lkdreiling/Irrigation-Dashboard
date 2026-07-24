# Irrigation Dashboard — TODO / Backlog

Running list of fixes, features, and edits we're working through. Check items off as they ship; add new ones anytime (Levi can edit this directly, or hand items to Claude to add).

**Numbering:** item numbers are permanent IDs, assigned once at creation and never reused or renumbered. When something is completed, its line moves down to the ✅ Completed section at the bottom but keeps its original number. New items always get the next unused number (highest number anywhere on this page, +1) — not a recount of the open list.

---

- [ ] 1. 🧮 Calculation Audit
  - **Goal:** Deep-dive into the formulas for soil water deficit, MAD, and daily ET.
  - **Crucial Question:** Are we accounting for extreme weather edge cases (like a sudden 2-inch downpour) without breaking the model?
  - [x] 1a. No crop coefficient (Kc) applied — deficit subtracts raw reference ET0 directly instead of ETc = ET0 × Kc, likely overestimating water need for turf/plantings.
  - [ ] 1b. Rain is credited at 100% with no infiltration-rate cap — a sudden 2" downpour zeroes the deficit even on clay soils where much of it would actually run off.
  - [ ] 1c. Deficit has no upper ceiling — it's floored at 0 but never capped at PAW_total, so an extended drought/fetch gap can inflate current_deficit past what the soil could physically hold.
  - [ ] 1d. Fixed 7-day warmup window before zone_start regardless of soil type — doesn't scale to how fast a given soil actually reaches its AD limit (sandy vs. clay).

- [ ] 2. 📊 Automated Weather & ET Excel Ledger
  - **Goal:** Automatically write daily historic and forecasted weather parameters to an external `.xlsx` sheet.
  - **Crucial Question:** How do we handle writing to the Excel file if the script runs multiple times in one day so we don't get duplicate rows?

- [ ] 5. 🎛️ All-Zones Bird's-Eye Grid
  - **Goal:** A master dashboard page displaying all zones as individual "status cards" at once.
  - **Crucial Question:** How does the UI handle a property with 2 zones versus a property with 16 zones without looking cluttered?

- [ ] 7. 🌦️ One-Click Rain Delay
  - **Goal:** A quick-action button to pause the system for 24–72 hours.

- [ ] 8. 🔮 7-Day Soil-Moisture Forecast
  - **Goal:** A predictive model showing which day of the week a zone is expected to run dry.

- [ ] 9. 📅 Monthly Weather Overview Graphic
  - **Goal:** A visual monthly chart showing rain events, temperature, wind, etc. — something homeowners can glance at to get a feel for expected weather.

- [ ] 10. 💾 Verify Open-Meteo Local JSON Fallback
  - **Goal:** Confirm weather fetches are actually being cached to local JSON, so graphs and tables still have data if we lose connection or hit the Open-Meteo API limit.
  - **Crucial Question:** Is the existing cache actually complete, or are there gaps where a failed fetch silently leaves no local data behind?

- [ ] 11. 🚫 Graceful API-Limit Error Handling
  - **Goal:** Stop a raw API-limit error code from rendering at the top of the page and bricking the whole dashboard — fail gracefully and fall back to cached/local weather data instead.

- [ ] 12. 📱 Mobile Layout Review
  - **Goal:** Check how the dashboard looks/behaves on a phone screen and adjust layout as needed.

- [ ] 13. 📲 Turn Into an Actual Phone App
  - **Goal:** Explore packaging this as a real mobile app rather than just a browser page.
  - **Crucial Question:** What's the right approach — a installable PWA (wraps the existing Streamlit app, least rework), a native wrapper (e.g. Capacitor), or a full native/React Native rebuild? Streamlit itself isn't really built for a proper mobile app, so this likely depends on how far Levi wants to go beyond the current stack.
- [ ] 14. easy and advanced zone profiles?
- [ ] 15. Should we change the zip code selector to an actual address to get coordinates to be more precise?
- [ ] 18. User profile setup with an email and authenticator code? Maybe more safe?
- [ ] 19. Setup security measures..?
- [ ] 20. Can we implement the soil type selector from Web Soil Survey
- [ ] 21. What about percentage of rain as well as a depth in inches? Maybe tell to not irrigate if the chance of rain is high... or just be able to set a threshold?
- [ ] 22. How do we get our app to not lag so much?
  Is it possible to download everything at the beginning, then upload it as needed?
- [ ] 24. We may need to model Van Genutchen curves...?
- [ ] 25. Remove this from sidebar???:
  "🏠 Active Property
  Select Active Property

  Hays
  Manage or add properties from the 🏡 Properties tab above."
- [ ] 26. Got this error when trying to record an irrigation event: "The widget with key "quick_mins" was created with a default value but also had its value set via the Session State API."

---

## ✅ Completed

- [x] 3. 💧 Reset "Water to Apply" on New Property
  - **Goal:** The water-to-apply / deficit tracker should reset to 0" whenever a new property is created, instead of carrying over stale state.
  - **Crucial Question:** Should the UI explicitly tell the user a new property starts at 0" deficit, so they understand the first readings won't reflect real soil conditions until data accumulates?

- [x] 4. 📖 Homeowner Flow-Rate (GPM) Guide
  - **Goal:** An interactive instructional page showing how to calculate zone GPM using the water meter or nozzle math.
  - **Crucial Question:** How do we keep the math dead-simple for a non-technical homeowner without sacrificing the precision your model needs?

- [x] 16. Is there a way to get an average flow rate of rotors/sprays/drip? Maybe select the amount/type of heads on a zone and it autofills the flow and area? Make this the easy zone profile

- [x] 17. Make sure property/zone info is not bleeding over into other user profiles — audited: every cloud query (properties/zones/watering_logs, incl. rename/delete) filters by `user_id`, and local JSON fallback paths are isolated per user via `data_manager.get_user_paths`/`get_prop_paths_for_user`. No bleed found; only `weather_cache`/`weather_fetch_meta` are shared, and that's intentional (lat/lon-keyed, not private data).

- [x] 27. After logging a watering event on a new zone, it autopopulated the 'water to apply' to a large number. this should start accumulating as daily ETs happen. — fixed: the deficit rolling window was anchored 7 days before `zone_start_date`, so the very first log pulled in a week of pre-existing ET debt. Now anchored at the date of the first actual watering log instead.

- [x] 28. Delete button for properties in the props tab. — added: each property row in the Properties tab now has a 🗑️ Delete button that removes it (and its zones/watering logs) from the cloud DB and local JSON, matching the existing zone-delete pattern.

- [x] 29. Zone rename wasn't updating watering_logs in the cloud DB — the UPDATE query referenced a nonexistent `property` column (table is `property_name`), silently failing inside a bare `except: pass`. Found during the item 17 audit; fixed the column name.

- [x] 6. 🧼 Smart Cycle-Soak Recommendations — the Reference tab already compared a zone's Precipitation Rate against its soil's published intake rate (`core_logic.INFILTRATION_DATA`) to recommend a cycle count, but the main Dashboard tab's day-to-day runtime callouts (both the normal "Irrigation Plan" box and the "New Zone" full-soak estimate) didn't show it. Factored the comparison into a shared `core_logic.recommend_cycle_soak()` helper and now both Dashboard callouts recommend a specific cycle count/soak schedule whenever PR exceeds the soil's intake rate, matching the Reference tab.

- [x] 23. Need to figure out infiltration rate of the soil types and suggest a cycle soak if the runtime is longer than the infil rate. — same fix as #6 above; `INFILTRATION_DATA` (Ksat by USDA texture class) already existed in `core_logic.py`, this pass surfaced the resulting cycle recommendation on the main Dashboard tab, not just the Reference tab.