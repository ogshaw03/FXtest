"""attach_ctrls -- 選択した骨に mGear 風のコントローラーを一括セットアップ (Maya 2023)

既にスキンが塗られている骨にコントローラーを載せる retrofit ワークフロー用。
mGear の Shifter は build-from-scratch 前提なので、そこは使わず
maya.cmds だけで同等の見た目 (NPO + CTL パターン、side 別カラー) を組む。

処理:
  1. 選択された各 joint に対し:
     - NURBS カーブでキューブ形状のコントローラを生成 (<joint>_ctl)
     - 1 段上に offset グループ NPO を挟む (<joint>_npo)
     - NPO を joint の pos/rot にスナップ (matchTransform)
     - side を joint 名から判定して drawing override で着色
       L=blue(6) / R=red(13) / C=yellow(17)
     - controller の scale / visibility を lock+hide
  2. NPO を骨階層に合わせて親子関係を再現
     - 親 joint も選択されていれば → 親 joint の CTL の下に parent
     - そうでなければ → attach_ctrls_grp の下に置く (ルート)
  3. joint を CTL に parentConstraint(mo=False)

MMD 由来の Japanese 骨名 (左腕 / 右足首 等) の side 判定に対応。
"""

from __future__ import annotations

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore

# fbx_renamer は同じ user scripts dir に install される。install.py で
# _REMOTE_FILES に列挙されているので存在保証あり。
try:
    import fbx_renamer  # type: ignore
except ImportError:
    fbx_renamer = None  # type: ignore


__version__ = "0.3.1"


WINDOW = "attach_ctrlsWin"

# --- CUSTOMIZE -----------------------------------------------------------
_GITHUB_OWNER = "ogshaw03"
_GITHUB_REPO = "FXtest"
_GITHUB_BRANCH = "main"
_PACKAGE = "attach_ctrls"
# --- END CUSTOMIZE -------------------------------------------------------

_GITHUB_API = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}"
_GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}"


ROOT_GROUP = "attach_ctrls_grp"

# Maya drawing override color indices
COLOR_L = 6   # blue
COLOR_R = 13  # red
COLOR_C = 17  # yellow
COLOR_DECOR = 2  # dark grey (装飾骨用、視覚的に控えめ)

# 装飾骨判定パターン (skip_decoration=True 時に attach_controllers から除外)
# 部分一致 (short name の lowercase に含まれれば装飾扱い)
_DECORATION_TOKENS = (
    "hair", "front_hair", "back_hair", "side_hair",
    "ribbon", "skirt", "sleeve", "necktie", "coat",
    "cat_ear", "ear", "tail",
    "cancel",  # MMD の cancel bone (物理補助)
)

# 完全一致で装飾判定するもの (H1..H99 のヘアチェーン等)
def _is_hair_chain(short_name):
    if len(short_name) < 2:
        return False
    if short_name[0] in ("H", "h") and short_name[1:].isdigit():
        return True
    # "H15_end" みたいなのも hair chain
    if short_name.endswith("_end"):
        core = short_name[:-4]
        if len(core) >= 2 and core[0] in ("H","h") and core[1:].isdigit():
            return True
    return False


def _is_decoration(joint_name):
    short = joint_name.split("|")[-1]
    if _is_hair_chain(short):
        return True
    lo = short.lower()
    # side/suffix 除去
    for suf in ("_l", "_r", "_c", "_end"):
        if lo.endswith(suf):
            lo = lo[:-len(suf)]
    for tok in _DECORATION_TOKENS:
        if tok in lo:
            return True
    return False


# --- Auto ctl scale (bone length ベース) ---

def _bone_length(joint):
    """joint から最初の child joint までの距離。無ければ parent 距離。0 なら None。"""
    p = cmds.xform(joint, q=True, ws=True, t=True)
    kids = cmds.listRelatives(joint, c=True, type="joint") or []
    if kids:
        cp = cmds.xform(kids[0], q=True, ws=True, t=True)
        d = sum((a-b)**2 for a,b in zip(p, cp))**0.5
        if d > 0.001:
            return d
    parents = cmds.listRelatives(joint, p=True, type="joint") or []
    if parents:
        pp = cmds.xform(parents[0], q=True, ws=True, t=True)
        d = sum((a-b)**2 for a,b in zip(p, pp))**0.5
        if d > 0.001:
            return d
    return None


def _auto_ctl_scale(joint, mult=1.0, min_s=0.2, max_s=20.0):
    """bone length に応じた ctl サイズ。leaf/root は fallback。"""
    d = _bone_length(joint)
    if d is None:
        return max(min_s, min(max_s, 1.0 * mult))
    return max(min_s, min(max_s, d * 1.0 * mult))


