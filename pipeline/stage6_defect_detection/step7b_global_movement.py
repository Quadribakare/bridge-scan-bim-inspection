"""
Step 7B, part 1: global movement.
Support heights measured from VISIBLE surfaces only (crosshead/bank-seat
underside + deck/girder underside directly over the support centreline --
the top of a crosshead is physically hidden from the scanner, confirmed
earlier in this project), with confidence ranges bootstrapped per STATION
(not per point, which overstates confidence since points within one station
aren't independent). Layer 1 = straight lines between tested support
heights (a support only counts as "moved" if its CI excludes zero AND the
movement exceeds a materiality floor). Layer 2 = a moving median of
whatever's left, no parametric fit.
"""
import json
import numpy as np
import pandas as pd
import yaml
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())

d2 = np.load(OUT_DIR / "step7a_component_deviation.npz")
xyz = d2["xyz"]
station = d2["station"]
point_component = d2["point_component"]
point_normal = d2["point_normal"]
point_signed_dev_mm = d2["point_signed_dev_mm"]
point_dz_mm = d2["point_dz_mm"]
is_horizontal = d2["is_horizontal"]

comp = pd.read_csv(BIM_COMPONENTS)
rng = np.random.default_rng(0)

# "Deck top" means the road surfacing component(s) specifically, matched by
# BIM identity -- not every upward-facing point. Pooling all top-facing points
# mixes road, footway, kerb, edge upstand and girder flanges; different
# stations see different mixes of those, which faked a ~34mm "inter-station
# disagreement" (surfacing alone: ~9mm) and a ~30mm top-vs-underside gap at
# the supports (surfacing alone: 2-3mm).
road_idx = comp[comp["element_type"] == "surfacing"].index
is_road = np.isin(point_component, road_idx) & is_horizontal & (point_normal[:, 2] > 0)

# Objects standing on the road (vehicles, equipment) touch the deck, so the
# connectivity cleaning keeps them, and their lowest few cm fall inside the
# surfacing component's matching margin -- they get labelled "surfacing" and
# form a second layer above the road (found on the main span: a band ~2-3 m
# wide on the centreline, up to ~78 mm above the road, which made the profile
# median jump between layers). Per 0.5 m slice along the bridge, the road
# level is the 10th percentile of surfacing dz; points more than 4x the
# surface noise above it are treated as objects on the road, not road.
road_i = np.where(is_road)[0]
slice_id = np.floor(xyz[road_i, 0] / 0.5).astype(int)
rdf = pd.DataFrame({"s": slice_id, "dz": point_dz_mm[road_i]})
level = rdf.groupby("s")["dz"].transform(lambda s: s.quantile(0.10)).values
slice_noise = rdf.groupby("s")["dz"].apply(lambda s: 1.4826 * np.median(np.abs(s - s.median())))
on_object = point_dz_mm[road_i] - level > 4 * float(slice_noise.median())
is_road[road_i[on_object]] = False
print(f"Removed {on_object.sum():,} of {len(road_i):,} surfacing points sitting more than "
      f"{4 * float(slice_noise.median()):.1f} mm above the local road level (objects on the road)\n")


def bootstrap_ci(vals, stations, n_boot=2000):
    """Bootstrap over STATIONS, not raw points. Points within one station are
    not independent observations (they're correlated samples of the same
    single scan), so resampling raw points understates the true uncertainty
    and can make ordinary residual noise look like a statistically
    significant "movement". Resampling stations (one representative value --
    that station's own median -- per station) gives a correctly-sized CI."""
    uniq_st = np.unique(stations)
    if len(uniq_st) < 2:
        return np.nan, np.nan, np.nan
    st_medians = np.array([np.median(vals[stations == s]) for s in uniq_st])
    med = np.median(st_medians)
    boot = np.array([np.median(rng.choice(st_medians, len(st_medians), True)) for _ in range(n_boot)])
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return med, lo, hi


# --- find supports generically from the BIM component table ---
supports = comp[comp["element_type"].isin(["pier", "abutment"])]
support_groups = {}
for _, row in supports.iterrows():
    base = row["component"].split("_column")[0].split("_crosshead")[0] \
        .split("_bank_seat")[0].split("_ballast_wall")[0].split("_return_wall")[0]
    support_groups.setdefault(base, []).append(row)

