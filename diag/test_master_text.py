"""_make_master_curve が "Jiggle" text で正しく curve 生成できるか test。"""
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import sys
sys.path.insert(0, r"E:/OG_Tools/FXtest")
import jiggle_bones as jb, importlib
importlib.reload(jb)

cmds.file(new=True, force=True)
try:
    ctl = jb._make_master_curve("test_master", size=3.0)
    print(f"created: {ctl}")
    shapes = cmds.listRelatives(ctl, s=True, type="nurbsCurve") or []
    print(f"shapes: {len(shapes)}")
    bbox = cmds.exactWorldBoundingBox(ctl)
    print(f"bbox: {[round(x,2) for x in bbox]}")
    print(f"width X: {bbox[3]-bbox[0]:.2f}, height Y: {bbox[4]-bbox[1]:.2f}, "
          f"depth Z: {bbox[5]-bbox[2]:.2f}")
    print("PASS: text master ctl 作成 OK")
except Exception as exc:
    import traceback; traceback.print_exc()
    print(f"FAIL: {exc}")
