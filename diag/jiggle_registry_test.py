"""v0.4.0: registry (pick-based) API 検証。

- add_registered_chain / get_registered_chains / remove_registered_chain
- build_chain_from_selection (単一/複数)
- scene 永続化 (jiggle_bones_grp.chainRegistry attr)
- setup → remove_jiggle → registry 経由の再 setup
"""
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

# 2 chain 作成 (hair と tail)
cmds.select(cl=True)
for i in range(1, 6):
    cmds.joint(n=f"hair{i}", p=(0, 10 - 2*i, 0))
if cmds.listRelatives("hair1", p=True):
    cmds.parent("hair1", world=True)

cmds.select(cl=True)
for i in range(1, 4):
    cmds.joint(n=f"tail{i}", p=(5, 5 - i, 0))
if cmds.listRelatives("tail1", p=True):
    cmds.parent("tail1", world=True)

# 別 chain (無名、ピック用)
cmds.select(cl=True)
for i in range(1, 4):
    cmds.joint(n=f"custom{i}", p=(-5, 5 - i, 0))
if cmds.listRelatives("custom1", p=True):
    cmds.parent("custom1", world=True)

# === build_chain_from_selection: 単一 joint 選択 → DFS ===
cmds.select("hair1", r=True)
chain = jb.build_chain_from_selection()
report("build_single_selection", chain == ["hair1","hair2","hair3","hair4","hair5"],
       f"got: {chain}")

# === build_chain_from_selection: 複数選択 (順序保持) ===
cmds.select(["custom1", "custom2", "custom3"], r=True)
chain = jb.build_chain_from_selection()
report("build_multi_selection", chain == ["custom1","custom2","custom3"],
       f"got: {chain}")

# === registry: add / get / remove ===
jb.add_registered_chain(["hair1","hair2","hair3","hair4","hair5"], category="hair")
jb.add_registered_chain(["tail1","tail2","tail3"], category="tail")
jb.add_registered_chain(["custom1","custom2","custom3"], category="ribbon")

reg = jb.get_registered_chains()
ok = (len(reg.get("hair", [])) == 1 and
       len(reg.get("tail", [])) == 1 and
       len(reg.get("ribbon", [])) == 1)
report("registry_add_3_categories", ok, f"reg keys: {list(reg.keys())}")

# category 未指定なら _classify で自動判定
jb.add_registered_chain(["hair1","hair2","hair3","hair4","hair5"])  # replace, auto cat
reg = jb.get_registered_chains()
report("registry_auto_classify_hair", "hair" in reg
       and reg["hair"][0][0] == "hair1")

# 重複除去 (chain[0] 名で dedup)
jb.add_registered_chain(["hair1","hair2","hair3"], category="skirt")
reg = jb.get_registered_chains()
ok = (len(reg.get("hair", [])) == 0 and
       len(reg.get("skirt", [])) == 1 and
       reg["skirt"][0] == ["hair1","hair2","hair3"])
report("registry_dedup_by_root_name", ok, f"reg: {reg}")

# === scene 永続化 attr の存在 ===
report("registry_persisted_attr",
       cmds.objExists("jiggle_bones_grp") and
       cmds.attributeQuery("chainRegistry", node="jiggle_bones_grp", exists=True))

# === remove_registered_chain ===
jb.remove_registered_chain(["hair1"])  # skirt category から除去
reg = jb.get_registered_chains()
report("registry_remove_by_root_name",
       "skirt" not in reg or all(c[0] != "hair1" for c in reg.get("skirt", [])))

# === setup → remove flow (registry を使う) ===
# hair chain を再登録して setup
jb.add_registered_chain(["hair1","hair2","hair3","hair4","hair5"], category="hair")
jb.create_jiggle_for_chain(["hair1","hair2","hair3","hair4","hair5"], "hair")
report("setup_via_registry_creates_ctls", cmds.objExists("hair1_jbCtl"))

# category param を dial してから remove する registry の残存確認
jb.set_category_params("hair", stiffness=0.5)
jb.remove_jiggle_for_chain(["hair1","hair2","hair3","hair4","hair5"])
report("remove_only_tears_down_rig",
       not cmds.objExists("hair1_jbCtl"))
# registry には残っている (再セットアップ可能)
reg = jb.get_registered_chains()
report("registry_survives_rig_removal",
       "hair" in reg and reg["hair"][0][0] == "hair1")

# === auto-detect populate (補助機能) ===
_write_reg = jb._write_registry
_write_reg([])   # clear
jb._ui_populate_registry_from_names() if hasattr(jb, "_ui_populate_registry_from_names") else None
# call the underlying find_jiggle_chains-based add
for cat, chains in jb.find_jiggle_chains().items():
    for c in chains:
        jb.add_registered_chain(c, category=cat)
reg = jb.get_registered_chains()
report("auto_populate_registry_from_names",
       len(reg) >= 2,   # hair + tail 少なくとも
       f"populated: {list(reg.keys())}")

print("=== JIGGLE V0.4.0 REGISTRY TEST DONE ===")
