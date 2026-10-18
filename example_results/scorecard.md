# Scorecard

Findings scored: `defects.csv` (66 rows). Answer key: 5 defects (2 structure-wide, 3 local).

Matching: a local finding matches a local defect only if it is on one of the defect's labelled components AND within 2.0 m of its labelled points. A global finding (sag, support movement) matches a structure-wide defect if it is within 2.0 m of the defect's centre along the bridge. Locations come from the answer key's own labels and scan-to-BIM transform, not from this pipeline's alignment.

## Per defect

| # | True defect | Found? | Matched finding(s) | Reported | True | Size error |
|---|---|---|---|---|---|---|
| 1 | Mid-span sag in span 2 (35 mm at centre) | **found** | D001 (high) | 39.8 mm | 35 mm | +4.8 mm (+14%) |
| 2 | Pier 2 settlement (18 mm), deck above follows | **found** | D006 (medium) | 10-16 mm | 18 mm | range -8 to -2 mm |
| 3 | Spalling with exposed rebar, pier 1 column 1 splash zone (up to 45 mm deep) | **found** | D005 (high) | 18.5 mm | 45 mm | -26.5 mm (-59%) |
| 4 | Bridge strike: girder 1 bottom flange bent up (up to 70 mm) over carriageway A | **found** | D004 (high) | 69.8 mm | 70 mm | -0.2 mm (-0%) |
| 5 | Abutment 2 bank seat + ballast wall rotated towards span (0.55 deg, ~26 mm at top) | **found** | D002 (high), D003 (high), D012 (low), D013 (low), D014 (low) | 14.3 mm, 0.58 deg | 26 mm, 0.55 deg | -11.7 mm (-45%); angle +0.03 deg |

## Summary

- **Found 5 of 5** true defects.
- Findings matching a true defect: 9 of 66.
- **False alarms: 57** - by confidence: high 0, medium 0, low 57.
- High + medium confidence findings: 6, of which 6 match a true defect.

## False alarms