# =========================================================================
# Update-from-GitHub flow  -- 触らない
# =========================================================================

def _resolve_latest_sha() -> str:
    import json
    import random
    import time
    import urllib.request

    salt = f"{time.time():.6f}_{random.randint(0, 2 ** 32)}"
    req = urllib.request.Request(
        f"{_GITHUB_API}/branches/{_GITHUB_BRANCH}?_={salt}",
        headers={
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache",
            "User-Agent": f"{_PACKAGE}-updater/{salt}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))["commit"]["sha"]
    except Exception as exc:
        print(f"[{_PACKAGE}] SHA lookup failed ({exc}); falling back to "
              f"{_GITHUB_BRANCH}")
        return _GITHUB_BRANCH


def update_from_github(*_args) -> None:
    cmds.evalDeferred(_run_update, lowestPriority=True)


def _run_update() -> None:
    import sys
    import traceback
    import urllib.request

    sha = _resolve_latest_sha()
    url = f"{_GITHUB_RAW_BASE}/{sha}/install.py"
    print(f"[{_PACKAGE}] update: fetching {url}")
    try:
        req = urllib.request.Request(url, headers={
            "Cache-Control": "no-cache",
            "User-Agent": f"{_PACKAGE}-updater/{sha[:10]}",
        })
        source = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(title="Update failed",
                           message=f"install.py fetch failed:\n{exc}",
                           button=["OK"])
        return

    if cmds.window(WINDOW, exists=True):
        try:
            cmds.deleteUI(WINDOW)
        except Exception:
            pass

    ns = {"__name__": "install", "__file__": "<github>"}
    try:
        exec(compile(source, "install.py (from GitHub)", "exec"), ns)
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="Update failed",
            message=(f"install.py raised:\n{type(exc).__name__}: {exc}\n\n"
                     "See Script Editor for full traceback."),
            button=["OK"])
        return

    for m in [k for k in list(sys.modules) if k == _PACKAGE]:
        sys.modules.pop(m, None)

    cmds.evalDeferred(_reopen_after_update, lowestPriority=True)


def _reopen_after_update() -> None:
    import importlib
    import sys
    import traceback
    try:
        if _PACKAGE in sys.modules:
            importlib.reload(sys.modules[_PACKAGE])
        mod = importlib.import_module(_PACKAGE)
        mod.show()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="Reopen failed",
            message=(f"Update finished but reopening the tool window "
                     f"failed:\n{type(exc).__name__}: {exc}\n\n"
                     "Click the shelf button to reopen manually."),
            button=["OK"])


# =========================================================================
# Core logic
# =========================================================================

def _detect_side(joint_name):
    """Return 'L' / 'R' / 'C'. MMD Japanese + Latin variants を吸収。"""
    n = joint_name.split(":")[-1]
    if n.startswith("左"):
        return "L"
    if n.startswith("右"):
        return "R"
    if "_左" in n:
        return "L"
    if "_右" in n:
        return "R"

    nl = n.lower()
    parts = nl.replace("|", "_").split("_")

    # first/last/second-last トークン単位で "l" "r" "left" "right" を厳密判定
    if not parts:
        return "C"
    candidates = [parts[0], parts[-1]]
    if len(parts) >= 2:
        candidates.append(parts[-2])
    for tok in candidates:
        if tok in ("l", "left", "lft"):
            return "L"
        if tok in ("r", "right", "rgt"):
            return "R"
    return "C"


def _base_name(joint_name):
    """joint short name から namespace を除去。_jnt 等の suffix は残す
    (骨名を保持するのが MMD 由来データではむしろ望ましい)。"""
    return joint_name.split("|")[-1].split(":")[-1]


def _make_cube_curve(name, scale=1.0):
    """16 CV の単一 NURBS カーブでキューブワイヤフレームを描く。"""
    s = scale * 0.5
    pts = [
        (-s, -s, -s), ( s, -s, -s), ( s,  s, -s), (-s,  s, -s), (-s, -s, -s),
        (-s, -s,  s), ( s, -s,  s), ( s,  s,  s), (-s,  s,  s), (-s, -s,  s),
        ( s, -s,  s), ( s, -s, -s), ( s,  s, -s), ( s,  s,  s), (-s,  s,  s), (-s,  s, -s),
    ]
    ctl = cmds.curve(d=1, p=pts, n=name)
    return ctl


