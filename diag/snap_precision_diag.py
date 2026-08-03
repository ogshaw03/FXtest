"""SNAP PRECISION scout - regression test for v0.9.29."""
import sys, json, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

REPO = r"E:/OG_Tools/FXtest"
FBX  = r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"
OUT  = r"E:/OG_Tools/FXtest/diag/_snap_precision_result.json"
sys.path.insert(0, REPO)

def pos(n): return cmds.xform(n, q=True, ws=True, t=True)
def dist(a,b): return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))

result = {"tests":{}}

cmds.file(new=True, force=True)
if not cmds.pluginInfo("fbxmaya", q=True, l=True):
    cmds.loadPlugin("fbxmaya")
try:
    mel.eval('FBXImport -f "' + FBX + '";')
except Exception:
    cmds.file(FBX, i=True, type="FBX", ignoreVersion=True)

import attach_ctrls, fbx_renamer, importlib
importlib.reload(fbx_renamer); importlib.reload(attach_ctrls)
attach_ctrls.full_auto_setup(scale=1.0, skip_decoration=False, delete_junk=True)

# --- Test A: arm_L IK move/rotate -> snap_fk_to_ik -> wrist WS shift
ui = "arm_L_UI_ctl"
ik = "arm_L_IK_ctl"
wrist = "wrist_L"
cmds.setAttr(ui + ".IK_FK", 1)  # IK mode
# move + rotate the IK ctl
cmds.setAttr(ik + ".translateX", cmds.getAttr(ik+".translateX") + 3.0)
cmds.setAttr(ik + ".translateY", cmds.getAttr(ik+".translateY") + 2.0)
cmds.setAttr(ik + ".rotateZ", cmds.getAttr(ik+".rotateZ") + 25.0)
wrist_before_IK = pos(wrist)
attach_ctrls.snap_fk_to_ik("arm_L")
wrist_after_FK = pos(wrist)
result["tests"]["snap_arm_L_wrist_shift"] = {
    "wrist_before_IK": wrist_before_IK,
    "wrist_after_FK": wrist_after_FK,
    "shift": dist(wrist_before_IK, wrist_after_FK),
    "ikfk_after": cmds.getAttr(ui + ".IK_FK"),
}

# --- Test B: leg_L FK 45° -> snap_ik_to_fk -> knee WS shift
ui = "leg_L_UI_ctl"
knee = "knee_L"
cmds.setAttr(ui + ".IK_FK", 0)  # FK mode
cmds.setAttr("leg_L_fk_ctl.rotateX", 45.0)
knee_before_FK = pos(knee)
attach_ctrls.snap_ik_to_fk("leg_L")
knee_after_IK = pos(knee)
result["tests"]["snap_leg_L_knee_shift"] = {
    "knee_before_FK": knee_before_FK,
    "knee_after_IK": knee_after_IK,
    "shift": dist(knee_before_FK, knee_after_IK),
    "ikfk_after": cmds.getAttr(ui + ".IK_FK"),
}

with open(OUT, "w") as f:
    json.dump(result, f, indent=2)
print("=== SNAP PRECISION done ===")
print("arm_L wrist shift =", result["tests"]["snap_arm_L_wrist_shift"]["shift"])
print("leg_L knee  shift =", result["tests"]["snap_leg_L_knee_shift"]["shift"])