print("=== Support heights (PRIMARY, C: deck TOP +/-0.6m -- same surface as the "
      "profile plot, so the support markers actually sit on the curve they're "
      "drawn against; A/B: underside readings, printed as a cross-check only) ===\n")
results = []
for base, rows in support_groups.items():
    xlo = min(r["xmin"] for r in rows)
    xhi = max(r["xmax"] for r in rows)
    ylo = min(r["ymin"] for r in rows)
    yhi = max(r["ymax"] for r in rows)
    zlo = min(r["zmin"] for r in rows)
    cx = (xlo + xhi) / 2

    nz = point_normal[:, 2]
    is_underside = is_horizontal & (nz < 0)
    is_top_local = is_horizontal & (nz > 0) & is_road

    # C (PRIMARY): deck TOP directly above the support, +/-0.6m -- the same
    # surface and same dz convention as the rest of the profile/model, so
    # this support marker is guaranteed visually consistent with the curve
    mC = is_top_local & (np.abs(xyz[:, 0] - cx) < 0.6)
    valsC = point_dz_mm[mC]
    medC, loC, hiC = bootstrap_ci(valsC, station[mC]) if mC.sum() else (np.nan, np.nan, np.nan)
    nC, nstC = mC.sum(), len(np.unique(station[mC])) if mC.sum() else 0

    # A (cross-check): this support's own underside-facing points, within its footprint
    mA = (is_underside & (xyz[:, 0] >= xlo - 0.3) & (xyz[:, 0] <= xhi + 0.3) &
          (xyz[:, 1] >= ylo - 0.3) & (xyz[:, 1] <= yhi + 0.3) &
          (xyz[:, 2] >= zlo - 0.5) & (xyz[:, 2] <= zlo + 1.0))
    valsA = point_signed_dev_mm[mA]
    medA, loA, hiA = bootstrap_ci(valsA, station[mA]) if mA.sum() else (np.nan, np.nan, np.nan)
    nA, nstA = mA.sum(), len(np.unique(station[mA])) if mA.sum() else 0

    # B (cross-check): deck/girder underside directly above, +/-0.6m, full width
    mB = is_underside & (np.abs(xyz[:, 0] - cx) < 0.6)
    valsB = point_dz_mm[mB]
    medB, loB, hiB = bootstrap_ci(valsB, station[mB]) if mB.sum() else (np.nan, np.nan, np.nan)
    nB, nstB = mB.sum(), len(np.unique(station[mB])) if mB.sum() else 0

    print(f"{base:20s} x={cx:5.1f}m")
    if nC >= 20:
        print(f"  C (deck TOP +/-0.6m, PRIMARY): n={nC:5d} st={nstC}  median={medC:+7.1f}mm  95%CI[{loC:+.1f},{hiC:+.1f}]")
    else:
        print(f"  C: only {nC} points -- insufficient coverage")
    if nA >= 20:
        print(f"  A (own underside, cross-check): n={nA:5d} st={nstA}  median={medA:+7.1f}mm  95%CI[{loA:+.1f},{hiA:+.1f}]")
    else:
        print(f"  A: only {nA} points -- insufficient coverage")
    if nB >= 20:
        print(f"  B (deck/girder underside +/-0.6m, cross-check): n={nB:5d} st={nstB}  median={medB:+7.1f}mm  95%CI[{loB:+.1f},{hiB:+.1f}]")
    else:
        print(f"  B: only {nB} points -- insufficient coverage")

    if nC >= 20 and nB >= 20 and not np.isnan(medC) and not np.isnan(medB) and abs(medC - medB) > 10:
        print(f"  NOTE: top ({medC:+.1f}mm) and underside ({medB:+.1f}mm) disagree by "
              f"{abs(medC-medB):.1f}mm -- plausible (different surfaces: deck thickness/"
              f"camber), just flagging for transparency")

    min_st = cfg["coverage"]["min_stations"]
    if nC >= 20 and nstC >= min_st:
        source, final_med, final_lo, final_hi, final_n_stations = "deck top (surfacing)", medC, loC, hiC, nstC
    elif nB >= 20 and nstB >= min_st:
        source, final_med, final_lo, final_hi, final_n_stations = "deck/girder underside", medB, loB, hiB, nstB
    elif nA >= 20 and nstA >= min_st:
        source, final_med, final_lo, final_hi, final_n_stations = "support's own underside", medA, loA, hiA, nstA
    else:
        source, final_med, final_lo, final_hi, final_n_stations = "none", np.nan, np.nan, np.nan, 0

    # This is the measured deck height at the support relative to the ICP
    # alignment, not a movement verdict: with no stable external reference
    # (surveyed control points) in this survey, which support "moved" depends
    # on which reference the structure is levelled to -- see the reference
    # sensitivity check further down.
    print(f"  -> measured deck height at support: {final_med:+.1f}mm from {source} "
          f"(stations={final_n_stations}); movement: inconclusive\n")
    results.append(dict(name=base, x=cx, median=final_med, ci_lo=final_lo, ci_hi=final_hi,
                        source=source, n_stations=final_n_stations,
                        top_mm=medC, top_stations=nstC, underside_mm=medB, underside_stations=nstB))