def _set_ctl_color(ctl, color_idx):
    shape = cmds.listRelatives(ctl, s=True, f=False)
    if not shape:
        return
    for s in shape:
        cmds.setAttr(s + ".overrideEnabled", 1)
        cmds.setAttr(s + ".overrideColor", color_idx)


def _lock_hide_attrs(ctl, attrs):
    for a in attrs:
        try:
            cmds.setAttr(f"{ctl}.{a}", lock=True, keyable=False, channelBox=False)
        except Exception:
            pass


def attach_controllers(joints=None, scale=1.0, do_constrain=True,
                        auto_scale=True, skip_decoration=False):
    """選択された joint に mGear 風のコントローラを一括セットアップ。

    Args:
        joints:          処理対象 joint リスト。None なら現在の selection を使う。
        scale:           auto_scale=True の時は multiplier、False の時は絶対サイズ。
        do_constrain:    True なら joint を ctl に parentConstraint する。
        auto_scale:      True なら bone 長さから ctl サイズを自動計算。
        skip_decoration: True なら hair/ribbon/skirt/etc の装飾骨は ctl 付けない。

    Returns:
        dict {joint: (npo, ctl)}
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")

    if joints is None:
        joints = cmds.ls(sl=True, type="joint")
    if not joints:
        cmds.warning("[attach_ctrls] No joints selected.")
        return {}

    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)

    jnt_to_ctl = {}
    created_ctls = []

    # Pass 1: create ctl + npo per joint (all under ROOT_GROUP temporarily)
    for jnt in joints:
        if not cmds.objExists(jnt):
            continue
        base = _base_name(jnt)
        npo_name = base + "_npo"
        ctl_name = base + "_ctl"

        if cmds.objExists(ctl_name):
            cmds.warning(f"[attach_ctrls] {ctl_name} already exists; skipping {jnt}")
            continue

        # 装飾骨スキップ
        is_decor = _is_decoration(jnt)
        if skip_decoration and is_decor:
            continue

        side = _detect_side(base)
        if is_decor:
            color = COLOR_DECOR
        else:
            color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]

        # 自動サイズ
        if auto_scale:
            ctl_size = _auto_ctl_scale(jnt, mult=scale)
        else:
            ctl_size = scale

        ctl = _make_cube_curve(ctl_name, scale=ctl_size)
        _set_ctl_color(ctl, color)

        npo = cmds.group(em=True, name=npo_name)
        cmds.parent(ctl, npo)

        cmds.matchTransform(npo, jnt, pos=True, rot=True)
        cmds.parent(npo, ROOT_GROUP)

        _lock_hide_attrs(ctl, ["sx", "sy", "sz", "v"])

        jnt_to_ctl[jnt] = (npo, ctl)
        created_ctls.append(ctl_name)

    # Pass 2: 骨階層に合わせて npo を親子付け直し
    # 親 joint が jnt_to_ctl にあれば normal (親 ctl の下)
    # 無ければ (IK chain 除外 or 削除された等)、親 joint を上に辿り
    #   - 途中で ctl 持ち joint に当たればその ctl の下
    #   - 当たらず joint 存在すればその joint の下 (wrist_L 等、IK/FK で
    #     rotate される joint に直接 parent すれば ctl が joint に追従)
    for jnt, (npo, ctl) in jnt_to_ctl.items():
        parents = cmds.listRelatives(jnt, p=True, type="joint") or []
        if not parents:
            continue
        immediate_parent = parents[0]
        target = None
        if immediate_parent in jnt_to_ctl:
            target = jnt_to_ctl[immediate_parent][1]  # 親 ctl
        else:
            # 除外骨 (IK chain の hero joint 等) が親 → その joint 直下に parent
            # これで指 ctl が wrist_L に追従、toe ctl が ankle_L に追従、等
            target = immediate_parent
        if target:
            try:
                cmds.parent(npo, target)
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] parent {npo} -> {target} failed: {exc}")

    # Pass 3: constraint
    if do_constrain:
        for jnt, (npo, ctl) in jnt_to_ctl.items():
            try:
                cmds.parentConstraint(ctl, jnt, mo=False, n=jnt + "_parentConstraint")
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] constraint {ctl} -> {jnt} failed: {exc}")

    print(f"[{_PACKAGE}] Attached controllers: {len(jnt_to_ctl)} joint(s), "
          f"constrain={do_constrain}, scale={scale}")
    return jnt_to_ctl


def delete_generated():
    """attach_ctrls_grp と付随する constraint / IK ノードをまとめて削除。"""
    if cmds is None:
        return
    n = 0
    if cmds.objExists(ROOT_GROUP):
        cmds.delete(ROOT_GROUP)
        n += 1
    for con in cmds.ls("*_parentConstraint*", type="parentConstraint") or []:
        try:
            cmds.delete(con); n += 1
        except Exception:
            pass
    for pattern in ("*_ikfk_oc*", "*_fk_oc*"):
        for con in cmds.ls(pattern, type="orientConstraint") or []:
            try:
                cmds.delete(con); n += 1
            except Exception:
                pass
    for ikh in cmds.ls(type="ikHandle") or []:
        try:
            cmds.delete(ikh); n += 1
        except Exception:
            pass
    print(f"[{_PACKAGE}] Deleted generated nodes: {n}")


# =========================================================================
# IK/FK setup (dual-chain blend)
# ------------------------------------------------------------------------
# 元 chain (bind joint) は保持。IK 用/FK 用の 3-joint clean chain を
# duplicate で作成し、元 joint は orientConstraint で FK/IK rotation を
# weight ブレンド。switch = 0..1 で完全 FK ↔ 完全 IK 切替可能。
# =========================================================================

def _find_chain(end_name, length=3):
    """end_name から joint parent を length-1 回さかのぼって chain を返す。
    途中に twist bones 等が挟まっていても、直接の joint parent を辿るのみ。
    MMD の twist 直系対策として、bone 名を明示する _find_chain_by_names を推奨。"""
    if not cmds.objExists(end_name):
        return None
    chain = [end_name]
    cur = end_name
    for _ in range(length - 1):
        p = cmds.listRelatives(cur, p=True, type="joint") or []
        if not p:
            return None
        chain.insert(0, p[0])
        cur = p[0]
    return chain


def _find_chain_by_names(names):
    """names 全て存在すれば list で返す (twist bones はスキップされる)。"""
    for n in names:
        if not cmds.objExists(n):
            return None
    return list(names)


def _dup_clean_chain(joints, suffix):
    """joints (親→子順) を worldspace 位置を保ちつつ 3-joint clean chain として
    duplicate する。twist bones 等の中間 joint は含めない。"""
    new_joints = []
    for j in joints:
        n = cmds.duplicate(j, po=True, n=j + suffix)[0]
        # duplicate 直後は同じ world 位置。ただし親は元 joint の親のままなので
        # 一旦 world に取り出す。
        cmds.parent(n, world=True)
        new_joints.append(n)
    # 親子付け (world 位置は preserve される)
    for i in range(1, len(new_joints)):
        cmds.parent(new_joints[i], new_joints[i - 1])
    return new_joints


def setup_ik_fk(start, mid, end, side="C", pv_z_offset=None):
    """3-joint chain (start, mid, end) に FK/IK blend rig を構築。

    方針: Maya native の ikHandle.ikBlend を使い、original chain に
    直接 IK handle を付ける。FK ctl は原 joint を orientConstraint。
    switch attr で ikBlend と FK weight を反転制御 (0=FK / 1=IK)。

    利点: chain duplication 不要。twist bones を含む arm chain でも
    local space の不一致問題が発生しない。

    Args:
        start, mid, end: 元 joint 名 (親→子順)
        side: "L"/"R"/"C" (ctl color 用)
        pv_z_offset: pole vector の Z offset (None なら arm/leg 名から推定)
    """
    color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]
    label = start  # side 込みで衝突回避 (arm_L / arm_R 等)

    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)

    # 自動サイズ (chain の平均 bone 長さから算出)
    _chain_lengths = [_bone_length(j) for j in [start, mid, end]]
    _chain_lengths = [d for d in _chain_lengths if d is not None]
    chain_size = sum(_chain_lengths) / len(_chain_lengths) if _chain_lengths else 1.0
    ik_size = chain_size * 1.5   # IK ctl: 大きめ (掴みやすさ優先)
    pv_size = chain_size * 0.5   # PV ctl: 小さめ
    fk_size = chain_size * 0.8   # FK ctl: 中間

    # --- 1. IK handle on ORIGINAL chain (twist bones を含む path をそのまま利用) ---
    ik_handle = cmds.ikHandle(sj=start, ee=end,
                              sol="ikRPsolver", n=label + "_ikh")[0]

    # --- 2. IK ctl (end joint 位置) ---
    ik_ctl = _make_cube_curve(label + "_IK_ctl", scale=ik_size)
    _set_ctl_color(ik_ctl, color)
    ik_npo = cmds.group(em=True, name=label + "_IK_npo")
    cmds.parent(ik_ctl, ik_npo)
    cmds.matchTransform(ik_npo, end, pos=True, rot=True)
    cmds.parent(ik_npo, ROOT_GROUP)
    cmds.pointConstraint(ik_ctl, ik_handle, mo=False)
    _lock_hide_attrs(ik_ctl, ["sx", "sy", "sz"])

    # --- 3. pole vector ctl ---
    pv_ctl = _make_cube_curve(label + "_PV_ctl", scale=pv_size)
    _set_ctl_color(pv_ctl, color)
    pv_npo = cmds.group(em=True, name=label + "_PV_npo")
    cmds.parent(pv_ctl, pv_npo)
    mid_pos = cmds.xform(mid, q=True, ws=True, t=True)
    if pv_z_offset is None:
        lo = label.lower()
        if "leg" in lo or "knee" in lo or "ankle" in lo:
            pv_z_offset = 3.0
        else:
            pv_z_offset = -3.0
    cmds.xform(pv_npo, ws=True, t=(mid_pos[0], mid_pos[1], mid_pos[2] + pv_z_offset))
    cmds.parent(pv_npo, ROOT_GROUP)
    cmds.poleVectorConstraint(pv_ctl, ik_handle)
    _lock_hide_attrs(pv_ctl, ["sx", "sy", "sz", "rx", "ry", "rz"])

    # --- 4. FK ctls (original 3 joints それぞれに) ---
    fk_ctls = []
    for j in [start, mid, end]:
        fk_ctl = _make_cube_curve(j + "_FK_ctl", scale=fk_size)
        _set_ctl_color(fk_ctl, color)
        fk_npo = cmds.group(em=True, name=j + "_FK_npo")
        cmds.parent(fk_ctl, fk_npo)
        cmds.matchTransform(fk_npo, j, pos=True, rot=True)
        _lock_hide_attrs(fk_ctl, ["sx", "sy", "sz", "tx", "ty", "tz"])
        fk_ctls.append((fk_npo, fk_ctl))
    # 階層: mid_npo -> start_ctl, end_npo -> mid_ctl
    cmds.parent(fk_ctls[1][0], fk_ctls[0][1])
    cmds.parent(fk_ctls[2][0], fk_ctls[1][1])
    cmds.parent(fk_ctls[0][0], ROOT_GROUP)

    # --- 5. Switch attribute (IK ctl に付与、 0=FK 1=IK) ---
    if not cmds.attributeQuery("IK_FK", node=ik_ctl, exists=True):
        cmds.addAttr(ik_ctl, ln="IK_FK", at="float", min=0.0, max=1.0, dv=1.0, k=True)

    rev = cmds.createNode("reverse", n=label + "_ikfk_rev")
    cmds.connectAttr(ik_ctl + ".IK_FK", rev + ".inputX")

    # --- 6. ikHandle.ikBlend = switch (native FK/IK blend) ---
    cmds.connectAttr(ik_ctl + ".IK_FK", ik_handle + ".ikBlend")

    # --- 7. Constraints on hero joints ---
    # start / mid: FK ctl orient constraint, weight = 1 - switch
    #   (IK 側は ikHandle が ikBlend で solver 出力を直接 joint に流す)
    for i, orig_j in enumerate([start, mid]):
        fk_ctl_j = fk_ctls[i][1]
        cons = cmds.orientConstraint(fk_ctl_j, orig_j, mo=False,
                                     n=orig_j + "_fk_oc")[0]
        wal = cmds.orientConstraint(cons, q=True, wal=True)
        cmds.connectAttr(rev + ".outputX", cons + "." + wal[0])

    # end: dual orient constraint (FK ctl + IK ctl), weights blended by switch
    #   IK solver は end 位置のみ制御 (rotation は残る) ので orient constraint 併用。
    fk_end_ctl = fk_ctls[2][1]
    end_cons = cmds.orientConstraint(fk_end_ctl, ik_ctl, end, mo=False,
                                     n=end + "_ikfk_oc")[0]
    wal = cmds.orientConstraint(end_cons, q=True, wal=True)  # [fkW, ikW]
    cmds.connectAttr(rev + ".outputX", end_cons + "." + wal[0])
    cmds.connectAttr(ik_ctl + ".IK_FK", end_cons + "." + wal[1])

    # --- 8. Visibility toggle: IK ctls when switch>0, FK ctls when switch<1 ---
    try:
        cmds.setAttr(ik_ctl + ".v", lock=False)
    except Exception:
        pass
    try:
        cmds.connectAttr(ik_ctl + ".IK_FK", ik_npo + ".v")
        cmds.connectAttr(ik_ctl + ".IK_FK", pv_npo + ".v")
        cmds.connectAttr(rev + ".outputX",  fk_ctls[0][0] + ".v")
    except Exception as exc:
        cmds.warning(f"[attach_ctrls] visibility connect failed: {exc}")

    print(f"[{_PACKAGE}] IK/FK rig: {label}  IK={ik_ctl}  PV={pv_ctl}  "
          f"FK={[c for _, c in fk_ctls]}  switch={ik_ctl}.IK_FK")

    return {
        "label":     label,
        "ik_handle": ik_handle,
        "ik_ctl":    ik_ctl,
        "pv_ctl":    pv_ctl,
        "fk_ctls":   [c for _, c in fk_ctls],
        "switch":    ik_ctl + ".IK_FK",
    }


# --- チェーン自動検出 & 一括セットアップ ------------------------------

_ARM_CANDIDATES = [
    ("arm",       "elbow",     "wrist"),
    ("shoulder",  "elbow",     "wrist"),
    ("upperarm",  "forearm",   "hand"),
]
_LEG_CANDIDATES = [
    ("leg",       "knee",      "ankle"),
    ("upleg",     "knee",      "foot"),
    ("thigh",     "shin",      "foot"),
]


def _try_find(triples, side):
    for start, mid, end in triples:
        names = [f"{start}_{side}", f"{mid}_{side}", f"{end}_{side}"]
        chain = _find_chain_by_names(names)
        if chain:
            return chain
    return None


def find_ik_chains():
    """L/R arm と L/R leg を自動検出して { "L_arm": [j,j,j], ... } を返す。"""
    out = {}
    for side in ("L", "R"):
        a = _try_find(_ARM_CANDIDATES, side)
        if a:
            out[f"{side}_arm"] = a
        lg = _try_find(_LEG_CANDIDATES, side)
        if lg:
            out[f"{side}_leg"] = lg
    return out


def setup_all_ik_fk():
    """検出できた L/R arm/leg 全てに IK/FK rig を構築。"""
    chains = find_ik_chains()
    results = []
    for label, chain in chains.items():
        side = "L" if label.startswith("L") else "R"
        try:
            r = setup_ik_fk(chain[0], chain[1], chain[2], side=side)
            results.append(r)
        except Exception as exc:
            cmds.warning(f"[attach_ctrls] IK setup for {label} failed: {exc}")
    print(f"[{_PACKAGE}] setup_all_ik_fk: {len(results)}/{len(chains)} chains")
    return results


# --- 不要ノード削除 -----------------------------------------------------

def _is_skinned(joint):
    """joint が skinCluster に接続されているか。"""
    try:
        conns = cmds.listConnections(joint + ".worldMatrix",
                                     type="skinCluster", d=True, s=False) or []
        return len(conns) > 0
    except Exception:
        return False


def delete_unnecessary(dry_run=False):
    """FBX import 由来の不要ノードを掃除。
    - locator ノード (rig で使わない)
    - 未 skinning の _end / shadow_ / _end 系 leaf joint
    削除順は子から親 (依存壊さないため)。
    """
    if cmds is None:
        return []

    deleted = []
    skipped_safety = []

    # 1. locator transforms (typically MMD の空 helper)
    # ただし joint 子孫を持つ locator (character root として使われている等) は
    # 削除すると骨全体を殺してしまうので skip。
    loc_shapes = cmds.ls(type="locator", long=True) or []
    loc_transforms = set()
    for ls in loc_shapes:
        parents = cmds.listRelatives(ls, p=True, f=True) or []
        for p in parents:
            loc_transforms.add(p)
    for lt in loc_transforms:
        if not cmds.objExists(lt):
            continue
        # 安全: joint descendant がある transform は skip
        joint_desc = cmds.listRelatives(lt, ad=True, type="joint") or []
        if joint_desc:
            skipped_safety.append(lt.split("|")[-1])
            continue
        # 安全: skinCluster に接続されている shape descendant があれば skip
        try:
            all_desc = cmds.listRelatives(lt, ad=True) or []
            has_skin = any(
                cmds.listConnections(d, type="skinCluster") for d in all_desc
            )
        except Exception:
            has_skin = False
        if has_skin:
            skipped_safety.append(lt.split("|")[-1])
            continue
        try:
            deleted.append(lt.split("|")[-1])
            if not dry_run:
                cmds.delete(lt)
        except Exception:
            pass

    # 2. unskinned helper joints (shadow_ / _end)
    #    深い joint から先に削除 (子から親)
    all_joints = cmds.ls(type="joint", long=True) or []
    scored = [(j.count("|"), j) for j in all_joints]
    scored.sort(reverse=True)

    for _, j in scored:
        if not cmds.objExists(j):
            continue
        short = j.split("|")[-1]
        candidate = (short.startswith("shadow_")
                     or short.endswith("_end")
                     or short.startswith("dummy_"))
        if not candidate:
            continue
        if _is_skinned(j):
            continue
        # 子が skinning されていたら残す
        kids = cmds.listRelatives(j, ad=True, type="joint") or []
        if any(_is_skinned(k) for k in kids):
            continue
        deleted.append(j)
        if not dry_run:
            try: cmds.delete(j)
            except Exception: pass

    tag = "DRY-RUN" if dry_run else "DELETED"
    print(f"[{_PACKAGE}] {tag} unnecessary nodes: {len(deleted)}  "
          f"(safety-skipped: {len(skipped_safety)})")
    return deleted


# --- Full auto-setup: rename -> delete unnecessary -> attach FK -> IK/FK ---

def full_auto_setup(scale=1.0, skip_decoration=False, delete_junk=True):
    """FBX 直後の状態から完全 rig setup を 1 コマンドで実行。
    1. namespace 除去
    2. joint 名 rename (fbx_renamer 経由)
    3. 不要ノード削除 (locator + 未skin _end/shadow/dummy)
    4. IK/FK chain を除いた全 joint に FK cube ctl 付与 (auto-scale)
    5. L/R arm/leg で IK/FK blend rig 構築

    Args:
        scale:           auto_scale multiplier (1.0 = bone 長さ準拠)
        skip_decoration: hair/ribbon/skirt/coat/ear/tail 系を除外
        delete_junk:     不要ノード削除を実行するか
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")

    # Step 1+2: rename
    if fbx_renamer is None:
        cmds.warning("[attach_ctrls] fbx_renamer not available -- skipping rename")
    else:
        fbx_renamer.remove_all_namespaces()
        fbx_renamer.rename_all_joints()

    # Step 3: cleanup
    if delete_junk:
        delete_unnecessary()

    # Step 4: attach FK ctls, exclude IK/FK chain joints
    chains = find_ik_chains()
    exclude = set()
    for chain in chains.values():
        exclude.update(chain)

    all_joints = cmds.ls(type="joint") or []
    other = [j for j in all_joints
             if j not in exclude
             and not j.endswith("_end")]
    attach_result = attach_controllers(joints=other, scale=scale,
                                        do_constrain=True,
                                        auto_scale=True,
                                        skip_decoration=skip_decoration)

    # Step 5: IK/FK setup
    ik_results = setup_all_ik_fk()

    print(f"[{_PACKAGE}] === full_auto_setup complete ===")
    print(f"  FK ctls attached: {len(attach_result)}")
    print(f"  IK/FK chains    : {len(ik_results)}")
    for r in ik_results:
        print(f"    {r['label']:15s} switch: {r['switch']}")

    return {"fk_attach": attach_result, "ik_fk": ik_results}


