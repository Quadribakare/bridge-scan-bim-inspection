# Method notes (facts for the report writer)

How the method was developed. Use these for the "Lessons learned" and "Limitations and next steps"
sections; do not add lessons or limitations that are not here.

## What the pipeline does

- Compares a terrestrial laser scan of a bridge with its design model (BIM).
- Steps: load the BIM and scan tiles; clean (statistical outlier removal); extract the structure with
  geometry only (height above local ground + physical connectivity, no trained model); align (several
  starting positions -- long axes lined up both ways round, plus seeded RANSAC -- each refined with ICP, best
  kept); orientation ambiguity check; alignment quality gate; deviation profile and heatmaps; match every scan point to its own
  named BIM component; deck movement model (support heights + in-span sag), alternated with the matching
  until the model stops changing; local defect search, girder flange check, inspection views; defect
  list; report.
- Confidence: high = major component, at least 3 scanner stations, at least 4x the component's own noise
  and at least 0.1 m2; or a flange bent beyond its matching margin seen by at least 3 stations. Medium =
  major component, at least 2 stations, above 4x noise. Low = thin members, rough patches at sharp BIM
  edges, point losses at flange ends or fittings, anything else.
- All thresholds are in config.yaml; none is tuned to a particular bridge.

## Lessons learned

1. A symmetric bridge can be aligned back to front. With matching 18/24/18 m spans the scan fits the BIM
   equally well after a 180-degree turn (2.79 vs 2.76 mm median); checks like "are the piers where the BIM
   says" cannot tell. The first alignment was exactly 180 degrees out, which would have put every defect at
   the mirror-image position. A permanent orientation check now tests both options and, when they tie,
   requires external information (scan tile names, control points, georeferencing) and records which was
   used.
2. Mixing surfaces fakes noise. Treating every upward-facing point as "deck" mixed the road with
   footways, kerbs and girder flanges; different scanner stations see different mixes, which looked like
   34.5 mm of disagreement between stations. On the road surface alone it was about 9 mm, and about 1.2 mm
   once the bottom few centimetres of a vehicle standing on the deck were filtered out. The same mixing
   made girder-underside noise look like 16 mm instead of 2-3 mm per flange. Rule: compare like with like;
   an implausible noise figure is a bug in the method, not a property of the scanner.
3. Quality filters can delete the damage they should find. Early checks switched off whole components
   (averaging surface normals over a round column cancels out; a face-count rule rejected flat surfaces),
   removing piers and abutments from the assessment. Checks now act on individual points; points rejected
   as irregular are kept, drawn in magenta and searched as possible rough or broken surfaces.
4. Movement of the whole deck can hide local damage. A 4 cm girder flange gets a matching tolerance of
   about 2 cm; where the deck sags 40 mm, 95-99% of its scan points fell outside it, the few left produced
   fake candidates, and the real 70 mm bridge-strike deformation was invisible. Members carried by the deck
   are now matched against the design plus the measured deck movement, and a per-metre check reports where
   points go missing.
5. AI image review was tested and not adopted for finding defects. Two vision models reviewed 308
   inspection views ($8.74). They agreed with each other on 2 of 20 defect calls, added nothing
   significant, and both dismissed the girder strike as an occlusion, because in the image it appears as
   missing data. Defects are found by measurement; an AI model only writes this report, from the verified
   findings.

6. A single random alignment start is not reliable. RANSAC is random, and a bridge with repeating piers
   and spans has many near-matches; in one run a single start left about a quarter of the bridge metres away
   from the design. The alignment now tries several starting positions (the long axes of scan and design
   lined up, both ways round, plus seeded RANSAC), refines each, keeps the best, and a quality gate stops the
   run if too few points end up close to the design.

## Limitations and next steps

- Synthetic data, one bridge. Real scans add wet or reflective surfaces, dense vegetation and larger
  registration errors.
- The method was developed with partial knowledge of the answers: the answer key was opened during an
  earlier development session, so some design choices were made knowing which defects existed. For the
  final scoring, defects.csv was frozen before the answer key was read, and the scoring rules were only
  made stricter after the first scoring run. The real test is to run the pipeline, unchanged, on an unseen
  second bridge.
- Support movement needs surveyed control points on stable ground. Without them the levelling must be
  anchored to the structure itself; a residual alignment tilt of about 0.013 degrees (about 7 mm end to
  end) cannot be told apart from settlement, so support movement is reported as a range with that caveat.
- Orientation relies on metadata: for a symmetric structure it comes from the tile names and the stated
  assumption that BIM +x runs west to east; georeferencing would remove that assumption.
- Small, deep defects are under-sized: depths are reported as 25 cm cell medians, which averages away the
  deepest point of a small spall. Reporting the peak depth is a next step.
- Low-confidence clutter: most low-confidence items are edge effects; they could be hidden by default.
- Cost guard for any AI calls: one model, a hard per-run spending cap, cached responses, and a spending
  limit on the API key.
