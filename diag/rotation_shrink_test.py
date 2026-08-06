"""Verify rotation_shrink: angle unchanged, values minimized."""
import sys, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds

sys.path.insert(0, r"E:/OG_Tools/FXtest")

def report(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {extra}")
    return ok

# ---- Test 1: static large value collapses to 0 ----
cmds.file(new=True, force=True)
loc = cmds.spaceLocator(n="tgt1")[0]
cmds.setAttr(loc + ".rotateY", 720.0)
before_mat = cmds.xform(loc, q=True, ws=True, m=True)
import rotation_shrink
rotation_shrink.shrink_rotations([loc], bake=False, apply_euler_filter=False)
after_val = cmds.getAttr(loc + ".rotateY")
after_mat = cmds.xform(loc, q=True, ws=True, m=True)
mat_diff = sum(abs(b - a) for b, a in zip(before_mat, after_mat))
report("static_720_becomes_0", abs(after_val) < 0.5, f"after={after_val:.3f}")
report("static_matrix_preserved", mat_diff < 1e-4, f"diff={mat_diff:.6f}")

# ---- Test 2: keyed constant spin gets shifted ----
cmds.file(new=True, force=True)
loc = cmds.spaceLocator(n="tgt2")[0]
cmds.playbackOptions(min=1, max=60)
# Keys: frame 1 = 0, frame 30 = 360, frame 60 = 720 (a 2-turn spin)
for t, v in [(1, 0), (30, 360), (60, 720)]:
    cmds.setKeyframe(loc, attribute="rotateY", time=t, value=v)

# Sample the visual orientation at several frames BEFORE
before_samples = {}
for t in (1, 15, 30, 45, 60):
    cmds.currentTime(t)
    before_samples[t] = cmds.xform(loc, q=True, ws=True, m=True)

rotation_shrink.shrink_rotations([loc], bake=True, apply_euler_filter=True,
                                  time_range=(1, 60))

# After: values should be centered around 0
values_after = cmds.keyframe(loc + ".rotateY", q=True, valueChange=True) or []
max_abs = max(abs(v) for v in values_after) if values_after else 0
report("spin_values_reduced", max_abs <= 360.5,
       f"before_max=720, after_max={max_abs:.1f}")

# Visual orientation must be same at every sampled frame
max_mat_diff = 0
for t in (1, 15, 30, 45, 60):
    cmds.currentTime(t)
    after_m = cmds.xform(loc, q=True, ws=True, m=True)
    diff = sum(abs(b - a) for b, a in zip(before_samples[t], after_m))
    max_mat_diff = max(max_mat_diff, diff)
report("spin_visual_preserved", max_mat_diff < 1e-3, f"max_diff={max_mat_diff:.6f}")

# ---- Test 3: three axes independently shifted ----
cmds.file(new=True, force=True)
loc = cmds.spaceLocator(n="tgt3")[0]
cmds.setAttr(loc + ".rotateX", 1080.0)
cmds.setAttr(loc + ".rotateY", -720.0)
cmds.setAttr(loc + ".rotateZ", 45.0)
before_mat = cmds.xform(loc, q=True, ws=True, m=True)
rotation_shrink.shrink_rotations([loc], bake=False, apply_euler_filter=False)
rx = cmds.getAttr(loc + ".rotateX")
ry = cmds.getAttr(loc + ".rotateY")
rz = cmds.getAttr(loc + ".rotateZ")
after_mat = cmds.xform(loc, q=True, ws=True, m=True)
mat_diff = sum(abs(b - a) for b, a in zip(before_mat, after_mat))
report("three_axes_shrunk", abs(rx) < 0.5 and abs(ry) < 0.5 and abs(rz - 45) < 0.5,
       f"rx={rx:.2f} ry={ry:.2f} rz={rz:.2f}")
report("three_axes_matrix_preserved", mat_diff < 1e-4, f"diff={mat_diff:.6f}")

# ---- Test 4: axis exclusion respected ----
cmds.file(new=True, force=True)
loc = cmds.spaceLocator(n="tgt4")[0]
cmds.setAttr(loc + ".rotateX", 720.0)
cmds.setAttr(loc + ".rotateY", 720.0)
# only shrink X
rotation_shrink.shrink_rotations([loc], axes=("rotateX",),
                                  bake=False, apply_euler_filter=False)
rx = cmds.getAttr(loc + ".rotateX")
ry = cmds.getAttr(loc + ".rotateY")
report("axis_exclusion_x_shrunk", abs(rx) < 0.5, f"rx={rx:.2f}")
report("axis_exclusion_y_untouched", abs(ry - 720) < 0.5, f"ry={ry:.2f}")

# ---- Test 5: negative large values ----
cmds.file(new=True, force=True)
loc = cmds.spaceLocator(n="tgt5")[0]
cmds.setAttr(loc + ".rotateZ", -1440.0)
before_mat = cmds.xform(loc, q=True, ws=True, m=True)
rotation_shrink.shrink_rotations([loc], bake=False, apply_euler_filter=False)
after_z = cmds.getAttr(loc + ".rotateZ")
after_mat = cmds.xform(loc, q=True, ws=True, m=True)
mat_diff = sum(abs(b - a) for b, a in zip(before_mat, after_mat))
report("negative_1440_becomes_0", abs(after_z) < 0.5, f"after={after_z:.2f}")
report("negative_matrix_preserved", mat_diff < 1e-4)

print("=== ROTATION SHRINK TESTS DONE ===")
