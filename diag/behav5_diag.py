"""BEHAV5 scout - v0.9.12 (cfbc6bf) behavior test. Read-only diag."""
import sys, json, traceback, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

REPO = r"E:/OG_Tools/FXtest"
FBX  = r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"
OUT  = r"E:/OG_Tools/FXtest/diag/_behav5_result.json"
sys.path.insert(0, REPO)

def pos(n): return cmds.xform(n, q=True, ws=True, t=True)
def dist(a,b): return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))
def try_get(n,a):
    try: return cmds.getAttr(n+"."+a)
    except Exception as e: return "ERR:"+str(e)[:120]
def try_set(n,a,v):
    try: cmds.setAttr(n+"."+a,v); return None
    except Exception as e: return str(e)[:200]
def cb_visible(n,a):
    try:
        k = cmds.getAttr(n+"."+a, k=True)
        cb= cmds.getAttr(n+"."+a, cb=True)
        return {"keyable":bool(k),"cb":bool(cb),"visible":bool(k or cb)}
    except Exception as e:
        return "ERR:"+str(e)[:100]

result = {"tests":{}, "errors":[], "warnings":[]}
_orig_warn = cmds.warning
def _capture_warn(*a, **kw):
    result["warnings"].append(str(a[0]) if a else "")
    return _orig_warn(*a, **kw)
