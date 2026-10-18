"""
Step 4, done properly, per external review feedback:
1. Point-to-SURFACE distance via Open3D RaycastingScene (exact), not
   point-to-nearest-sampled-point.
2. Headline median computed only on points within 0.3m after alignment
   (excludes residual non-bridge/edge outliers).
3. Multi-scale point-to-plane ICP: 1.0 -> 0.3 -> 0.1 -> 0.03 m, robust kernel.
4. Report median + 90th pct after EACH round.
5. Sanity check: locate the two piers in the aligned scan, check their BIM
   x-coordinates against the expected ~18m and ~42m (flip/orientation check).
"""
import numpy as np
import open3d as o3d
import trimesh
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step3_bridge_only.npz")
xyz, rgb = d["xyz"], d["rgb"]
n0 = len(xyz)
print(f"Bridge-only scan points: {n0:,}")

# --- exact point-to-surface distance engine (Open3D RaycastingScene) ---
bim_legacy = o3d.io.read_triangle_mesh(str(OUT_DIR / "step1_bim.obj"))
bim_legacy.compute_vertex_normals()
bim_t = o3d.t.geometry.TriangleMesh.from_legacy(bim_legacy)
scene = o3d.t.geometry.RaycastingScene()
scene.add_triangles(bim_t)

def surface_dist(pts):
    q = o3d.core.Tensor(pts.astype(np.float32))
    return scene.compute_distance(q).numpy()

# --- dense BIM point cloud (2M points) with normals, for ICP correspondences ---
bim_mesh_tri = trimesh.load(OUT_DIR / "step1_bim.obj")
print("Sampling 2,000,000 BIM surface points for ICP correspondences...")
bim_dense_pts, bim_face_idx = trimesh.sample.sample_surface(bim_mesh_tri, 2_000_000)
bim_dense_normals = bim_mesh_tri.face_normals[bim_face_idx]

bim_pcd = o3d.geometry.PointCloud()
bim_pcd.points = o3d.utility.Vector3dVector(bim_dense_pts)
bim_pcd.normals = o3d.utility.Vector3dVector(bim_dense_normals)

# start from the previous best transform (position roughly right already)
prev = np.load(OUT_DIR / "step4_aligned_v2.npz")
start_pts = prev["xyz_aligned"]

scan_pcd = o3d.geometry.PointCloud()
scan_pcd.points = o3d.utility.Vector3dVector(start_pts)

def report(label, pts):
    dist = surface_dist(pts) * 1000  # mm, EXACT point-to-surface
    med_all = np.median(dist)
    p90_all = np.percentile(dist, 90)
    close = dist < 300  # within 0.3m -- the "real bridge, not outliers" subset
    med_close = np.median(dist[close])
    p90_close = np.percentile(dist[close], 90)
    print(f"[{label}] ALL pts: median {med_all:.2f}mm, p90 {p90_all:.2f}mm  |  "
          f"<0.3m subset ({close.mean()*100:.1f}% of pts): median {med_close:.2f}mm, p90 {p90_close:.2f}mm")
    return dist

report("start (before this refinement)", start_pts)

for i, thresh in enumerate([1.0, 0.3, 0.1, 0.03]):
    loss = o3d.pipelines.registration.TukeyLoss(k=thresh * 0.5)
    result = o3d.pipelines.registration.registration_icp(
        scan_pcd, bim_pcd, thresh, np.eye(4),
        o3d.pipelines.registration.TransformationEstimationPointToPlane(loss),
        o3d.pipelines.registration.ICPConvergenceCriteria(
            max_iteration=100, relative_fitness=1e-7, relative_rmse=1e-7)
    )
    scan_pcd.transform(result.transformation)
    print(f"round {i+1} (max_corr_dist={thresh}m): open3d fitness {result.fitness:.4f}, "
          f"open3d rmse {result.inlier_rmse:.4f}m")
    report(f"round {i+1}", np.asarray(scan_pcd.points))

final_pts = np.asarray(scan_pcd.points)
final_dist = surface_dist(final_pts) * 1000

# --- sanity check: pier locations, flip/orientation check ---
print("\n--- Flip/orientation sanity check ---")
z = final_pts[:, 2]
z_lo, z_hi = np.percentile(z, 1), np.percentile(z, 99)
near_ground_band = (z - z_lo) < (z_hi - z_lo) * 0.25  # lower quarter of height range = pier shafts
pier_candidates = final_pts[near_ground_band]
print(f"Near-ground (pier shaft) candidate points: {len(pier_candidates):,}")

xs = pier_candidates[:, 0]
hist, edges = np.histogram(xs, bins=60)
# find the two biggest peaks, far enough apart to be two different piers
peak_order = np.argsort(hist)[::-1]
centers = (edges[:-1] + edges[1:]) / 2
chosen = []
for idx in peak_order:
    c = centers[idx]
    if all(abs(c - c2) > 5 for c2 in chosen):
        chosen.append(c)
    if len(chosen) == 2:
        break
chosen.sort()
print(f"Detected pier x-positions (BIM coords): {chosen[0]:.1f}m and {chosen[1]:.1f}m")
print(f"Expected (3-span 18+24+18m bridge): ~18m and ~42m")
print(f"Overall scan x range: {final_pts[:,0].min():.1f}m to {final_pts[:,0].max():.1f}m "
      f"(expected ~0 to ~60m)")

np.savez_compressed(
    OUT_DIR / "step4_aligned_final.npz",
    xyz_aligned=final_pts, rgb=rgb, dist_to_bim_mm=final_dist,
)
print("\nsaved step4_aligned_final.npz")