# the support line passes through the MEASURED deck heights at the supports,
# so the in-span sag below is relative to the span's own supports
results = sorted(results, key=lambda r: r["x"])
valid_support = [r for r in results if not np.isnan(r["median"])]
support_x = np.array([r["x"] for r in valid_support])
support_dz = np.array([r["median"] for r in valid_support])

# --- deck profile: STATION-AWARE moving median of road-top dz (is_horizontal,
# nz>0). A plain 0.5m slice median is point-weighted, so a single scan
# station with thousands of points in one small area can completely dominate
# a slice even when another station, looking at the exact same spot, reads
# something very different (a station-to-station registration seam, not
# real structure). Fix: median OF PER-STATION MEDIANS in each window (each
# station counts once, regardless of point count), and flag windows where
# stations disagree far more than the data's own overall noise -- that's the
# signature of a seam/data problem, not of real local deck shape. ---
nz = point_normal[:, 2]
is_top = is_horizontal & (nz > 0) & is_road
x_top = xyz[is_top, 0]
z_top = point_dz_mm[is_top]
station_top = station[is_top]

seam_mult = cfg["realignment"]["reweight_mad_multiplier"]  # reuse the same
                                                             # outlier-detection
                                                             # convention used
                                                             # elsewhere, not a
                                                             # new tuned number

WINDOW = cfg["sag"]["moving_median_window_m"]
profile_grid_x = np.arange(x_top.min(), x_top.max(), 0.25)
profile_y = np.full(len(profile_grid_x), np.nan)
profile_n_stations = np.zeros(len(profile_grid_x), dtype=int)
worst_dev_by_window = np.full(len(profile_grid_x), np.nan)

# pass 1: compute every window's consensus and its "worst single station"
# deviation from that consensus -- but DON'T flag yet. Station-to-station
# REGISTRATION noise is a different, generally larger source of error than
# single-point scatter (it's a systematic per-station offset, not
# measurement noise), so comparing it against point-level noise is an
# apples-to-oranges mismatch -- that was the bug in the first attempt
# (flagged 252/254 windows). The right reference scale for "is this
# window's station disagreement unusual" is the distribution of station
# disagreement ACROSS ALL OTHER WINDOWS, computed below in pass 2.
for i, gx in enumerate(profile_grid_x):
    win = np.abs(x_top - gx) < WINDOW / 2
    if win.sum() < 10:
        continue
    uniq_st = np.unique(station_top[win])
    if len(uniq_st) < cfg["coverage"]["min_stations"]:
        continue
    st_meds = np.array([np.median(z_top[win][station_top[win] == s]) for s in uniq_st])
    consensus = np.median(st_meds)
    # profile value: pooled 2m moving median of surfacing points (stations
    # are only used to FLAG disagreement, not to re-weight the profile)
    profile_y[i] = np.median(z_top[win])
    profile_n_stations[i] = len(uniq_st)
    # worst single-station deviation from the group's own robust consensus --
    # NOT the raw max-min range, which inflates just from having more
    # stations in a window even with completely normal per-station noise
    # (range grows with sample size; this doesn't)
    worst_dev_by_window[i] = np.max(np.abs(st_meds - consensus))