cmds.warning = _capture_warn

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
    result["setup_chains"] = [r["label"] for r in setup["ik_fk"]]
    tests = result["tests"]

    end_map = {"arm_L":"wrist_L","arm_R":"wrist_R","leg_L":"ankle_L","leg_R":"ankle_R"}
    mid_map = {"arm_L":"elbow_L","arm_R":"elbow_R","leg_L":"knee_L","leg_R":"knee_R"}

    # Test 1: IK/FK Switch (direct IK_FK edit)
    for chain, end in end_map.items():
        ui = chain + "_UI_ctl"
        rec = {"ui_exists": cmds.objExists(ui), "end_exists": cmds.objExists(end)}
        if not (rec["ui_exists"] and rec["end_exists"]):
            tests["switch_"+chain] = rec; continue
        rec["IK_FK_cb"]  = cb_visible(ui,"IK_FK")
        rec["ikVis_cb"]  = cb_visible(ui,"ikVis")
        rec["fkVis_cb"]  = cb_visible(ui,"fkVis")
        rec["stretch_cb"]= cb_visible(ui,"stretch")
        rec["init_IK_FK"]= try_get(ui,"IK_FK")
        rec["ep_start"]  = pos(end)
        rec["set_0"]     = try_set(ui,"IK_FK",0)
        rec["ep_FK"]     = pos(end)
        rec["set_05"]    = try_set(ui,"IK_FK",0.5)
        rec["ep_half"]   = pos(end)
        rec["set_1"]     = try_set(ui,"IK_FK",1)
        rec["ep_IK"]     = pos(end)
        rec["hero_delta_across_switch"] = dist(rec["ep_FK"], rec["ep_IK"])
        tests["switch_"+chain] = rec

    # Test 2: Bug 1 - elbow/knee FK vs IK delta
    for c in ("arm_L","arm_R","leg_L","leg_R"):
        try_set(c+"_UI_ctl", "IK_FK", 1)
    for chain in ("arm_L","arm_R","leg_L","leg_R"):
        ui = chain + "_UI_ctl"; mid = mid_map[chain]; end = end_map[chain]
        rec = {}
        try_set(ui,"IK_FK",1)
        rec["mid_IK"] = pos(mid); rec["end_IK"] = pos(end)
        try_set(ui,"IK_FK",0)
        rec["mid_FK"] = pos(mid); rec["end_FK"] = pos(end)
        try_set(ui,"IK_FK",1)
        rec["mid_delta"] = dist(rec["mid_IK"], rec["mid_FK"])
        rec["end_delta"] = dist(rec["end_IK"], rec["end_FK"])
        rec["pass_bug1"] = rec["mid_delta"] < 0.1
        tests["bug1_"+chain] = rec

    # Test 3: Bug 2 - waist down -> knee shift
    for c in ("arm_L","arm_R","leg_L","leg_R"):
        try_set(c+"_UI_ctl","IK_FK",1)
    waist = "waist_ctl"
    rec = {"waist_exists": cmds.objExists(waist)}
    if cmds.objExists(waist):
        base_ty = cmds.getAttr(waist+".translateY")
        rec["base_ty"] = base_ty
        for lbl in ("knee_L","knee_R"):
            rec[lbl+"_base"] = pos(lbl)
        for dy in (-10, -20, -30):
            try_set(waist,"translateY", base_ty + dy)
            for lbl in ("knee_L","knee_R"):
                p = pos(lbl)
                rec[lbl+"_ty"+str(dy)] = p
                rec[lbl+"_shift_z_ty"+str(dy)] = p[2] - rec[lbl+"_base"][2]
                rec[lbl+"_shift_y_ty"+str(dy)] = p[1] - rec[lbl+"_base"][1]
        try_set(waist,"translateY", base_ty)
    tests["bug2_waist_down"] = rec

    # Test 4: stretch (rest_len 越え)
    for c in ("arm_L","arm_R","leg_L","leg_R"):
        try_set(c+"_UI_ctl", "IK_FK", 1)
    for chain, end in end_map.items():
        ui = chain + "_UI_ctl"; ik = chain + "_IK_ctl"
        start = chain; mid = mid_map[chain]
        rec = {}
        if not (cmds.objExists(ik) and cmds.objExists(end)):
            tests["stretch_"+chain]={"skip":"missing"}; continue
        p_s=pos(start); p_m=pos(mid); p_e=pos(end)
        rec["rest_len"] = dist(p_s,p_m)+dist(p_m,p_e)
        dy = 25.0 if "arm" in chain else -25.0
        cur_ty = cmds.getAttr(ik+".translateY")
        try_set(ik,"translateY", cur_ty + dy)
        try_set(ui,"stretch",0)
        rec["stretch0_gap"] = dist(pos(end), pos(ik))
        rec["stretch0_chain_len"] = dist(pos(start), pos(mid)) + dist(pos(mid), pos(end))
        try_set(ui,"stretch",1)
        rec["stretch1_gap"] = dist(pos(end), pos(ik))
        rec["stretch1_chain_len"] = dist(pos(start), pos(mid)) + dist(pos(mid), pos(end))
        try_set(ui,"stretch",0)
        try_set(ik,"translateY", cur_ty)
        tests["stretch_"+chain] = rec

    # Test 5: snap (both dirs, all chains)
    for c in ("arm_L","arm_R","leg_L","leg_R"):
        try_set(c+"_UI_ctl", "IK_FK", 1)
    for chain in ("arm_L","arm_R","leg_L","leg_R"):
        rec = {}
        try:
            attach_ctrls.snap_fk_to_ik(chain); rec["fk_snap_ok"]=True
            rec["ikfk_after_fkSnap"] = try_get(chain+"_UI_ctl","IK_FK")
        except Exception as e:
            rec["fk_snap_ok"]=False; rec["fk_err"]=str(e)[:200]
        try:
            attach_ctrls.snap_ik_to_fk(chain); rec["ik_snap_ok"]=True
            rec["ikfk_after_ikSnap"] = try_get(chain+"_UI_ctl","IK_FK")
        except Exception as e:
            rec["ik_snap_ok"]=False; rec["ik_err"]=str(e)[:200]
        tests["snap_"+chain] = rec

    # Test 6: reverse foot (heel/ball/tip rot 30)
    for side in ("L","R"):
        ui = "leg_"+side+"_UI_ctl"
        try_set(ui,"IK_FK",1)
        ankle = "ankle_"+side; toe = "toe_"+side
        heel_c = "leg_"+side+"_heel_ctl"
        ball_c = "ankle_"+side+"_ball_ctl"
        tip_c  = "leg_"+side+"_tip_ctl"
        rec = {"heel_ctl":cmds.objExists(heel_c),
               "ball_ctl":cmds.objExists(ball_c),
               "tip_ctl":cmds.objExists(tip_c),
               "ankle":cmds.objExists(ankle),
               "toe":cmds.objExists(toe)}
        if not (rec["ankle"] and rec["toe"]):
            tests["revfoot_"+side] = rec; continue
        rec["ankle_base"] = pos(ankle); rec["toe_base"] = pos(toe)
        if rec["heel_ctl"]:
            try_set(heel_c,"rotateX",30)
            rec["ankle_heel30"] = pos(ankle); rec["toe_heel30"] = pos(toe)
            rec["ankle_shift_heel30"] = dist(rec["ankle_base"], rec["ankle_heel30"])
            rec["toe_shift_heel30"]   = dist(rec["toe_base"], rec["toe_heel30"])
            try_set(heel_c,"rotateX",0)
        if rec["ball_ctl"]:
            try_set(ball_c,"rotateX",30)
            rec["ankle_ball30"] = pos(ankle); rec["toe_ball30"] = pos(toe)
            rec["ankle_shift_ball30"] = dist(rec["ankle_base"], rec["ankle_ball30"])
            rec["toe_shift_ball30"]   = dist(rec["toe_base"], rec["toe_ball30"])
            try_set(ball_c,"rotateX",0)
        if rec["tip_ctl"]:
            try_set(tip_c,"rotateX",30)
            rec["ankle_tip30"] = pos(ankle); rec["toe_tip30"] = pos(toe)
            rec["ankle_shift_tip30"] = dist(rec["ankle_base"], rec["ankle_tip30"])
            rec["toe_shift_tip30"]   = dist(rec["toe_base"], rec["toe_tip30"])
            try_set(tip_c,"rotateX",0)
        tests["revfoot_"+side] = rec

    # Test 7: mirror_pose (arm_L FK -> arm_R FK)
    for c in ("arm_L_FK_ctl","arm_R_FK_ctl"):
        for a in ("rotateX","rotateY","rotateZ"):
            try_set(c,a,0)
    rec = {}
    src = "arm_L_FK_ctl"; dst = "arm_R_FK_ctl"
    if cmds.objExists(src) and cmds.objExists(dst):
        try_set(src,"rotateX",25); try_set(src,"rotateY",15); try_set(src,"rotateZ",-40)
        rec["src_before"] = {a: try_get(src,a) for a in ("rotateX","rotateY","rotateZ")}
        rec["dst_before"] = {a: try_get(dst,a) for a in ("rotateX","rotateY","rotateZ")}
        try:
            n_ok, n_skip = attach_ctrls.mirror_pose([src])
            rec["ok"]=True; rec["n_ok"]=n_ok; rec["n_skip"]=n_skip
        except Exception as e:
            rec["ok"]=False; rec["err"]=str(e)[:200]
        rec["dst_after"] = {a: try_get(dst,a) for a in ("rotateX","rotateY","rotateZ")}
        rec["dst_inv"] = {a: try_get(dst,a) for a in ("invTx","invTy","invTz","invRx","invRy","invRz")}
        for a in ("rotateX","rotateY","rotateZ"):
            try_set(src,a,0); try_set(dst,a,0)
    else:
        rec["src_exists"]=cmds.objExists(src); rec["dst_exists"]=cmds.objExists(dst)
    tests["mirror_arm_L_FK_to_R"] = rec

    rec2 = {}
    src2="arm_L_IK_ctl"; dst2="arm_R_IK_ctl"
    if cmds.objExists(src2) and cmds.objExists(dst2):
        base_src = [try_get(src2,a) for a in ("translateX","translateY","translateZ")]
        base_dst = [try_get(dst2,a) for a in ("translateX","translateY","translateZ")]
        try_set(src2,"translateX", base_src[0]+10)
        try_set(src2,"translateY", base_src[1]-5)
        try:
            n_ok,n_skip = attach_ctrls.mirror_pose([src2])
            rec2["ok"]=True; rec2["n_ok"]=n_ok
        except Exception as e:
            rec2["ok"]=False; rec2["err"]=str(e)[:200]
        rec2["src_after"] = [try_get(src2,a) for a in ("translateX","translateY","translateZ")]
        rec2["dst_after"] = [try_get(dst2,a) for a in ("translateX","translateY","translateZ")]
        rec2["dst_inv"]   = {a: try_get(dst2,a) for a in ("invTx","invTy","invTz")}
        try_set(src2,"translateX", base_src[0]); try_set(src2,"translateY", base_src[1])
        try_set(dst2,"translateX", base_dst[0]); try_set(dst2,"translateY", base_dst[1])
    tests["mirror_arm_L_IK_to_R"] = rec2

    result["verdict"] = "OK"

except Exception as exc:
    result["fatal"] = str(exc)
    result["trace"] = traceback.format_exc()

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, default=str)
print("=== BEHAV5 done ===")
