"""
Step 4b: orientation ambiguity check (runs after alignment, before anything
that measures deviation).

A structure that is symmetric end-to-end (matching spans, piers and abutments)
fits its BIM equally well after a 180 deg rotation about the vertical axis
(x -> L - x, y -> -y). The alignment can land either way and checks like
"are the piers where the BIM says" can't tell the difference. So:
  1. build both candidates (the current alignment and its 180 deg rotation
     about the BIM's vertical centre line), refine each with the same ICP,
     and compare exact fit quality;
  2. if one clearly fits better, geometry decides;
  3. if they tie, the orientation MUST come from external metadata -- here the
     scan tile names (west/east) plus a stated assumption about which way BIM
     +x runs. Control points or georeferencing should replace this when
     available.
The decision and the evidence are recorded in orientation_check.json.
"""
import json
import sys
import numpy as np
import open3d as o3d
import trimesh
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
ocfg, rcfg = cfg["orientation"], cfg["realignment"]

d4 = np.load(OUT_DIR / "step4_aligned_final.npz")
xyz0, rgb = d4["xyz_aligned"], d4["rgb"]
s2 = np.load(OUT_DIR / "step2_cleaned.npz")
is_bridge = np.load(OUT_DIR / "step3_connected_bridge.npz")["is_bridge"]
tile_names = list(np.load(OUT_DIR / "step1_loaded.npz")["tile_names"])
tile = s2["tile"][is_bridge]            # index into tile_names (taken from the scan file names)
assert len(tile) == len(xyz0)
W = tile_names.index("west") if "west" in tile_names else None
E = tile_names.index("east") if "east" in tile_names else None

bim = trimesh.load(BIM_OBJ)
cx, cy = bim.bounds.mean(axis=0)[:2]
bim_pts, fidx = trimesh.sample.sample_surface(bim, 1_000_000, seed=0)
bim_pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(bim_pts))
bim_pcd.normals = o3d.utility.Vector3dVector(bim.face_normals[fidx])
legacy = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(bim.vertices), o3d.utility.Vector3iVector(bim.faces))
scene = o3d.t.geometry.RaycastingScene()
scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(legacy))

rng = np.random.default_rng(0)
fit_idx = rng.choice(len(xyz0), 400_000, replace=False)
test_idx = rng.choice(len(xyz0), 200_000, replace=False)

# 180 deg about the vertical axis through the BIM's plan centre
R180 = np.eye(4)
R180[0, 0] = R180[1, 1] = -1
R180[0, 3], R180[1, 3] = 2 * cx, 2 * cy


def refine(T_start):
    """Same robust multi-scale point-to-plane ICP for both candidates."""
    src = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz0[fit_idx]))
    T = T_start.copy()
    for thresh in rcfg["icp_schedule_m"]:
        res = o3d.pipelines.registration.registration_icp(
            src, bim_pcd, thresh, T,
            o3d.pipelines.registration.TransformationEstimationPointToPlane(
                o3d.pipelines.registration.TukeyLoss(k=thresh * 0.5)),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=rcfg["icp_max_iter"]))
        T = res.transformation
    return T


def apply(T, pts):
    return pts @ T[:3, :3].T + T[:3, 3]


def fit_quality(T):
    q = o3d.core.Tensor(apply(T, xyz0[test_idx]).astype(np.float32))
    dist = scene.compute_distance(q).numpy()
    return dict(median_mm=round(float(np.median(dist) * 1000), 2),
                p90_mm=round(float(np.percentile(dist, 90) * 1000), 2),
                inlier_pct=round(float((dist < ocfg["inlier_m"]).mean() * 100), 2))


candidates = {}
for label, T0 in [("as_aligned", np.eye(4)), ("rotated_180", R180)]:
    T = refine(T0)
    q = fit_quality(T)
    pts = apply(T, xyz0)
    q["west_tile_median_x_m"] = round(float(np.median(pts[tile == W, 0])), 2) if W is not None else None
    q["east_tile_median_x_m"] = round(float(np.median(pts[tile == E, 0])), 2) if E is not None else None
    candidates[label] = dict(T=T, quality=q)
    print(f"{label:12s}: exact fit median {q['median_mm']:.2f} mm, p90 {q['p90_mm']:.2f} mm, "
          f"inliers(<{ocfg['inlier_m']*100:.0f}cm) {q['inlier_pct']:.2f}% | west tile median x = "
          f"{q['west_tile_median_x_m']} m, east tile median x = {q['east_tile_median_x_m']} m")

qa, qb = candidates["as_aligned"]["quality"], candidates["rotated_180"]["quality"]
rel_med = abs(qa["median_mm"] - qb["median_mm"]) / max(min(qa["median_mm"], qb["median_mm"]), 1e-6)
tied = rel_med <= ocfg["tie_rel_median"] and abs(qa["inlier_pct"] - qb["inlier_pct"]) <= ocfg["tie_inlier_pct"]

