"""
Render the extracted bridge-only points (the real connected island) using
their real scanned colours, not the white/blue classification colours --
both views, saved as normal-looking pictures.
"""
import numpy as np
import pyvista as pv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step2_cleaned.npz")
xyz_all, rgb_all = d["xyz"], d["rgb"]
is_bridge = np.load(OUT_DIR / "step3_connected_bridge.npz")["is_bridge"]

xyz = xyz_all[is_bridge]
rgb = rgb_all[is_bridge]
print(f"Bridge-only points: {len(xyz):,} / {len(xyz_all):,}")

rgb_norm = (np.clip(rgb / max(rgb_all.max(), 1), 0, 1) * 255).astype(np.uint8)

center = xyz.mean(axis=0)
extent = xyz.max(axis=0) - xyz.min(axis=0)

cloud = pv.PolyData(xyz)
cloud["rgb"] = rgb_norm
pl = pv.Plotter(off_screen=True, window_size=[1400, 1600])
pl.add_mesh(cloud, scalars="rgb", rgb=True, point_size=3)
pl.set_background("white")
pl.enable_parallel_projection()
pl.camera_position = [(center[0], center[1], center[2] + 300),
                       (center[0], center[1], center[2]), (0, 1, 0)]
pl.camera.parallel_scale = max(extent[0], extent[1]) / 2 * 1.05
pl.screenshot(str(OUT_DIR / "step3_bridge_only_topdown.png"))
print("saved step3_bridge_only_topdown.png")

z_all = xyz_all[:, 2]
center_all = xyz_all.mean(axis=0)
band = (z_all > np.percentile(z_all, 80)) & (z_all < np.percentile(z_all, 96))
xy_band = xyz_all[band, :2] - center_all[:2]
cov = np.cov(xy_band.T)
eigvals, eigvecs = np.linalg.eigh(cov)
long_axis = eigvecs[:, np.argmax(eigvals)]
perp_axis = np.array([-long_axis[1], long_axis[0]])
target_xy, target_z = center_all[:2], center_all[2]
distance_3d, height_offset = 75.0, 15.0
horiz_dist = np.sqrt(distance_3d**2 - height_offset**2)
angle_off_axis = np.radians(43)
along = -np.cos(angle_off_axis) * horiz_dist
cross = -np.sin(angle_off_axis) * horiz_dist
cam_xy = target_xy + along * long_axis + cross * perp_axis
cam_z = target_z + height_offset

pl2 = pv.Plotter(off_screen=True, window_size=[1920, 1080])
pl2.add_mesh(cloud, scalars="rgb", rgb=True, point_size=2, lighting=False)
pl2.enable_eye_dome_lighting()
pl2.set_background([0.03, 0.03, 0.06], top=[0.16, 0.18, 0.26])
pl2.camera_position = [(cam_xy[0], cam_xy[1], cam_z), (target_xy[0], target_xy[1], target_z), (0, 0, 1)]
aspect = 1920 / 1080
horiz_fov = np.radians(50)
vert_fov = 2 * np.arctan(np.tan(horiz_fov / 2) / aspect)
pl2.camera.view_angle = np.degrees(vert_fov)
pl2.screenshot(str(OUT_DIR / "step3_bridge_only_angled.png"))
print("saved step3_bridge_only_angled.png")

np.savez_compressed(OUT_DIR / "step3_bridge_only.npz", xyz=xyz, rgb=rgb)
print("saved step3_bridge_only.npz")
