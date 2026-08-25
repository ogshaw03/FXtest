import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import sys
sys.path.insert(0, r"E:/OG_Tools/FXtest")
import jiggle_bones as jb, importlib
importlib.reload(jb)

cmds.file(new=True, force=True)
# 素の textCurves 挙動確認
result = cmds.textCurves(f='Arial', t='Jiggle', ch=False)
top = result[0]
print(f"top: {top}")
all_desc = cmds.listRelatives(top, ad=True, type="transform") or []
letter_xforms = [x for x in all_desc if cmds.listRelatives(x, s=True, type="nurbsCurve")]
print(f"letter_xforms: {letter_xforms}")
for lx in letter_xforms[:3]:
    p = cmds.xform(lx, q=True, t=True, ws=True)
    shapes = cmds.listRelatives(lx, s=True, type="nurbsCurve") or []
    cvs = []
    for s in shapes:
        n_cv = cmds.getAttr(s + ".spans") + cmds.getAttr(s + ".degree")
        cvs.append(n_cv)
    print(f"  {lx}: ws_pos={[round(x,2) for x in p]}, shapes={len(shapes)}, cvs_per={cvs}")

cmds.delete(top)

# 次に _make_master_curve をステップごとに実行してどこで失敗するか
print("\n\n=== _make_master_curve step trace ===")
result = cmds.textCurves(f='Arial', t='Jiggle', ch=False)
text_top = result[0]
all_desc = cmds.listRelatives(text_top, ad=True, type="transform") or []
letter_xforms = [x for x in all_desc
                 if cmds.listRelatives(x, s=True, type="nurbsCurve")]
print(f"step 2: {len(letter_xforms)} letter_xforms")

freed = []
for lx in letter_xforms:
    try:
        lx_w = cmds.parent(lx, world=True)[0]
        cmds.makeIdentity(lx_w, apply=True, t=True, r=True, s=True)
        freed.append(lx_w)
    except Exception as e:
        print(f"  freed fail on {lx}: {e}")
print(f"step 3: {len(freed)} freed")

try: cmds.delete(text_top)
except: pass

master = cmds.createNode("transform", n="test_master")
for lx in freed:
    shapes = cmds.listRelatives(lx, s=True, type="nurbsCurve") or []
    for s in shapes:
        try:
            cmds.parent(s, master, s=True, r=True)
        except Exception as e:
            print(f"  parent fail on {s}: {e}")
    try: cmds.delete(lx)
    except: pass

bbox_a = cmds.exactWorldBoundingBox(master)
print(f"BEFORE rotate: bbox={[round(x,2) for x in bbox_a]}")

cmds.xform(master, ro=(-90, 0, 0))
bbox_b = cmds.exactWorldBoundingBox(master)
print(f"AFTER rotate (no freeze): bbox={[round(x,2) for x in bbox_b]}")

cmds.makeIdentity(master, apply=True, r=True)
bbox_c = cmds.exactWorldBoundingBox(master)
print(f"AFTER rotate freeze: bbox={[round(x,2) for x in bbox_c]}")

bbox = cmds.exactWorldBoundingBox(master)
cx = (bbox[0]+bbox[3])/2
cy = (bbox[1]+bbox[4])/2
cz = (bbox[2]+bbox[5])/2
print(f"center: cx={cx:.2f} cy={cy:.2f} cz={cz:.2f}")
cmds.xform(master, t=(-cx, -cy, -cz))
bbox_d = cmds.exactWorldBoundingBox(master)
print(f"AFTER center translate: bbox={[round(x,2) for x in bbox_d]}")

cmds.makeIdentity(master, apply=True, t=True)
bbox_e = cmds.exactWorldBoundingBox(master)
print(f"AFTER translate freeze: bbox={[round(x,2) for x in bbox_e]}")

cmds.xform(master, s=(3, 3, 3))
bbox_f = cmds.exactWorldBoundingBox(master)
print(f"AFTER scale 3: bbox={[round(x,2) for x in bbox_f]}")

cmds.makeIdentity(master, apply=True, s=True)
bbox_g = cmds.exactWorldBoundingBox(master)
print(f"AFTER scale freeze: bbox={[round(x,2) for x in bbox_g]}")
