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


__version__ = "0.4.3"


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


_CACHED_MESH_DIAG = None


def _scene_mesh_bbox_diag():
    """シーン内 mesh の world bbox 対角線長さ。cache する。"""
    global _CACHED_MESH_DIAG
    if _CACHED_MESH_DIAG is not None:
        return _CACHED_MESH_DIAG
    meshes = cmds.ls(type="mesh") or []
    if not meshes:
        _CACHED_MESH_DIAG = 100.0
        return _CACHED_MESH_DIAG
    tforms = set()
    for m in meshes:
        p = cmds.listRelatives(m, p=True, f=True) or []
        if p:
            tforms.add(p[0])
    if not tforms:
        _CACHED_MESH_DIAG = 100.0
        return _CACHED_MESH_DIAG
    try:
        bb = cmds.exactWorldBoundingBox(list(tforms))
        d = ((bb[3]-bb[0])**2 + (bb[4]-bb[1])**2 + (bb[5]-bb[2])**2)**0.5
        _CACHED_MESH_DIAG = d
    except Exception:
        _CACHED_MESH_DIAG = 100.0
    return _CACHED_MESH_DIAG


def _reset_scale_cache():
    global _CACHED_MESH_DIAG
    _CACHED_MESH_DIAG = None


def _auto_ctl_scale(joint, mult=1.0):
    """bone length * 0.35 を base に、mesh bbox 対角の [1/400, 1/50] で clamp。
    小ぶりだが sizes は明確に differentiate される。"""
    diag = _scene_mesh_bbox_diag()
    min_s = diag / 400.0
    max_s = diag / 50.0
    d = _bone_length(joint)
    base = (d * 0.35) if d else min_s * 3
    return max(min_s, min(max_s, base * mult))


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


def _make_diamond_curve(name, scale=1.0):
    """ダイヤモンド (八面体) 形の NURBS カーブ。pole vector 等の視認性重視用。"""
    s = scale
    pts = [
        ( 0,  s,  0), ( s,  0,  0), ( 0, -s,  0), (-s,  0,  0), ( 0,  s,  0),
        ( 0,  0,  s), ( s,  0,  0), ( 0,  0, -s), (-s,  0,  0), ( 0,  0,  s),
        ( 0, -s,  0), ( 0,  0, -s), ( 0,  s,  0),
    ]
    ctl = cmds.curve(d=1, p=pts, n=name)
    return ctl


def _make_ring_curve(name, scale=1.0, sides=16):
    """水平 (XZ plane) の円リング。背骨/首用。"""
    import math
    pts = []
    for i in range(sides + 1):
        a = 2 * math.pi * i / sides
        pts.append((math.cos(a) * scale, 0, math.sin(a) * scale))
    ctl = cmds.curve(d=1, p=pts, n=name)
    return ctl


def _make_octagon_curve(name, scale=1.0):
    """水平 8 角形。root ctl 用 (地面に置く)。"""
    return _make_ring_curve(name, scale=scale, sides=8)


def _make_flat_box_curve(name, scale=1.0, x_ratio=1.4, z_ratio=1.0):
    """水平面 (XZ plane) の長方形 (box を平らに)。手/足 IK ctl 用。"""
    sx = scale * x_ratio * 0.5
    sz = scale * z_ratio * 0.5
    pts = [
        (-sx, 0, -sz), ( sx, 0, -sz), ( sx, 0,  sz), (-sx, 0,  sz), (-sx, 0, -sz),
    ]
    ctl = cmds.curve(d=1, p=pts, n=name)
    return ctl


def _make_square_curve(name, scale=1.0):
    """垂直 (XY plane) の正方形。FK 指用 (bone に沿って表示)。"""
    s = scale * 0.5
    pts = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0), (-s, -s, 0)]
    ctl = cmds.curve(d=1, p=pts, n=name)
    return ctl


def _make_wide_flat_box_curve(name, scale=1.0):
    """腰 (waist) 用: 横長 flat box (床置きイメージ)。"""
    return _make_flat_box_curve(name, scale, x_ratio=2.4, z_ratio=1.6)