# =========================================================================
# UI
# =========================================================================

_UI_SCALE = "attach_ctrls_ui_scale"
_UI_CONSTRAIN = "attach_ctrls_ui_constrain"
_UI_SKIP_DECOR = "attach_ctrls_ui_skip_decor"
_UI_DELETE_JUNK = "attach_ctrls_ui_delete_junk"


def _build_body() -> None:
    # ============ Section 0: FULL AUTO (推奨) ============
    cmds.text(l="=== Full Auto Setup (FBX 直後推奨) ===",
              al="left", fn="boldLabelFont")
    cmds.text(l="rename → 不要削除 → 全 joint に auto-scale FK ctl → L/R arm+leg IK/FK",
              al="left", fn="smallObliqueLabelFont")

    cmds.floatSliderGrp(
        _UI_SCALE,
        label="Size ×", field=True,
        min=0.1, max=5.0, fieldMinValue=0.01, fieldMaxValue=100.0,
        value=1.0, cw3=(80, 60, 120),
        ann="ctl サイズ倍率 (1.0 = bone 長さ準拠、大きくすると全 ctl が拡大)",
    )
    cmds.checkBoxGrp(
        _UI_SKIP_DECOR,
        label="Skip:", label1="装飾骨 (hair/ribbon/skirt/coat/ear/tail 等)",
        value1=False, cw2=(60, 280),
        ann="装飾系の骨は ctl を付けない (視覚的にすっきり)",
    )
    cmds.checkBoxGrp(
        _UI_DELETE_JUNK,
        label="Del:", label1="locator + 未skin _end/shadow/dummy 自動削除",
        value1=True, cw2=(60, 280),
    )

    cmds.rowLayout(nc=2, adj=1, cw2=(280, 100),
                   ct2=("both", "both"), co2=(4, 4))
    cmds.button(l="⚡ FULL AUTO SETUP", h=40, c=_ui_full_auto,
                bgc=(0.90, 0.55, 0.10))
    cmds.button(l="Delete ALL", h=40, c=_ui_delete,
                bgc=(0.55, 0.20, 0.20))
    cmds.setParent("..")

    cmds.separator(h=10, style="in")

    # ============ Section 1: 個別ステップ (advanced) ============
    cmds.text(l="=== 個別ステップ (advanced) ===",
              al="left", fn="boldLabelFont")

    cmds.rowLayout(nc=3, adj=1, cw3=(130, 130, 130),
                   ct3=("both", "both", "both"), co3=(2, 2, 2))
    cmds.button(l="① Rename (dry)", h=26, c=_ui_rename_dry)
    cmds.button(l="① Rename", h=26, c=_ui_rename,
                bgc=(0.20, 0.55, 0.85))
    cmds.button(l="② Del junk", h=26, c=_ui_del_junk,
                bgc=(0.55, 0.35, 0.20))
    cmds.setParent("..")

    cmds.checkBoxGrp(
        _UI_CONSTRAIN,
        label="Attach:", label1="parentConstraint (joint follows ctl)",
        value1=True, cw2=(60, 280),
    )
    cmds.rowLayout(nc=2, adj=1, cw2=(190, 190),
                   ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="③ Attach FK ctls to selected", h=28, c=_ui_attach,
                bgc=(0.20, 0.55, 0.85))
    cmds.button(l="④ Setup IK/FK (L/R arm+leg)", h=28, c=_ui_ikfk,
                bgc=(0.20, 0.85, 0.55))
    cmds.setParent("..")

    cmds.separator(h=6, style="none")
    cmds.text(l="IK/FK 切替: IK ctl の 'IK_FK' attr (0=FK, 1=IK)",
              al="left", fn="smallObliqueLabelFont")
    cmds.text(l="装飾骨は暗灰色。IK モードで waist を回すと足が接地したまま追従。",
              al="left", fn="smallObliqueLabelFont")


