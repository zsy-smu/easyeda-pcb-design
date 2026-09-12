# Manufacturing export checker

Use `scripts/check_manufacturing.py` to compare an existing Gerber/Excellon ZIP against an independently established expected geometry manifest. It reads inputs, does not extract the ZIP, does not invoke EDA, and writes only `--out` (creating its parent directories when needed). Input paths, symbolic aliases and hardlink aliases are protected from report overwrite. It never installs packages or changes the environment.

```text
python scripts/check_manufacturing.py EXPORT.zip --expected EXPECTED.json --out REPORT.json
python -m unittest discover -s tests -p test_manufacturing.py -v
```

Python 3.10+ is required. Geometry checks use normal imports of **Shapely >=2,<3**, declared in `scripts/requirements-manufacturing.txt`. This is optional for the rest of the skill and necessary for this checker. If unavailable, the CLI returns a helpful JSON dependency error with exit 2. Use an environment where the dependency is already available, or separately follow the user's environment setup authorization. No absolute dependency path is embedded in the helper or tests.

| Exit | JSON status | Meaning |
|---|---|---|
| 0 | `PASS`, `checked: true` | Supported geometry and layer inventory checks match the declared expected manifest; no known plating mismatch. |
| 1 | `FAIL`, `checked: true` | Inputs were interpreted, and one or more supported checks failed. |
| 2 | `ERROR`, `checked: false` | Invalid/unsupported input, unknown syntax, unavailable dependency, or read/write error prevented a complete check. |

Stdout is a compact JSON summary. The report contains file hashes, matched/missing/extra/ambiguous drill occurrences, outline comparison, layer identities, plating status, and any special drill-file role proof. A `PASS` does **not** mean manufacturing release, native-save verification, functional qualification, or full copper connectivity/DRC verification. **Unknown plating is explicitly unverified**, even when the supported geometric checks pass. A known plating mismatch always fails.

## Expected manifest

Start from [the synthetic example](../assets/examples/manufacturing-expected.json), replacing its values with the current design's requirements or reviewed source geometry. Do not derive the expectation solely from the same export being checked. `schema` is descriptive metadata; validation follows the fields below, so an older manifest with compatible fields may be used without project-specific binding flags.

| Field | Contract |
|---|---|
| `board.widthMm`, `board.heightMm` | Required finite positive dimensions of the expected outline envelope, in mm. They must agree with its point envelope within `toleranceMm`; conversion roundoff is permitted. |
| `board.outlinePointsMm` | Required ordered `[xMm, yMm]` vertices of one simple closed outer contour, including an identical final copy of the first point. Canvas axes are X right and Y down. Coordinates may use any common canvas origin. Points and holes must share that frame. |
| `board.expectedCopperLayers` | Required integer 2 through 64; supports 2, 4, 6 and other layer counts without a fixed stackup. Legacy top-level `expectedCopperLayers` is accepted only when the board field is absent. |
| `holes` | Required nonempty list. Zero-drill expectations are explicitly unsupported. Every physical round hole or slot must occur once, with a unique `id` and unique geometry; no silent deduplication. |
| `holes[].id` | Required nonempty unique string. |
| `holes[].xMm`, `yMm` | Required finite hole/slot center coordinates in the board's canvas frame. |
| `holes[].shape` | `ROUND` or `SLOT`. |
| `holes[].diameterMm` | Required positive drill diameter or slot width. |
| `holes[].totalLengthMm` | Required positive total end-to-end opening length. For `ROUND`, equals the diameter; for `SLOT`, exceeds the diameter. Centerline endpoint separation is length minus diameter. |
| `holes[].rotationCanvasDeg` | Required finite angle in degrees. A slot's long axis starts along positive canvas X and rotates toward positive canvas Y. Round-hole rotation is geometrically irrelevant. |
| `holes[].plated` | Optional Boolean. `true` expects metallized; `false` expects nonmetallized. Only explicit Excellon `;TYPE=PLATED` / `;TYPE=NON_PLATED` can verify this. Missing declaration or expectation stays unknown. |
| `holes[].kind` | Optional descriptive string; `via` or legacy `via-drill` identifies vias **only** for the optional subset proof. |
| `toleranceMm` | Optional positive comparison tolerance, default 0.003 mm; values above 0.01 mm are unsupported. Applied to outline geometry/dimensions and drill positions, diameters and slot endpoints. |
| `drillFileRoles` | Optional object with exact ZIP entry names `fullPTH` and `viaSubset`; requests the proof below, never unconditional deduplication. |
| `provenance`, `reviewEvidence` | Optional JSON metadata, copied under `suppliedEvidence` with `SUPPLIED_NOT_INDEPENDENTLY_VERIFIED`. Legacy `sourceFiles` and `binding` are preserved the same way and do not gate or establish qualification. |

