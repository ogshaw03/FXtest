"""orient_joints_preserving_weights の 保護性を verify (multi scenario)。"""
import sys, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.api.OpenMaya as om

sys.path.insert(0, r"E:/OG_Tools/FXtest")
import jiggle_bones as jb
import importlib
importlib.reload(jb)


def snapshot_vertex_ws(mesh):
    n = cmds.polyEvaluate(mesh, v=True)
    return [cmds.xform(f"{mesh}.vtx[{i}]", q=True, ws=True, t=True)
            for i in range(n)]


def max_vertex_drift(before, after):
    md = 0.0
    for a, b in zip(before, after):
        d = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))
        md = max(md, d)
    return md


def check_jo_points_at_child(joints, aim_axis_key="y"):
    ok = True
    axis_local_map = {"x": om.MVector(1, 0, 0),
                       "y": om.MVector(0, 1, 0),
                       "z": om.MVector(0, 0, 1)}
    axis_local = axis_local_map[aim_axis_key]
    for j in joints:
        child = cmds.listRelatives(j, c=True, type="joint") or []
        if not child:
            continue
        c = child[0]
        wm = om.MMatrix(cmds.getAttr(j + ".worldMatrix[0]"))
        axis_world = axis_local * wm
        j_ws = cmds.xform(j, q=True, ws=True, t=True)
        c_ws = cmds.xform(c, q=True, ws=True, t=True)
        aim = om.MVector(c_ws[0] - j_ws[0], c_ws[1] - j_ws[1], c_ws[2] - j_ws[2])
        if aim.length() < 1e-6: continue
        aim = aim.normalize()
        axis_w_v = om.MVector(axis_world.x, axis_world.y, axis_world.z).normalize()
        cos = aim * axis_w_v
        marker = "OK" if cos > 0.98 else "BAD"
        print(f"    {j}: {aim_axis_key}_local->world={[round(axis_w_v[i],3) for i in range(3)]}, "
              f"aim->child={[round(aim[i],3) for i in range(3)]}, dot={cos:.3f} [{marker}]")
        if cos < 0.98: ok = False
    return ok


# ============ Scenarios ============

def scenario_simple():
    cmds.file(new=True, force=True)
    parent = cmds.joint(p=(0, 15, 0), n="root_j")
    cmds.select(parent, r=True)
    j1 = cmds.joint(p=(0, 10, 0), n="s1")
    j2 = cmds.joint(p=(0, 5, 0), n="s2")
    j3 = cmds.joint(p=(0, 0, 0), n="s3")
    for j in (parent, j1, j2, j3):
        cmds.setAttr(j + ".jointOrient", 0, 0, 0)
        cmds.setAttr(j + ".rotate", 0, 0, 0)
    cmds.setAttr(j1 + ".rotate", 10, 5, -3)
    cmds.setAttr(j2 + ".rotate", -8, 12, 2)
    return parent, [j1, j2, j3]


def scenario_mmd_like():
    """MMD 系: joint に pre-existing jointOrient (X aim 想定)。"""
    cmds.file(new=True, force=True)
    parent = cmds.joint(p=(0, 15, 0), n="hips")
    cmds.select(parent, r=True)
    j1 = cmds.joint(p=(0, 10, 0), n="skirt_00")
    j2 = cmds.joint(p=(0, 5, 0), n="skirt_01")
    j3 = cmds.joint(p=(0, 0, 0), n="skirt_02")
    # 全 jointOrient を X aim 想定 (X が 下方向、なので (0,0,-90))
    cmds.setAttr(j1 + ".jointOrient", 0, 0, -90)
    cmds.setAttr(j2 + ".jointOrient", 0, 0, -90)
    cmds.setAttr(j3 + ".jointOrient", 0, 0, -90)
    # 一部 rotate 非ゼロ
    cmds.setAttr(j1 + ".rotate", 5, 3, 0)
    return parent, [j1, j2, j3]


