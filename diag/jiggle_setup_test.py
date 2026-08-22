"""End-to-end verification for jiggle_bones v0.3.0.

- 合成 hair chain (5 joints, top-level world-parented)
- FK ctls が全 joint に作られる (root は translate+rotate、子は rotate のみ)
- root ctl を translate → chain 全体が移動 (v0.3.0 の主要修正点)
- root ctl.rotate で FK 動作 (dynamics=0 時)
- dynamics=1 で hairSystem が rotate override (親揺らして tip 動く)
- remove で全掃除
- collider add/remove
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

    # ---- Build synthetic chain: TOP-LEVEL H1..H5 (親骨なし、user 報告状況) ----
    cmds.select(cl=True)
    chain = []
    for i in range(1, 6):
        nm = f"H{i}"
        j = cmds.joint(n=nm, p=(0, 10 - 2*i, 0))
        chain.append(nm)
    # H1 を top-level に (H1 の親 = world) — 既に world なら何もしない
    if cmds.listRelatives(chain[0], p=True):
        cmds.parent(chain[0], world=True)

    report("synthetic_chain_toplevel", cmds.listRelatives(chain[0], p=True) is None,
           f"chain[0]={chain[0]} 親={cmds.listRelatives(chain[0], p=True)}")

    # ---- Setup jiggle (v0.3.0 FK ctls + dynamics) ----
    result = jiggle_bones.create_jiggle_for_chain(chain, category="hair")
    ok = result is not None
    report("setup_returned_result", ok)

    # FK ctl が全 joint 分できている
    fk_ctls = result["fk_ctls"]
    report("fk_ctls_created", len(fk_ctls) == 5, f"ctls={fk_ctls}")
    for c in fk_ctls:
        report(f"  ctl_exists_{c}", cmds.objExists(c))

    # root ctl に dynamics attr がある
    root_ctl = result["root_ctl"]
    has_dyn = cmds.attributeQuery("dynBlend", node=root_ctl, exists=True)
    report("dynamics_attr_on_root", has_dyn, f"root_ctl={root_ctl}")

    # ---- FK モード (dynamics=0) で root ctl 動かす → chain 全体移動 ----
    cmds.setAttr(f"{root_ctl}.dynamics", 0.0)
    cmds.currentTime(1)
    tip_before = cmds.xform(chain[-1], q=True, ws=True, t=True)
    root_before = cmds.xform(chain[0], q=True, ws=True, t=True)

    # root ctl を translateX +5
    cmds.setAttr(f"{root_ctl}.translateX", 5.0)
    tip_after = cmds.xform(chain[-1], q=True, ws=True, t=True)
    root_after = cmds.xform(chain[0], q=True, ws=True, t=True)
    dx_root = root_after[0] - root_before[0]
    dx_tip  = tip_after[0] - tip_before[0]
    report("fk_root_translate_moves_chain", abs(dx_root - 5.0) < 0.1 and abs(dx_tip - 5.0) < 0.1,
           f"root Δx={dx_root:.3f}, tip Δx={dx_tip:.3f} (期待: どちらも 5.0)")

    # reset
    cmds.setAttr(f"{root_ctl}.translateX", 0.0)

    # ---- FK モードで child ctl.rotateZ で joint が回る ----
    child_ctl = fk_ctls[2]   # mid ctl (H3)
    cmds.setAttr(f"{child_ctl}.rotateZ", 45.0)
    tip_after = cmds.xform(chain[-1], q=True, ws=True, t=True)
    dx = tip_after[0] - tip_before[0]
    report("fk_child_rotate_bends_chain", abs(dx) > 0.5,
           f"child rotZ=45 → tip Δx={dx:.3f}")
    cmds.setAttr(f"{child_ctl}.rotateZ", 0.0)

    # ---- Dynamics モード (dynamics=1) で hairSystem が働く ----
    cmds.setAttr(f"{root_ctl}.dynamics", 1.0)
    cmds.playbackOptions(min=1, max=40)
    cmds.currentTime(1)
    tip_start = cmds.xform(chain[-1], q=True, ws=True, t=True)
    for f in range(1, 40):
        cmds.currentTime(f)
        cmds.setAttr(f"{root_ctl}.translateX", math.sin(f * 0.3) * 5)
    tip_end = cmds.xform(chain[-1], q=True, ws=True, t=True)
    delta = math.sqrt(sum((a - b) ** 2 for a, b in zip(tip_start, tip_end)))
    report("dynamics_tip_moved", delta > 0.3,
           f"dynamics on + sway → tip Δ={delta:.3f}")

    # ---- v0.3.1: snap_ctls_to_sim ----
    # dynBlend=1 のまま、DYN chain の rotate を FK ctl に immediate copy
    n_snap = jiggle_bones.snap_ctls_to_sim(chain=chain)
    report("snap_ctls_to_sim_updated", n_snap == len(chain),
           f"updated {n_snap} joint(s)")
    # snap 後、mid ctl の rotate が非ゼロになっている (DYN が sway した後)
    mid_ctl_rotZ = cmds.getAttr(f"{fk_ctls[2]}.rotateZ")
    report("snap_result_nonzero", abs(mid_ctl_rotZ) > 0.01,
           f"mid ctl rotZ = {mid_ctl_rotZ:.3f}")

    # ---- Collider add / remove ----
    cmds.currentTime(1)
    ground = cmds.polyPlane(w=20, h=20, sx=2, sy=2, n="ground_geo")[0]
    jiggle_bones.add_collider(ground)
    report("collider_created", cmds.objExists("jb_collider_ground_geo"))
    jiggle_bones.remove_collider(ground)
    report("collider_removed", not cmds.objExists("jb_collider_ground_geo"))

    # ---- Remove jiggle → FK ctls / duplicates / constraint / dyn attr すべて掃除 ----
    jiggle_bones.remove_jiggle_for_chain(chain)
    still_exists = [n for n in (fk_ctls + [
        result["fk_chain"][0], result["dyn_chain"][0],
        result["ik_handle"], result["rest_curve"],
    ]) if cmds.objExists(n)]
    report("remove_cleaned_all", len(still_exists) == 0,
           f"still exists: {still_exists}")
    report("is_chain_active_false_after_remove",
           not jiggle_bones.is_chain_active(chain))

except Exception:
    import traceback
    print("[FAIL] fatal exception:")
    traceback.print_exc()

print("=== JIGGLE V0.3.0 SETUP TEST DONE ===")