# --- 骨名パターン -> ctl shape maker マッピング ---
def _pick_ctl_maker(joint_name):
    """joint 名から (maker function, absolute_size or None, flat_horizontal_bool) を返す。
    - absolute_size None なら auto_ctl_scale を使う
    - flat_horizontal True なら matchTransform 後に CV を world 水平平面に再配置
      (MMD の joint local Y が world Y に一致しないケース対策)"""
    n = joint_name.split(":")[-1].lower()
    diag = _scene_mesh_bbox_diag()

    if n == "waist":
        return _make_wide_flat_box_curve, diag * 0.19, True
    if "upper_body" in n or "chest" in n:
        base = 0.16 if n == "upper_body_2" else 0.18
        return _make_ring_curve, diag * base, True
    if "lower_body" in n:
        return _make_ring_curve, diag * 0.20, True
    if "neck" in n:
        return _make_ring_curve, diag * 0.05, True
    if n == "head" or n.endswith("_head"):
        return _make_cube_curve, diag * 0.09, False
    if any(n.startswith(k) or ("_" + k) in n for k in
           ("thumb", "index", "middle", "ring", "pinky", "finger")):
        return _make_square_curve, None, False
    return _make_cube_curve, None, False


def _rewrite_flat_horizontal(ctl_name, world_pos, scale, maker):
    """matchTransform 後 (ctl が joint 回転を継承した状態) の shape の CV を
    world XZ 平面に強制配置する。transform 階層 (npo の rotate) は保持するので
    parentConstraint 追従は継続、見た目のみ水平化。"""
    import math
    p = world_pos
    if maker is _make_ring_curve or maker is _make_octagon_curve:
        sides = 16 if maker is _make_ring_curve else 8
        pts = [(p[0] + math.cos(2*math.pi*i/sides) * scale,
                p[1],
                p[2] + math.sin(2*math.pi*i/sides) * scale)
               for i in range(sides + 1)]
    elif maker is _make_wide_flat_box_curve:
        sx = scale * 2.4 * 0.5
        sz = scale * 1.6 * 0.5
        pts = [(p[0]-sx, p[1], p[2]-sz), (p[0]+sx, p[1], p[2]-sz),
               (p[0]+sx, p[1], p[2]+sz), (p[0]-sx, p[1], p[2]+sz),
               (p[0]-sx, p[1], p[2]-sz)]
    elif maker is _make_flat_box_curve:
        sx = scale * 1.4 * 0.5
        sz = scale * 1.0 * 0.5
        pts = [(p[0]-sx, p[1], p[2]-sz), (p[0]+sx, p[1], p[2]-sz),
               (p[0]+sx, p[1], p[2]+sz), (p[0]-sx, p[1], p[2]+sz),
               (p[0]-sx, p[1], p[2]-sz)]
    else:
        return
    for i, pt in enumerate(pts):
        try:
            cmds.xform(f"{ctl_name}.cv[{i}]", ws=True, t=pt)
        except Exception:
            pass


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

        # 骨タイプに応じた shape 選定
        maker, absolute_size, flat_horizontal = _pick_ctl_maker(jnt)
        if absolute_size is not None:
            ctl_size = absolute_size * scale
        elif auto_scale:
            ctl_size = _auto_ctl_scale(jnt, mult=scale)
        else:
            ctl_size = scale

        ctl = maker(ctl_name, scale=ctl_size)
        _set_ctl_color(ctl, color)

        npo = cmds.group(em=True, name=npo_name)
        cmds.parent(ctl, npo)

        cmds.matchTransform(npo, jnt, pos=True, rot=True)
        cmds.parent(npo, ROOT_GROUP)

        # flat 系 ctl は matchTransform で joint 回転を継承してしまい
        # 傾いて見える (MMD joint rotate.x が焼き込まれている問題) ので
        # shape CV を world 水平平面に再配置する。
        if flat_horizontal:
            joint_ws = cmds.xform(jnt, q=True, ws=True, t=True)
            _rewrite_flat_horizontal(ctl, joint_ws, ctl_size, maker)

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
    for pattern in ("*_ikfk_oc*", "*_fk_oc*", "*_ik_orient_oc*"):
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
    # dual chain 副産物: <joint>_ik / <joint>_fk joints
    for suffix in ("_ik", "_fk"):
        for j in cmds.ls("*" + suffix, type="joint") or []:
            if cmds.objExists(j):
                try:
                    cmds.delete(j); n += 1
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


