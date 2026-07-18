# Irrigation Head Spec Database — Sources & Methodology

Compiled 2026-07-18. Scope agreed with Levi: **core residential lineup** (the handful of
product families that cover most home installs, not each manufacturer's full catalog),
across four head types — spray, rotor, rotary/multi-stream, drip.

## What's here

- `head_database.csv` — structured spec data extracted from the PDFs below. One row per
  nozzle/model at one representative operating pressure (usually the manufacturer's stated
  "recommended" or "optimum" pressure, or 30 psi where the source didn't call one out) and,
  for spray/rotor/rotary rows, the largest arc available (360° where offered). The full
  source PDFs have many more pressure/arc combinations if finer granularity is ever needed —
  this table intentionally picks one row per nozzle rather than reproducing entire charts.
- `RainBird/`, `Hunter/`, `Toro/`, `Orbit/` — the actual downloaded manufacturer PDFs the
  CSV was transcribed from, for traceability and re-verification.

## Column notes

- `Radius_ft` / `Flow_GPM` / `Precip_in_hr` apply to spray, rotor, and rotary rows.
- `Emitter_GPH` / `Emitter_Spacing_in` apply to drip rows instead.
- `Notes` flags anything that isn't a like-for-like comparison with the rest of the table
  (range-only data, a value pulled from search results rather than a downloaded PDF, etc.)
- `Source_File` points to the file in this folder the row came from. `(not downloaded - see
  notes)` means the source site blocked automated PDF access and the numbers are corroborated
  from the official product page plus multiple independent distributor listings instead —
  flagged in `Notes` on those rows.

## Per-manufacturer notes

**Rain Bird** — all four PDFs downloaded directly from rainbird.com without issue.

**Hunter** — hunterirrigation.com returned HTTP 403 to every direct download attempt (even
with a browser user-agent), so the Pro-Spray, PGP Ultra, and MP Rotator sheets were sourced
from third-party distributor mirrors (dripworks.com, dbcirrigation.com) instead. The dollar
figures matched what the search summaries described from the official pages, so treated as
reliable — but if these ever need re-verification, expect the same 403 from hunterirrigation.com
directly. The HDL dripline flow rates (0.4/0.6/0.9 GPH) could not be pulled from a primary PDF
for the same reason; they're corroborated instead via the official product page's spec bullets
plus multiple independent distributor listings (search results, not a saved PDF).

**Toro** — all downloaded cleanly from media.toro.com / cdn2.toro.com / a dbcirrigation.com
mirror. The T5 rotor sheet is a "bidding specification" document, not a full pressure/radius/
GPM performance chart like the other rotor lines — it only gives aggregate ranges (e.g.
"1.15-9.70 GPM over a 33'-50' radius" for the whole standard-angle nozzle set), so the two T5
rows in the CSV are range-level, not per-nozzle-size like every other rotor row.

**Orbit** — positioned as the consumer/DIY brand of the four (their own spec sheet says their
spray nozzles are cross-compatible with Rain Bird and Hunter bodies). The adjustable spray
nozzle chart downloaded cleanly and is full performance data. The Voyager II rotor numbers are
summary-level, assembled from product-page/search descriptions rather than a downloaded
per-pressure chart — flagged in the CSV. Orbit does not appear to sell a dedicated matched-
precipitation multi-stream rotary nozzle (Hunter MP Rotator / Rain Bird R-VAN / Toro Precision
Rotating Nozzle equivalent) — noted as a single placeholder row rather than fabricated.

## Known gaps / next steps if this gets extended

- Every "representative pressure" row is a simplification of a full pressure/radius/GPM chart
  in the source PDF. If a specific install needs a different operating pressure, go back to
  the source PDF in the manufacturer's folder.
- Hunter HDL and Orbit Voyager II / drip emitters don't have a locally saved source PDF —
  worth revisiting if hunterirrigation.com/orbitonline.com stop blocking automated fetches, or
  if someone can grab the PDF manually and drop it in the relevant folder.
- Toro's newer Precision Spray Nozzle (PSN) line was downloaded (`Toro_Precision_Spray_Nozzles_Charts.pdf`)
  but not transcribed into the CSV — MPR Plus already covers Toro's spray category and PSN is
  largely its pressure-compensating successor; add it if PSN specifically becomes relevant.