def _ui_full_auto(*_):
    scale = cmds.floatSliderGrp(_UI_SCALE, q=True, value=True)
    skip_dec = cmds.checkBoxGrp(_UI_SKIP_DECOR, q=True, value1=True)
    del_junk = cmds.checkBoxGrp(_UI_DELETE_JUNK, q=True, value1=True)
    full_auto_setup(scale=scale, skip_decoration=skip_dec, delete_junk=del_junk)


def _ui_rename(*_):
    if fbx_renamer is None:
        cmds.warning("fbx_renamer not available")
        return
    fbx_renamer.remove_all_namespaces()
    fbx_renamer.rename_all_joints()


def _ui_rename_dry(*_):
    if fbx_renamer is None:
        cmds.warning("fbx_renamer not available")
        return
    fbx_renamer.rename_all_joints(dry_run=True)


def _ui_del_junk(*_):
    delete_unnecessary()


def _ui_ikfk(*_):
    setup_all_ik_fk()


def _ui_attach(*_):
    scale = cmds.floatSliderGrp(_UI_SCALE, q=True, value=True)
    do_constrain = cmds.checkBoxGrp(_UI_CONSTRAIN, q=True, value1=True)
    skip_dec = cmds.checkBoxGrp(_UI_SKIP_DECOR, q=True, value1=True)
    attach_controllers(scale=scale, do_constrain=do_constrain,
                        auto_scale=True, skip_decoration=skip_dec)


def _ui_delete(*_):
    delete_generated()


def show() -> str:
    if cmds is None:
        raise RuntimeError("show() must be called inside Maya.")

    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)

    win = cmds.window(WINDOW,
                      t=f"AttachCtrl  --  v{__version__}",
                      w=440, h=280, mnb=True, mxb=False, s=True)
    cmds.columnLayout(adj=True, rs=8, cat=("both", 10))

    _build_body()

    cmds.separator(h=10, style="in")
    cmds.rowLayout(nc=2, adj=1, cw2=(240, 150))
    cmds.text(l=f"{_PACKAGE}  v{__version__}",
              al="left", fn="smallObliqueLabelFont")
    cmds.button(l="GitHub から更新", h=24, c=update_from_github)
    cmds.setParent("..")

    cmds.showWindow(win)
    return win