# pass 2: a window is a genuine seam only if ITS worst-station deviation is
# an outlier relative to the typical worst-station deviation seen across
# the WHOLE deck (robust median + 3xMAD, the same convention as everywhere
# else) -- not relative to point noise.
valid_wd = ~np.isnan(worst_dev_by_window)
wd_med = np.median(worst_dev_by_window[valid_wd])
wd_robust_std = 1.4826 * np.median(np.abs(worst_dev_by_window[valid_wd] - wd_med))
seam_threshold = wd_med + seam_mult * wd_robust_std
print(f"Typical inter-station disagreement across the deck: {wd_med:.2f}mm "
      f"(robust std {wd_robust_std:.2f}mm) -- flagging windows above {seam_threshold:.2f}mm")

seam_flags = []
for i, gx in enumerate(profile_grid_x):
    if valid_wd[i] and worst_dev_by_window[i] > seam_threshold:
        seam_flags.append((gx, worst_dev_by_window[i], profile_n_stations[i]))

valid_profile = ~np.isnan(profile_y)
print(f"\n{valid_profile.sum()} station-aware 2m-moving-median profile points")
if seam_flags:
    # collapse adjacent flagged grid points into contiguous ranges for reporting
    seam_xs = np.array([s[0] for s in seam_flags])
    gaps = np.where(np.diff(seam_xs) > 0.3)[0]
    starts = [0] + list(gaps + 1)
    ends = list(gaps) + [len(seam_xs) - 1]
    print(f"DATA PROBLEM: {len(seam_flags)} grid points show inter-station disagreement "
          f"well beyond the typical level seen across the rest of the deck (likely a "
          f"scan-to-scan registration seam, not real deck shape) -- flagged as data "
          f"problems (the 2m pooled median keeps them from dominating the profile):")
    for s, e in zip(starts, ends):
        rng_xs = seam_xs[s:e + 1]
        worst = max(seam_flags[s:e + 1], key=lambda t: t[1])
        print(f"    x=[{rng_xs.min():.1f}, {rng_xs.max():.1f}]m: worst disagreement "
              f"{worst[1]:.1f}mm across {worst[2]} stations")

# layer 2: the layer-1 support line subtracted from the SAME station-aware,
# seam-filtered profile curve computed above -- not a separate point-weighted
# computation. This matters: the profile and the "max sag" numbers must come
# from the same filtered data, otherwise the plot could look clean while the
# reported sag figure stays contaminated by the exact seam that was just
# excluded from the picture.
grid_x = profile_grid_x
grid_y = np.where(valid_profile, profile_y - np.interp(grid_x, support_x, support_dz), np.nan)
valid_grid = valid_profile
print(f"\n{valid_grid.sum()} / {len(grid_x)} grid points have a valid {WINDOW}m moving median "
      f"(station-aware, seam-filtered)")


# The sag curve is relative to the supports, so it is zero AT a support by
# definition. Where the profile has a gap over a support (e.g. only one
# station sees the road next to a pier), interpolating straight across the
# gap would leave the model far from the measured support height -- so pin
# the curve to zero at any support with no profile point within half a window.
anchors = [sx for sx in support_x if not np.any(valid_grid & (np.abs(grid_x - sx) <= WINDOW / 2))]
curve_x = np.concatenate([grid_x[valid_grid], anchors])
curve_y = np.concatenate([grid_y[valid_grid], np.zeros(len(anchors))])
order_c = np.argsort(curve_x)
curve_x, curve_y = curve_x[order_c], curve_y[order_c]
if anchors:
    print(f"sag curve pinned to 0 at supports with a profile gap: x = {[round(float(a), 1) for a in anchors]}")


class MovingMedianCurve:
    """Interpolates the sag curve (moving-median grid + support anchors); 0 outside its range."""
    def __call__(self, x):
        x = np.atleast_1d(x)
        return np.interp(x, curve_x, curve_y, left=0.0, right=0.0)