The outline fixes translation and the declared canvas-to-manufacturing Y-axis conversion. No scale, rotation, reflection search, or drill best-fit alignment is performed. With canvas envelope minimum `(cx, cy)` and Gerber envelope `(minX, minY, maxX, maxY)`, `X = canvasX + minX - cx` and `Y = maxY + cy - canvasY`. The *whole* expected contour, including declared notches, must match; equal dimensions alone do not establish the transform.

Copper identities use X2 `TF.FileFunction,Copper,Ln,Top|Inr|Bot` when present. Without X2 identity, `.GTL` maps to layer 1, `.GBL` to the declared final layer, and `.G1`, `.G2`, etc. map to successive internal layers. Every expected layer must be uniquely present and nonempty with the expected position. This checks inventory, not rendered copper contents. Unknown/nonstandard identities need a supported export or a separate native/manual check; do not rename files to force a pass without confirming their roles.

## Explicit duplicate-file proof

Ordinarily all Excellon occurrences in all files count. Coincident records, repeated tools, or files with different names are not silently deduplicated.

The helper recognizes the exporter pair `Drill_PTH_Through.DRL` / `Drill_PTH_Through_Via.DRL` only with the matching `;Layer: PTH_Through` / `;Layer: PTH_Through_Via` comments. Alternatively, declare exact names in `drillFileRoles`. Either path additionally requires:

- Both files explicitly declare `;TYPE=PLATED`.
- The full-PTH file matches every expected `plated: true` hole exactly once, including expected vias.
- The subset matches every expected plated `kind: via` or `via-drill` hole exactly once.
- Every subset geometry occurs in the full-PTH file as an **exact multiset subset**, preserving multiplicity; both expected lists must be nonempty.

Only after all proofs pass does `FULL_PTH_AND_PROVEN_VIA_SUBSET` exclude the repeated subset file from the physical-hole count. The report preserves raw occurrence counts, proofs and excluded count. All other drill files remain included. Failure retains all occurrences and normally produces a drill mismatch. Never submit the repeated file as a second physical drilling pass just because the geometry report passes.

## Supported parser boundary

Gerber: absolute FS formats with leading/trailing zero suppression, mm/inch units, D01/D02/D03, conventional apertures C/R/O/P, aperture macro declarations (recognized primitive syntax retained for inventory only), linear moves, multiquadrant circular arcs, region records for inventory, dark/clear polarity, X2 attributes, and neutral transforms. Arcs are approximated with at most 0.0002 mm chord error; outlines still undergo topology and whole-contour matching. Clear-polarity outlines fail. Flashed/region profiles, noncircular outline apertures, multiple outline loops/cutouts, incremental coordinates, single-quadrant arcs and nonneutral transforms are unsupported. Aperture macros and copper regions are **not** rendered, so there is no copper-image equivalence claim.

Gerber leading-zero coordinates use the declared decimal count; the helper retains compatibility with exporters whose numeric integer portion exceeds the stated integer count. It does not infer a new unit, decimal scale or transform. Conflicting units/formats/identities, malformed terminators, unknown commands and undeclared apertures block the check.

Excellon: explicit decimal XY or declared implicit `FILE_FORMAT`/unit format, mm/inch, absolute/modal coordinates, tool definitions/selections, round drills, G85 slots, linear routed slots with M15/M16, repeat drills, M30 completion. Routed arcs, incremental coordinates, unknown commands, contradictory plating declarations, missing units and undeclared implicit coordinate format are rejected. A routed polyline remains multiple slot segments; it is not merged into a guessed single slot.

ZIP duplicate names (including case/separator aliases), invalid CRC, unsupported text encoding for manufacturing files, duplicate JSON keys, ambiguous expected holes and invalid outlines are rejected. The bounded input limit is 10,000 files and 512 MiB uncompressed. Ancillary files are hashed but not interpreted. Missing/extra layer files, open/changed outlines, shifted/missing/extra/duplicate holes and known plating mismatches are checked failures.

Tests create independent synthetic ZIPs in temporary directories and never require a project drive, EDA, network access or global installation. Geometry tests clearly skip if Shapely is unavailable; parser and CLI/dependency-error tests still run. A skipped geometry suite is not geometry validation.
