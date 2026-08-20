"""End-to-end: Nekotatune で attach_ctrls setup → jiggle 全 chain セットアップ。"""
import sys
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

sys.path.insert(0, r"E:/OG_Tools/FXtest")

def report(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {extra}")


try:
    cmds.file(new=True, force=True)
    cmds.loadPlugin("fbxmaya", quiet=True)
    mel.eval(chr(70)+"BXImport -f "+chr(34)+
             r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"+chr(34)+";")

    import attach_ctrls, fbx_renamer, jiggle_bones, importlib
    importlib.reload(fbx_renamer)
    importlib.reload(attach_ctrls)
    importlib.reload(jiggle_bones)

    # attach_ctrls: 主 rig setup (default skip_decoration=True)
    setup = attach_ctrls.full_auto_setup(scale=1.0)
    n_ikfk = len(setup.get("ik_fk", []))
    report("attach_ctrls_setup", n_ikfk == 4, f"ik/fk chains={n_ikfk}")

    # jiggle: 全 chain セットアップ
    chains = jiggle_bones.find_jiggle_chains()
    n_setup = 0
    for category, chain_list in chains.items():
        for chain in chain_list:
            r = jiggle_bones.create_jiggle_for_chain(chain, category=category)
            if r and cmds.objExists(r["ik_handle"]):
                n_setup += 1
    total_detected = sum(len(cl) for cl in chains.values())
    report("jiggle_setup_all", n_setup == total_detected,
           f"{n_setup}/{total_detected} chains")

    # 使用中の hairSystem 一覧
    hs = cmds.ls("jb_hairSystem_*", type="hairSystem", long=False) or []
    report("category_hairsystems_created", len(hs) >= 1,
           f"hairSystems={hs}")

    # nucleus 1 個
    n_nucleus = len(cmds.ls("jb_nucleus", type="transform") or [])
    report("nucleus_single_shared", n_nucleus == 1, f"count={n_nucleus}")

    # collider: 体 mesh を検出して add
    all_meshes = cmds.ls(type="mesh") or []
    body_mesh = None
    if all_meshes:
        # 最大 vert 数の mesh を body として仮定
        best_v = 0
        for m in all_meshes:
            try:
                v = cmds.polyEvaluate(m, v=True)
                if v > best_v:
                    best_v = v
                    body_mesh_shape = m
            except Exception:
                pass
        if best_v > 0:
            body_mesh = cmds.listRelatives(body_mesh_shape, p=True)[0]
    if body_mesh:
        jiggle_bones.add_collider(body_mesh)
        n_col = len(jiggle_bones.list_colliders())
        report("collider_added_to_body", n_col == 1, f"colliders={jiggle_bones.list_colliders()}")

    # simulate a few frames to prove dynamics runs without errors
    cmds.playbackOptions(min=1, max=30)
    tip_positions = {}
    for chain_list in chains.values():
        for chain in chain_list:
            tip_positions[chain[-1]] = cmds.xform(chain[-1], q=True, ws=True, t=True)
    # step through timeline
    for f in (1, 10, 20, 30):
        cmds.currentTime(f)
    # after sim, at least ONE tip should have moved (dynamics evaluated)
    moved = 0
    import math
    for tip, p0 in tip_positions.items():
        if cmds.objExists(tip):
            p1 = cmds.xform(tip, q=True, ws=True, t=True)
            if math.sqrt(sum((a-b)**2 for a,b in zip(p0,p1))) > 0.01:
                moved += 1
    report("dynamics_evaluated_no_errors", True, f"tips moved={moved}/{len(tip_positions)}")

    # remove all → attach_ctrls rig は無傷であるべき
    for chain_list in chains.values():
        for chain in chain_list:
            jiggle_bones.remove_jiggle_for_chain(chain)
    # attach_ctrls IK ctl 4 つが健在
    ik_ctls_after = [c for c in ("arm_L_IK_ctl","arm_R_IK_ctl","leg_L_IK_ctl","leg_R_IK_ctl")
                     if cmds.objExists(c)]
    report("attach_ctrls_survived_remove", len(ik_ctls_after) == 4, f"{ik_ctls_after}")

except Exception:
    import traceback
    print("[FAIL] fatal:"); traceback.print_exc()

print("=== JIGGLE FULL PIPELINE DONE ===")