| id | component | finding | size (mm) | stations | confidence |
|---|---|---|---|---|---|
| D007 | abutment_1_ballast_wall | wall tilt: lean +0.21 deg, top displaced -5.2 mm (+ = outward) over a 12.4 x 1.4 m face | -5.2 | 8 | low |
| D008 | abutment_1_ballast_wall | rough/irregular surface at a sharp BIM edge | 78.4 | 2 | low |
| D009 | abutment_1_ballast_wall | rough/irregular surface at a sharp BIM edge | 53.8 | 2 | low |
| D010 | abutment_1_return_wall_N | rough/irregular surface at a sharp BIM edge | 92.9 | 3 | low |
| D011 | abutment_1_return_wall_S | rough/irregular surface at a sharp BIM edge | 85.6 | 3 | low |
| D015 | abutment_2_return_wall_N | rough/irregular surface at a sharp BIM edge | 98.9 | 5 | low |
| D016 | abutment_2_return_wall_N | rough/irregular surface at a sharp BIM edge | 84.5 | 3 | low |
| D017 | abutment_2_return_wall_S | rough/irregular surface at a sharp BIM edge | 82.9 | 3 | low |
| D018 | crossframe_019 | rough/irregular surface at a sharp BIM edge | 3.1 | 3 | low |
| D019 | crossframe_022 | rough/irregular surface at a sharp BIM edge | 1.3 | 2 | low |
| D020 | crossframe_026 | rough/irregular surface at a sharp BIM edge | 2.0 | 3 | low |
| D021 | crossframe_030 | rough/irregular surface at a sharp BIM edge | 1.9 | 2 | low |
| D022 | crossframe_037 | rough/irregular surface at a sharp BIM edge | 2.9 | 2 | low |
| D023 | crossframe_041 | rough/irregular surface at a sharp BIM edge | 2.4 | 2 | low |
| D024 | crossframe_046 | rough/irregular surface at a sharp BIM edge | 5.4 | 3 | low |
| D025 | crossframe_055 | rough/irregular surface at a sharp BIM edge | 1.8 | 3 | low |
| D026 | crossframe_063 | rough/irregular surface at a sharp BIM edge | 5.6 | 3 | low |
| D027 | crossframe_086 | rough/irregular surface at a sharp BIM edge | 0.8 | 2 | low |
| D028 | edge_upstand_N | rough/irregular surface at a sharp BIM edge | 94.0 | 3 | low |
| D029 | girder_1_bf | points rejected at flange end / fitting | -12.0 | 6 | low |
| D030 | girder_1_bf | points rejected at flange end / fitting | 27.1 | 5 | low |
| D031 | girder_1_bf | points rejected at flange end / fitting | -7.5 | 6 | low |
| D032 | girder_1_tf | rough/irregular surface at a sharp BIM edge | 5.1 | 2 | low |
| D033 | girder_2_bf | rough/irregular surface at a sharp BIM edge | 5.5 | 2 | low |
| D034 | girder_2_bf | rough/irregular surface at a sharp BIM edge; points rejected at flange end / fitting | 5.0 | 6 | low |
| D035 | girder_2_bf | rough/irregular surface at a sharp BIM edge | 3.4 | 2 | low |
| D036 | girder_2_bf | rough/irregular surface at a sharp BIM edge | 1.9 | 4 | low |
| D037 | girder_2_bf | rough/irregular surface at a sharp BIM edge | 1.9 | 4 | low |
| D038 | girder_2_bf | points rejected at flange end / fitting | 1.9 | 7 | low |
| D039 | girder_2_bf | points rejected at flange end / fitting | 25.2 | 5 | low |
| D040 | girder_2_bf | points rejected at flange end / fitting | 25.4 | 3 | low |
| D041 | girder_2_bf | points rejected at flange end / fitting | -8.3 | 6 | low |
| D042 | girder_3_bf | rough/irregular surface at a sharp BIM edge | 1.7 | 2 | low |
| D043 | girder_3_bf | local flange deviation (higher than deck movement) | 16.4 | 2 | low |
| D044 | girder_3_bf | local flange deviation (higher than deck movement) | 11.0 | 2 | low |
| D045 | girder_3_bf | local flange deviation (higher than deck movement) | 11.4 | 2 | low |
| D046 | girder_3_bf | points rejected at flange end / fitting | 2.5 | 7 | low |
| D047 | girder_3_bf | points rejected at flange end / fitting | 24.4 | 6 | low |
| D048 | girder_3_bf | points rejected at flange end / fitting | 2.0 | 7 | low |
| D049 | girder_4_bf | rough/irregular surface at a sharp BIM edge | 8.3 | 3 | low |
| D050 | girder_4_bf | rough/irregular surface at a sharp BIM edge | 5.6 | 2 | low |
| D051 | girder_4_bf | rough/irregular surface at a sharp BIM edge | 5.0 | 3 | low |
| D052 | girder_4_bf | rough/irregular surface at a sharp BIM edge | 4.8 | 2 | low |
| D053 | girder_4_bf | points rejected at flange end / fitting | 4.7 | 6 | low |
| D054 | girder_4_bf | points rejected at flange end / fitting | -1.8 | 4 | low |
| D055 | girder_5_bf | points rejected at flange end / fitting | -0.0 | 7 | low |
| D056 | girder_5_bf | points rejected at flange end / fitting | 15.0 | 7 | low |
| D057 | girder_5_bf | points rejected at flange end / fitting | -9.0 | 6 | low |
| D058 | kerb_S | rough/irregular surface at a sharp BIM edge | 2.0 | 2 | low |
| D059 | parapet_post_N_022 | local deviation (vertical surface) | 12.1 | 3 | low |
| D060 | parapet_post_N_025 | rough/irregular surface at a sharp BIM edge; local deviation (underside surface) | 27.5 | 2 | low |
| D061 | parapet_post_N_026 | rough/irregular surface at a sharp BIM edge | 23.7 | 2 | low |
| D062 | parapet_post_N_027 | rough/irregular surface at a sharp BIM edge | 19.1 | 3 | low |
| D063 | parapet_post_N_028 | local deviation (underside surface) | 28.8 | 2 | low |
| D064 | parapet_post_S_028 | local deviation (underside surface) | 22.0 | 2 | low |
| D065 | parapet_rail_N_002 | rough/irregular surface at a sharp BIM edge | 36.9 | 3 | low |
| D066 | parapet_rail_S_002 | rough/irregular surface at a sharp BIM edge | 35.8 | 3 | low |

