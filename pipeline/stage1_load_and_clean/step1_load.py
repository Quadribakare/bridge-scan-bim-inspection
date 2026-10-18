"""
Step 1 - LOAD
Reads the BIM mesh and both scan tiles (west + east), merges the scans,
and saves everything to outputs/step1_loaded.npz + outputs/step1_bim.obj
so step 2 can load the result without re-reading the raw files.
"""
import numpy as np
import trimesh
import laspy
import matplotlib.pyplot as plt
from pathlib import Path

from paths import OUT_DIR, BIM_OBJ as BIM_PATH, SCANS, tile_name

print("Loading BIM mesh...")
bim = trimesh.load(BIM_PATH)
bim_min, bim_max = bim.bounds
print(f"  BIM triangles: {len(bim.faces):,}")
print(f"  BIM bounding box: x[{bim_min[0]:.2f}, {bim_max[0]:.2f}]  "
      f"y[{bim_min[1]:.2f}, {bim_max[1]:.2f}]  z[{bim_min[2]:.2f}, {bim_max[2]:.2f}]")

def load_tile(path, tile_name, tile_id):
    print(f"Loading scan tile: {tile_name}...")
    las = laspy.read(path)
    xyz = np.vstack([las.x, las.y, las.z]).T
    rgb = np.vstack([las.red, las.green, las.blue]).T
    intensity = np.array(las.intensity)
    station = np.array(las.point_source_id)
    n = len(xyz)
    tile_col = np.full(n, tile_id, dtype=np.int8)
    orig_idx = np.arange(n, dtype=np.int64)  # index within this tile's original LAZ file
    print(f"  points: {n:,}")
    print(f"  x[{xyz[:,0].min():.2f}, {xyz[:,0].max():.2f}]  "
          f"y[{xyz[:,1].min():.2f}, {xyz[:,1].max():.2f}]  "
          f"z[{xyz[:,2].min():.2f}, {xyz[:,2].max():.2f}]")
    return xyz, rgb, intensity, station, tile_col, orig_idx

tiles = [load_tile(p, tile_name(p), i) for i, p in enumerate(SCANS)]
tile_names = np.array([tile_name(p) for p in SCANS])

xyz = np.vstack([t[0] for t in tiles])
rgb = np.vstack([t[1] for t in tiles])
intensity = np.concatenate([t[2] for t in tiles])
station = np.concatenate([t[3] for t in tiles])
tile = np.concatenate([t[4] for t in tiles])          # which file a point came from (index into tile_names)
orig_index = np.concatenate([t[5] for t in tiles])    # index within that original file -- for step 9 lookup

print(f"\nMerged scan total: {len(xyz):,} points")
print(f"Merged bounding box: x[{xyz[:,0].min():.2f}, {xyz[:,0].max():.2f}]  "
      f"y[{xyz[:,1].min():.2f}, {xyz[:,1].max():.2f}]  z[{xyz[:,2].min():.2f}, {xyz[:,2].max():.2f}]")

np.savez_compressed(
    OUT_DIR / "step1_loaded.npz",
    xyz=xyz, rgb=rgb, intensity=intensity, station=station,
    tile=tile, orig_index=orig_index, tile_names=tile_names,
    bim_min=bim_min, bim_max=bim_max,
)
bim.export(OUT_DIR / "step1_bim.obj")

# --- top-down pictures ---
fig, axes = plt.subplots(1, 2, figsize=(16, 7))

axes[0].scatter(bim.vertices[:,0], bim.vertices[:,1], s=0.3, color='#4a6fa5')
axes[0].set_title("BIM design model (top-down, x vs y)")
axes[0].set_xlabel("x (m, along bridge)"); axes[0].set_ylabel("y (m, across)")
axes[0].set_aspect('equal')

sample = np.random.default_rng(0).choice(len(xyz), size=min(300000, len(xyz)), replace=False)
rgb_norm = np.clip(rgb[sample] / max(rgb.max(), 1), 0, 1)  # LAS stores colour as 16-bit, not 8-bit
axes[1].scatter(xyz[sample,0], xyz[sample,1], s=0.2, c=rgb_norm)
axes[1].set_title(f"Laser scan, merged {'+'.join(tile_names)} (top-down, {len(xyz):,} pts, {len(sample):,} shown)")
axes[1].set_xlabel("x (survey coords)"); axes[1].set_ylabel("y (survey coords)")
axes[1].set_aspect('equal')

plt.tight_layout()
plt.savefig(OUT_DIR / "step1_topdown.png", dpi=130)
print(f"\nSaved: outputs/step1_loaded.npz, outputs/step1_bim.obj, outputs/step1_topdown.png")