def scenario_multi_chain():
    """複数 chain (skirt 3 本)。"""
    cmds.file(new=True, force=True)
    hips = cmds.joint(p=(0, 15, 0), n="hips")
    chains = []
    for i, ang in enumerate([0, 120, 240]):
        cmds.select(hips, r=True)
        rad = math.radians(ang)
        # 円周状に skirt を配置
        x0, z0 = 3 * math.cos(rad), 3 * math.sin(rad)
        j1 = cmds.joint(p=(x0, 10, z0), n=f"skirt{i}_00")
        j2 = cmds.joint(p=(x0 * 1.3, 5, z0 * 1.3), n=f"skirt{i}_01")
        j3 = cmds.joint(p=(x0 * 1.5, 0, z0 * 1.5), n=f"skirt{i}_02")
        for j in (j1, j2, j3):
            cmds.setAttr(j + ".jointOrient", 0, 0, 0)
        cmds.setAttr(j1 + ".rotate", 4, 2, -1)
        chains.append([j1, j2, j3])
    return hips, chains


def build_mesh_and_bind(parent, joints_flat):
    mesh = cmds.polyCube(w=4, h=16, d=4, sx=1, sy=8, sz=1, n="testMesh")[0]
    cmds.move(0, 7, 0, mesh)
    cmds.select(mesh, r=True)
    cmds.select(parent, add=True)
    for j in joints_flat:
        cmds.select(j, add=True)
    return cmds.skinCluster(parent, *joints_flat, mesh, tsb=True)[0]


def run_scenario(name, build_fn, aim_str="yzx"):
    print(f"\n{'='*60}\n=== SCENARIO: {name} (aim={aim_str}) ===\n{'='*60}")
    result = build_fn()
    if isinstance(result[1][0], list):   # multi_chain の場合
        parent, chains = result
        all_joints = [parent] + [j for chain in chains for j in chain]
        roots = [chain[0] for chain in chains]   # 各 chain の root を渡す
    else:
        parent, joints = result
        all_joints = [parent] + joints
        roots = [joints[0]]   # skirt root
    mesh = "testMesh"
    build_mesh_and_bind(parent, all_joints[1:])

    vtx_before = snapshot_vertex_ws(mesh)
    for j in all_joints:
        r = cmds.getAttr(j + ".rotate")[0]
        jo = cmds.getAttr(j + ".jointOrient")[0]
        print(f"  BEFORE {j}: r={[round(x,1) for x in r]} jo={[round(x,1) for x in jo]}")

    n = jb.orient_joints_preserving_weights(roots, aim=aim_str)
    print(f"orient ran on {n} joint(s)")

    vtx_after = snapshot_vertex_ws(mesh)
    for j in all_joints:
        r = cmds.getAttr(j + ".rotate")[0]
        jo = cmds.getAttr(j + ".jointOrient")[0]
        print(f"  AFTER  {j}: r={[round(x,1) for x in r]} jo={[round(x,1) for x in jo]}")

    drift = max_vertex_drift(vtx_before, vtx_after)
    print(f"\nMAX VERTEX DRIFT: {drift:.6f}")
    key = aim_str[0]
    # v0.5.27 test fix: aim check は "私たちが orient した joint" だけを対象に。
    # parent (hips 等) は orient 対象外なので aim が child を向かなくて当然。
    oriented_joints = []
    for root in roots:
        oriented_joints.extend(jb._collect_chain_from_root(root))
    aim_ok = check_jo_points_at_child(oriented_joints, key)
    mesh_ok = drift < 0.01
    print(f"MESH PRESERVED   : {'PASS' if mesh_ok else 'FAIL(drift=' + str(round(drift, 4)) + ')'}")
    print(f"AIM POINTS TO CHILD: {'PASS' if aim_ok else 'FAIL'}")
    return mesh_ok and aim_ok


