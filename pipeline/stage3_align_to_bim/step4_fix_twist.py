"""
Fix the residual rotation (twist) from step 4: run ICP again starting from
the current result, but in several passes with a progressively TIGHTER
correspondence distance each time. A loose single pass can leave a small
angle uncorrected (especially on a long, repetitive structure like this
bridge deck); tightening the net each pass forces it to notice and fix
that remaining twist instead of settling for "close enough".
"""
import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step3_bridge_only.npz")
xyz, rgb = d["xyz"], d["rgb"]
n0 = len(xyz)

prev = np.load(OUT_DIR / "step4_aligned.npz")
T = prev["transform"]
print("Starting from previous transform, previous median gap:",
      f"{np.median(prev['dist_to_bim'])*1000:.1f}mm")

bim = trimesh.load(OUT_DIR / "step1_bim.obj")
bim_sample_pts, bim_face_idx = trimesh.sample.sample_surface(bim, 400_000)
bim_normals = bim.face_normals[bim_face_idx]

scan_pcd = o3d.geometry.PointCloud()
scan_pcd.points = o3d.utility.Vector3dVector(xyz)
scan_pcd.transform(T)  # start from where we already got to

bim_pcd = o3d.geometry.PointCloud()
bim_pcd.points = o3d.utility.Vector3dVector(bim_sample_pts)
bim_pcd.normals = o3d.utility.Vector3dVector(bim_normals)

current_T = np.eye(4)
for i, (thresh, iters) in enumerate([(0.3, 50), (0.15, 50), (0.08, 60), (0.04, 60)]):
    loss = o3d.pipelines.registration.TukeyLoss(k=thresh * 0.5)
    result = o3d.pipelines.registration.registration_icp(
        scan_pcd, bim_pcd, thresh, current_T,
        o3d.pipelines.registration.TransformationEstimationPointToPlane(loss),
        o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=iters)
    )
    current_T = result.transformation
    scan_pcd.transform(current_T)
    current_T = np.eye(4)  # we've already applied it to scan_pcd, so reset the incremental
    print(f"pass {i+1} (threshold {thresh}m): fitness {result.fitness:.4f}, rmse {result.inlier_rmse:.4f}m")

final_pts = np.asarray(scan_pcd.points)
print("Measuring final self-consistency...")
bim_tree = cKDTree(bim_sample_pts)
dists, _ = bim_tree.query(final_pts, k=1, workers=-1)
print(f"Final self-consistency: median {np.median(dists)*1000:.1f}mm, mean {np.mean(dists)*1000:.1f}mm, "
      f"90th pct {np.percentile(dists,90)*1000:.1f}mm")

# per-strip check along x to confirm the twist is gone
xs = np.linspace(-2, 62, 17)
print("\nPer-strip median gap (should be roughly flat now, not ramping):")
for i in range(len(xs)-1):
    mask = (final_pts[:,0]>=xs[i]) & (final_pts[:,0]<xs[i+1])
    if mask.sum() > 100:
        print(f"  x[{xs[i]:.0f},{xs[i+1]:.0f}): median {np.median(dists[mask])*1000:.0f}mm, n={mask.sum()}")

np.savez_compressed(
    OUT_DIR / "step4_aligned_v2.npz",
    xyz_aligned=final_pts, xyz_original=xyz, rgb=rgb,
    dist_to_bim=dists,
)
print("\nsaved step4_aligned_v2.npz")