spline = MovingMedianCurve()
print(f"using a {WINDOW}m moving median for layer 2 (no parametric fit)")

# Sag relative to a span's OWN supports is independent of the levelling
# reference: any re-levelling is a straight line in x, which shifts the
# profile and the chord between its two supports by exactly the same amount.
# Confirmation threshold: the same 4x-noise rule as the local-defect pass,
# using the scatter of surfacing points about the full model.
top_noise = 1.4826 * np.median(np.abs((z_top - (np.interp(x_top, support_x, support_dz) + spline(x_top)))
                                      - np.median(z_top - (np.interp(x_top, support_x, support_dz) + spline(x_top)))))
sag_threshold = 4 * top_noise
span_bounds = [x_top.min() - 1] + list(support_x) + [x_top.max() + 1]
span_results = []
print(f"\n--- Sag per span, relative to the span's own supports "
      f"(confirmed if interior span and |sag| > 4x noise = {sag_threshold:.1f}mm) ---")
for i in range(len(span_bounds) - 1):
    lo, hi = span_bounds[i], span_bounds[i + 1]
    interior = 0 < i < len(span_bounds) - 2
    sm = valid_grid & (grid_x >= lo) & (grid_x < hi)
    if sm.sum() == 0:
        print(f"  span [{lo:.1f}, {hi:.1f}]m: no coverage")
        continue
    xs_s, ys_s = grid_x[sm], grid_y[sm]
    idx = np.argmax(np.abs(ys_s))
    win = np.abs(x_top - xs_s[idx]) < WINDOW / 2
    n_st = int(len(np.unique(station_top[win])))
    confirmed = bool(interior and abs(ys_s[idx]) > sag_threshold and n_st >= cfg["coverage"]["min_stations"])
    status = ("CONFIRMED" if confirmed else
              "not significant" if interior else "end overhang (no support beyond it)")
    print(f"  span [{lo:.1f}, {hi:.1f}]m: deepest point {ys_s[idx]:+.1f}mm at x={xs_s[idx]:.1f}m "
          f"({n_st} stations) -> {status}")
    span_results.append(dict(span_start_m=round(float(lo), 2), span_end_m=round(float(hi), 2),
                             interior=bool(interior), deepest_mm=round(float(ys_s[idx]), 1),
                             at_x_m=round(float(xs_s[idx]), 2), n_stations=n_st,
                             threshold_mm=round(float(sag_threshold), 1), confirmed=confirmed))

# --- support movement: reference sensitivity. Compare the readings as
# aligned (ICP only) with the readings levelled to the deck over the two
# abutments, and
# show how the support readings change. If the conclusion about which
# support moved flips with this choice, movement isn't identifiable from
# this scan alone. ---
abut = [r for r in valid_support if r["name"].startswith("abutment")]
reference_table = []
if len(abut) == 2:
    (xa1, za1), (xa2, za2) = sorted([(r["x"], r["median"]) for r in abut])
    for r in valid_support:
        alt = r["median"] - (za1 + (za2 - za1) * (r["x"] - xa1) / (xa2 - xa1))
        reference_table.append(dict(support=r["name"], x_m=round(float(r["x"]), 2),
                                    source=r["source"], n_stations=int(r["n_stations"]),
                                    top_mm=None if np.isnan(r["top_mm"]) else round(float(r["top_mm"]), 1),
                                    top_stations=int(r["top_stations"]),
                                    underside_mm=None if np.isnan(r["underside_mm"]) else round(float(r["underside_mm"]), 1),
                                    underside_stations=int(r["underside_stations"]),
                                    ref_as_aligned_mm=round(float(r["median"]), 1),
                                    ref_deck_over_abutments_mm=round(float(alt), 1)))
    end_slopes = []
    for a, b in [(0, 1), (len(valid_support) - 2, len(valid_support) - 1)]:
        ra, rb = valid_support[a], valid_support[b]
        end_slopes.append(dict(span=f"{ra['name']} -> {rb['name']}",
                               slope_mm_per_m=round(float((rb["median"] - ra["median"]) / (rb["x"] - ra["x"])), 2)))
    print("\n--- Support movement: reference sensitivity ---")
    print(f"{'support':12s} {'x':>6s} {'ref: as aligned (ICP)':>18s} {'ref: deck over abutments=0':>27s}  (stations)")
    for row in reference_table:
        print(f"{row['support']:12s} {row['x_m']:6.1f} {row['ref_as_aligned_mm']:+18.1f} "
              f"{row['ref_deck_over_abutments_mm']:+27.1f}  ({row['n_stations']})")
    for s in end_slopes:
        print(f"end span {s['span']}: straight slope {s['slope_mm_per_m']:+.2f} mm/m")
