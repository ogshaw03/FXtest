"""Bug 2 deep diag - measure knee drift with ankle pinned to bind pos."""
import sys, json, traceback, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

REPO = r"E:/OG_Tools/FXtest"
FBX  = r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"
OUT  = r"E:/OG_Tools/FXtest/diag/_bug2_deep_result.json"
sys.path.insert(0, REPO)

def pos(n): return cmds.xform(n, q=True, ws=True, t=True)

result = {"depths": {}, "errors": []}

try:
    cmds.file(new=True, force=True)
    if not cmds.pluginInfo("fbxmaya", q=True, l=True):
        cmds.loadPlugin("fbxmaya")
    try:
        _mel_cmd = chr(70) + "BXImport -f " + chr(34) + FBX + chr(34) + ";"
        mel.eval(_mel_cmd)
    except Exception:
        cmds.file(FBX, i=True, type="FBX", ignoreVersion=True)

    import attach_ctrls, fbx_renamer, importlib
    importlib.reload(fbx_renamer); importlib.reload(attach_ctrls)

    setup = attach_ctrls.full_auto_setup(scale=1.0, skip_decoration=False, delete_junk=True)

    for c in ("arm_L","arm_R","leg_L","leg_R"):
        try: cmds.setAttr(c+"_UI_ctl.IK_FK", 1)
        except Exception: pass

    waist = "waist_ctl"
    base_ty = cmds.getAttr(waist+".translateY")

    # bind snapshot (no waist move)
    result["bind"] = {
        "hip_L": pos("leg_L"), "knee_L": pos("knee_L"), "ank_L": pos("ankle_L"),
        "ik_ctl_L": pos("leg_L_IK_ctl"), "ik_npo_L": pos("leg_L_IK_npo"),
        "pv_L": pos("leg_L_PV_ctl"),
    }
    if cmds.objExists("leg_L_PV_dyn_helper"):
        result["bind"]["dyn_helper"] = pos("leg_L_PV_dyn_helper")

    knee_L_bind = result["bind"]["knee_L"]
    ik_ctl_bind_L = result["bind"]["ik_ctl_L"]

    # for each depth, DON'T move IK ctl — foot planted (proper Bug 2 test)
    for dy in (-5, -10, -15, -20, -25, -30, -35):
        cmds.setAttr(waist+".translateY", base_ty + dy)
        rec = {
            "hip_L": pos("leg_L"),
            "knee_L": pos("knee_L"),
            "ank_L": pos("ankle_L"),
            "ik_ctl_L": pos("leg_L_IK_ctl"),
            "pv_L": pos("leg_L_PV_ctl"),
        }
        if cmds.objExists("leg_L_PV_dyn_helper"):
            rec["dyn_helper"] = pos("leg_L_PV_dyn_helper")
        # knee X drift from bind
        rec["knee_L_dx"] = rec["knee_L"][0] - knee_L_bind[0]
        rec["knee_L_dy"] = rec["knee_L"][1] - knee_L_bind[1]
        rec["knee_L_dz"] = rec["knee_L"][2] - knee_L_bind[2]
        # Did IK ctl move? (should be 0)
        rec["ik_ctl_drift"] = math.sqrt(sum((rec["ik_ctl_L"][i]-ik_ctl_bind_L[i])**2 for i in range(3)))
        # perp of knee from hip-ankle line (bend amount)
        hip = rec["hip_L"]; ank = rec["ank_L"]; knee = rec["knee_L"]
        se = [ank[i]-hip[i] for i in range(3)]
        len2 = sum(a*a for a in se)
        if len2 > 1e-9:
            t = sum((knee[i]-hip[i])*se[i] for i in range(3)) / len2
            proj = [hip[i] + t*se[i] for i in range(3)]
        else:
            proj = list(knee)
        rec["perp_from_line"] = [knee[i]-proj[i] for i in range(3)]
        result["depths"][str(dy)] = rec
    cmds.setAttr(waist+".translateY", base_ty)
    result["verdict"] = "OK"

except Exception as exc:
    result["fatal"] = str(exc)
    result["trace"] = traceback.format_exc()

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
print("=== DONE ===", OUT)
