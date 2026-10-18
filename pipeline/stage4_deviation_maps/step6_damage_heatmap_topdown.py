"""
Same damage-size heatmap style/spec, top-down camera instead of the angled one.
"""
import numpy as np
import pyvista as pv
import matplotlib.colors as mcolors
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repository root
from paths import OUT_DIR, BIM_OBJ, BIM_COMPONENTS  # noqa: E402

d = np.load(OUT_DIR / "step4b_oriented.npz")
xyz_a = d["xyz_aligned"]
dist_mm = d["dist_to_bim_mm"]
n = len(xyz_a)
print(f"Using ALL {n:,} bridge points, no sampling")

stops = ["#173fb5", "#1f8fe0", "#2bbf8a", "#a8d933", "#f5d01a", "#f08a1a", "#d81e1e"]
positions = np.linspace(0, 1, len(stops))
cmap = mcolors.LinearSegmentedColormap.from_list("damage", list(zip(positions, stops)))
norm = mcolors.Normalize(vmin=0, vmax=30)
colour = (cmap(norm(np.clip(dist_mm, 0, 30)))[:, :3] * 255).astype(np.uint8)

cloud = pv.PolyData(xyz_a)
cloud["c"] = colour

W, H = 3840, 1600
pl = pv.Plotter(off_screen=True, window_size=[W, H])
pl.add_mesh(cloud, scalars="c", rgb=True, point_size=2, lighting=False)
pl.enable_eye_dome_lighting()
pl.set_background([3/255, 5/255, 13/255], top=[13/255, 18/255, 36/255])

center = xyz_a.mean(axis=0)
extent = xyz_a.max(axis=0) - xyz_a.min(axis=0)
pl.enable_parallel_projection()
pl.camera_position = [(center[0], center[1], center[2] + 300), (center[0], center[1], center[2]), (0, 1, 0)]
pl.camera.parallel_scale = max(extent[0], extent[1]) / 2 * 1.05

pl.screenshot(str(OUT_DIR / "heatmap_damage_size_topdown_4k.png"))
print("saved 4K render")

img = Image.open(OUT_DIR / "heatmap_damage_size_topdown_4k.png").convert("RGB")
img = img.resize((1920, 800), Image.LANCZOS)

canvas = Image.new("RGB", (1920, 900), (13, 18, 36))
canvas.paste(img, (0, 50))
draw = ImageDraw.Draw(canvas)

try:
    font_title = ImageFont.truetype("arialbd.ttf", 30)
    font_sub = ImageFont.truetype("arial.ttf", 18)
    font_lbl = ImageFont.truetype("arial.ttf", 16)
except Exception:
    font_title = font_sub = font_lbl = ImageFont.load_default()

draw.text((24, 20), "Scan vs BIM deviation heatmap (top-down)", fill=(255, 255, 255), font=font_title)
draw.text((24, 850), "blue = matches design • red = 30 mm+ off design", fill=(180, 180, 190), font=font_sub)

bar_x0, bar_x1 = 1860, 1890
bar_y0, bar_y1 = 100, 800
for y in range(bar_y0, bar_y1):
    frac = 1 - (y - bar_y0) / (bar_y1 - bar_y0)
    r, g, b, _ = cmap(frac)
    draw.line([(bar_x0, y), (bar_x1, y)], fill=(int(r*255), int(g*255), int(b*255)))
draw.rectangle([bar_x0, bar_y0, bar_x1, bar_y1], outline=(255, 255, 255), width=1)
for val in [0, 10, 20, 30]:
    y = bar_y1 - (val / 30) * (bar_y1 - bar_y0)
    draw.line([(bar_x0-6, y), (bar_x0, y)], fill=(255, 255, 255), width=1)
    draw.text((bar_x0 - 34, y - 8), str(val), fill=(220, 220, 220), font=font_lbl)
draw.text((bar_x0 - 10, bar_y0 - 28), "deviation (mm)", fill=(220, 220, 220), font=font_lbl)

canvas.save(OUT_DIR / "heatmap_damage_size_topdown.png")
print("saved outputs/heatmap_damage_size_topdown.png")