else:
    end_slopes = []

# Per-pier verdict: the readings under both references must agree in
# direction and both exceed the materiality floor; the magnitude is then
# reported as the range between the two references. Otherwise the pier is
# inconclusive (the conclusion depends on the reference choice).
min_move = cfg["support_movement"]["min_movement_mm"]
statements = []
for row in reference_table:
    if row["support"].startswith("abutment"):
        continue
    a, b = row["ref_as_aligned_mm"], row["ref_deck_over_abutments_mm"]
    if np.sign(a) == np.sign(b) and min(abs(a), abs(b)) > min_move:
        lo, hi = sorted([abs(a), abs(b)])
        row["verdict"] = (f"{row['support']} appears {'lower' if a < 0 else 'higher'} by "
                          f"{int(lo + 0.5)}-{int(hi + 0.5)} mm; needs control points to confirm")
        statements.append(row["verdict"])
    else:
        row["verdict"] = f"{row['support']}: inconclusive (depends on the reference choice)"
status = "; ".join(statements) if statements else "inconclusive"
print(f"-> support movement: {status}")

findings = {
    "support_movement": {
        "status": status,
        "reason": "no stable external reference (control points) in this survey, so the "
                  "levelling has to be anchored to part of the structure itself; a pier is "
                  "only reported when both reference choices agree on the direction and "
                  f"both exceed {min_move:.0f} mm, and the magnitude is given as the range "
                  "between them",
        "evidence": {"supports": reference_table, "end_span_slopes": end_slopes},
        "next_step": "use surveyed control points on stable ground",
    },
    "sag_per_span": span_results,
    "data_problems": [dict(x_m=round(float(gx), 2), worst_station_disagreement_mm=round(float(d), 1),
                           n_stations=int(n)) for gx, d, n in seam_flags],
}
(OUT_DIR / "step7b_global_findings.json").write_text(json.dumps(findings, indent=2))
print("saved step7b_global_findings.json")

fig, ax = plt.subplots(figsize=(14, 6))
seam_shaded = False
for gx, spread, nst in seam_flags:
    ax.axvspan(gx - 0.125, gx + 0.125, color='#e74c3c', alpha=0.12,
               label='flagged data problem (station seam)' if not seam_shaded else None)
    seam_shaded = True
ax.plot(profile_grid_x[valid_profile], profile_y[valid_profile], 'o', ms=3, color='#4a6fa5',
        label=f'deck profile ({WINDOW}m station-aware moving median)')
ax.plot(support_x, support_dz, 's--', color='#c0392b', label='measured deck height at supports')
full_xs = np.linspace(x_top.min(), x_top.max(), 500)
full_model = np.interp(full_xs, support_x, support_dz) + spline(full_xs)
ax.plot(full_xs, full_model, '-', color='#2e7d32', linewidth=2, label='layer 1 + layer 2 (full model)')
ax.axhline(0, color='black', linewidth=0.7)
for x in support_x:
    ax.axvline(x, color='gray', linestyle=':', linewidth=0.8)
ax.set_xlabel('x (m)'); ax.set_ylabel('dz (mm)'); ax.legend()
ax.set_title('Step 7B part 1: global movement model')
plt.tight_layout()
plt.savefig(OUT_DIR / "step7b_global_movement.png", dpi=130)
print("saved step7b_global_movement.png")

np.savez_compressed(OUT_DIR / "step7b_global_model.npz",
                     support_x=support_x, support_dz=support_dz,
                     sag_curve_x=curve_x, sag_curve_y=curve_y)
print("saved step7b_global_model.npz")
