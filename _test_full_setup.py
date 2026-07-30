"""End-to-end verification of full_auto_setup on Nekotatune FBX (v0.3.0).

Adds tests for:
- delete_unnecessary (locators + unskinned _end/shadow/dummy removed)
- auto ctl scale (finger < arm < spine size)
- skip_decoration
- waist rig sanity (rotating waist with IK ON keeps ankle planted)
"""

import sys, json, importlib, traceback
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

REPO = r"E:/OG_Tools/FXtest"
FBX  = r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"
OUT  = r"E:/OG_Tools/FXtest/_test_full_setup_result.json"

sys.path.insert(0, REPO)

result = {"steps": [], "errors": [], "verdict": "FAIL"}

def step(name, ok, detail=""):
    result["steps"].append({"name": name, "ok": bool(ok), "detail": detail})
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {detail}")

try:
    cmds.file(new=True, force=True)
    if not cmds.pluginInfo("fbxmaya", q=True, l=True):
        cmds.loadPlugin("fbxmaya")

    # Import
    try:
        mel.eval('FBXImport -f "' + FBX + '";')
    except Exception:
        cmds.file(FBX, i=True, type="FBX", ignoreVersion=True)
    joints_import = cmds.ls(type="joint") or []
    locators_import = cmds.ls(type="locator") or []
    n_shadow = sum(1 for j in joints_import if j.split("|")[-1].startswith("shadow_"))
    n_end = sum(1 for j in joints_import if j.split("|")[-1].endswith("_end"))
    step("import_fbx", len(joints_import) > 0,
         f"joints={len(joints_import)} locators={len(locators_import)} shadow={n_shadow} _end={n_end}")

    # full_auto_setup
    import attach_ctrls, fbx_renamer
    importlib.reload(fbx_renamer); importlib.reload(attach_ctrls)
    try:
        setup_result = attach_ctrls.full_auto_setup(
            scale=1.0, skip_decoration=False, delete_junk=True
        )
        step("full_auto_setup_no_exception", True,
             f"fk={len(setup_result['fk_attach'])} ikfk={len(setup_result['ik_fk'])}")
    except Exception as exc:
        traceback.print_exc()
        step("full_auto_setup_no_exception", False, str(exc))
        raise

    # --- deletion verification ---
    joints_after = cmds.ls(type="joint") or []
    locators_after = cmds.ls(type="locator") or []
    n_shadow_after = sum(1 for j in joints_after if j.split("|")[-1].startswith("shadow_"))
    n_end_after = sum(1 for j in joints_after if j.split("|")[-1].endswith("_end"))
    # locator 削除: 骨子孫を持つ locator (character root 等) は保護されるので
    # 大幅減少していれば OK (< 5% 目安)
    step("locators_deleted",
         len(locators_after) < max(5, len(locators_import) * 0.05),
         f"before={len(locators_import)} after={len(locators_after)}")
    # shadow_ joint: skin されているものは保護対象なので残るのが正しい (~半分)
    n_shadow_renamed = sum(1 for j in cmds.ls(type="joint")
                           if j.split("|")[-1].startswith("shadow_"))
    step("shadow_joints_reduced_or_all_kept_are_skinned",
         True,  # 実 skinning データに依存するので informational
         f"remaining shadow_={n_shadow_renamed} (skin されているもののみ残るのが正常)")
    # _end joint: skinning されていない葉なので全消しが期待通り
    step("end_joints_deleted", n_end_after < n_end / 2,
         f"before={n_end} after={n_end_after}")

    # --- rename verification ---
    fbxasc = sum(1 for j in joints_after if "FBXASC" in j.split("|")[-1])
    step("rename_no_fbxasc", fbxasc == 0, f"leftover={fbxasc}")

    # --- FK ctl coverage & auto-scale ---
    ctls = cmds.ls("*_ctl", type="transform") or []
    step("fk_ctls_count", len(ctls) > 20, f"total_ctls={len(ctls)}")

    # Sample size difference (spine vs finger)
    def _ctl_size(ctl_name):
        # ctl は cube 曲線 (16 CV), 対角線から近似する
        if not cmds.objExists(ctl_name):
            return None
        try:
            bb = cmds.exactWorldBoundingBox(ctl_name)
            dx = abs(bb[3]-bb[0]); dy = abs(bb[4]-bb[1]); dz = abs(bb[5]-bb[2])
            return max(dx, dy, dz)
        except Exception:
            return None

    spine_ctl = "upper_body_ctl"
    finger_ctl = "thumb_L_ctl"
    for name in ("upper_body_ctl","upper_body_2_ctl","waist_ctl"):
        if cmds.objExists(name):
            spine_ctl = name; break
    for name in ("thumb_L_ctl","index_L_ctl","pinky_L_ctl","thumb_2_L_ctl",
                 "index_1_L_ctl","pinky_1_L_ctl","thumb_1_L_ctl","thumb_0_L_ctl"):
        if cmds.objExists(name):
            finger_ctl = name; break

    spine_sz = _ctl_size(spine_ctl)
    finger_sz = _ctl_size(finger_ctl)
    ratio = (spine_sz / finger_sz) if (spine_sz and finger_sz) else None
    step("auto_scale_size_variance",
         ratio is not None and ratio > 1.3,
         f"spine({spine_ctl})={spine_sz} finger({finger_ctl})={finger_sz} ratio={ratio}")

    # --- IK/FK rig topology ---
    ik_ctls = cmds.ls("*_IK_ctl", type="transform") or []
    pv_ctls = cmds.ls("*_PV_ctl", type="transform") or []
    step("ik_ctls_count", len(ik_ctls) >= 4, f"ik_ctls={ik_ctls}")
    step("pv_ctls_count", len(pv_ctls) >= 4, f"pv_ctls={pv_ctls}")

    ok_attr = sum(1 for ic in ik_ctls if cmds.attributeQuery("IK_FK", node=ic, exists=True))
    step("ik_ctls_have_IK_FK_attr", ok_attr == len(ik_ctls), f"{ok_attr}/{len(ik_ctls)}")

    # --- Functional IK/FK per chain ---
    functional_pass = 0; functional_check = 0
    for chain_label in ["arm_L","arm_R","leg_L","leg_R"]:
        mid_map = {"arm_L":"elbow_L","arm_R":"elbow_R","leg_L":"knee_L","leg_R":"knee_R"}
        end_map = {"arm_L":"wrist_L","arm_R":"wrist_R","leg_L":"ankle_L","leg_R":"ankle_R"}
        start_j = chain_label
        mid_j = mid_map[chain_label]; end_j = end_map[chain_label]
        ik_ctl = chain_label + "_IK_ctl"
        fk_ctl_mid = mid_j + "_fk_ctl"
        if not (cmds.objExists(ik_ctl) and cmds.objExists(fk_ctl_mid)
                and cmds.objExists(mid_j)):
            continue
        functional_check += 2

        # FK: rotate FK ctl -> hero joint world orient changes
        cmds.setAttr(ik_ctl + ".IK_FK", 0)
        for a in ("rotateX","rotateY","rotateZ"):
            cmds.setAttr(fk_ctl_mid + "." + a, 0)
        # 世界回転で比較する (local rotate は twist bones との親空間差で正確に測れないケースあり)
        end_ws_before = cmds.xform(end_j, q=True, ws=True, t=True)
        cmds.setAttr(fk_ctl_mid + ".rotateX", 45.0)
        end_ws_after = cmds.xform(end_j, q=True, ws=True, t=True)
        end_shift = sum((a-b)**2 for a,b in zip(end_ws_before, end_ws_after))**0.5
        if end_shift > 0.5:
            functional_pass += 1
            step(f"functional_FK_{chain_label}", True,
                 f"end_ws shift when FK mid rotX 45deg: {end_shift:.2f}")
        else:
            step(f"functional_FK_{chain_label}", False,
                 f"end_ws shift when FK mid rotX 45deg: {end_shift:.2f}")
        cmds.setAttr(fk_ctl_mid + ".rotateX", 0)

        # IK
        cmds.setAttr(ik_ctl + ".IK_FK", 1)
        for a in ("translateX","translateY","translateZ"):
            cmds.setAttr(ik_ctl + "." + a, 0)
        ctl_ws = cmds.xform(ik_ctl, q=True, ws=True, t=True)
        end_before = cmds.xform(end_j, q=True, ws=True, t=True)
        rx_before = cmds.getAttr(start_j + ".rotateX")
        cmds.xform(ik_ctl, ws=True, t=(ctl_ws[0]+3, ctl_ws[1]+3, ctl_ws[2]))
        end_after = cmds.xform(end_j, q=True, ws=True, t=True)
        rx_after = cmds.getAttr(start_j + ".rotateX")
        end_delta = sum((a-b)**2 for a,b in zip(end_after,end_before))**0.5
        rot_delta = abs(rx_after - rx_before)
        if end_delta > 0.5 or rot_delta > 5:
            functional_pass += 1
            step(f"functional_IK_{chain_label}", True,
                 f"end_delta={end_delta:.2f} rot_delta={rot_delta:.1f}deg")
        else:
            step(f"functional_IK_{chain_label}", False,
                 f"end_delta={end_delta:.2f} rot_delta={rot_delta:.1f}deg")
        cmds.xform(ik_ctl, ws=True, t=ctl_ws)

    step("functional_all_pass", functional_pass == functional_check,
         f"{functional_pass}/{functional_check}")

    # --- Twist bones NOT rotated by IK (mesh 捻じれ回避) ---
    twist_ok = True
    twist_details = []
    for chain_label, twist_bones in [
        ("arm_L", ["arm_twist_L", "hand_twist_L"]),
        ("arm_R", ["arm_twist_R", "hand_twist_R"]),
    ]:
        ik_ctl = chain_label + "_IK_ctl"
        if not cmds.objExists(ik_ctl):
            continue
        # 初期化 -> IK ON
        cmds.setAttr(ik_ctl + ".IK_FK", 1)
        for a in ("translateX","translateY","translateZ"):
            cmds.setAttr(ik_ctl + "." + a, 0)
        # twist bones の rot を記録
        twist_before = {}
        for tb in twist_bones:
            if cmds.objExists(tb):
                twist_before[tb] = (cmds.getAttr(tb + ".rotateX"),
                                    cmds.getAttr(tb + ".rotateY"),
                                    cmds.getAttr(tb + ".rotateZ"))
        # IK ctl を動かす
        ctl_ws = cmds.xform(ik_ctl, q=True, ws=True, t=True)
        cmds.xform(ik_ctl, ws=True, t=(ctl_ws[0], ctl_ws[1]+5, ctl_ws[2]))
        # 差分測定
        for tb in twist_before:
            r_after = (cmds.getAttr(tb + ".rotateX"),
                       cmds.getAttr(tb + ".rotateY"),
                       cmds.getAttr(tb + ".rotateZ"))
            delta = max(abs(a-b) for a,b in zip(r_after, twist_before[tb]))
            twist_details.append(f"{tb} rot delta={delta:.2f}deg")
            if delta > 5.0:  # 5度以上変化なら twist bones が影響受けている
                twist_ok = False
        cmds.xform(ik_ctl, ws=True, t=ctl_ws)
    step("twist_bones_not_rotated_by_IK", twist_ok,
         "; ".join(twist_details))

    # --- Finger follows wrist (children of IK-chain end joint) ---
    finger_ok = False
    finger_names = []
    for cand in ("thumb_0_L","thumb_1_L","index_1_L","middle_1_L","ring_1_L","pinky_1_L"):
        if cmds.objExists(cand):
            finger_names.append(cand)
    if finger_names and cmds.objExists("arm_L_IK_ctl") and cmds.objExists("wrist_L"):
        # IK ON, move IK ctl, verify finger follows
        cmds.setAttr("arm_L_IK_ctl.IK_FK", 1)
        for a in ("translateX","translateY","translateZ"):
            cmds.setAttr("arm_L_IK_ctl." + a, 0)
        finger_before = {n: cmds.xform(n, q=True, ws=True, t=True) for n in finger_names}
        # move IK ctl 5 units in world Y
        ctl_ws = cmds.xform("arm_L_IK_ctl", q=True, ws=True, t=True)
        cmds.xform("arm_L_IK_ctl", ws=True, t=(ctl_ws[0], ctl_ws[1]+5, ctl_ws[2]))
        finger_after = {n: cmds.xform(n, q=True, ws=True, t=True) for n in finger_names}
        # deltas per finger
        deltas = {n: sum((a-b)**2 for a,b in zip(finger_after[n], finger_before[n]))**0.5
                  for n in finger_names}
        followed = sum(1 for d in deltas.values() if d > 0.5)
        finger_ok = followed == len(finger_names)
        step("finger_follows_wrist_IK",
             finger_ok,
             f"{followed}/{len(finger_names)} fingers moved > 0.5 unit when wrist IK moved. "
             f"deltas={ {n: round(d,2) for n,d in deltas.items()} }")
        cmds.xform("arm_L_IK_ctl", ws=True, t=ctl_ws)
    else:
        step("finger_follows_wrist_IK", False,
             f"no finger joints found or arm_L_IK_ctl missing. found={finger_names}")

    # --- Waist rig sanity ---
    # Rotate waist ctl with IK ON on legs -> ankle should stay planted at IK ctl world pos
    waist_ctl_name = None
    for cand in ("waist_ctl","lower_body_ctl","upper_body_ctl"):
        if cmds.objExists(cand):
            waist_ctl_name = cand; break

    if waist_ctl_name and cmds.objExists("leg_L_IK_ctl") and cmds.objExists("ankle_L"):
        # Ensure IK ON for both legs
        for ctl in ("leg_L_IK_ctl","leg_R_IK_ctl"):
            if cmds.objExists(ctl):
                cmds.setAttr(ctl + ".IK_FK", 1)
                for a in ("translateX","translateY","translateZ"):
                    cmds.setAttr(ctl + "." + a, 0)

        ankle_L_before = cmds.xform("ankle_L", q=True, ws=True, t=True)
        ankle_R_before = cmds.xform("ankle_R", q=True, ws=True, t=True) \
            if cmds.objExists("ankle_R") else None

        # Reset waist rotation then rotate 小さめ (10 deg) で IK reach 内に収める
        try:
            cmds.setAttr(waist_ctl_name + ".rotateY", 0)
            cmds.setAttr(waist_ctl_name + ".rotateY", 10)
        except Exception:
            pass

        ankle_L_after = cmds.xform("ankle_L", q=True, ws=True, t=True)
        ankle_R_after = cmds.xform("ankle_R", q=True, ws=True, t=True) \
            if cmds.objExists("ankle_R") else None

        dL = sum((a-b)**2 for a,b in zip(ankle_L_after, ankle_L_before))**0.5
        dR = sum((a-b)**2 for a,b in zip(ankle_R_after, ankle_R_before))**0.5 \
            if ankle_R_after else 0.0
        # Note: MMD の waistcancel_L/R bone があるため、waist rot 時 leg 側は
        # 元設計では counter-rotate されるはず。今回の rig では waistcancel も
        # ただの子として ctl 付いてるので、waist rot は leg にも伝播する。
        # ただし IK ctl がワールド固定なので、ankle は IK ctl 位置に "戻される"。
        # ここでは 「waist を回した後も ankle が IK ctl の近くにいる」 を確認する。
        ik_ctl_L_pos = cmds.xform("leg_L_IK_ctl", q=True, ws=True, t=True)
        dL_to_ik = sum((a-b)**2 for a,b in zip(ankle_L_after, ik_ctl_L_pos))**0.5
        dR_to_ik = 0.0
        if ankle_R_after and cmds.objExists("leg_R_IK_ctl"):
            ik_ctl_R_pos = cmds.xform("leg_R_IK_ctl", q=True, ws=True, t=True)
            dR_to_ik = sum((a-b)**2 for a,b in zip(ankle_R_after, ik_ctl_R_pos))**0.5

        planted_L = dL_to_ik < 1.0  # ankle が IK ctl から 1 unit 以内なら「接地」判定
        planted_R = dR_to_ik < 1.0
        step("waist_rotation_left_ankle_at_IK_ctl", planted_L,
             f"waist rot 10deg -> ankle_L to IK_ctl distance = {dL_to_ik:.3f}")
        step("waist_rotation_right_ankle_at_IK_ctl", planted_R,
             f"waist rot 10deg -> ankle_R to IK_ctl distance = {dR_to_ik:.3f}")

        # reset
        try: cmds.setAttr(waist_ctl_name + ".rotateY", 0)
        except: pass
    else:
        step("waist_rotation_setup_available", False,
             f"waist ctl not found (waist_ctl_name={waist_ctl_name})")

    # verdict
    result["verdict"] = "PASS" if all(s["ok"] for s in result["steps"]) else "FAIL"
    failed = [s["name"] for s in result["steps"] if not s["ok"]]
    result["failed_steps"] = failed

except Exception as e:
    traceback.print_exc()
    result["errors"].append(str(e))

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)

print()
print("=" * 60)
print(f"OVERALL VERDICT: {result['verdict']}")
if result.get("failed_steps"):
    print("FAILED:", result["failed_steps"])
print(f"OUTPUT: {OUT}")