def _dup_hero_joint(orig, suffix, new_parent=None):
    """joint を子なしで duplicate、任意の parent に付け直す。world transform は preserve。"""
    n = cmds.duplicate(orig, po=True, n=orig + suffix)[0]
    # 子が付いてきたら削除
    kids = cmds.listRelatives(n, c=True) or []
    for k in kids:
        try: cmds.delete(k)
        except Exception: pass
    if new_parent is None:
        try: cmds.parent(n, world=True)
        except Exception: pass
    else:
        try: cmds.parent(n, new_parent)
        except Exception: pass
    return n


def setup_ik_fk(start, mid, end, side="C", pv_offset=None):
    """3-joint chain (start, mid, end) に FK/IK blend rig を構築。

    方針: original chain の 3 hero joint を CLEAN な dual chain (IK 用/FK 用)
    に duplicate し、それぞれ元の hero joint の親の下に parent。IK solver は
    clean chain だけを解くので twist bones (arm_twist_L 等) には触れず、
    メッシュが捻じれない。元 hero joint は orient constraint mo=True で
    IK/FK chain の rotation を blend 受信する (twist bones は元 joint の
    子として通常通り追従)。

    Args:
        start, mid, end: 元 joint 名 (親→子順)
        side: "L"/"R"/"C" (ctl color 用)
        pv_offset: pole vector のワールド Z offset (None なら mesh diag / 8)
    """
    color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]
    label = start

    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)

    # 元 start joint の親 (arm_L なら shoulder_C_L 等)
    orig_parent_list = cmds.listRelatives(start, p=True, type="joint") or []
    orig_parent = orig_parent_list[0] if orig_parent_list else None

    # サイズ (mesh bbox 準拠の auto scale)
    diag = _scene_mesh_bbox_diag()
    ik_size = diag / 18.0    # IK ctl 大きめ (掴みやすさ)
    pv_size = diag / 40.0    # PV ctl やや大きめ (見つけやすさ)
    fk_size = diag / 45.0    # FK ctl 中間

    # --- 1. Clean dual chain (IK 用, FK 用) ---
    # 全 hero joint を duplicate → 元 start の親の下に置く → chain 化。
    # twist bones を含まない clean な 3-joint chain。
    arm_ik = _dup_hero_joint(start, "_ik", new_parent=orig_parent)
    elbow_ik = _dup_hero_joint(mid,  "_ik", new_parent=arm_ik)
    wrist_ik = _dup_hero_joint(end,  "_ik", new_parent=elbow_ik)
    ik_chain = [arm_ik, elbow_ik, wrist_ik]

    arm_fk = _dup_hero_joint(start, "_fk", new_parent=orig_parent)
    elbow_fk = _dup_hero_joint(mid,  "_fk", new_parent=arm_fk)
    wrist_fk = _dup_hero_joint(end,  "_fk", new_parent=elbow_fk)
    fk_chain = [arm_fk, elbow_fk, wrist_fk]

    # 描画は None にして viewport ノイズ削減
    for j in ik_chain + fk_chain:
        try: cmds.setAttr(j + ".drawStyle", 2)
        except Exception: pass

    # --- 2. IK handle on CLEAN chain ---
    ik_handle = cmds.ikHandle(sj=arm_ik, ee=wrist_ik,
                              sol="ikRPsolver", n=label + "_ikh")[0]
    # IK handle は attach_ctrls_grp 下に (元 chain 内に置かないほうが管理しやすい)
    try: cmds.parent(ik_handle, ROOT_GROUP)
    except Exception: pass

    # --- 3. IK ctl (end joint 位置) — 手/足 IK は水平 box shape ---
    is_leg = "leg" in label.lower()
    ik_ctl = _make_flat_box_curve(
        label + "_IK_ctl", scale=ik_size,
        x_ratio=1.6 if is_leg else 1.4,
        z_ratio=2.2 if is_leg else 1.6,
    )
    _set_ctl_color(ik_ctl, color)
    ik_npo = cmds.group(em=True, name=label + "_IK_npo")
    cmds.parent(ik_ctl, ik_npo)
    cmds.matchTransform(ik_npo, end, pos=True, rot=True)
    cmds.parent(ik_npo, ROOT_GROUP)
    cmds.pointConstraint(ik_ctl, ik_handle, mo=False)
    _lock_hide_attrs(ik_ctl, ["sx", "sy", "sz"])

    # --- 4. Pole vector ctl (diamond 形状で目立たせる) ---
    pv_ctl = _make_diamond_curve(label + "_PV_ctl", scale=pv_size)
    _set_ctl_color(pv_ctl, 20 if side == "L" else 12 if side == "R" else 17)
    # ↑ PV は色を通常ctl とは変えて識別しやすく (L=lightblue20, R=lightred12, C=yellow17)
    pv_npo = cmds.group(em=True, name=label + "_PV_npo")
    cmds.parent(pv_ctl, pv_npo)
    mid_pos = cmds.xform(mid, q=True, ws=True, t=True)
    if pv_offset is None:
        base_offset = diag / 8.0  # 大きめ offset (mesh 外に出す)
    else:
        base_offset = pv_offset
    lo = label.lower()
    if "leg" in lo or "knee" in lo or "ankle" in lo:
        z_off = +base_offset  # 脚: 前方 (膝側)
    else:
        z_off = -base_offset  # 腕: 後方 (肘側)
    cmds.xform(pv_npo, ws=True,
               t=(mid_pos[0], mid_pos[1], mid_pos[2] + z_off))
    cmds.parent(pv_npo, ROOT_GROUP)
    # NOTE: poleVectorConstraint はここでは張らない。
    #       張ると IK 再ソルブが走り clean chain の rotate が bind から drift、
    #       そのまま mo=True で orient blend を作ると offset が不整合になり、
    #       PV を動かすたびに hero joint (wrist_L 等) が振り回される。
    #       → step 7 の orient blend 構築が済んだ後 (step 8) で PV constraint を張る。
    _lock_hide_attrs(pv_ctl, ["sx", "sy", "sz", "rx", "ry", "rz"])

    # --- 5. FK ctls (clean FK chain の各 joint を drive) ---
    fk_ctls = []
    for j in fk_chain:
        fk_ctl = _make_cube_curve(j + "_ctl", scale=fk_size)
        _set_ctl_color(fk_ctl, color)
        fk_npo = cmds.group(em=True, name=j + "_npo")
        cmds.parent(fk_ctl, fk_npo)
        cmds.matchTransform(fk_npo, j, pos=True, rot=True)
        cmds.orientConstraint(fk_ctl, j, mo=False)
        _lock_hide_attrs(fk_ctl, ["sx", "sy", "sz", "tx", "ty", "tz"])
        fk_ctls.append((fk_npo, fk_ctl))
    # FK ctl chain 階層 (mid_npo -> start_ctl, end_npo -> mid_ctl)
    cmds.parent(fk_ctls[1][0], fk_ctls[0][1])
    cmds.parent(fk_ctls[2][0], fk_ctls[1][1])
    cmds.parent(fk_ctls[0][0], ROOT_GROUP)

    # --- 6. Switch attribute (IK ctl) ---
    if not cmds.attributeQuery("IK_FK", node=ik_ctl, exists=True):
        cmds.addAttr(ik_ctl, ln="IK_FK", at="float", min=0.0, max=1.0, dv=1.0, k=True)

    rev = cmds.createNode("reverse", n=label + "_ikfk_rev")
    cmds.connectAttr(ik_ctl + ".IK_FK", rev + ".inputX")

    # --- 7. Blend original hero joints between IK chain and FK chain ---
    # orientConstraint mo=True で local space 差異を初期化時に吸収。
    # twist bones は元 hero joint の child なので、hero joint 回転すれば自然追従。
    # IK solver は clean chain だけ動かし、twist bones を直接触らない。
    for orig, ikj, fkj in zip([start, mid, end], ik_chain, fk_chain):
        cons = cmds.orientConstraint(fkj, ikj, orig, mo=True,
                                     n=orig + "_ikfk_oc")[0]
        wal = cmds.orientConstraint(cons, q=True, wal=True)  # [fkW, ikW]
        cmds.connectAttr(rev + ".outputX",  cons + "." + wal[0])
        cmds.connectAttr(ik_ctl + ".IK_FK", cons + "." + wal[1])

    # end joint に IK ctl の rotation も反映 (手/足の向き制御)
    end_orient_cons = cmds.orientConstraint(ik_ctl, wrist_ik, mo=True,
                                            n=end + "_ik_orient_oc")[0]

    # --- 8. Pole vector constraint (orient blend 構築の後で張る) ---
    # ここで初めて IK re-solve が発生するが、既に orient blend の mo=True offset
    # は bind (ik_j == fk_j == orig 全部同じ) で捕捉済みなので、以降の PV 操作は
    # hero joint を bind から drift させない。
    cmds.poleVectorConstraint(pv_ctl, ik_handle)

    # --- 8. Visibility ---
    try: cmds.setAttr(ik_ctl + ".v", lock=False)
    except Exception: pass
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
        "ik_chain":  ik_chain,
        "fk_chain":  fk_chain,
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

    # scale cache reset (毎回 fresh に mesh bbox を測定)
    _reset_scale_cache()

    # Step 1+2: rename
    if fbx_renamer is None:
        cmds.warning("[attach_ctrls] fbx_renamer not available -- skipping rename")
    else:
        fbx_renamer.remove_all_namespaces()
        fbx_renamer.rename_all_joints()

    # Step 3: cleanup
    if delete_junk:
        delete_unnecessary()

    # Step 4: root/main ctls を作成 (地面のオクタゴン + 主体 box)
    #         attach_ctrls_grp の直下、他の ctl の親になる
    diag = _scene_mesh_bbox_diag()
    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)
    # world ctl (地面) — 大きめオクタゴン
    world_ctl_name = "world_ctl"
    if not cmds.objExists(world_ctl_name):
        world_ctl = _make_octagon_curve(world_ctl_name, scale=diag * 0.28)
        _set_ctl_color(world_ctl, 13)  # 赤
        cmds.parent(world_ctl, ROOT_GROUP)
        _lock_hide_attrs(world_ctl, ["sx","sy","sz","v"])
    else:
        world_ctl = world_ctl_name
    # main ctl (体を包む) — 中サイズ box (床置き)
    main_ctl_name = "main_ctl"
    if not cmds.objExists(main_ctl_name):
        main_ctl = _make_flat_box_curve(main_ctl_name, scale=diag * 0.2,
                                        x_ratio=1.5, z_ratio=1.5)
        _set_ctl_color(main_ctl, 17)  # 黄
        cmds.parent(main_ctl, world_ctl)
        _lock_hide_attrs(main_ctl, ["sx","sy","sz","v"])
    else:
        main_ctl = main_ctl_name

    # Step 5: attach FK ctls, exclude IK/FK chain joints
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

    # Step 6: ルート系 ctl の親を main_ctl に寄せる
    # (attach_ctrls_grp 直下の孤立 npo を main_ctl の下に移動)
    for child in cmds.listRelatives(ROOT_GROUP, c=True, type="transform") or []:
        if child in (world_ctl_name, "world_ctl"):
            continue
        # world_ctl の下は触らない、それ以外の root-level npo だけ移す
        if child.endswith("_npo"):
            try:
                cmds.parent(child, main_ctl)
            except Exception:
                pass

    # Step 7: IK/FK setup (IK ctl NPO は独立世界置き = 足接地 目的)
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
