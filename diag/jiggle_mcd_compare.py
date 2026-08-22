"""Maya の makeCurvesDynamic MEL が作る follicle/hairSystem 接続を dump し、
自分の _add_follicle_to_hair_system と比較する。"""
import sys
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

sys.path.insert(0, r"E:/OG_Tools/FXtest")


def dump_connections(node, tag):
    """node の全 in/out connection を list"""
    print(f"\n--- {tag}: {node} ---")
    ins = cmds.listConnections(node, s=True, d=False, p=True, c=True) or []
    outs = cmds.listConnections(node, s=False, d=True, p=True, c=True) or []
    if ins:
        print("  INCOMING:")
        for i in range(0, len(ins), 2):
            dst, src = ins[i], ins[i+1]
            print(f"    {src} → {dst}")
    if outs:
        print("  OUTGOING:")
        for i in range(0, len(outs), 2):
            src, dst = outs[i], outs[i+1]
            print(f"    {src} → {dst}")


# ============ Case 1: makeCurvesDynamic (Maya 標準) ============
cmds.file(new=True, force=True)
try:
    cmds.loadPlugin("nHair", quiet=True)
except:
    pass

pts = [(0, 15, 0), (0, 12, 0), (0, 9, 0), (0, 6, 0), (0, 3, 0)]
c = cmds.curve(d=3, p=pts, n="hairA_crv")
cmds.select(c, r=True)
# makeCurvesDynamic の引数: 1st=1 or 2 (existing/new hair system)、
# 2nd=[ "attachToSelected", "createRestCurves", "createRestPositions", "duplicateOnly", "start" ]
result = mel.eval('makeCurvesDynamic 2 { "0", "0", "1", "1", "0" };')
print("makeCurvesDynamic result:", result)

# 生成された follicle と hairSystem を検索
follicles = cmds.ls(type="follicle") or []
hair_systems = cmds.ls(type="hairSystem") or []
print(f"follicles: {follicles}")
print(f"hairSystems: {hair_systems}")

if follicles:
    foll = follicles[0]
    foll_xf = cmds.listRelatives(foll, p=True)[0]
    dump_connections(foll, "MCD follicle shape")
    dump_connections(foll_xf, "MCD follicle transform")

if hair_systems:
    hs = hair_systems[0]
    dump_connections(hs, "MCD hairSystem shape")

# 主要 attr 値
if follicles:
    foll = follicles[0]
    print("\n--- MCD follicle attrs ---")
    for a in ("simulationMethod", "pointLock", "restPose",
              "startDirection", "flipDirection", "collide"):
        try:
            print(f"  .{a} = {cmds.getAttr(foll + '.' + a)}")
        except:
            pass

if hair_systems:
    hs = hair_systems[0]
    print("\n--- MCD hairSystem attrs ---")
    for a in ("collide", "collideStrength", "iterations",
              "collideOverSample", "collideWidthOffset",
              "stiffness", "damp", "startCurveAttract", "mass"):
        try:
            print(f"  .{a} = {cmds.getAttr(hs + '.' + a)}")
        except:
            pass


# ============ Case 2: 私の _add_follicle_to_hair_system ============
print("\n\n" + "=" * 70)
print("Case 2: my custom setup")
print("=" * 70)
cmds.file(new=True, force=True)

import jiggle_bones as jb, importlib
importlib.reload(jb)

cmds.select(cl=True)
chain = []
for i in range(1, 6):
    nm = f"h{i}"
    cmds.joint(n=nm, p=(0, 15 - i * 3, 0))
    chain.append(nm)
if cmds.listRelatives(chain[0], p=True):
    cmds.parent(chain[0], world=True)

jb.create_jiggle_for_chain(chain, category="hair")

my_follicles = cmds.ls("jb_foll_*Shape", type="follicle")
my_hs = cmds.ls("jb_hairSystem_*Shape", type="hairSystem")

if my_follicles:
    foll = my_follicles[0]
    foll_xf = cmds.listRelatives(foll, p=True)[0]
    dump_connections(foll, "MY follicle shape")
    dump_connections(foll_xf, "MY follicle transform")
    print("\n--- MY follicle attrs ---")
    for a in ("simulationMethod", "pointLock", "restPose",
              "startDirection", "flipDirection", "collide"):
        try:
            print(f"  .{a} = {cmds.getAttr(foll + '.' + a)}")
        except:
            pass

if my_hs:
    dump_connections(my_hs[0], "MY hairSystem shape")

print("\n=== DONE ===")
