"""Verify mapping API + UDE-style rename override."""
import sys, json, traceback
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

sys.path.insert(0, r"E:/OG_Tools/FXtest")

def report(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {extra}")
    return ok

result = {"tests": []}
try:
    # ---- Test 1: auto_detect_mapping on Nekotatune ----
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval(chr(70)+"BXImport -f "+chr(34)+r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"+chr(34)+";")
    import attach_ctrls, fbx_renamer, importlib
    importlib.reload(fbx_renamer); importlib.reload(attach_ctrls)
    # ensure names normalized
    fbx_renamer.remove_all_namespaces()
    fbx_renamer.rename_all_joints()

    detected = attach_ctrls.auto_detect_mapping()
    fixed = detected.get("fixed", {})
    ok = (
        fixed.get("arm_L") == ["arm_L", "elbow_L", "wrist_L"] and
        fixed.get("arm_R") == ["arm_R", "elbow_R", "wrist_R"] and
        fixed.get("leg_L") == ["leg_L", "knee_L", "ankle_L"] and
        fixed.get("leg_R") == ["leg_R", "knee_R", "ankle_R"]
    )
    report("auto_detect_mapping_finds_all_4_chains", ok, f"got {list(fixed.keys())}")

    # ---- Test 2: set/get roundtrip ----
    # Need ROOT_GROUP first
    if not cmds.objExists(attach_ctrls.ROOT_GROUP):
        cmds.group(em=True, name=attach_ctrls.ROOT_GROUP)
    payload = {
        "fixed": {"arm_L": ["arm_L","elbow_L","wrist_L"]},
        "chains": {"spine": ["waist","upper_body"]},
    }
    attach_ctrls.set_mapping(payload)
    loaded = attach_ctrls.get_mapping()
    ok = (loaded.get("fixed", {}).get("arm_L") == ["arm_L","elbow_L","wrist_L"]
          and loaded.get("chains", {}).get("spine") == ["waist","upper_body"])
    report("mapping_roundtrip", ok, f"loaded={loaded}")

    # ---- Test 3: full_auto_setup with UDE-style rename ----
    cmds.file(new=True, force=True)
    mel.eval(chr(70)+"BXImport -f "+chr(34)+r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"+chr(34)+";")
    importlib.reload(fbx_renamer); importlib.reload(attach_ctrls)
    fbx_renamer.remove_all_namespaces()
    fbx_renamer.rename_all_joints()

    # Rename to UDE style (non-standard, breaks auto-detect)
    ude_renames = {
        "arm_L": "UDE_L", "elbow_L": "HIJI_L", "wrist_L": "TE_L",
        "arm_R": "UDE_R", "elbow_R": "HIJI_R", "wrist_R": "TE_R",
    }
    for old, new in ude_renames.items():
        if cmds.objExists(old):
            cmds.rename(old, new)

    # Verify auto-detect NOW fails to find arms
    auto = attach_ctrls.auto_detect_mapping()
    ok = "arm_L" not in auto.get("fixed", {}) and "arm_R" not in auto.get("fixed", {})
    report("ude_rename_defeats_auto_detect", ok, f"fixed_keys={list(auto.get('fixed',{}).keys())}")

    # Now provide explicit mapping to rescue
    mapping_override = {
        "fixed": {
            "arm_L": ["UDE_L", "HIJI_L", "TE_L"],
            "arm_R": ["UDE_R", "HIJI_R", "TE_R"],
            "leg_L": ["leg_L", "knee_L", "ankle_L"],
            "leg_R": ["leg_R", "knee_R", "ankle_R"],
        },
        "chains": {},
    }
    setup = attach_ctrls.full_auto_setup(
        scale=1.0, skip_decoration=False, delete_junk=True,
        mapping=mapping_override,
    )
    labels = [r["label"] for r in setup.get("ik_fk", [])]
    ok = set(labels) == {"arm_L", "arm_R", "leg_L", "leg_R"}
    report("ude_mapping_override_rig_setup", ok, f"chains built: {labels}")

    # Verify IK ctls exist for both arms (proof rig is functional on UDE joints)
    ok = all(cmds.objExists(f"{lbl}_IK_ctl") for lbl in ("arm_L","arm_R","leg_L","leg_R"))
    report("ude_all_IK_ctls_created", ok)

    # UI must reference original joint names
    ok = cmds.objExists("UDE_L") and cmds.objExists("HIJI_L") and cmds.objExists("TE_L")
    report("ude_original_joints_preserved", ok)

except Exception:
    print("[FAIL] fatal:", traceback.format_exc())

print("=== MAPPING TESTS DONE ===")