## Where each true defect is (from the answer key labels, BIM coordinates)

- Defect 1: 745,348 labelled points, components crossframe, deck_slab, diaphragm, edge_upstand_N, edge_upstand_S, footway_N, footway_S, girder_1_bf, girder_1_stiff, girder_1_tf, girder_1_web, girder_2_bf, girder_2_stiff, girder_2_tf, girder_2_web, girder_3_bf, girder_3_stiff, girder_3_tf, girder_3_web, girder_4_bf, girder_4_stiff, girder_4_tf, girder_4_web, girder_5_bf, girder_5_stiff, girder_5_tf, girder_5_web, kerb_N, kerb_S, parapet_post_N, parapet_post_S, parapet_rail_N, parapet_rail_S, surfacing_L, surfacing_R, x 19.0 to 41.5 m, y -5.8 to 5.8 m, z 5.7 to 8.2 m
- Defect 2: 560,235 labelled points, components bearing_s2, crossframe, deck_slab, diaphragm, edge_upstand_N, edge_upstand_S, footway_N, footway_S, girder_1_bf, girder_1_stiff, girder_1_tf, girder_1_web, girder_2_bf, girder_2_stiff, girder_2_tf, girder_2_web, girder_3_bf, girder_3_stiff, girder_3_tf, girder_3_web, girder_4_bf, girder_4_stiff, girder_4_tf, girder_4_web, girder_5_bf, girder_5_stiff, girder_5_tf, girder_5_web, kerb_N, kerb_S, parapet_post_N, parapet_post_S, parapet_rail_N, parapet_rail_S, pier_2_column_1, pier_2_column_2, pier_2_crosshead, plinth_s2, surfacing_L, surfacing_R, x 41.2 to 56.0 m, y -5.8 to 5.8 m, z 1.1 to 8.2 m
- Defect 3: 1,109 labelled points, components pier_1_column_1, x 18.5 to 18.6 m, y -3.1 to -2.5 m, z 0.6 to 1.7 m
- Defect 4: 5,559 labelled points, components girder_1_bf, girder_1_web, x 24.1 to 26.9 m, y -4.6 to -4.1 m, z 5.7 to 6.2 m
- Defect 5: 19,951 labelled points, components abutment_2_ballast_wall, abutment_2_bank_seat, x 59.4 to 61.6 m, y -6.0 to 6.0 m, z 4.6 to 6.9 m

## Alignment error vs the true scan-to-BIM transform

Same physical points (the 2.8M extracted bridge points), placed in BIM coordinates by the answer key's transform and by this pipeline. Error = pipeline position minus true position.

| stage | median error | p95 | max | mean offset x / y / z (mm) | rotation error | at bridge ends (x<2 / x>58 m) |
|---|---|---|---|---|---|---|
| step 4 alignment | 3.7 mm | 10.3 mm | 11.8 mm | -1.2 / +0.4 / +3.8 | 0.0124 deg | 2.3 / 10.8 mm |
| after orientation check (4b) | 3.6 mm | 9.6 mm | 11.4 mm | -1.7 / +0.4 / +3.4 | 0.0133 deg | 2.8 / 10.1 mm |
| final (7A robust re-alignment) | 3.5 mm | 9.4 mm | 11.2 mm | -1.7 / +0.4 / +3.3 | 0.0133 deg | 2.8 / 9.9 mm |

(For scale: answer-key per-station registration error is "range noise ~1 mm + 25 ppm, worse at grazing angles; per-station registration error ~2.5 mm / 0.004 deg".)