def scenario_complex_parent():
    """親 chain が deep で 親 に rotation あり、user rig 想定。"""
    cmds.file(new=True, force=True)
    world_root = cmds.joint(p=(0, 20, 0), n="world_root")
    spine = cmds.joint(p=(0, 18, 0), n="spine")
    hips = cmds.joint(p=(0, 15, 0), n="hips")
    # 親に rotation
    cmds.setAttr(world_root + ".rotate", 5, 10, -3)
    cmds.setAttr(spine + ".rotate", -2, 8, 1)
    # skirt chain
    cmds.select(hips, r=True)
    j1 = cmds.joint(p=(0, 10, 0), n="skirt_00")
    j2 = cmds.joint(p=(0, 5, 0), n="skirt_01")
    j3 = cmds.joint(p=(0, 0, 0), n="skirt_02")
    # skirt に 変な jointOrient (MMD import 風)
    cmds.setAttr(j1 + ".jointOrient", 45, 30, 120)
    cmds.setAttr(j2 + ".jointOrient", -20, 60, 90)
    cmds.setAttr(j3 + ".jointOrient", 10, -30, 45)
    cmds.setAttr(j1 + ".rotate", 3, 2, -1)
    return world_root, [j1, j2, j3]


def scenario_mesh_with_extra_influence():
    """mesh が chain 外 joint (parent) にも weight を持つ 実 rig 想定。"""
    cmds.file(new=True, force=True)
    parent = cmds.joint(p=(0, 15, 0), n="hips")
    cmds.select(parent, r=True)
    j1 = cmds.joint(p=(0, 10, 0), n="skirt_00")
    j2 = cmds.joint(p=(0, 5, 0), n="skirt_01")
    j3 = cmds.joint(p=(0, 0, 0), n="skirt_02")
    for j in (j1, j2, j3):
        cmds.setAttr(j + ".jointOrient", 0, 0, 30)   # 変な JO
    cmds.setAttr(j1 + ".rotate", 5, 5, 5)
    # 別 joint (chain 外) も 追加、mesh weight は これにも入る
    cmds.select(cl=True)
    body = cmds.joint(p=(0, 12, 3), n="body")
    return parent, [j1, j2, j3]


results = []
results.append(("simple", run_scenario("simple", scenario_simple, "yzx")))
results.append(("mmd_like", run_scenario("mmd_like", scenario_mmd_like, "yzx")))
results.append(("multi_chain", run_scenario("multi_chain", scenario_multi_chain, "yzx")))
results.append(("complex_parent", run_scenario("complex_parent",
                                                 scenario_complex_parent, "yzx")))
results.append(("aim_xyz", run_scenario("aim_xyz", scenario_simple, "xyz")))
results.append(("aim_zxy", run_scenario("aim_zxy", scenario_simple, "zxy")))


# scenario: jiggle setup 後に orient (user が やっていた可能性)
def scenario_orient_after_jiggle():
    """jiggle setup 済み chain で orient を実行。SplineIK/curve が壊れる?"""
    parent, joints = scenario_mmd_like()
    print("  [PRE-JIGGLE-SETUP: create_jiggle_for_chain]")
    try:
        jb.create_jiggle_for_chain(joints, category="skirt")
        print("  [JIGGLE SETUP OK]")
    except Exception as exc:
        print(f"  [JIGGLE SETUP FAILED: {exc}]")
    return parent, joints


print(f"\n{'='*60}\n=== jiggle setup 済みで orient (v0.5.27: 拒否確認) ===")
# jiggle setup 済み chain で orient → v0.5.27 は abort (return 0) が正しい挙動。
# mesh 保護されて返す = テスト PASS。
try:
    parent, joints = scenario_mmd_like()
    jb.create_jiggle_for_chain(joints, category="skirt")
    build_mesh_and_bind(parent, [parent] + joints)
    vtx_before = snapshot_vertex_ws("testMesh")
    n = jb.orient_joints_preserving_weights([joints[0]], aim="yzx")
    vtx_after = snapshot_vertex_ws("testMesh")
    drift = max_vertex_drift(vtx_before, vtx_after)
    abort_ok = (n == 0 and drift < 0.01)
    print(f"orient aborted (n={n}), mesh drift = {drift:.6f}")
    print(f"jiggle-active abort: {'PASS' if abort_ok else 'FAIL'}")
    results.append(("jiggle_active_abort", abort_ok))
except Exception as exc:
    import traceback; traceback.print_exc()
    results.append(("jiggle_active_abort", False))

print(f"\n{'='*60}\n=== SUMMARY ===")
for name, ok in results:
    print(f"  {name:20s}: {'PASS' if ok else 'FAIL'}")
overall = all(ok for _, ok in results)
print(f"\nOVERALL: {'ALL PASS' if overall else 'FAIL'}")
