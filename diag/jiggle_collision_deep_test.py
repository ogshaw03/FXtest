"""collision が実際に効いているか強力に検証: hair を高所→落下→ 平面に衝突。"""
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

# 高所から吊り下がる hair chain
cmds.select(cl=True)
chain = []
for i in range(1, 11):
    nm = f"H{i}"
    cmds.joint(n=nm, p=(0, 30 - i * 1.5, 0))   # Y=28.5 → 15
    chain.append(nm)
if cmds.listRelatives(chain[0], p=True):
    cmds.parent(chain[0], world=True)

# 平面 collider at Y=5 (chain の tip Y=15 の下)
ground = cmds.polyPlane(w=30, h=30, sx=4, sy=4, n="ground_geo")[0]
cmds.xform(ground, ws=True, t=(0, 5, 0))

# jiggle setup
result = jb.create_jiggle_for_chain(chain, category="hair")
# 極端に柔らかく + 引き戻し無し → 重力で確実に落ちるはず
jb.set_category_params("hair", stiffness=0.0, damp=0.02,
                        startCurveAttract=0.0, mass=1.0)
jb.add_collider(ground)
# dynBlend = 1 で simulation full active
cmds.setAttr(f"{result['root_ctl']}.dynBlend", 1.0)

# 診断出力
jb.diagnose_collision()

# playback で hair が落ちて ground (Y=5) に衝突するはず。
# ground を貫通する = tip Y が 5 未満に落ちる → 貫通判定
cmds.playbackOptions(min=1, max=200)
cmds.setAttr("jb_nucleus.subSteps", 10)   # 高精度
min_tip_y = 999.0
tip_ys = []
for f in range(1, 201):
    cmds.currentTime(f)
    tip_y = cmds.xform("H10", q=True, ws=True, t=True)[1]
    tip_ys.append((f, tip_y))
    min_tip_y = min(min_tip_y, tip_y)

# 期待: hair は落ちるが ground (Y=5) より下には行かない (thickness 分は許容)
# thickness = mesh bbox 最短辺 * 2% ~ 0.02 * 30 = 0.6 くらい
thickness = cmds.getAttr("jb_collider_ground_geo|jb_collider_ground_geoShape.thickness")
print(f"\ncollider thickness = {thickness}")
print(f"H10 min Y throughout playback = {min_tip_y:.3f}")
print(f"H10 Y trajectory (10-frame stride):")
for i in range(0, len(tip_ys), 10):
    f, y = tip_ys[i]
    print(f"  frame {f:3d}  Y={y:.3f}")

# 判定: 貫通してなければ min_tip_y > (5 - thickness - 0.5) (少し余裕)
threshold = 5 - thickness - 0.5
ok = min_tip_y > threshold
report("hair_bounces_off_ground_plane", ok,
       f"min_y={min_tip_y:.3f}, threshold={threshold:.3f} "
       f"(ground=5, thickness={thickness:.2f})")

print("=== JIGGLE V0.4.3 DEEP COLLISION TEST DONE ===")
