"""
Step 7A, part 1: robust re-alignment. Start from the already-aligned step 4
result and run additional Tukey-reweighted ICP passes, excluding
high-residual points from each successive pass's correspondence search --
so a real localised deformation (a sag, a settled support) can't quietly
pull the whole rigid-body fit toward it. Sanity check: the structure's
ends, where it meets the ground, should read close to 0 after this.
"""
import numpy as np
import open3d as o3d
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402
cfg = yaml.safe_load((ROOT / "config.yaml").read_text())["realignment"]

d4 = np.load(OUT_DIR / "step4b_oriented.npz")
xyz_start = d4["xyz_aligned"]
n = len(xyz_start)
print(f"Points: {n:,}")

import trimesh
bim = trimesh.load(BIM_OBJ)
bim_pts, face_idx = trimesh.sample.sample_surface(bim, 2_000_000)
bim_normals = bim.face_normals[face_idx]
bim_pcd = o3d.geometry.PointCloud()
bim_pcd.points = o3d.utility.Vector3dVector(bim_pts)
bim_pcd.normals = o3d.utility.Vector3dVector(bim_normals)

from scipy.spatial import cKDTree
bim_tree = cKDTree(bim_pts)  # used only for ICP's own internal correspondence search

# EXACT point-to-surface distance (RaycastingScene) for all reporting/sanity
# checks -- nearest-sampled-point distance has its own ~18mm discretization
# noise from the 2M-point sample spacing and must never be used to judge
# alignment quality (this bit us once already earlier in this project)
bim_legacy = o3d.io.read_triangle_mesh(str(BIM_OBJ))
bim_legacy.compute_vertex_normals()
bim_scene = o3d.t.geometry.RaycastingScene()
bim_scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(bim_legacy))


def report(label, pts, sample_n=200_000):
    rng = np.random.default_rng(0)
    s = rng.choice(len(pts), size=min(sample_n, len(pts)), replace=False)
    query = o3d.core.Tensor(pts[s].astype(np.float32))
    dist = bim_scene.compute_distance(query).numpy()
    print(f"[{label}] EXACT median {np.median(dist)*1000:.2f}mm, p90 {np.percentile(dist,90)*1000:.2f}mm")


report("start", xyz_start)

scan_pcd = o3d.geometry.PointCloud()
scan_pcd.points = o3d.utility.Vector3dVector(xyz_start)
current_T = np.eye(4)
include_mask = np.ones(n, dtype=bool)

for rp in range(cfg["reweight_passes"]):
    print(f"\nrobust pass {rp+1}/{cfg['reweight_passes']}...")
    active = o3d.geometry.PointCloud()
    active.points = o3d.utility.Vector3dVector(np.asarray(scan_pcd.points)[include_mask])

    for thresh in cfg["icp_schedule_m"]:
        loss = o3d.pipelines.registration.TukeyLoss(k=thresh * 0.5)
        result = o3d.pipelines.registration.registration_icp(
            active, bim_pcd, thresh, np.eye(4),
            o3d.pipelines.registration.TransformationEstimationPointToPlane(loss),
            o3d.pipelines.registration.ICPConvergenceCriteria(
                max_iteration=cfg["icp_max_iter"], relative_fitness=1e-7, relative_rmse=1e-7))
        active.transform(result.transformation)
        scan_pcd.transform(result.transformation)

    all_pts = np.asarray(scan_pcd.points)
    dist, _ = bim_tree.query(all_pts, k=1, workers=-1)
    mad = np.median(np.abs(dist - np.median(dist)))
    robust_std = 1.4826 * mad
    threshold = cfg["reweight_mad_multiplier"] * robust_std
    include_mask = dist <= max(threshold, 1e-6)
    print(f"  residual median {np.median(dist)*1000:.2f}mm, robust std {robust_std*1000:.2f}mm, "
          f"excluding {(~include_mask).sum():,} points from next pass")

xyz_final = np.asarray(scan_pcd.points)
report("final", xyz_final)

# sanity check: the structure's ends (abutments, where it meets the ground)
# should read close to 0 -- find them generically from the BIM component
# table rather than hardcoding a position
import pandas as pd
comp = pd.read_csv(BIM_COMPONENTS)
abutments = comp[comp["element_type"] == "abutment"]
print("\n--- Sanity check: structure ends (abutments) should read ~0 ---")
for base in sorted(set(a.rsplit("_", 1)[0] if "_bank_seat" in a or "_ballast" in a or "_return" in a else a
                        for a in abutments["component"])):
    pass
abut_groups = {}
for _, row in abutments.iterrows():
    key = row["component"].split("_bank_seat")[0].split("_ballast_wall")[0].split("_return_wall")[0]
    abut_groups.setdefault(key, []).append(row)
for key, rows in abut_groups.items():
    xlo = min(r["xmin"] for r in rows)
    xhi = max(r["xmax"] for r in rows)
    cx = (xlo + xhi) / 2
    m = np.abs(xyz_final[:, 0] - cx) < 1.0
    if m.sum() > 20:
        q = o3d.core.Tensor(xyz_final[m].astype(np.float32))
        d2 = bim_scene.compute_distance(q).numpy()
        print(f"  {key} (x~{cx:.1f}m): n={m.sum()}, EXACT median dist to BIM = {np.median(d2)*1000:.2f}mm")

np.savez_compressed(OUT_DIR / "step7a_realigned.npz", xyz_aligned=xyz_final)
print("\nsaved step7a_realigned.npz")
