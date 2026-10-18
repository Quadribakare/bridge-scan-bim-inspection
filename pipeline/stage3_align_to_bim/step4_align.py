"""
Step 4 - ALIGN the clean, bridge-only extraction (from step 3) to the BIM.
Coarse alignment (RANSAC+FPFH) -> fine alignment (point-to-plane ICP).
This time the scan going in is already just the real bridge (2.8M points,
geometrically verified) -- no grass, road, vehicles, or trees confusing the
match like in the earlier failed attempts.
"""
import numpy as np
import open3d as o3d
import trimesh
import yaml
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step3_bridge_only.npz")
xyz, rgb = d["xyz"], d["rgb"]
n0 = len(xyz)
print(f"Clean bridge-only scan points entering step 4: {n0:,}")

bim = trimesh.load(OUT_DIR / "step1_bim.obj")
bim_sample_pts, bim_face_idx = trimesh.sample.sample_surface(bim, 400_000, seed=0)
bim_normals = bim.face_normals[bim_face_idx]
print(f"BIM surface sampled: {len(bim_sample_pts):,} points")

scan_pcd = o3d.geometry.PointCloud()
scan_pcd.points = o3d.utility.Vector3dVector(xyz)

bim_pcd = o3d.geometry.PointCloud()
bim_pcd.points = o3d.utility.Vector3dVector(bim_sample_pts)
bim_pcd.normals = o3d.utility.Vector3dVector(bim_normals)

VOXEL = 0.25
scan_down = scan_pcd.voxel_down_sample(VOXEL)
bim_down = bim_pcd.voxel_down_sample(VOXEL)
scan_down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=VOXEL*2, max_nn=30))
bim_down.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=VOXEL*2, max_nn=30))
print(f"Downsampled for coarse align: scan {len(scan_down.points):,} pts, BIM {len(bim_down.points):,} pts")

radius_feature = VOXEL * 5
scan_fpfh = o3d.pipelines.registration.compute_fpfh_feature(
    scan_down, o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))
bim_fpfh = o3d.pipelines.registration.compute_fpfh_feature(
    bim_down, o3d.geometry.KDTreeSearchParamHybrid(radius=radius_feature, max_nn=100))

# RANSAC is random, and a structure with repeating piers and spans has many
# near-matches it can lock onto (seen: a run where a quarter of the bridge
# ended up metres off). So: several RANSAC starts with fixed seeds (repeatable),
# each refined with a quick ICP on a subsample and scored by the fraction of
# points that end up close to the design; the best start wins.
from scipy.spatial import cKDTree  # noqa: E402
acfg = yaml.safe_load((ROOT / "config.yaml").read_text())["alignment"]
distance_threshold = VOXEL * 1.5
bim_tree = cKDTree(bim_sample_pts)
sub = np.random.default_rng(0).choice(n0, size=min(200_000, n0), replace=False)
sub_pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz[sub]))


def refine_and_score(T0):
    """Multi-scale ICP on a subsample from a starting transform; score = share of
    points within score_within_m of the design afterwards."""
    T = T0
    for thr in (3.0, 1.0, 0.5, 0.2):
        T = o3d.pipelines.registration.registration_icp(
            sub_pcd, bim_pcd, thr, T,
            o3d.pipelines.registration.TransformationEstimationPointToPlane(
                o3d.pipelines.registration.TukeyLoss(k=thr / 2)),
            o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=30)).transformation
    moved = xyz[sub] @ T[:3, :3].T + T[:3, 3]
    return float((bim_tree.query(moved, k=1, workers=-1)[0] < acfg["score_within_m"]).mean()), T


candidates = []

# deterministic candidates from the principal axes: a bridge is long and thin,
# and survey scans are captured with z up, so lining up the long axes of the
# scan and the design (plan view) leaves only the heading: two options, 180 deg
# apart. Skipped if either shape is not clearly elongated.
def long_axis(pts2d):
    c = pts2d.mean(axis=0)
    w, v = np.linalg.eigh(np.cov((pts2d - c).T))
    return c, v[:, 1], np.sqrt(w[1] / max(w[0], 1e-12))


