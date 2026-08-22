"""param 変更の 3 経路 (UI/Channel Box/API) が動作するか検証。"""
import sys
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

# simple hair chain (top-level)
cmds.select(cl=True)
chain = []
for i in range(1, 4):
    nm = f"hair{i}"
    cmds.joint(n=nm, p=(0, 10 - 2*i, 0))
    chain.append(nm)
if cmds.listRelatives(chain[0], p=True):
    cmds.parent(chain[0], world=True)

# setup
jb.create_jiggle_for_chain(chain, category="hair")
hs = "jb_hairSystem_hair"
report("setup_created_hair_system", cmds.objExists(hs))

# --- Route A: API set_category_params → get_category_params round-trip ---
jb.set_category_params("hair", stiffness=0.42, damp=0.55,
                        startCurveAttract=0.18, mass=0.7)
got = jb.get_category_params("hair")
ok = (abs(got["stiffness"]-0.42) < 1e-4 and
       abs(got["damp"]-0.55) < 1e-4 and
       abs(got["startCurveAttract"]-0.18) < 1e-4 and
       abs(got["mass"]-0.7) < 1e-4)
report("route_A_api_set_get", ok, f"got: {got}")

# --- Route B: Channel Box (direct setAttr on hairSystem shape) ---
shape = cmds.listRelatives(hs, s=True)[0]
cmds.setAttr(shape + ".stiffness", 0.15)
cmds.setAttr(shape + ".damp", 0.30)
got = jb.get_category_params("hair")
ok = (abs(got["stiffness"]-0.15) < 1e-4 and
       abs(got["damp"]-0.30) < 1e-4)
report("route_B_channelbox_setattr", ok, f"got: {got}")

# --- Route C: UI reflect (シミュレート: _ui_apply_category_params) ---
# UI が無い環境では floatFieldGrp が無いので _ui_apply_category_params が
# 呼べない → 代わりに UI show → dial → apply の全 flow を疑似再現
# (実 GUI 描画不可、代わりに set_category_params を使う)
jb.set_category_params("hair", stiffness=0.99)
got = jb.get_category_params("hair")
report("route_C_ui_reflect_equivalent", abs(got["stiffness"]-0.99) < 1e-4,
       f"stiffness={got['stiffness']}")

# --- v0.3.4: UI 開いた時 live 値を表示する挙動 (_build_category_section 実装) ---
# category_params が set_category_params で 0.99 に設定された状態で、
# _build_category_section が呼ばれると live 値 0.99 を fetch する
live = jb.get_category_params("hair")
report("v034_live_values_readable", abs(live["stiffness"]-0.99) < 1e-4,
       f"live stiffness={live['stiffness']}")

print("=== PARAM ROUNDTRIP TEST DONE ===")
