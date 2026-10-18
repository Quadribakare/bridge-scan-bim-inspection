"""
Step 2 - CLEAN STRAY DOTS
Removes isolated noise points using Open3D's statistical outlier removal.
Loads outputs/step1_loaded.npz, saves outputs/step2_cleaned.npz for step 3.
"""
import numpy as np
import open3d as o3d
import matplotlib.pyplot as plt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step1_loaded.npz")
xyz, rgb, intensity, station, tile, orig_index = (
    d["xyz"], d["rgb"], d["intensity"], d["station"], d["tile"], d["orig_index"]
)
n_before = len(xyz)
print(f"Points before cleaning: {n_before:,}")

pcd = o3d.geometry.PointCloud()
pcd.points = o3d.utility.Vector3dVector(xyz)

NB_NEIGHBORS = 20
STD_RATIO = 2.0
_, keep_idx = pcd.remove_statistical_outlier(nb_neighbors=NB_NEIGHBORS, std_ratio=STD_RATIO)
keep_idx = np.array(keep_idx)
removed_idx = np.setdiff1d(np.arange(n_before), keep_idx)

n_after = len(keep_idx)
n_removed = n_before - n_after
print(f"Points after cleaning:  {n_after:,}")
print(f"Removed as stray/noise: {n_removed:,} ({n_removed/n_before*100:.2f}%)")
print(f"Settings used: nb_neighbors={NB_NEIGHBORS}, std_ratio={STD_RATIO}")

np.savez_compressed(
    OUT_DIR / "step2_cleaned.npz",
    xyz=xyz[keep_idx], rgb=rgb[keep_idx], intensity=intensity[keep_idx],
    station=station[keep_idx], tile=tile[keep_idx], orig_index=orig_index[keep_idx],
    bim_min=d["bim_min"], bim_max=d["bim_max"],
)

# --- before/after picture: removed points highlighted in red ---
rng = np.random.default_rng(0)
sample_keep = rng.choice(keep_idx, size=min(250000, len(keep_idx)), replace=False)
sample_removed = removed_idx  # show all removed points, there should be far fewer

fig, ax = plt.subplots(figsize=(10, 9))
ax.scatter(xyz[sample_keep,0], xyz[sample_keep,1], s=0.2, color='#6b8e6b', label=f'kept ({n_after:,})')
ax.scatter(xyz[sample_removed,0], xyz[sample_removed,1], s=3, color='red', label=f'removed as noise ({n_removed:,})')
ax.set_title("Step 2: stray-point removal (top-down)")
ax.set_xlabel("x (survey coords)"); ax.set_ylabel("y (survey coords)")
ax.set_aspect('equal')
ax.legend(markerscale=8)
plt.tight_layout()
plt.savefig(OUT_DIR / "step2_before_after.png", dpi=130)
print("Saved: outputs/step2_cleaned.npz, outputs/step2_before_after.png")