cs, axs, els = long_axis(xyz[sub][:, :2])
cb, axb, elb = long_axis(bim_sample_pts[:, :2])
if min(els, elb) >= acfg["min_elongation"]:
    base = np.arctan2(axb[1], axb[0]) - np.arctan2(axs[1], axs[0])
    zs, zb = np.median(xyz[sub][:, 2]), np.median(bim_sample_pts[:, 2])
    for k, yaw in enumerate((base, base + np.pi)):
        R = np.array([[np.cos(yaw), -np.sin(yaw), 0], [np.sin(yaw), np.cos(yaw), 0], [0, 0, 1]])
        T0 = np.eye(4)
        T0[:3, :3] = R
        T0[:3, 3] = np.r_[cb - R[:2, :2] @ cs, zb - zs]
        score, T = refine_and_score(T0)
        print(f"  principal-axis start {k} (heading {np.degrees(yaw) % 360:.1f} deg): "
              f"{score * 100:.1f}% of points within {acfg['score_within_m'] * 100:.0f} cm of the design")
        candidates.append((score, T, f"principal axes, heading {k}"))
else:
    print(f"  shapes not clearly elongated (scan {els:.1f}, design {elb:.1f}): principal-axis starts skipped")

best = None
print(f"Running RANSAC global registration (coarse alignment), {acfg['ransac_restarts']} seeded starts...")
for seed in range(acfg["ransac_restarts"]):
    o3d.utility.random.seed(seed)
    r = o3d.pipelines.registration.registration_ransac_based_on_feature_matching(
        scan_down, bim_down, scan_fpfh, bim_fpfh, True,
        distance_threshold,
        o3d.pipelines.registration.TransformationEstimationPointToPoint(False),
        3,
        [
            o3d.pipelines.registration.CorrespondenceCheckerBasedOnEdgeLength(0.9),
            o3d.pipelines.registration.CorrespondenceCheckerBasedOnDistance(distance_threshold),
        ],
        # (max_iteration, confidence): confidence must be a probability -- a value
        # above 1 can never be reached, so RANSAC would always run all iterations
        o3d.pipelines.registration.RANSACConvergenceCriteria(4_000_000, 0.999)
    )
    score, T = refine_and_score(r.transformation)
    print(f"  RANSAC start {seed}: fitness {r.fitness:.3f} -> after refinement, "
          f"{score * 100:.1f}% of points within {acfg['score_within_m'] * 100:.0f} cm of the design")
    candidates.append((score, T, f"RANSAC seed {seed}"))
best_score, init_T, best_label = max(candidates, key=lambda c: c[0])
print(f"best start: {best_label} ({best_score * 100:.1f}% within {acfg['score_within_m'] * 100:.0f} cm)")

print("Running point-to-plane ICP (fine alignment)...")
loss = o3d.pipelines.registration.TukeyLoss(k=0.1)
result_icp = o3d.pipelines.registration.registration_icp(
    scan_pcd, bim_pcd, 0.5, init_T,
    o3d.pipelines.registration.TransformationEstimationPointToPlane(loss),
    o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=60)
)
print(f"Fine (ICP) fitness: {result_icp.fitness:.4f}  inlier RMSE: {result_icp.inlier_rmse:.4f} m")

T = result_icp.transformation
xyz_h = np.hstack([xyz, np.ones((n0, 1))])
xyz_aligned = (T @ xyz_h.T).T[:, :3]

print("Measuring distance of every aligned point to nearest BIM surface point (self-consistency check)...")
dists, _ = bim_tree.query(xyz_aligned, k=1, workers=-1)
print(f"Self-consistency: median {np.median(dists)*1000:.1f}mm, mean {np.mean(dists)*1000:.1f}mm, "
      f"90th pct {np.percentile(dists,90)*1000:.1f}mm")

np.savez_compressed(
    OUT_DIR / "step4_aligned.npz",
    xyz_aligned=xyz_aligned, xyz_original=xyz, rgb=rgb,
    transform=T, icp_fitness=result_icp.fitness, icp_rmse=result_icp.inlier_rmse,
    start_score=best_score, start_label=best_label,
    dist_to_bim=dists,
)
print("saved step4_aligned.npz")

fig, axes = plt.subplots(1, 2, figsize=(16, 8))
rng = np.random.default_rng(0)
s0 = rng.choice(n0, size=min(300000, n0), replace=False)
axes[0].scatter(xyz_aligned[s0,0], xyz_aligned[s0,1], s=0.3, color='#4a6fa5', label='aligned scan (bridge-only)')
axes[0].scatter(bim_sample_pts[::20,0], bim_sample_pts[::20,1], s=0.5, color='red', alpha=0.5, label='BIM surface')
axes[0].set_title("Aligned bridge-only scan vs BIM (top-down)")
axes[0].legend(markerscale=10); axes[0].set_aspect('equal')

axes[1].hist(dists*1000, bins=100, range=(0, 300))
axes[1].set_xlabel("distance to nearest BIM surface (mm)")
axes[1].set_title("Self-consistency distance distribution")

plt.tight_layout()
plt.savefig(OUT_DIR / "step4_align_result.png", dpi=130)
print("saved step4_align_result.png")
