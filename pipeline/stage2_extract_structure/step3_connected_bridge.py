"""
Grow the bridge region from a confident deck seed, following only points
that are physically connected (close together in real 3D space) -- this
should pull in the full pier down to its base (solid, touching, no gap)
while leaving disconnected trees alone (real air gap, never touched).
Uses a voxel-grid dilation for speed on 7.8M points instead of a per-point
KD-tree BFS.
"""
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step2_cleaned.npz")
xyz = d["xyz"]
n = len(xyz)

# height of every point above its own LOCAL ground (not one global number) --
# a 2m x 2m grid, local ground = the 10th percentile height within each cell
import pandas as pd
CELL = 2.0
mins2d = xyz[:, :2].min(axis=0)
cell_idx = np.floor((xyz[:, :2] - mins2d) / CELL).astype(np.int64)
flat_idx = cell_idx[:, 0] * (cell_idx[:, 1].max() + 1) + cell_idx[:, 1]
ground_df = pd.DataFrame({"cell": flat_idx, "z": xyz[:, 2]})
ground_per_cell = ground_df.groupby("cell")["z"].quantile(0.10)
local_ground_z = ground_per_cell.loc[flat_idx].values
h = xyz[:, 2] - local_ground_z
print(f"Height-above-local-ground computed for {n:,} points")

# confident seed: only the clear, unambiguous deck/parapet cluster (well above
# the ground/vehicle/tree-trunk range, from the earlier percentile gap)
SEED_THRESH = 4.0
is_seed = h > SEED_THRESH
print(f"Seed (confident deck) points: {is_seed.sum():,}")

# voxelize at a coarse-ish resolution for a fast connectivity grid
VOXEL = 0.15  # metres
mins = xyz.min(axis=0)
vox = np.floor((xyz - mins) / VOXEL).astype(np.int64)

# map each occupied voxel to whether it contains a seed point
import pandas as pd
df = pd.DataFrame({"vx": vox[:, 0], "vy": vox[:, 1], "vz": vox[:, 2], "seed": is_seed})
vox_seed = df.groupby(["vx", "vy", "vz"])["seed"].any()
occupied = set(map(tuple, vox[:, :3]))
frontier = set(vox_seed[vox_seed].index.tolist())
bridge_voxels = set(frontier)
print(f"Seed voxels: {len(frontier):,}")

neighbours = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1) if (dx, dy, dz) != (0, 0, 0)]

MAX_ITERS = 25  # ~25*0.15m = 3.75m growth radius from the seed, enough to reach pier bases
for it in range(MAX_ITERS):
    new_frontier = set()
    for (vx, vy, vz) in frontier:
        for dx, dy, dz in neighbours:
            cand = (vx + dx, vy + dy, vz + dz)
            if cand in occupied and cand not in bridge_voxels:
                new_frontier.add(cand)
    if not new_frontier:
        print(f"stopped growing at iteration {it} (no new voxels)")
        break
    bridge_voxels |= new_frontier
    frontier = new_frontier
    if it % 5 == 0:
        print(f"iter {it}: +{len(new_frontier):,} voxels, total {len(bridge_voxels):,}")

print(f"Final bridge voxel count (before isolating the real bridge island): {len(bridge_voxels):,}")

# IMPORTANT FIX: different seeds (deck AND any tall tree) all grew independently
# into the same set, so a tall tree becomes its own separate white island that
# never actually touched the deck. Label connected components properly and
# keep only the single largest one -- the deck+piers are one unbroken ~120m
# structure, far bigger than any standalone tree island.
from scipy import ndimage

bv = np.array(list(bridge_voxels))
grid_shape = (vox[:, 0].max() + 1, vox[:, 1].max() + 1, vox[:, 2].max() + 1)
print(f"Building dense grid {grid_shape} for component labelling...")
grid = np.zeros(grid_shape, dtype=bool)
grid[bv[:, 0], bv[:, 1], bv[:, 2]] = True

structure = np.ones((3, 3, 3), dtype=int)  # full 26-connectivity
labels, n_components = ndimage.label(grid, structure=structure)
print(f"Found {n_components} separate connected islands")

sizes = ndimage.sum(grid, labels, index=range(1, n_components + 1))
main_label = np.argmax(sizes) + 1
print(f"Largest island (the real bridge): label {main_label}, {int(sizes[main_label-1]):,} voxels "
      f"(2nd biggest: {int(sorted(sizes)[-2]):,} voxels, likely a tree)")

main_bridge_voxels = set(map(tuple, np.argwhere(labels == main_label)))
vox_tuples = list(map(tuple, vox[:, :3]))
is_bridge_connected = np.array([v in main_bridge_voxels for v in vox_tuples])
print(f"Points classified as the real connected bridge: {is_bridge_connected.sum():,} / {n:,} ({is_bridge_connected.sum()/n*100:.1f}%)")

np.savez_compressed(OUT_DIR / "step3_connected_bridge.npz", is_bridge=is_bridge_connected)
print("saved step3_connected_bridge.npz")