if not tied:
    choice = min(candidates, key=lambda k: (-candidates[k]["quality"]["inlier_pct"], candidates[k]["quality"]["median_mm"]))
    source = "geometry (one orientation fits clearly better)"
elif W is None or E is None:
    # tied and no orientation metadata: keep the alignment as is, but say so loudly
    choice = "as_aligned"
    source = ("UNRESOLVED: the two orientations fit equally well and no external orientation info "
              "(tile names, control points, georeferencing) is available -- results may be mirrored end-to-end")
    print("WARNING: " + source)
else:
    # metadata: west tile should sit at the BIM's west end
    west_low = ocfg["bim_x_direction"] == "west_to_east"
    choice = next(k for k, c in candidates.items()
                  if (c["quality"]["west_tile_median_x_m"] < c["quality"]["east_tile_median_x_m"]) == west_low)
    source = (f"external metadata: scan tile names (west/east) + ASSUMPTION that BIM +x runs "
              f"{ocfg['bim_x_direction'].replace('_', ' ')}")
print(f"\nfit difference: median {rel_med*100:.1f}% relative, inliers "
      f"{abs(qa['inlier_pct']-qb['inlier_pct']):.2f} points -> {'TIED' if tied else 'not tied'}")
print(f"chosen orientation: {choice} (decided by {source})")
if choice != "as_aligned":
    print("-> the step 4 alignment was flipped end-to-end; using the 180 deg rotated alignment")

gate = cfg["alignment"]["min_inlier_pct"]
got = candidates[choice]["quality"]["inlier_pct"]
if got < gate:
    sys.exit(f"ALIGNMENT FAILED the quality gate: only {got:.1f}% of points are within "
             f"{ocfg['inlier_m'] * 100:.0f} cm of the design (need {gate}%). Defects would be reported "
             f"from a wrong alignment, so the run stops here. Check the inputs, or raise "
             f"alignment.ransac_restarts in config.yaml.")
print(f"alignment quality gate passed: {got:.1f}% of points within {ocfg['inlier_m'] * 100:.0f} cm (need {gate}%)")

T = candidates[choice]["T"]
xyz = apply(T, xyz0)
dist = scene.compute_distance(o3d.core.Tensor(xyz.astype(np.float32))).numpy() * 1000
np.savez_compressed(OUT_DIR / "step4b_oriented.npz", xyz_aligned=xyz, rgb=rgb, dist_to_bim_mm=dist)

report = {
    "candidates": {k: v["quality"] for k, v in candidates.items()},
    "relative_median_difference": round(rel_med, 4),
    "tied": bool(tied),
    "chosen": choice,
    "decided_by": source,
    "assumption": f"BIM +x runs {ocfg['bim_x_direction'].replace('_', ' ')}" if tied else None,
    "transform_applied_to_step4_alignment": np.round(T, 6).tolist(),
}
(OUT_DIR / "orientation_check.json").write_text(json.dumps(report, indent=2))

# figure: the two candidate alignments side by side, scan tiles coloured by
# name -- both fit the BIM equally well, only the tile names tell them apart
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
sub = np.random.default_rng(1).choice(len(xyz0), 60_000, replace=False)
fig, axes = plt.subplots(2, 1, figsize=(11, 7.5), dpi=120, sharex=True)
for ax, (label, c) in zip(axes, candidates.items()):
    pts = apply(c["T"], xyz0[sub])
    ax.scatter(bim.vertices[:, 0], bim.vertices[:, 1], s=0.5, c="#b0b0b0", label="BIM design model")
    for k, colour in [(W, "#1f77b4"), (E, "#d62728")]:
        if k is not None:
            m = tile[sub] == k
            ax.scatter(pts[m, 0], pts[m, 1], s=0.3, c=colour, alpha=0.5, label=f"scan tile: {tile_names[k]}")
    q = c["quality"]
    ax.set_title(f"{label.replace('_', ' ')}: median fit {q['median_mm']} mm, {q['inlier_pct']}% within 2 cm"
                 + ("   <- chosen" if label == choice else ""), fontsize=10,
                 fontweight="bold" if label == choice else "normal")
    ax.set_aspect("equal")
    ax.set_ylabel("y across bridge (m)")
    ax.legend(loc="upper right", fontsize=7, markerscale=12)
axes[-1].set_xlabel("x along bridge (m)  -  BIM west end at low x (stated assumption)")
fig.suptitle("Orientation check: a symmetric bridge fits equally well both ways round", fontsize=12)
plt.tight_layout()
plt.savefig(OUT_DIR / "step4b_orientation_check.png")
print("saved step4b_oriented.npz, orientation_check.json, step4b_orientation_check.png")
