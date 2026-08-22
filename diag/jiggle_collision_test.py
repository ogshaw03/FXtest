"""v0.4.1: hairSystem.collide 有効化と衝突判定の実測。

- 新規 hairSystem に collide=1 が set されるか
- add_collider が既存 hairSystem にも collide=1 を反映するか
- 実際に mesh との衝突で hair joint が mesh 面より下に進入しないか
"""
import sys, math
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds

sys.path.insert(0, r"E:/OG_Tools/FXtest")


def report(name, ok, extra=""):
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {name}  {extra}")


import jiggle_bones as jb, importlib
importlib.reload(jb)

cmds.file(new=True, force=True)

# hair chain (top-level、垂直に長い)
cmds.select(cl=True)
chain = []
for i in range(1, 8):
    nm = f"H{i}"
    cmds.joint(n=nm, p=(0, 15 - 2*i, 0))   # Y=13 → -1
    chain.append(nm)
if cmds.listRelatives(chain[0], p=True):
    cmds.parent(chain[0], world=True)

# setup jiggle
jb.create_jiggle_for_chain(chain, category="hair")
hs_shape = cmds.listRelatives("jb_hairSystem_hair", s=True)[0]

# Case 1: 新規 hairSystem に collide=1 が付いているか
val = cmds.getAttr(f"{hs_shape}.collide")
report("new_hair_system_has_collide_on", val == 1,
       f"collide = {val}")

# Case 2: 既存 hairSystem に collide=0 を戻して enable_collision_on_all() で復活
cmds.setAttr(f"{hs_shape}.collide", 0)
jb.enable_collision_on_all_hair_systems()
val = cmds.getAttr(f"{hs_shape}.collide")
report("enable_collision_all_reactivates", val == 1)

# Case 3: add_collider が呼ばれた時にも collide=1 反映
cmds.setAttr(f"{hs_shape}.collide", 0)
ground = cmds.polyPlane(w=30, h=30, sx=2, sy=2, n="ground_geo")[0]
# ground を Y=0 平面に配置 (hair は Y=1〜13 で吊り下がる)
cmds.xform(ground, ws=True, t=(0, 0, 0))
jb.add_collider(ground)
val = cmds.getAttr(f"{hs_shape}.collide")
report("add_collider_forces_collide_on", val == 1)

# Case 4: nucleus と nRigid の接続
nr_shape = cmds.listRelatives("jb_collider_ground_geo", s=True)[0]
# nRigid の startState → nucleus.inputPassiveStart に繋がっているか
conns = cmds.listConnections(f"{nr_shape}.startState", d=True, s=False,
                              type="nucleus") or []
report("nrigid_connected_to_nucleus", "jb_nucleus" in conns,
       f"conns: {conns}")

# Case 5: 実際の衝突挙動をシミュレート
#   - hair 下端 (H7) を Y=1 に置く。dynBlend=1 で重力落下する条件を作る。
#   - ground は Y=0。 collide が効けば H7.Y は 0 未満に落ちないはず (± thickness)。
cmds.playbackOptions(min=1, max=80)
cmds.setAttr("H1_jbCtl.dynBlend", 1.0)
# nucleus の substeps を増やして高精度化
cmds.setAttr("jb_nucleus.subSteps", 6)
# hair をやや柔らかく (下端が確実に地面まで到達するように)
jb.set_category_params("hair", stiffness=0.02, damp=0.10,
                        startCurveAttract=0.0, mass=1.0)

# playback 80 frames
cmds.currentTime(1)
lowest_y = 999.0
for f in range(1, 81):
    cmds.currentTime(f)
    tip_y = cmds.xform("H7", q=True, ws=True, t=True)[1]
    lowest_y = min(lowest_y, tip_y)
report("collision_prevents_penetration", lowest_y > -1.0,
       f"H7 最低 Y = {lowest_y:.3f} (期待: > -1.0、地面 Y=0 を大きく貫通しない)")

print("=== JIGGLE V0.4.1 COLLISION TEST DONE ===")
