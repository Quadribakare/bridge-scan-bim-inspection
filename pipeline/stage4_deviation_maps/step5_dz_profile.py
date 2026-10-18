"""
Corrected vertical-shift measurement, per external review:
- match each scan point to the BIM height at the SAME (x,y) spot (2D nearest,
  not 3D nearest -- 3D nearest can snap sideways and contaminate the vertical
  reading, which is exactly what produced the wrong "got thicker" conclusion)
- only road-surface (top) and girder-underside (bottom) points
- dz = scan z - BIM z at that spot, median per 2m slice along the bridge
- plot both lines on one chart
- also scan the whole deck for any other leftover vehicles
"""
import numpy as np
import trimesh
from scipy.spatial import cKDTree
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step4b_oriented.npz")
xyz_a, rgb = d["xyz_aligned"], d["rgb"]
n = len(xyz_a)
print(f"Scan points: {n:,}")

bim = trimesh.load(OUT_DIR / "step1_bim.obj")
print("Sampling BIM surface with normals...")
bim_pts, face_idx = trimesh.sample.sample_surface(bim, 1_500_000)
bim_normals = bim.face_normals[face_idx]

top_mask = bim_normals[:, 2] > 0.5
bottom_mask = bim_normals[:, 2] < -0.5
bim_top = bim_pts[top_mask]
bim_bottom = bim_pts[bottom_mask]
print(f"BIM top-facing points: {len(bim_top):,}, bottom-facing: {len(bim_bottom):,}")

# 2D (x,y)-only trees -- find what's directly above/below, not nearest in 3D
tree_top_xy = cKDTree(bim_top[:, :2])
tree_bottom_xy = cKDTree(bim_bottom[:, :2])

dist_top_xy, idx_top = tree_top_xy.query(xyz_a[:, :2], k=1, workers=-1)
dist_bottom_xy, idx_bottom = tree_bottom_xy.query(xyz_a[:, :2], k=1, workers=-1)

# only trust a match if it's actually close in xy (within 15cm) -- otherwise
# this scan point isn't really "on" that surface at all (e.g. it's on a
# parapet or girder web, not the road or the underside)
CLOSE_XY = 0.15
is_road_point = (dist_top_xy < CLOSE_XY) & (dist_top_xy <= dist_bottom_xy)
is_underside_point = (dist_bottom_xy < CLOSE_XY) & (dist_bottom_xy < dist_top_xy)

# SANITY GUARD: with multiple girders side by side, pure xy-nearest can snap
# to the WRONG girder/component (close in plan view, very different real
# z) -- producing nonsense multi-hundred-mm "deviations" that are really just
# mismatches. Reject any match whose implied dz is further than a real defect
# could plausibly be (100mm) -- better to under-report than report a fake
# metre-scale "deviation".
PLAUSIBLE_DZ_MM = 100
dz_check_road = (xyz_a[:, 2] - bim_top[idx_top, 2]) * 1000
dz_check_under = (xyz_a[:, 2] - bim_bottom[idx_bottom, 2]) * 1000
is_road_point = is_road_point & (np.abs(dz_check_road) < PLAUSIBLE_DZ_MM)
is_underside_point = is_underside_point & (np.abs(dz_check_under) < PLAUSIBLE_DZ_MM)
print(f"(after rejecting implausible-distance matches, likely wrong-component snaps)")

print(f"\nClassified as road-surface points: {is_road_point.sum():,}")
print(f"Classified as girder-underside points: {is_underside_point.sum():,}")

dz_road = np.full(n, np.nan)
dz_road[is_road_point] = (xyz_a[is_road_point, 2] - bim_top[idx_top[is_road_point], 2]) * 1000

dz_underside = np.full(n, np.nan)
dz_underside[is_underside_point] = (xyz_a[is_underside_point, 2] - bim_bottom[idx_bottom[is_underside_point], 2]) * 1000

# median per 2m slice along the bridge, for both surfaces
x_bins = np.arange(-2, 64, 2)
road_profile, under_profile, centers = [], [], []
for i in range(len(x_bins) - 1):
    xlo, xhi = x_bins[i], x_bins[i+1]
    c = (xlo + xhi) / 2
    m_road = is_road_point & (xyz_a[:, 0] >= xlo) & (xyz_a[:, 0] < xhi)
    m_under = is_underside_point & (xyz_a[:, 0] >= xlo) & (xyz_a[:, 0] < xhi)
    r = np.median(dz_road[m_road]) if m_road.sum() > 20 else np.nan
    u = np.median(dz_underside[m_under]) if m_under.sum() > 20 else np.nan
    road_profile.append(r)
    under_profile.append(u)
    centers.append(c)
    print(f"x[{xlo:.0f},{xhi:.0f}): road dz={r:.1f}mm (n={m_road.sum()}), "
          f"underside dz={u:.1f}mm (n={m_under.sum()})")

fig, ax = plt.subplots(figsize=(14, 6))
ax.plot(centers, road_profile, 'o-', color='#4a6fa5', label='road/top-of-deck dz')
ax.plot(centers, under_profile, 'o-', color='#c0392b', label='girder-underside dz')
ax.axhline(0, color='black', linewidth=0.8)
ax.set_xlabel('position along the bridge, x (m)')
ax.set_ylabel('dz = scan height - BIM height (mm), same (x,y) spot')
ax.set_title('Vertical shift profile along the bridge (corrected: matched at same x,y)')
ax.legend()
plt.tight_layout()
plt.savefig(OUT_DIR / "step5_dz_profile.png", dpi=130)
print("\nsaved step5_dz_profile.png")

# --- check the whole deck for any other leftover vehicles ---
print("\n--- Checking the whole deck for leftover vehicles ---")
road_pts = xyz_a[is_road_point]
road_rgb = rgb[is_road_point] / max(rgb.max(), 1)
road_dz = dz_road[is_road_point]
# a vehicle sitting on the deck would show as a cluster of points sitting
# clearly ABOVE the road surface (large positive dz), not just noise
suspect = road_dz > 150  # more than 15cm above the road surface
np.savez_compressed(
    OUT_DIR / "step5_dz_points.npz",
    xyz_aligned=xyz_a, rgb=rgb,
    dz_road=dz_road, dz_underside=dz_underside,
    is_road_point=is_road_point, is_underside_point=is_underside_point,
)
print("saved step5_dz_points.npz")

print(f"Road-surface points sitting >150mm above the road: {suspect.sum():,}")
if suspect.sum() > 0:
    from scipy.ndimage import label
    pts = road_pts[suspect]
    # cluster by simple grid binning to find distinct blobs
    cell = 0.5
    ij = np.floor((pts[:, :2] - pts[:, :2].min(axis=0)) / cell).astype(int)
    grid_shape = ij.max(axis=0) + 1
    grid = np.zeros(grid_shape, dtype=bool)
    grid[ij[:, 0], ij[:, 1]] = True
    labels, n_clusters = label(grid)
    print(f"Distinct suspect clusters on the deck: {n_clusters}")
    for cl in range(1, n_clusters + 1):
        mask_cl = labels[ij[:, 0], ij[:, 1]] == cl
        if mask_cl.sum() < 5:
            continue
        cl_pts = pts[mask_cl]
        print(f"  cluster at x~{cl_pts[:,0].mean():.1f}m, y~{cl_pts[:,1].mean():.1f}m, "
              f"{mask_cl.sum()} points, height above road up to {road_dz[suspect][mask_cl].max():.0f}mm")
