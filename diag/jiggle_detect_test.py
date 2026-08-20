"""Verify jiggle_bones chain detection on Nekotatune."""
import sys
import maya.standalone
maya.standalone.initialize()
import maya.cmds as cmds
import maya.mel as mel

sys.path.insert(0, r"E:/OG_Tools/FXtest")
cmds.file(new=True, force=True)
cmds.loadPlugin("fbxmaya", quiet=True)
mel.eval(chr(70)+"BXImport -f "+chr(34)+
         r"E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx"+chr(34)+";")

import jiggle_bones, fbx_renamer, importlib
importlib.reload(fbx_renamer); importlib.reload(jiggle_bones)
fbx_renamer.remove_all_namespaces()
fbx_renamer.rename_all_joints()

chains = jiggle_bones.find_jiggle_chains()
print("\n=== Detected jiggle chains ===")
total = 0
for tag, chain_list in sorted(chains.items()):
    print(f"\n[{tag}]  ({len(chain_list)} chain)")
    for chain in chain_list:
        print(f"  {chain[0]}  →  ({len(chain)} joints)  →  {chain[-1]}")
        total += 1
print(f"\ntotal chains: {total}")
print("=== DONE ===")
