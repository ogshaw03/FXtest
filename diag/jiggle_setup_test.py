"""End-to-end verification for jiggle_bones v0.2.0.

- Build a synthetic hair chain (5 joints)
- Setup hairSystem-based jiggle
- Simulate 30 frames, verify tip joint moves (dynamics is active)
- Remove and verify cleanup
- Add collider mesh and verify nRigid created
"""
import sys, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

sys.path.insert(0, r"E:/OG_Tools/FXtest")


def report(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {extra}")
    return ok


try:
    import jiggle_bones, importlib
    importlib.reload(jiggle_bones)

    cmds.file(new=True, force=True)

    # Load required plugins
    for pl in ("MayaMuscle", "nearestPointOnMesh"):
        pass  # skip
    try:
        if not cmds.pluginInfo("mayaHair", q=True, l=True):
            cmds.loadPlugin("mayaHair", quiet=True)
    except Exception:
        pass

    # ---- Build synthetic hair chain (5 joints, vertical) ----
    # 親 joint は "head" (非 jiggle 名) にして、その子として H1..H5 を作る。
    cmds.select(cl=True)
    parent_joint = cmds.joint(n="head", p=(0, 10, 0))
    chain = []
    for i in range(1, 6):
        nm = f"H{i}"
        cmds.joint(n=nm, p=(0, 10 - 2*i, 0))
        chain.append(nm)

    report("synthetic_chain_built", len(chain) == 5, f"chain={chain}")

    # ---- Verify detection classifies as hair ----
    detected = jiggle_bones.find_jiggle_chains()
    hair_chains = detected.get("hair", [])
    matched = any(_c[0] == chain[0] and len(_c) == 5 for _c in hair_chains)
    report("chain_detected_as_hair", matched,
           f"got: {[(c[0], len(c)) for c in hair_chains]}")

    # ---- Setup jiggle ----
    result = jiggle_bones.create_jiggle_for_chain(chain, category="hair")
    ok = result is not None and cmds.objExists(result["ik_handle"])
    report("setup_created_ik_handle", ok,
           f"ikh={result['ik_handle'] if result else None}")
    ok = cmds.objExists(result["rest_curve"]) and cmds.objExists("jb_nucleus")
    report("setup_created_rest_curve_and_nucleus", ok)
    ok = cmds.objExists(result["hair_system"])
    report("setup_created_hair_system", ok, f"hs={result['hair_system']}")

    # ---- Simulate: play frames 1..30 and check tip joint moved ----
    cmds.playbackOptions(min=1, max=60)
    cmds.currentTime(1)
    tip_start = cmds.xform(chain[-1], q=True, ws=True, t=True)
    # Apply a "impulse" by shaking the parent joint
    for f in range(1, 40):
        cmds.currentTime(f)
        # Sway parent left-right to induce hair motion
        cmds.setAttr(f"{parent_joint}.translateX", math.sin(f * 0.3) * 5)
    tip_end = cmds.xform(chain[-1], q=True, ws=True, t=True)
    delta = math.sqrt(sum((a - b) ** 2 for a, b in zip(tip_start, tip_end)))
    report("dynamics_active_tip_moved", delta > 0.5,
           f"tip delta = {delta:.3f} unit")

    # ---- Category param get/set ----
    jiggle_bones.set_category_params("hair", stiffness=0.5, damp=0.4)
    params = jiggle_bones.get_category_params("hair")
    ok = abs(params.get("stiffness", 0) - 0.5) < 1e-4 \
         and abs(params.get("damp", 0) - 0.4) < 1e-4
    report("category_params_roundtrip", ok, f"got: {params}")

    # ---- is_chain_active ----
    report("is_chain_active_true", jiggle_bones.is_chain_active(chain))

    # ---- Collider add ----
    cmds.currentTime(1)
    ground = cmds.polyPlane(w=20, h=20, sx=2, sy=2, n="ground_geo")[0]
    jiggle_bones.add_collider(ground)
    ok = cmds.objExists("jb_collider_ground_geo")
    report("collider_created", ok)
    ok = ground in jiggle_bones.list_colliders()
    report("collider_listed", ok, f"colliders={jiggle_bones.list_colliders()}")

    # ---- Remove chain ----
    jiggle_bones.remove_jiggle_for_chain(chain)
    ok = not cmds.objExists(result["ik_handle"]) \
         and not cmds.objExists(result["rest_curve"])
    report("remove_cleaned_nodes", ok)
    report("is_chain_active_false", not jiggle_bones.is_chain_active(chain))

    # ---- Remove collider ----
    jiggle_bones.remove_collider(ground)
    ok = not cmds.objExists("jb_collider_ground_geo")
    report("collider_removed", ok)

except Exception:
    import traceback
    print("[FAIL] fatal exception:")
    traceback.print_exc()

print("=== JIGGLE SETUP TEST DONE ===")
