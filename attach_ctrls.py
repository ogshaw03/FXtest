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


__version__ = "0.9.34"


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

# Maya drawing override color indices (mGear 系配色に合わせる)
COLOR_L = 18  # light blue
COLOR_R = 20  # light rose
COLOR_C = 17  # yellow
COLOR_WORLD = 13  # red (top-level world ctl)
COLOR_DECOR = 2  # dark grey (装飾骨、視覚的に控えめ)
COLOR_UI = 25  # olive (UI host ctl、目立つ集約 attr host)

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

_MESH_FRONT_OFFSET_TOKENS = ("breast", "chest_l", "chest_r", "buttock")


def _needs_mesh_front_offset(joint_name):
    """胸や尻など、joint 位置が mesh 内部にあり ctl が埋没する骨を判定。"""
    n = joint_name.split(":")[-1].split("|")[-1].lower()
    return any(n.startswith(t) or ("_" + t) in n for t in _MESH_FRONT_OFFSET_TOKENS)


def _mesh_front_offset(joint, weight_thresh=0.3, min_verts=5, search_radius=15.0):
    """joint が影響する mesh の高重み vtx 重心 - joint pos を返す。
    ctl の CV を world 相対でこの vec 分 shift すれば mesh 前面に飛ばせる。
    無理なら None。

    高速化: 86k vtx の全 skinPercent は遅いので、joint 近傍
    (search_radius 内) の vtx だけを候補にして weight 判定する。
    """
    if cmds is None:
        return None
    jp = cmds.xform(joint, q=True, ws=True, t=True)
    scs = cmds.listConnections(joint + ".worldMatrix", type="skinCluster",
                                d=True, s=False) or []
    scs = list(set(scs))
    if not scs:
        for sc in cmds.ls(type="skinCluster") or []:
            try:
                if joint in (cmds.skinCluster(sc, q=True, inf=True) or []):
                    scs.append(sc)
            except Exception:
                pass
    if not scs:
        return None
    r2 = search_radius * search_radius
    for sc in scs:
        try:
            geo = cmds.skinCluster(sc, q=True, g=True) or []
        except Exception:
            continue
        if not geo:
            continue
        mesh = geo[0]
        try:
            nv = cmds.polyEvaluate(mesh, v=True)
        except Exception:
            nv = 0
        if not nv:
            continue
        # 1. joint bbox 半径内の vtx を絞り込み (距離判定は cheap)
        near = []
        for i in range(nv):
            try:
                p = cmds.xform(f"{mesh}.vtx[{i}]", q=True, ws=True, t=True)
            except Exception:
                continue
            d2 = ((p[0]-jp[0])**2 + (p[1]-jp[1])**2 + (p[2]-jp[2])**2)
            if d2 <= r2:
                near.append((i, p))
        # 2. 近傍 vtx について weight 判定
        hi = []
        for i, p in near:
            try:
                w = cmds.skinPercent(sc, f"{mesh}.vtx[{i}]",
                                     transform=joint, q=True)
            except Exception:
                continue
            if w is not None and w >= weight_thresh:
                hi.append(p)
        if len(hi) < min_verts:
            continue
        cx = sum(p[0] for p in hi) / len(hi)
        cy = sum(p[1] for p in hi) / len(hi)
        cz = sum(p[2] for p in hi) / len(hi)
        return (cx - jp[0], cy - jp[1], cz - jp[2])
    return None


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
    # breast (MMD 胸_L/胸_R → chest_L/chest_R): upper_body の ring 判定より
    # 前に置いて substring "chest" が誤マッチしないようにする。bone 長で
    # cube を作り、flat_horizontal=False で乳房の向きに沿わせる。
    if n.startswith("chest_l") or n.startswith("chest_r") or "breast" in n:
        bl = _bone_length(joint_name) or (diag * 0.03)
        return _make_cube_curve, bl, False
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


def _rewrite_flat_horizontal(ctl_name, world_pos, scale, maker,
                              x_ratio=None, z_ratio=None, ground_y=None):
    """matchTransform 後 (ctl が joint 回転を継承した状態) の shape の CV を
    world XZ 平面に強制配置する。transform 階層 (npo の rotate) は保持するので
    parentConstraint 追従は継続、見た目のみ水平化。

    Args:
        x_ratio / z_ratio: flat_box 系で default (1.4/1.0 or 2.4/1.6) を上書き。
                            leg IK ctl (1.6/2.2) 等呼び出し側の比率と一致させる。
        ground_y: None なら world_pos[1] (joint 高) に置く、指定あれば地面 Y
                    に置いて足元フラット box にする。
    """
    import math
    p = world_pos
    y = ground_y if ground_y is not None else p[1]
    if maker is _make_ring_curve or maker is _make_octagon_curve:
        sides = 16 if maker is _make_ring_curve else 8
        pts = [(p[0] + math.cos(2*math.pi*i/sides) * scale,
                y,
                p[2] + math.sin(2*math.pi*i/sides) * scale)
               for i in range(sides + 1)]
    elif maker is _make_wide_flat_box_curve:
        xr = x_ratio if x_ratio is not None else 2.4
        zr = z_ratio if z_ratio is not None else 1.6
        sx = scale * xr * 0.5
        sz = scale * zr * 0.5
        pts = [(p[0]-sx, y, p[2]-sz), (p[0]+sx, y, p[2]-sz),
               (p[0]+sx, y, p[2]+sz), (p[0]-sx, y, p[2]+sz),
               (p[0]-sx, y, p[2]-sz)]
    elif maker is _make_flat_box_curve:
        xr = x_ratio if x_ratio is not None else 1.4
        zr = z_ratio if z_ratio is not None else 1.0
        sx = scale * xr * 0.5
        sz = scale * zr * 0.5
        pts = [(p[0]-sx, y, p[2]-sz), (p[0]+sx, y, p[2]-sz),
               (p[0]+sx, y, p[2]+sz), (p[0]-sx, y, p[2]+sz),
               (p[0]-sx, y, p[2]-sz)]
    else:
        return
    for i, pt in enumerate(pts):
        try:
            cmds.xform(f"{ctl_name}.cv[{i}]", ws=True, t=pt)
        except Exception:
            pass


def _mark_as_ctl(ctl):
    """v0.9.12 自作化: mGear 依存 (isCtl / rig_controllers_grp) を撤去、
    controller tag (Maya 標準 pick-walk) + `attach_ctrl` marker (delete_generated
    が識別に使う) + Mirror Pose 用 `invTx..invSz` の 3 種のみ残置。
    """
    # 1. attach_ctrls 起源判定用 marker (delete_generated が使用)
    try:
        if not cmds.attributeQuery("attach_ctrl", node=ctl, exists=True):
            cmds.addAttr(ctl, ln="attach_ctrl", at="bool", dv=True, k=False)
        cmds.setAttr(ctl + ".attach_ctrl", channelBox=False)
    except Exception:
        pass
    # 2. Maya 2019+ controller tag (pick-walk / marking menu 連携、mGear 非依存)
    try:
        cmds.controller(ctl)
    except Exception:
        pass
    # 3. Mirror Pose 用 `invTx..invSz` 9 bool attr。
    # side が R の ctl は default で invTx=1, invRy=1, invRz=1 (YZ 平面 mirror)。
    try:
        _short = ctl.split(":")[-1].split("|")[-1]
        _side_r = _short.endswith("_R") or _short.endswith("_R_ctl") or \
                   "_R_" in _short or _short.startswith("R_")
        _defaults = {
            "invTx": 1 if _side_r else 0,
            "invTy": 0, "invTz": 0,
            "invRx": 0,
            "invRy": 1 if _side_r else 0,
            "invRz": 1 if _side_r else 0,
            "invSx": 0, "invSy": 0, "invSz": 0,
        }
        for _a, _dv in _defaults.items():
            if not cmds.attributeQuery(_a, node=ctl, exists=True):
                cmds.addAttr(ctl, ln=_a, at="bool", dv=bool(_dv), k=False)
            try: cmds.setAttr(ctl + "." + _a, channelBox=False)
            except Exception: pass
    except Exception:
        pass


def _set_ctl_color(ctl, color_idx):
    """override color + controller marker を一括で付ける (v0.9.12: 自作化)。
    ctl 生成後に必ず呼ばれる場所なので、marker 付与漏れを構造的に防ぐ。"""
    _mark_as_ctl(ctl)
    return _set_ctl_color_only(ctl, color_idx)


def _set_ctl_color_only(ctl, color_idx):
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
                        auto_scale=True, skip_decoration=True):
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
    total_j = len(joints)

    # Pass 1: create ctl + npo per joint (all under ROOT_GROUP temporarily)
    for _pass1_i, jnt in enumerate(joints):
        if total_j and (_pass1_i % max(1, total_j // 20) == 0):
            # attach_controllers 全体を [0, 70]% とし、Pass 1 が [0, 60]% を占める
            _pw_sub(60.0 * _pass1_i / total_j,
                    f"Attach controllers {_pass1_i}/{total_j}")
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

        if flat_horizontal:
            # WORLD-ALIGN (WAIST scout): bone tilt (waist 44°/lower_body 30°等)
            # を継承させないため npo/ctl を world identity で置く。joint bind
            # rot は Pass 3 の parentConstraint mo=True で吸収させる。これで
            # waist_ctl.rotateY=10 が pure world yaw になる (旧 tilted 軸 →
            # roll 7° 混入を解消)。
            joint_ws = cmds.xform(jnt, q=True, ws=True, t=True)
            cmds.xform(npo, ws=True, t=joint_ws, ro=(0, 0, 0))
        else:
            cmds.matchTransform(npo, jnt, pos=True, rot=True)
        cmds.parent(npo, ROOT_GROUP)

        if flat_horizontal:
            _rewrite_flat_horizontal(ctl, joint_ws, ctl_size, maker)
        elif _needs_mesh_front_offset(jnt):
            # 胸 (breast) 等 joint が mesh 内部に埋まる骨は、shape の CV だけを
            # mesh 前面重心へ world 相対 shift。transform は joint 位置維持で
            # parentConstraint に影響なし (BREAST scout 発見)。
            off = _mesh_front_offset(jnt)
            if off:
                try:
                    cmds.move(off[0], off[1], off[2],
                              ctl + ".cv[*]", relative=True, worldSpace=True)
                except Exception:
                    pass

        _lock_hide_attrs(ctl, ["sx", "sy", "sz", "v"])

        jnt_to_ctl[jnt] = (npo, ctl)
        created_ctls.append(ctl_name)

    # Pass 2: 骨階層に合わせて npo を親子付け直し
    # 親 joint が jnt_to_ctl にあれば normal (親 ctl の下)
    # 無ければ (IK chain 除外 or 削除された等)、親 joint を上に辿り
    #   - 途中で ctl 持ち joint に当たればその ctl の下
    #   - 当たらず joint 存在すればその joint の下 (wrist_L 等、IK/FK で
    #     rotate される joint に直接 parent すれば ctl が joint に追従)
    _pw_sub(60.0, "Attach controllers: reparent")
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
    # flat_horizontal 系 (waist/spine 系) は npo を world identity にしたので
    # joint bind rot との差分を parentConstraint mo=True で吸収させる (WAIST scout)。
    if do_constrain:
        _pw_sub(80.0, "Attach controllers: constrain")
        for jnt, (npo, ctl) in jnt_to_ctl.items():
            _, _, _flat = _pick_ctl_maker(jnt)
            try:
                cmds.parentConstraint(ctl, jnt, mo=bool(_flat),
                                      n=jnt + "_parentConstraint")
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] constraint {ctl} -> {jnt} failed: {exc}")
    _pw_sub(100.0)

    print(f"[{_PACKAGE}] Attached controllers: {len(jnt_to_ctl)} joint(s), "
          f"constrain={do_constrain}, scale={scale}")
    return jnt_to_ctl


def delete_generated():
    """attach_ctrls が生成したノードのみ削除。他 rig 資産を壊さない。

    削除対象:
      1. ROOT_GROUP (`attach_ctrls_grp`) とその子孫全て
      2. attach_ctrls 特有 suffix を持つ constraint (`*_ikfk_oc`,`*_fk_oc`,
         `*_ik_orient_oc`,`*_parentConstraint*` は attach 起源のみ判定)
      3. attach_ctrls が生成した ikHandle (`*_ikh` `*_toeIkh`)
      4. dual chain 副産物 (`*_ik`/`*_fk` joint)
    """
    if cmds is None:
        return
    n = 0
    # ROOT_GROUP 配下は無条件で消す (attach_ctrls_grp とその中身)
    if cmds.objExists(ROOT_GROUP):
        cmds.delete(ROOT_GROUP)
        n += 1

    def _safe_del(node, why):
        nonlocal n
        try:
            cmds.delete(node); n += 1
        except Exception as exc:
            print(f"[{_PACKAGE}] delete {node} failed ({why}): {exc}")

    # attach 起源の constraint/handle のみ (suffix で識別)
    for pat in ("*_ikfk_oc", "*_fk_oc", "*_ik_orient_oc", "*_ikctl_oc"):
        for con in cmds.ls(pat, type="orientConstraint") or []:
            _safe_del(con, "orient constraint")
    # v0.9.13 Bug 1: hero joint 用 blend を parentConstraint に置換 (per-target
    # offset で FK↔IK 切替時の bind 復帰を保証)。suffix `_ikfk_pc` を掃討対象へ。
    for pat in ("*_ikfk_pc",):
        for con in cmds.ls(pat, type="parentConstraint") or []:
            _safe_del(con, "ikfk parent constraint")
    # attach 起源 parentConstraint (naming: <joint>_parentConstraint)
    for con in cmds.ls("*_parentConstraint*", type="parentConstraint") or []:
        # attach_ctrls は attach_controllers で `n=jnt + "_parentConstraint"` で
        # 作る。名前が完全に <対象joint>_parentConstraint\d* の形かを追加チェック
        name = con.split(":")[-1]
        if "_parentConstraint" in name:
            _safe_del(con, "parent constraint")
    # attach 起源 IK handles (`*_ikh`, `*_toeIkh`)
    for pat in ("*_ikh", "*_toeIkh"):
        for ikh in cmds.ls(pat, type="ikHandle") or []:
            _safe_del(ikh, "ik handle")
    # dual chain: <joint>_ik / <joint>_fk の joint
    # 加えて reverse foot 用 helper (<ankle>_rfBallBone / _rfToeBone)
    # (v0.9.12: mGear 用 `_mth` は撤去済)
    for suf in ("_ik", "_fk", "_rfBallBone", "_rfToeBone"):
        for j in cmds.ls("*" + suf, type="joint") or []:
            if cmds.objExists(j):
                _safe_del(j, "generated joint")
    # attach_ctrls 起源 ctl (isCtl marker + Pass 2 で親 joint 下に移動されて
    # ROOT_GROUP 削除で残った *_ctl / *_npo) を全部掃討する (AUDIT2 NEW: 2回目
    # setup で 90+ warning "already exists; skipping" の原因)。
    # v0.9.12: 自作 marker `attach_ctrl` で判定 (旧 `isCtl` 廃止)。
    for ctl in cmds.ls("*_ctl", type="transform") or []:
        if not cmds.objExists(ctl):
            continue
        try:
            has_marker = cmds.attributeQuery("attach_ctrl", node=ctl, exists=True)
        except Exception:
            has_marker = False
        if has_marker:
            _safe_del(ctl, "orphan ctl")
    # 対応する npo も掃討
    for npo in cmds.ls("*_npo", type="transform") or []:
        if cmds.objExists(npo):
            _safe_del(npo, "orphan npo")
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


def _compute_pv_position(start_pos, mid_pos, end_pos, distance, fallback_dir=None):
    """3-joint chain (start-mid-end) の bind pose を保つ pole vector 位置。

    chain plane 内で mid から start-end 直線の perpendicular 方向に distance 押し出す。
    T-pose のほぼ直線 chain (Nekotatune 腕、pole_len 0.4 等) だと perpendicular が
    数値的に不安定 → fallback_dir (chain 種類別 hint) が指定されていれば使う。
    """
    import math
    v_se = [end_pos[i] - start_pos[i] for i in range(3)]
    v_sm = [mid_pos[i] - start_pos[i] for i in range(3)]
    len2_se = sum(a*a for a in v_se)
    len_se = math.sqrt(len2_se)
    if len2_se < 1e-6:
        return list(mid_pos)
    t = sum(v_sm[i] * v_se[i] for i in range(3)) / len2_se
    projected = [start_pos[i] + t * v_se[i] for i in range(3)]
    pole_dir = [mid_pos[i] - projected[i] for i in range(3)]
    pole_len = math.sqrt(sum(a*a for a in pole_dir))

    # fallback を使うか判定。LEGPV scout 発見: Nekotatune の脚は tiny bend
    # (2 unit / chain 58 = 3.4%) を amplify すると PV が反対側へ飛ぶ。
    # bind pose (T-pose) では natural bend は基本 noise なので、fallback_dir
    # が指定されていたら 常に fallback を優先する (chain plane に射影して使う)。
    # fallback_dir 無指定時のみ natural bend を使う (pose 済 chain 想定)。
    use_fallback = (fallback_dir is not None) or (pole_len < max(len_se * 0.02, 0.05))
    if use_fallback:
        if fallback_dir is not None:
            pd = list(fallback_dir)
            # fallback は v_se 方向成分を差し引いて chain 平面に強制射影
            se_unit_len = len_se
            if se_unit_len > 1e-6:
                v_se_unit = [v_se[i] / se_unit_len for i in range(3)]
                dot = sum(pd[i] * v_se_unit[i] for i in range(3))
                pd = [pd[i] - dot * v_se_unit[i] for i in range(3)]
            pd_len = math.sqrt(sum(a*a for a in pd))
            if pd_len > 1e-6:
                pole_dir = [pd[i] / pd_len for i in range(3)]
                pole_len = 1.0
        if pole_len < 1e-6:
            pole_dir = [0, 0, 1]
            pole_len = 1.0
    return [mid_pos[i] + pole_dir[i] / pole_len * distance for i in range(3)]


def _dup_hero_joint(orig, suffix, new_parent=None):
    """orig と同じ world 位置/回転で joint を作り、指定 parent 下に置く。

    STRETCH2 scout 発見: 従来の `cmds.duplicate + cmds.parent` は FBX の world
    scale=100 + twist bone 混在階層で「intermediate transform を挟む」病理を
    起こし、IK chain が transform1 の下に取り残されて stretch が完全破綻した
    (arm 側だけ発生、leg は twist 無しなので免れていた)。

    対策: `cmds.duplicate` の代わりに `cmds.select(new_parent)` してから
    `cmds.joint()` で直接生成、位置/回転/jointOrient/radius を手でコピーする。
    これで parent は cmds.joint が正しく設定するため intermediate が生じない。
    """
    ws_t = cmds.xform(orig, q=True, ws=True, t=True)
    ws_r = cmds.xform(orig, q=True, ws=True, ro=True)
    if new_parent is not None and cmds.objExists(new_parent):
        cmds.select(new_parent, r=True)
    else:
        cmds.select(cl=True)
    n = cmds.joint(n=orig + suffix, p=ws_t)
    # rotation を world で合わせる (freeze_joint_rotations 後は rotate=0,
    # jointOrient に全て入ってるので orient を継承)
    try: cmds.xform(n, ws=True, ro=ws_r)
    except Exception: pass
    # jointOrient を hero と揃える (IK solve が hero と同じ chain plane を解く)
    for a in ("jointOrientX", "jointOrientY", "jointOrientZ"):
        try:
            v = cmds.getAttr(orig + "." + a)
            cmds.setAttr(n + "." + a, v)
        except Exception:
            pass
    # rotate を再度 0 に (jointOrient にセットしたので)
    try: cmds.setAttr(n + ".rotate", 0, 0, 0, type="double3")
    except Exception: pass
    # v0.9.21: preferredAngle も copy (RP solver bend hint 保持)
    for a in ("preferredAngleX", "preferredAngleY", "preferredAngleZ"):
        try:
            v = cmds.getAttr(orig + "." + a)
            cmds.setAttr(n + "." + a, v)
        except Exception:
            pass
    # visual radius を hero と同じに (0.016 想定)
    try:
        cmds.setAttr(n + ".radius", cmds.getAttr(orig + ".radius"))
    except Exception: pass
    return n


def setup_ik_fk(start, mid, end, side="C", pv_offset=None, label=None):
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
        label: 生成する ctl/npo/ikh の canonical 名前 prefix (`arm_L` 等)。
               None の場合 start joint 名を使う (v0.9.31 前の既定挙動)。
               UDE_L 等 非標準命名の joint を arm_L canonical name の rig に
               したい場合に mapping 経由で指定する。
    """
    color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]
    if label is None:
        label = start

    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)

    # 元 start joint の親 (arm_L なら shoulder_C_L 等)
    orig_parent_list = cmds.listRelatives(start, p=True, type="joint") or []
    orig_parent = orig_parent_list[0] if orig_parent_list else None

    # v0.9.13 Bug 1: 真の bind WS matrix / position を rig 構築前に捕捉。
    # 後段 (twist scan) は「orig を bind に戻す twist 値」を探すが、以前は
    # orient constraint 生成後に snapshot していたため単一 offset の誤差を
    # そのまま bind として固定していた。真 bind を先取りすれば scan が
    # ik chain の実 drift を最小化できる。
    _true_bind_ws_mat = {
        j: cmds.xform(j, q=True, ws=True, m=True) for j in (start, mid, end)
    }
    _true_bind_ws_pos = {
        j: cmds.xform(j, q=True, ws=True, t=True) for j in (start, mid, end)
    }

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

    # (probe removed)
    # --- 2. IK handle on CLEAN chain ---
    # v0.9.0 の freeze_joint_rotations で rotate=0 になっているので RP solver
    # は bind pose を曖昧と判定せず chain を perturb しない (spa fix 不要)。
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
    # 水平化 (bone 継承の斜め表示解消):
    # - leg は地面 (ground_y) に敷く flat box
    # - arm は wrist 高さの水平面 (AUDIT #4: hand IK ctl 未水平化を修正)
    end_ws = cmds.xform(end, q=True, ws=True, t=True)
    if is_leg:
        gy = end_ws[1]
        try:
            _lm = _detect_foot_landmarks(end, _find_toe_joint(end))
            if _lm:
                gy = _lm.get("ground_y", end_ws[1])
        except Exception:
            pass
        _rewrite_flat_horizontal(ik_ctl, end_ws, ik_size, _make_flat_box_curve,
                                  x_ratio=1.6, z_ratio=2.2, ground_y=gy)
    else:
        # arm: wrist 高さの水平 flat box (地面には落とさない、掴みやすい)
        _rewrite_flat_horizontal(ik_ctl, end_ws, ik_size, _make_flat_box_curve,
                                  x_ratio=1.4, z_ratio=1.6, ground_y=end_ws[1])

    # (probe removed)
    # --- 4. Pole vector ctl (diamond 形状で目立たせる) ---
    pv_ctl = _make_diamond_curve(label + "_PV_ctl", scale=pv_size)
    # PV は通常 side 色より少し変えて識別性を上げる (light green vs light blue/rose)
    _set_ctl_color(pv_ctl, 14 if side == "L" else 15 if side == "R" else 17)
    # ↑ PV は色を通常ctl とは変えて識別しやすく (L=lightblue20, R=lightred12, C=yellow17)
    pv_npo = cmds.group(em=True, name=label + "_PV_npo")
    cmds.parent(pv_ctl, pv_npo)
    # bind pose を保つ幾何位置に PV を配置 (chain plane 内、start-end 直線の
    # perpendicular 方向、mid から distance 押し出し)。これで IK RP solver が
    # bind pose = solved pose を保つ → PV 追加でも hero joint が drift しない。
    start_pos = cmds.xform(start, q=True, ws=True, t=True)
    mid_pos = cmds.xform(mid, q=True, ws=True, t=True)
    end_pos = cmds.xform(end, q=True, ws=True, t=True)
    if pv_offset is None:
        # SPIDERMAN scout 実測: arm 0.71 / leg 0.60 × chain_len が Spider-Man
        # (mGear 出力) の実位置比率。掴みやすさと画面圧迫のバランス。
        import math as _math
        chain_len = _math.sqrt(sum(
            (end_pos[i] - start_pos[i])**2 for i in range(3)))
        is_leg_pv = "leg" in label.lower()
        base_offset = chain_len * (0.60 if is_leg_pv else 0.71)
    else:
        base_offset = pv_offset
    # T-pose straight chain の fallback: 腕は後方 (-Z), 脚は前方 (+Z)
    lo = label.lower()
    if "leg" in lo or "knee" in lo or "ankle" in lo:
        fallback_pv = [0, 0, +1]
    else:
        fallback_pv = [0, 0, -1]
    pv_pos = _compute_pv_position(start_pos, mid_pos, end_pos,
                                   base_offset, fallback_dir=fallback_pv)
    cmds.xform(pv_npo, ws=True, t=pv_pos)
    # v0.9.12 Bug 2: pv_npo を chain の親骨 (orig_parent) 下に parent。
    # ROOT_GROUP (world 固定) だと腰が下がった時 PV は動かず膝が chain plane
    # 外へ大きく飛ぶ (leg で knee Z+17.5)。腕/脚とも hip/shoulder 系に追従
    # させれば bend 幾何が保持される。
    #
    # v0.9.31 Bug 2 dynamic PV 検討結果 (未採用):
    #   `hip (leg_L) と ik_ctl の中点` に pointConstraint 追従する dyn_helper
    #   下に pv_npo を parent する案を試作 (bug2_dynpv セッション)。
    #   dy=-35 で knee X drift 0.63 → 0.08 unit と激減する一方、
    #   dy=-20 (通常 squat 想定) で 0.26 → 0.43 unit と ±0.3 spec を逸脱。
    #   浅い depth への副作用を打ち消すには compression 依存の blend
    #   (condition/multiplyDivide) が必要で、character-specific tuning が
    #   前提になる。汎用ツールとして安全側を採り v0.9.12 の hip-follow を
    #   維持。将来 UI attr で auto-blend を露出する形で再検討する余地あり。
    pv_parent = orig_parent if (orig_parent and cmds.objExists(orig_parent)) else ROOT_GROUP
    cmds.parent(pv_npo, pv_parent)
    # v0.9.12 Bug 1: poleVectorConstraint を **orient constraint より前** に
    # 張る。以前の設計は「後で張ると再ソルブが走る」と誤診断していたが、
    # 実際は逆で、PV constraint 無しで作った ikHandle が RP solver default
    # で 32° twist を焼き付け、続く orient constraint mo=True がクリーンな
    # offset を捕えられず arm hero elbow が 0.386 unit ズレる。PV を先に
    # 張れば ik chain は bind に近い姿勢で解け offset が正しく取れる。
    cmds.poleVectorConstraint(pv_ctl, ik_handle)
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

    # --- 6. Switch attribute (UI host ctl に集約、mGear 慣習に合わせる) ---
    # UI host は wrist/ankle から少し離した目立つ位置に配置 (ユーザ意向:
    # 「手首の近くにあるオプションコントローラー」で switch / roll を制御)。
    end_pos = cmds.xform(end, q=True, ws=True, t=True)
    ui_offset = diag / 15.0  # v0.9.6 の diag/25 より離して掴みやすく
    ui_pos = (end_pos[0] + (ui_offset if side == "L" else -ui_offset),
              end_pos[1] + ui_offset * 0.6, end_pos[2])
    ui_host = _create_ui_host_ctl(label, ui_pos, ik_size * 0.9, side)
    # divider (Channel Box 見出し) + 主要 attr
    if not cmds.attributeQuery("__" + label + "__", node=ui_host, exists=True):
        cmds.addAttr(ui_host, ln="__" + label + "__", at="enum", en=label,
                     k=False, category="divider")
        cmds.setAttr(ui_host + ".__" + label + "__", channelBox=True)
    for attr_name, dv in [("IK_FK", 1.0), ("stretch", 0.0),
                          ("ikVis", 1.0), ("fkVis", 0.0)]:
        if not cmds.attributeQuery(attr_name, node=ui_host, exists=True):
            cmds.addAttr(ui_host, ln=attr_name, at="float",
                         min=0.0, max=1.0, dv=dv, k=True)
    # v0.9.11 Stretch UI: 伸び上限 (maxstretch, default 1.5x) と
    # volume 保存 (体積保持で伸ばした bone を横方向に細くする)
    if not cmds.attributeQuery("maxstretch", node=ui_host, exists=True):
        cmds.addAttr(ui_host, ln="maxstretch", at="float",
                     min=1.0, max=5.0, dv=1.5, k=True)
    if not cmds.attributeQuery("volume", node=ui_host, exists=True):
        cmds.addAttr(ui_host, ln="volume", at="float",
                     min=0.0, max=1.0, dv=0.0, k=True)
    # v0.9.12: mGear 撤去。`IK_FK` を master 直接、`_blend` エイリアス廃止、
    # `_id0_ctl_cnx` message array 廃止、`_mth` joint 廃止 (snap は現 hero
    # joint 位置を直接使う)、`_tag_mgear_ctl` 廃止。
    # IK_FK を keyable にして直接ユーザが編集可能に (旧 driven lock 撤去)。
    try:
        cmds.setAttr(ui_host + ".IK_FK", k=True, channelBox=True)
    except Exception:
        pass
    # ikVis / fkVis は依然として IK_FK/rev で driven なので隠す
    for driven in ("ikVis", "fkVis"):
        try:
            cmds.setAttr(ui_host + "." + driven, k=False, channelBox=False)
        except Exception:
            pass

    rev = cmds.createNode("reverse", n=label + "_ikfk_rev")
    cmds.connectAttr(ui_host + ".IK_FK", rev + ".inputX")

    # (probe removed)
    # --- 7. Blend original hero joints between IK chain and FK chain ---
    # v0.9.13 Bug 1: 従来は 2-source `orientConstraint` mo=True で blend したが、
    # Maya の orientConstraint は per-target offset を持たず top-level 単一
    # `offsetX/Y/Z` のみ。初期化時 weight=1/1 で `offset = orig - avg(FK, IK)`
    # となり、weight=1/0 (FK) や 0/1 (IK) に切替えても bind に完全復帰しない
    # (arm mid_delta=0.386, leg=1.75)。
    #
    # 対策: `parentConstraint` (per-target `targetOffsetRotate` あり) を使い、
    # translate は skip して rotation のみ blend する。mo=True で各 source ごと
    # に独立 offset が計算されるので、weight=1 側の source 単独時は bind に
    # 完全復帰する。挙動は事前に mayapy で検証済 (per-target offset 正解)。
    #
    # End joint (wrist/ankle) は IK RP solver が rotation を制御しないため、
    # `orientConstraint(ik_ctl, wrist_ik, mo=True)` を先に張って ik_ctl の
    # WS 回転を wrist_ik.rotate に注入する (single-source は正しく解決)。
    # 以前の「wrist_ik 経由 local-space 不整合」は 2-source 単一 offset が
    # 原因だったので、per-target offset の parentConstraint では再発しない。
    end_ikj = ik_chain[2]
    try:
        cmds.orientConstraint(ik_ctl, end_ikj, mo=True,
                              n=end_ikj + "_ikctl_oc")
    except Exception:
        pass

    for orig, ikj, fkj in zip([start, mid, end], ik_chain, fk_chain):
        cons = cmds.parentConstraint(
            fkj, ikj, orig, mo=True,
            st=("x", "y", "z"),   # translate は skip: rotation blend のみ
            n=orig + "_ikfk_pc")[0]
        wal = cmds.parentConstraint(cons, q=True, wal=True)  # [fkW, ikW]
        cmds.connectAttr(rev + ".outputX",  cons + "." + wal[0], f=True)
        cmds.connectAttr(ui_host + ".IK_FK", cons + "." + wal[1], f=True)
        # interpType=2 (shortest) で 180°付近の euler flip を回避
        try:
            cmds.setAttr(cons + ".interpType", 2)
        except Exception:
            pass

    # v0.9.12: mGear tag (`_tag_mgear_ctl` / ctl_role / uiHost / match_ref /
    # _mth joint dup) は撤去。自作 UI (snap ボタン / mirror_pose 関数) で対応。

    # (probe removed)
    # --- 8. Pole vector constraint (orient blend 構築の後で張る) ---
    # 判定は world X 軸 (bone 軸) の acos で行う。start+mid+end 3 joint の合算 drift
    # を最小化する twist を選ぶ (FOOTROT scout 発見: start だけだと elbow/wrist の
    # 163°/127° flip を見逃す)。
    import math as _math
    def _bone_axis_diff(j, ref_matrix):
        """world matrix の 3 軸 (X/Y/Z) それぞれの dot を平均、twist も検知。
        以前は X 軸だけ測定していたが Y-axis twist を素通ししていた (v0.9.12 Bug 1)。"""
        m = cmds.xform(j, q=True, ws=True, m=True) or [0]*16
        _angles = []
        for _row in (0, 4, 8):  # X, Y, Z 軸それぞれの row (matrix row-major)
            dot = m[_row]*ref_matrix[_row] + m[_row+1]*ref_matrix[_row+1] + m[_row+2]*ref_matrix[_row+2]
            dot = max(-1.0, min(1.0, dot))
            _angles.append(_math.degrees(_math.acos(dot)))
        return sum(_angles) / 3.0

    def _pos_diff(j, ref_pos):
        p = cmds.xform(j, q=True, ws=True, t=True) or [0,0,0]
        return _math.sqrt(sum((p[i]-ref_pos[i])**2 for i in range(3)))

    # v0.9.13 Bug 1: 関数冒頭で捕捉した真 bind (`_true_bind_ws_mat/pos`) を使用。
    # 従来は orient constraint 後に snapshot していたため単一 offset の誤差が
    # bind として固定され、twist scan が real drift を見えなくしていた。
    bind_matrices = _true_bind_ws_mat
    bind_positions = _true_bind_ws_pos

    def _total_drift():
        # 3-axis rotate drift + position drift の合成 (v0.9.12: twist と
        # 位置ズレを両方検知)。position は unit なのでそのまま加算 (角度と
        # 位置は比較尺度違うが、scan の相対比較には十分)。
        _rot = sum(_bone_axis_diff(j, bind_matrices[j]) for j in bind_matrices)
        _pos = sum(_pos_diff(j, bind_positions[j]) for j in bind_positions)
        return _rot + _pos

    # (v0.9.12 Bug 1: poleVectorConstraint は step 4 で orient constraint
    #  より前に既に張られている。二重張りしない)

    # (probe removed)
    # --- 8.5. Twist 自動補正 (RP solver plane flip 対策) ---
    # AUDIT #11: v0.9.0 の freeze_joint_rotations で bind pose が identity 化
    # された後、全 chain で twist=0° が最適解に収束する (実測)。73 候補 × 4
    # chain の総当りは 10-15% の実行時間を消費するだけで無駄なので、初期解
    # が既に十分小さければスキップ。念のため fallback として drift > 5° の
    # 場合のみ限定的に探索 (v0.9.0 前の pose データ想定)。
    best_twist = 0.0
    best_drift = _total_drift()
    _pw_sub(80.0, f"Solve twist ({label})")
    # v0.9.12 Bug 1 対処: PV constraint 張っても RP solver が chain 方向次第で
    # twist 焼き込みしがち (arm 側で観測、32° や 50° の Y twist)。drift 閾値を
    # 1° に絞って必要なら常に scan する。粗い step (15°) → 細かい step (3°) 二段
    if best_drift > 1.0:
        # 粗い探索
        for twist_try in range(-180, 181, 15):
            try:
                cmds.setAttr(ik_handle + ".twist", float(twist_try))
                d = _total_drift()
                if d < best_drift:
                    best_drift = d
                    best_twist = float(twist_try)
            except Exception:
                pass
        # 細かい探索 (best_twist ± 15° を 3° step で)
        for twist_try in range(int(best_twist) - 15, int(best_twist) + 16, 3):
            try:
                cmds.setAttr(ik_handle + ".twist", float(twist_try))
                d = _total_drift()
                if d < best_drift:
                    best_drift = d
                    best_twist = float(twist_try)
            except Exception:
                pass
    try:
        cmds.setAttr(ik_handle + ".twist", best_twist)
    except Exception:
        pass
    final_total = _total_drift()
    final_per_j = {j: _bone_axis_diff(j, bind_matrices[j]) for j in bind_matrices}
    print(f"[{_PACKAGE}] {label} twist={best_twist}° "
          f"(total drift {final_total:.1f}°, per-joint: "
          f"{ {k.split('|')[-1]: round(v,1) for k,v in final_per_j.items()} })")

    # (probe removed)
    # --- 8.7. Stretch (ui_host.stretch attr で ON/OFF blend) ---
    # 標準 IK stretch: chain root ↔ IK ctl の距離が rest_len を超えたら
    # 各 bone の translate をスケール。stretch attr で ON/OFF blend。
    # hero chain も translate 駆動 (IK モード時のみ) しないとメッシュが届かない。
    try:
        import math as _sm
        # rest_len は clean IK chain の bind 時 bone 長総和 (chain 全長)
        p0 = cmds.xform(arm_ik, q=True, ws=True, t=True)
        p1 = cmds.xform(mid_ik if False else ik_chain[1], q=True, ws=True, t=True)
        p2 = cmds.xform(wrist_ik, q=True, ws=True, t=True)
        rest_len = (_sm.sqrt(sum((a-b)**2 for a, b in zip(p0, p1))) +
                    _sm.sqrt(sum((a-b)**2 for a, b in zip(p1, p2))))
        if rest_len > 1e-4:
            # 距離ノード: arm_ik root ↔ IK ctl
            db = cmds.createNode("distanceBetween", n=label + "_stretch_dist")
            cmds.connectAttr(arm_ik + ".worldMatrix[0]", db + ".inMatrix1")
            cmds.connectAttr(ik_ctl + ".worldMatrix[0]", db + ".inMatrix2")
            # v0.9.11: rest_len を world_ctl.sx で補正 (global scale 変えても
            # stretch trigger threshold が rig scale と一致するように)
            rest_scaled = cmds.createNode("multiplyDivide",
                                           n=label + "_stretch_rest_scale")
            cmds.setAttr(rest_scaled + ".input1X", rest_len)
            if cmds.objExists("world_ctl"):
                try:
                    cmds.connectAttr("world_ctl.scaleX", rest_scaled + ".input2X")
                except Exception:
                    cmds.setAttr(rest_scaled + ".input2X", 1.0)
            else:
                cmds.setAttr(rest_scaled + ".input2X", 1.0)
            # 距離 / rest_len (scaled)
            div = cmds.createNode("multiplyDivide", n=label + "_stretch_div")
            cmds.setAttr(div + ".operation", 2)  # divide
            cmds.connectAttr(db + ".distance", div + ".input1X")
            cmds.connectAttr(rest_scaled + ".outputX", div + ".input2X", f=True)
            # rest_len 超え時のみ scale 適用 (それ以下は 1.0 で bone 短縮を防ぐ)
            cnd = cmds.createNode("condition", n=label + "_stretch_cond")
            cmds.setAttr(cnd + ".operation", 2)  # greater
            cmds.connectAttr(db + ".distance", cnd + ".firstTerm")
            cmds.connectAttr(rest_scaled + ".outputX", cnd + ".secondTerm", f=True)
            # v0.9.11: maxstretch で伸び上限 clamp (min = clamp scale, max = maxstretch)
            # UI attr `maxstretch` (default 1.5) は setup_ik_fk の attr 追加時に定義
            cl = cmds.createNode("clamp", n=label + "_stretch_clamp")
            cmds.setAttr(cl + ".minR", 1.0)
            if cmds.attributeQuery("maxstretch", node=ui_host, exists=True):
                cmds.connectAttr(ui_host + ".maxstretch", cl + ".maxR")
            else:
                cmds.setAttr(cl + ".maxR", 999.0)
            cmds.connectAttr(div + ".outputX", cl + ".inputR")
            cmds.connectAttr(cl + ".outputR", cnd + ".colorIfTrueR", f=True)
            cmds.setAttr(cnd + ".colorIfFalseR", 1.0)
            # stretch attr で 1.0 (off) ↔ scale (on) blend
            bta = cmds.createNode("blendTwoAttr", n=label + "_stretch_blend")
            cmds.setAttr(bta + ".input[0]", 1.0)
            cmds.connectAttr(cnd + ".outColorR", bta + ".input[1]")
            cmds.connectAttr(ui_host + ".stretch", bta + ".attributesBlender")
            # IK_FK gate: FK モード時は stretch を切って bind translate 維持
            # (これが無いと FK モードで IK bone も勝手に伸縮して snap 時に狂う)
            ik_gate = cmds.createNode("blendTwoAttr", n=label + "_stretch_ikgate")
            cmds.setAttr(ik_gate + ".input[0]", 1.0)
            cmds.connectAttr(bta + ".output", ik_gate + ".input[1]")
            cmds.connectAttr(ui_host + ".IK_FK", ik_gate + ".attributesBlender")
            # v0.9.10 (STRETCH4 案A + AUDIT3): translate は per-axis 接続
            # (`.translateX/Y/Z` 個別) にしないと既存 parentConstraint の
            # per-component 接続が優先されて scale が無効化される。加えて
            # hero_chain_bones を DFS 化して兄弟 twist (arm_twist_1..3 等)
            # も回収、arm chain 全体で reach 1:1 達成。
            def _conn_per_axis(md, target):
                for _ax in ("X", "Y", "Z"):
                    try:
                        cmds.connectAttr(md + ".output" + _ax,
                                          target + ".translate" + _ax, f=True)
                    except Exception:
                        pass
            # IK chain bones (mid, end) の translate に ik_gate 経由で scale
            for b in (ik_chain[1], ik_chain[2]):
                rest_t = cmds.getAttr(b + ".translate")[0]
                md = cmds.createNode("multiplyDivide", n=b + "_stretch_mul")
                cmds.setAttr(md + ".input1", *rest_t, type="double3")
                cmds.connectAttr(ik_gate + ".output", md + ".input2X")
                cmds.connectAttr(ik_gate + ".output", md + ".input2Y")
                cmds.connectAttr(ik_gate + ".output", md + ".input2Z")
                _conn_per_axis(md, b)
            # hero chain: start の全 descendant joint (end の descendant は除く
            # = 指骨/末端は stretch 対象外) を回収して scale。DFS で兄弟
            # twist (arm_twist_1..3, hand_twist_1..3 等) も含める。
            all_desc = cmds.listRelatives(start, ad=True, type="joint") or []
            end_desc = set(cmds.listRelatives(end, ad=True, type="joint") or [])
            hero_bones = [j for j in all_desc if j not in end_desc]
            # v0.9.20 リバースフット回帰対策: MMD の D (Direct) 系 bone は
            # waistcancel_L 経由の hidden constraint を持ち、translate を直接
            # 接続で置き換えると constraint が壊れて chain が world 座標系で
            # jump する (ankle_L Y=8.85 → 65.01 現象)。dummy_/shadow_/D 系
            # bone は stretch 対象から除外する。skinning は twist bones で
            # 十分カバーされる想定。
            def _is_d_family(name):
                s = name.split("|")[-1].split(":")[-1].lower()
                if s.startswith("dummy_") or s.startswith("shadow_"):
                    return True
                for tok in ("legd", "kneed", "ankled", "toed", "footd"):
                    if tok in s:
                        return True
                return False
            for h in hero_bones:
                if not cmds.objExists(h):
                    continue
                if _is_d_family(h):
                    continue
                try:
                    rest_h = cmds.getAttr(h + ".translate")[0]
                except Exception:
                    continue
                if abs(rest_h[0])+abs(rest_h[1])+abs(rest_h[2]) < 1e-6:
                    continue
                md_h = cmds.createNode("multiplyDivide", n=h + "_stretch_mul")
                cmds.setAttr(md_h + ".input1", *rest_h, type="double3")
                cmds.connectAttr(ik_gate + ".output", md_h + ".input2X")
                cmds.connectAttr(ik_gate + ".output", md_h + ".input2Y")
                cmds.connectAttr(ik_gate + ".output", md_h + ".input2Z")
                _conn_per_axis(md_h, h)

            # v0.9.11 Volume preservation: hero bone の X/Z scale を stretch
            # 逆数の平方根で駆動 (bone 縦に伸びたら横に細くする)。
            # volume attr で強度 blend (0 = 効かない、1 = 完全に体積保存)。
            # `pow(stretch, -0.5)` は power ノードで実現、volume で blend。
            try:
                vol_pow = cmds.createNode("multiplyDivide",
                                            n=label + "_volume_pow")
                cmds.setAttr(vol_pow + ".operation", 3)  # power
                cmds.connectAttr(ik_gate + ".output", vol_pow + ".input1X")
                cmds.setAttr(vol_pow + ".input2X", -0.5)  # 1/sqrt
                # volume で 1.0 (no compress) ↔ pow (full compress) blend
                vol_bta = cmds.createNode("blendTwoAttr",
                                           n=label + "_volume_blend")
                cmds.setAttr(vol_bta + ".input[0]", 1.0)
                cmds.connectAttr(vol_pow + ".outputX", vol_bta + ".input[1]")
                cmds.connectAttr(ui_host + ".volume",
                                  vol_bta + ".attributesBlender")
                # hero bones (mid, end のみ、twist は skinning 補間で自然追従)
                for h in (mid, end):
                    if not cmds.objExists(h):
                        continue
                    for _ax in ("X", "Z"):
                        try:
                            cmds.connectAttr(vol_bta + ".output",
                                              h + ".scale" + _ax, f=True)
                        except Exception:
                            pass
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] volume setup failed: {exc}")
    except Exception as exc:
        cmds.warning(f"[attach_ctrls] stretch setup failed for {label}: {exc}")

    # (probe removed)
    # --- 8. Visibility (UI host の ikVis/fkVis で明示制御) ---
    # (AUDIT #14: setAttr(ik_ctl.v, lock=False) は dead code だったので削除)
    try:
        cmds.connectAttr(ui_host + ".ikVis", ik_npo + ".v")
        cmds.connectAttr(ui_host + ".ikVis", pv_npo + ".v")
        cmds.connectAttr(ui_host + ".fkVis", fk_ctls[0][0] + ".v")
        # ikVis/fkVis を IK_FK に自動追従 (デフォルト): switch=1 → ikVis=1, fkVis=0
        cmds.connectAttr(ui_host + ".IK_FK", ui_host + ".ikVis", f=True)
        cmds.connectAttr(rev + ".outputX", ui_host + ".fkVis", f=True)
    except Exception as exc:
        cmds.warning(f"[attach_ctrls] visibility connect failed: {exc}")

    print(f"[{_PACKAGE}] IK/FK rig: {label}  IK={ik_ctl}  PV={pv_ctl}  "
          f"FK={[c for _, c in fk_ctls]}  switch={ui_host}.IK_FK")

    return {
        "label":     label,
        "ik_chain":  ik_chain,
        "fk_chain":  fk_chain,
        "ik_handle": ik_handle,
        "ik_ctl":    ik_ctl,
        "pv_ctl":    pv_ctl,
        "fk_ctls":   [c for _, c in fk_ctls],
        "ui_host":   ui_host,
        "switch":    ui_host + ".IK_FK",  # UI host が master
    }


# --- チェーン自動検出 & 一括セットアップ ------------------------------

# 対応命名 (rig ソース別):
#   MMD/日本語 rename: arm_L, elbow_L, wrist_L / leg_L, knee_L, ankle_L
#   Mixamo:            LeftArm, LeftForeArm, LeftHand / LeftUpLeg, LeftLeg, LeftFoot
#   Unreal/UE:         upperarm_l, lowerarm_l, hand_l / thigh_l, calf_l, foot_l
#   Blender T-pose:    arm.L, forearm.L, hand.L / thigh.L, shin.L, foot.L
#   汎用:              upperarm/forearm/hand, thigh/shin/foot

_ARM_CANDIDATES = [
    # MMD rename 系
    ("arm",       "elbow",     "wrist"),
    ("shoulder",  "elbow",     "wrist"),
    # 汎用英語
    ("upperarm",  "forearm",   "hand"),
    ("upperarm",  "lowerarm",  "hand"),
]
_LEG_CANDIDATES = [
    # MMD rename 系
    ("leg",       "knee",      "ankle"),
    # 汎用英語
    ("upleg",     "leg",       "foot"),
    ("upleg",     "knee",      "foot"),
    ("thigh",     "shin",      "foot"),
    ("thigh",     "calf",      "foot"),
]

# side 表記のバリエーション (`_L` / `L_` / `.L` / `_l` 等)
def _side_variants(base, side):
    """base='arm', side='L' → ['arm_L', 'arm_l', 'L_arm', 'l_arm',
                                 'arm.L', 'arm.l', 'LeftArm', 'leftArm']"""
    s_up = side.upper()
    s_lo = side.lower()
    full = {"L": "Left", "R": "Right"}.get(s_up, "")
    base_cap = base[:1].upper() + base[1:] if base else base
    variants = [
        f"{base}_{s_up}",   # arm_L
        f"{base}_{s_lo}",   # arm_l  (UE)
        f"{s_up}_{base}",   # L_arm
        f"{s_lo}_{base}",   # l_arm
        f"{base}.{s_up}",   # arm.L  (Blender)
        f"{base}.{s_lo}",   # arm.l
    ]
    if full:
        variants += [
            f"{full}{base_cap}",  # LeftArm (Mixamo)
            f"{full.lower()}{base_cap}",  # leftArm
        ]
    return variants


def _try_find(triples, side):
    """triples の各組合せを各 side バリエーションで試す。"""
    for start_kw, mid_kw, end_kw in triples:
        for s_var, m_var, e_var in zip(
                _side_variants(start_kw, side),
                _side_variants(mid_kw, side),
                _side_variants(end_kw, side)):
            chain = _find_chain_by_names([s_var, m_var, e_var])
            if chain:
                return chain
    return None


def find_ik_chains():
    """L/R arm と L/R leg を自動検出して { "L_arm": [j,j,j], ... } を返す。
    複数命名規則 (MMD rename / Mixamo / Unreal / Blender) 全部試す。"""
    out = {}
    for side in ("L", "R"):
        a = _try_find(_ARM_CANDIDATES, side)
        if a:
            out[f"{side}_arm"] = a
        lg = _try_find(_LEG_CANDIDATES, side)
        if lg:
            out[f"{side}_leg"] = lg
    return out


# =========================================================================
# Manual chain mapping (v0.9.31)
# =========================================================================
#
# 目的: 命名規則が特殊 (UDE/HIJI/TE 等) なキャラでも user が手動で joint を
# 割り当てて確実に rig を組めるようにする。auto-detect (find_ik_chains) は
# 初期値プリセットとしてのみ使い、失敗しても user が UI で補正可能。
#
# データ構造 (attach_ctrls_grp.mappingJson に JSON で保存):
#   {
#     "fixed": {                          # 固定長 3 joint chain (IK/FK 対象)
#       "arm_L": ["arm_L", "elbow_L", "wrist_L"],
#       "arm_R": [...],
#       "leg_L": [...],
#       "leg_R": [...]
#     },
#     "chains": {                         # 可変長 chain (spine/tail/etc)
#       "spine": ["waist", "upper_body", "chest"],
#       "tail":  ["tail_1", "tail_2", "tail_3", "tail_4"]
#     }
#   }
#
# fixed は IK/FK rig 生成に使う。chains は選択順を記録するだけで、
# 現状は setup 対象外だが将来 spline IK / dynamics / mirror 順序等で消費予定。

MAPPING_ATTR = "mappingJson"
FIXED_LABELS = ("arm_L", "arm_R", "leg_L", "leg_R")


def _mapping_container():
    """mapping JSON を保持する transform を返す (無ければ ROOT_GROUP)。
    ROOT_GROUP が未生成の段階でも呼べるようにする。"""
    if cmds.objExists(ROOT_GROUP):
        return ROOT_GROUP
    return None


def get_mapping():
    """scene から mapping dict を読む。無ければ空の template を返す。"""
    import json as _json
    holder = _mapping_container()
    if holder and cmds.attributeQuery(MAPPING_ATTR, node=holder, exists=True):
        raw = cmds.getAttr(f"{holder}.{MAPPING_ATTR}") or ""
        if raw:
            try:
                data = _json.loads(raw)
                # 正規化 (欠損 key を補う)
                data.setdefault("fixed", {})
                data.setdefault("chains", {})
                return data
            except Exception as exc:
                cmds.warning(f"[{_PACKAGE}] mappingJson parse failed: {exc}")
    return {"fixed": {}, "chains": {}}


def set_mapping(mapping):
    """mapping dict を scene attr に JSON で保存。ROOT_GROUP が無ければ作成。"""
    import json as _json
    if not cmds.objExists(ROOT_GROUP):
        cmds.group(em=True, name=ROOT_GROUP)
    if not cmds.attributeQuery(MAPPING_ATTR, node=ROOT_GROUP, exists=True):
        cmds.addAttr(ROOT_GROUP, ln=MAPPING_ATTR, dt="string")
    payload = _json.dumps(mapping, ensure_ascii=False, indent=2)
    cmds.setAttr(f"{ROOT_GROUP}.{MAPPING_ATTR}", payload, type="string")
    print(f"[{_PACKAGE}] mapping saved to {ROOT_GROUP}.{MAPPING_ATTR}")


def auto_detect_mapping():
    """命名規則 heuristic で mapping を自動推定。
    fixed は find_ik_chains の結果を label 統一 (arm_L etc) して詰める。
    chains は現状ヒューリスティックを持たないので空。"""
    detected = {"fixed": {}, "chains": {}}
    raw = find_ik_chains()  # {"L_arm": [j,j,j], ...}
    for key, joints in raw.items():
        # "L_arm" → "arm_L"
        side = key[0]
        part = key[2:]  # "arm" or "leg"
        label = f"{part}_{side}"
        if label in FIXED_LABELS and len(joints) >= 3:
            detected["fixed"][label] = list(joints[:3])
    return detected


def resolve_chains_for_ikfk(mapping=None):
    """IK/FK setup が消費する形式 { label: [start,mid,end] } を返す。
    優先順位:
      1. 明示 mapping 引数 (UI 経由の指定)
      2. scene attr (get_mapping)
      3. auto_detect_mapping (fallback)
    fixed セクションのみ返す (可変 chain は IK/FK 対象外)。

    mapping は 2 形式受付 (setup_all_ik_fk と対称):
      (a) {"fixed": {label: [j,j,j]}, "chains": {...}}  完全形
      (b) {label: [j,j,j]}                              fixed のみのフラット形
    """
    if mapping is None:
        mapping = get_mapping()
    # (a) / (b) 両対応
    if isinstance(mapping, dict) and "fixed" in mapping \
            and isinstance(mapping["fixed"], dict):
        fixed = mapping["fixed"]
    else:
        # フラット形 or 空 dict → 全 key を fixed とみなす
        fixed = {k: v for k, v in (mapping or {}).items()
                 if k in FIXED_LABELS}
    # 空なら auto-detect fallback
    if not fixed:
        auto = auto_detect_mapping()
        fixed = auto.get("fixed") or {}
    # joint 存在チェック
    out = {}
    for label, joints in fixed.items():
        if len(joints) < 3:
            continue
        if all(cmds.objExists(j) for j in joints[:3]):
            out[label] = list(joints[:3])
        else:
            missing = [j for j in joints[:3] if not cmds.objExists(j)]
            cmds.warning(f"[{_PACKAGE}] mapping '{label}' の joint 不在 "
                         f"{missing} → skip")
    return out


def _detect_foot_landmarks(ankle_joint, toe_joint):
    """足メッシュから heel/tip/ball 位置を skinCluster + vertex サンプリングで検出。

    汎用アルゴリズム (どのモデルでも動く):
      1. ankle_joint に接続された skinCluster を探索 (無ければ 全 skinCluster から)
      2. ankle + 子孫 joint に weight > 0.5 の vertex 収集 (最大 500 サンプル)
      3. Y filter: ankle.y 以下 (boot 上端 vertex 除去)
      4. 足底 Y = filtered vertex の min Y
      5. Forward = (toe - ankle) XZ 正規化
      6. Heel = forward 投影最負 / Tip = 最正
      7. Ball = 足底近傍 & forward 正 & tip の 85% 以内で最後方

    Returns: {heel, tip, ball, ground_y} or None (skinCluster 無い等の失敗時)。
    """
    ankle_pos = cmds.xform(ankle_joint, q=True, ws=True, t=True)
    toe_pos = cmds.xform(toe_joint, q=True, ws=True, t=True)

    fwd_x = toe_pos[0] - ankle_pos[0]
    fwd_z = toe_pos[2] - ankle_pos[2]
    fwd_mag = (fwd_x**2 + fwd_z**2) ** 0.5
    if fwd_mag < 0.01:
        return None
    fwd_x /= fwd_mag
    fwd_z /= fwd_mag

    # 1. skinCluster 検出
    scs = cmds.listConnections(ankle_joint + ".worldMatrix",
                                type="skinCluster", d=True, s=False) or []
    scs = list(set(scs))
    if not scs:
        for sc in cmds.ls(type="skinCluster") or []:
            try:
                infs = cmds.skinCluster(sc, q=True, inf=True) or []
                if ankle_joint in infs:
                    scs.append(sc)
            except Exception:
                pass
    if not scs:
        return None

    # 2. ankle + descendant joint
    influences = {ankle_joint}
    for j in cmds.listRelatives(ankle_joint, ad=True, type="joint") or []:
        influences.add(j)

    # 3. 影響 vertex 収集
    candidates = []
    for sc in scs:
        try:
            geo = cmds.skinCluster(sc, q=True, g=True) or []
            if not geo:
                continue
            mesh_shape = geo[0]
        except Exception:
            continue
        try:
            n_verts = cmds.polyEvaluate(mesh_shape, v=True)
        except Exception:
            n_verts = 0
        if not n_verts:
            continue
        try:
            sc_infs = cmds.skinCluster(sc, q=True, inf=True) or []
        except Exception:
            sc_infs = []
        # ankle 世界位置近傍の vertex に絞ってから weight 判定 (計算量削減 + 精度向上)
        # 予め ankle 中心の radius = bone_length * 3 で bbox pre-filter
        toe_dist = ((ankle_pos[0]-toe_pos[0])**2 +
                    (ankle_pos[1]-toe_pos[1])**2 +
                    (ankle_pos[2]-toe_pos[2])**2) ** 0.5
        radius = max(toe_dist * 2.5, 5.0)
        target_ids = [i for i, inf in enumerate(sc_infs) if inf in influences]
        if not target_ids:
            continue
        # 全 vertex を走査 (mesh に対して 1 回だけ) → radius filter → weight 判定
        for vi in range(n_verts):
            try:
                wp = cmds.pointPosition(f"{mesh_shape}.vtx[{vi}]", w=True)
                # ankle からの距離で早期スキップ
                dx = wp[0] - ankle_pos[0]
                dy = wp[1] - ankle_pos[1]
                dz = wp[2] - ankle_pos[2]
                if dx*dx + dy*dy + dz*dz > radius * radius:
                    continue
                weights = cmds.skinPercent(sc, f"{mesh_shape}.vtx[{vi}]",
                                            q=True, v=True)
                if not weights:
                    continue
                total_w = sum(weights[i] for i in target_ids if i < len(weights))
                if total_w > 0.5:
                    candidates.append(wp)
            except Exception:
                continue

    if not candidates:
        return None

    # 4. Y filter (2 段階)
    #    (a) ankle 以下の大まかフィルタ (装飾 vertex 除去のため必要)
    below = [v for v in candidates if v[1] < ankle_pos[1]]
    if not below:
        # v0.9.20 リバースフット robustness: candidates に ankle 以下の vertex
        # が 1 個も無い場合、スキニング破綻 (e.g. 上半身 vertex が ankle_L に
        # 誤って高い weight を持っている) → 検出失敗として fallback に委ねる。
        print(f"[{_PACKAGE}] foot landmarks: no verts below ankle ({ankle_pos[1]:.2f}), "
              f"skinning anomaly; fall back to geometric approximation")
        return None
    ground_y = min(v[1] for v in below)
    # ground_y は ankle より十分下 (少なくとも bone 長の 20% 以上) であるべき。
    # 上に来ている場合はサニティ違反 → fallback に委ねる。
    toe_dist = ((ankle_pos[0]-toe_pos[0])**2 + (ankle_pos[1]-toe_pos[1])**2
                + (ankle_pos[2]-toe_pos[2])**2) ** 0.5
    if ankle_pos[1] - ground_y < toe_dist * 0.2:
        print(f"[{_PACKAGE}] foot landmarks: ground_y={ground_y:.2f} too close to ankle "
              f"Y={ankle_pos[1]:.2f} (diff < 20% of {toe_dist:.2f}) → fallback")
        return None

    #    (b) 床帯厳格フィルタ: ground から ankle Y までの下 25% 帯のみを heel/tip 判定に使う。
    #        これで装飾骨 (後方バルジ Y=3.6) が heel と誤判定される問題を回避。
    floor_band_tol = (ankle_pos[1] - ground_y) * 0.25
    floor_band = [v for v in below if v[1] < ground_y + floor_band_tol]
    if len(floor_band) < 3:
        # フォールバック: 床帯 vertex 少なすぎ → 下 50%
        floor_band = [v for v in below if v[1] < ground_y + (ankle_pos[1] - ground_y) * 0.5]
    if not floor_band:
        floor_band = below

    def _fwd_proj(v):
        return (v[0] - ankle_pos[0]) * fwd_x + (v[2] - ankle_pos[2]) * fwd_z

    heel_v = min(floor_band, key=_fwd_proj)
    tip_v  = max(floor_band, key=_fwd_proj)

    # (c) heel 距離クランプ: |heel_proj| > toe_dist * 0.6 は装飾骨混入と判定、
    #     幾何近似に戻す (ankle → toe 反対方向 0.4 倍)
    toe_dist = ((toe_pos[0]-ankle_pos[0])**2 + (toe_pos[2]-ankle_pos[2])**2)**0.5
    heel_proj = _fwd_proj(heel_v)
    if abs(heel_proj) > toe_dist * 0.6:
        heel_v = [ankle_pos[0] - fwd_x * toe_dist * 0.4,
                  ground_y,
                  ankle_pos[2] - fwd_z * toe_dist * 0.4]

    tip_proj = _fwd_proj(tip_v)
    tol = (ankle_pos[1] - ground_y) * 0.15
    floor = [v for v in below
             if v[1] < ground_y + tol
             and _fwd_proj(v) > 0
             and _fwd_proj(v) < tip_proj * 0.85]
    if floor:
        ball_v = min(floor, key=_fwd_proj)
    else:
        ball_v = [(ankle_pos[0] + tip_v[0]) * 0.5,
                  ground_y,
                  (ankle_pos[2] + tip_v[2]) * 0.5]

    # (d) heel/tip の Y を ground_y に射影して pivot ctl が床面に来るように
    heel_out = [heel_v[0], ground_y, heel_v[2]]
    tip_out  = [tip_v[0],  ground_y, tip_v[2]]
    ball_out = [ball_v[0], ground_y, ball_v[2]]

    print(f"[{_PACKAGE}] foot landmarks from {len(candidates)} verts "
          f"(floor band {len(floor_band)}): "
          f"heel Z={heel_out[2]:.2f}, tip Z={tip_out[2]:.2f}, ground={ground_y:.2f}")
    return {
        "heel": heel_out,
        "tip":  tip_out,
        "ball": ball_out,
        "ground_y": ground_y,
    }


def _find_toe_joint(ankle_joint):
    """ankle_joint の child joint から toe を推定 (汎用検出)。
    優先: 「toe」を含む名前 > child joint 1 個目
    無ければ None。dummy/shadow/cancel は除外 (MMD 派生の非本命骨)。"""
    kids = cmds.listRelatives(ankle_joint, c=True, type="joint") or []
    if not kids:
        return None
    _skip = ("dummy", "shadow", "cancel", "sub_", "ik_", "fk_", "twist")
    def valid(k):
        short = k.split("|")[-1].lower()
        return not any(s in short for s in _skip) and not short.endswith("_end")
    for k in kids:
        short = k.split("|")[-1].lower()
        if not valid(k):
            continue
        if any(t in short for t in ("toe", "foot", "tip")):
            return k
    # フォールバック: 除外語無しで _end でない最初の child joint
    for k in kids:
        if valid(k):
            return k
    # 最後の手段
    return kids[0]


def _create_rfoot_bones(ankle_joint, landmarks):
    """ball 骨が無いモデル (MMD 系等) 向けに reverse foot 用 helper joint を生成。

    生成階層 (ankle_joint 直下、skinCluster には追加しない):
      ankle_joint (hero, skinned)
      └── ankle_joint_rfBallBone
           └── ankle_joint_rfToeBone

    Args:
        ankle_joint: skin される ankle
        landmarks: `_detect_foot_landmarks` の返り値 dict (heel/ball/tip/ground_y)

    Returns:
        dict {"rf_ball": name, "rf_toe": name} or None (landmarks 無効時)
    """
    if not landmarks:
        return None
    ball_pos = landmarks.get("ball")
    tip_pos = landmarks.get("tip")
    if not ball_pos or not tip_pos:
        return None
    # Y は地面 (ground_y) 高さ。ankle Y に置くと rf chain と toe_piv (地面)
    # の乖離が大きく SC 解が大角度スイングして hero toe が地面下 -3.6 unit
    # まで沈む (RFOOT2 scout P2)。ground_y に揃えると rf chain が地面沿いで
    # 動き、hero toe の drift も小さく収まる。
    rf_y = landmarks.get("ground_y",
                          cmds.xform(ankle_joint, q=True, ws=True, t=True)[1])
    ball_ws = (ball_pos[0], rf_y, ball_pos[2])
    toe_ws = (tip_pos[0], rf_y, tip_pos[2])
    rf_ball = ankle_joint + "_rfBallBone"
    rf_toe = ankle_joint + "_rfToeBone"
    if cmds.objExists(rf_ball):
        try: cmds.delete(rf_ball)
        except Exception: pass
    # 生成: 一旦 selection をクリアして正確な parent を得る
    try:
        cmds.select(ankle_joint, r=True)
        b = cmds.joint(n=rf_ball, p=ball_ws)
        t = cmds.joint(n=rf_toe, p=toe_ws)
        # aim を X+ 前方に固定 (bind aim を rest pose として orient joint)
        cmds.select(b, r=True)
        cmds.joint(b, e=True, oj="xyz", sao="yup", ch=True, zso=True)
        cmds.setAttr(b + ".radius", 0.016)
        cmds.setAttr(t + ".radius", 0.016)
        # 描画を控えめに (viewport ノイズ削減)
        try:
            cmds.setAttr(b + ".drawStyle", 2)
            cmds.setAttr(t + ".drawStyle", 2)
        except Exception:
            pass
        return {"rf_ball": b, "rf_toe": t}
    except Exception as exc:
        cmds.warning(f"[{_PACKAGE}] _create_rfoot_bones failed: {exc}")
        return None


def setup_reverse_foot(ankle_joint, foot_ik_ctl, foot_ikh, side="C"):
    """Reverse foot rig: heel/ball/toe の pivot chain と footRoll attr を付与。

    汎用: ankle joint の child joint を toe として自動検出 (MMD の `toe_L`、
    Mixamo の `LeftToeBase`、T-pose export の `foot_end` 等いずれも対応)。
    heel 位置は ankle 位置 - (toe - ankle) の 0.4 倍 後方 で概算、
    Y は mesh 全体の bbox 最下端 (足元) を採用。
    """
    toe_joint = _find_toe_joint(ankle_joint)
    if toe_joint is None:
        cmds.warning(f"[{_PACKAGE}] setup_reverse_foot: no toe child under {ankle_joint}")
        return None

    ankle_pos = cmds.xform(ankle_joint, q=True, ws=True, t=True)
    toe_pos = cmds.xform(toe_joint, q=True, ws=True, t=True)

    # mesh-aware 検出 (skinCluster + vertex サンプリング)
    landmarks = _detect_foot_landmarks(ankle_joint, toe_joint)

    # ball 骨が hero chain に存在しないモデル (MMD 等) 向けに、reverse foot
    # 用 helper joint (ankle_L_rfBallBone / _rfToeBone) を ankle 直下に生成。
    # 通常の setup (toe_L_ik dup + SC toe_ikh) は skin joint toe に IK 影響を
    # 直接与えるため toe rotation の副作用が読みにくい。rfBone 系は独立 chain
    # として toe_ikh の pivot 用に使い、hero toe は parentConstraint(mo=True)
    # で追従させる (RFBONE scout 提案)。
    rf_bones = None
    has_ball_child = False
    for k in cmds.listRelatives(ankle_joint, c=True, type="joint") or []:
        if "ball" in k.lower():
            has_ball_child = True; break
    if not has_ball_child and landmarks:
        rf_bones = _create_rfoot_bones(ankle_joint, landmarks)
    if landmarks:
        # X は ankle_pos[0] にクランプして L/R bank 対称化
        # (heel_pos の X が足外縁 3 unit 外側になり bank が非対称になる問題、
        # RFOOT2 scout P3 の対応)
        ax = ankle_pos[0]
        heel_pos      = [ax, landmarks["ground_y"], landmarks["heel"][2]]
        ball_pos      = [ax, landmarks["ground_y"], landmarks["ball"][2]]
        toe_pivot_pos = [ax, landmarks["ground_y"], landmarks["tip"][2]]
        ground_y      = landmarks["ground_y"]
    else:
        # フォールバック: skinCluster 無い / 検出失敗 → 幾何近似 + scene bbox Y
        ground_y = 0.0
        try:
            meshes = cmds.ls(type="mesh") or []
            tforms = set()
            for m in meshes:
                p = cmds.listRelatives(m, p=True, f=True) or []
                if p: tforms.add(p[0])
            if tforms:
                bb = cmds.exactWorldBoundingBox(list(tforms))
                ground_y = bb[1]
        except Exception:
            pass
        back_vec = [ankle_pos[i] - toe_pos[i] for i in range(3)]
        heel_pos = [ankle_pos[0] + back_vec[0] * 0.4,
                    ground_y,
                    ankle_pos[2] + back_vec[2] * 0.4]
        ball_pos = [(ankle_pos[0] + toe_pos[0]) * 0.5,
                    ground_y,
                    (ankle_pos[2] + toe_pos[2]) * 0.5]
        toe_pivot_pos = [toe_pos[0], ground_y, toe_pos[2]]
        print(f"[{_PACKAGE}] foot landmarks fallback (no skinCluster on {ankle_joint})")

    label = ankle_joint  # e.g. ankle_L
    color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]

    # 可視 ctl として heel/tip/ball を生成 (mGear の heel_ctl/tip_ctl/bk*_ctl 相当)
    # size は toe-ankle 距離ベース
    piv_size = ((ankle_pos[0]-toe_pos[0])**2 + (ankle_pos[2]-toe_pos[2])**2)**0.5 * 0.3
    piv_size = max(piv_size, 0.5)

    # NOTE: translate lock は **全ての parent 操作が終わってから** 実行する。
    # parent 前に tx/ty/tz を lock すると `cmds.parent` が world 位置を保存できず
    # pivot ツリーが飛び、後段の toe_ikh が ankle を 100° 回転させる致命バグに繋がる
    # (FOOTROT scout で確定原因)。ここでは scale だけ lock、translate は後で。
    # pivot ctl は地面に置く flat_box にして cube 埋没 (P4) を回避
    heel_piv = _make_flat_box_curve(label + "_heel_ctl", scale=piv_size,
                                    x_ratio=1.0, z_ratio=1.0)
    _set_ctl_color(heel_piv, color)
    cmds.xform(heel_piv, ws=True, t=heel_pos)
    _lock_hide_attrs(heel_piv, ["sx","sy","sz"])

    toe_piv = _make_flat_box_curve(label + "_tip_ctl", scale=piv_size,
                                    x_ratio=1.0, z_ratio=1.0)
    _set_ctl_color(toe_piv, color)
    cmds.xform(toe_piv, ws=True, t=toe_pivot_pos)
    cmds.parent(toe_piv, heel_piv)
    _lock_hide_attrs(toe_piv, ["sx","sy","sz"])

    ball_piv = _make_flat_box_curve(label + "_ball_ctl", scale=piv_size * 0.8,
                                     x_ratio=1.0, z_ratio=1.0)
    _set_ctl_color(ball_piv, color)
    cmds.xform(ball_piv, ws=True, t=ball_pos)
    cmds.parent(ball_piv, toe_piv)
    _lock_hide_attrs(ball_piv, ["sx","sy","sz"])

    # 既存の pointConstraint (ik_ctl -> foot_ikh) を削除。
    # そうしないと pivot chain の rotation で handle 位置が動いても
    # constraint が override して IK が再ソルブしない。
    pt_cons = cmds.listConnections(foot_ikh + ".translateX",
                                    s=True, d=False, type="pointConstraint") or []
    for pc in set(pt_cons):
        try: cmds.delete(pc)
        except Exception: pass

    # foot IK handle を ball_piv の下に。以降は pivot chain の world transform
    # (heel -> toe -> ball) だけで handle が動く。
    try:
        cmds.parent(foot_ikh, ball_piv)
    except Exception:
        pass

    # ankle -> toe の SC IK handle (toe を持ち上げる用)
    # 元 hero ankle に張ると orient blend と競合し ankle world Y 軸が回されて
    # 「足が縦になる」問題を起こす (TWIST scout 発見)。ik_chain 側の ankle_ik
    # に対応する joint に張って副作用を避ける。もし ankle_ik が無ければ skip。
    ankle_ik = ankle_joint + "_ik"
    toe_ikh = None

    if rf_bones:
        # 自作 rfoot chain (rfBallBone → rfToeBone) に SC ikh を張って toe_piv
        # 下にペアレント。hero toe は rfToeBone を mo=True で追従、bind 位置
        # 保持しつつ ball_ctl 回転が hero toe に伝わる。ankle は既存 leg-IK に
        # 触れない (rotate は leg-IK が占有)。
        rf_ball = rf_bones["rf_ball"]; rf_toe = rf_bones["rf_toe"]
        try:
            toe_ikh = cmds.ikHandle(sj=rf_ball, ee=rf_toe,
                                    sol="ikSCsolver", n=label + "_rfToeIkh")[0]
            cmds.parent(toe_ikh, toe_piv)
            if cmds.objExists(toe_joint):
                # v0.9.9-2 (AUDIT2 P0-C 回帰): 単純 orientConstraint 追加は
                # `Object is already connected` で失敗、あるいは既存の rotate
                # 接続を上書きして FK ctl 無効化していた。正しい構造:
                # 既存 parentConstraint (toe_L_ctl → toe_L) を **一旦 delete**
                # し、2-source parentConstraint(toe_L_ctl, rf_toe → toe_L,
                # mo=True) で作り直す。weight を IK_FK で切替:
                #   FK モード (IK_FK=0) → toe_L_ctl weight=1、rf_toe=0
                #   IK モード (IK_FK=1) → toe_L_ctl weight=0、rf_toe=1
                _leg_lbl = "leg_L" if side == "L" else "leg_R" if side == "R" else "leg_C"
                _ui_host_name = _leg_lbl + "_UI_ctl"
                try:
                    # 既存 parentConstraint (toe_L_ctl → toe_L) を探して削除。
                    # attach_controllers Pass 3 が `<jnt>_parentConstraint` で作る。
                    existing = cmds.listConnections(toe_joint + ".rotateX",
                                                    s=True, d=False,
                                                    type="parentConstraint") or []
                    fk_source_ctl = None
                    for con in existing:
                        # source ctl を記録してから削除
                        tgs = cmds.parentConstraint(con, q=True, tl=True) or []
                        if tgs:
                            fk_source_ctl = tgs[0]
                        try: cmds.delete(con)
                        except Exception: pass
                    if fk_source_ctl and cmds.objExists(fk_source_ctl):
                        pc = cmds.parentConstraint(fk_source_ctl, rf_toe,
                                                    toe_joint, mo=True,
                                                    n=toe_joint + "_ikfk_pc")[0]
                        wal = cmds.parentConstraint(pc, q=True, wal=True) or []
                        if len(wal) >= 2 and cmds.objExists(_ui_host_name) and \
                                cmds.attributeQuery("IK_FK", node=_ui_host_name,
                                                     exists=True):
                            # rev: IK_FK=0 → FK weight=1, IK_FK=1 → IK weight=1
                            rev_n = cmds.createNode("reverse",
                                                     n=toe_joint + "_ikfk_pc_rev")
                            cmds.connectAttr(_ui_host_name + ".IK_FK",
                                              rev_n + ".inputX")
                            cmds.connectAttr(rev_n + ".outputX",
                                              pc + "." + wal[0], f=True)  # FK
                            cmds.connectAttr(_ui_host_name + ".IK_FK",
                                              pc + "." + wal[1], f=True)  # IK
                except Exception as e:
                    cmds.warning(f"[attach_ctrls] toe ikfk_pc: {e}")
        except Exception as exc:
            cmds.warning(f"[attach_ctrls] rfToe ikHandle failed: {exc}")
            toe_ikh = None
    else:
        # ball 骨が既に存在する (mixamo 等) か landmark 検出失敗時: 従来経路
        # (toe_L_ik を ankle_ik 子として dup + SC ikh)
        toe_ik = toe_joint + "_ik"
        if cmds.objExists(ankle_ik) and not cmds.objExists(toe_ik):
            try:
                toe_ik = _dup_hero_joint(toe_joint, "_ik", new_parent=ankle_ik)
                try: cmds.setAttr(toe_ik + ".drawStyle", 2)
                except Exception: pass
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] toe_ik dup failed: {exc}")
                toe_ik = None
        if ankle_ik and toe_ik and cmds.objExists(ankle_ik) and cmds.objExists(toe_ik):
            try:
                toe_ikh = cmds.ikHandle(sj=ankle_ik, ee=toe_ik,
                                        sol="ikSCsolver", n=label + "_toeIkh")[0]
                cmds.parent(toe_ikh, toe_piv)
                if cmds.objExists(toe_joint):
                    for con in (cmds.listConnections(toe_joint + ".translateX",
                                s=True, d=False, type="constraint") or []):
                        try: cmds.delete(con)
                        except Exception: pass
                    try:
                        cmds.parentConstraint(toe_ik, toe_joint, mo=False,
                                              n=toe_joint + "_ikfk_pc")
                    except Exception as e:
                        cmds.warning(f"[attach_ctrls] toe pc: {e}")
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] toe ikHandle failed: {exc}")
                toe_ikh = None
        else:
            cmds.warning(f"[attach_ctrls] toe ikHandle skipped: no ankle_ik/toe_ik")
            toe_ikh = None

    # heel_piv を foot IK ctl の下に置くと ctl (=ankle joint rot 継承) の局所軸で
    # pivot が回転してしまい footRoll が水平スライドになる。ctl と heel_piv の間に
    # world-aligned な offset group を挟んで world 軸で回転させる。
    # NOTE: os=True, ro=(0,0,0) は local=0 = parent (=ctl) rot 継承なので逆効果。
    # ws=True, ro=(0,0,0) で world 軸に揃えると heel_piv の local rot が baked
    # せず、後段の connectAttr(heelRoll→heel.rotateX) が rot 破壊しない
    # (ankle 2.25 unit drift の真因)。
    piv_root = cmds.group(em=True, n=ankle_joint + "_pivRoot")
    cmds.parent(piv_root, foot_ik_ctl)
    cmds.xform(piv_root, ws=True, t=cmds.xform(foot_ik_ctl, q=True, ws=True, t=True),
                ro=(0, 0, 0))
    try:
        cmds.parent(heel_piv, piv_root)
    except Exception:
        pass

    # 全 parent 完了後に tx/ty/tz を lock (順序が逆だと world 位置が飛ぶ)
    for piv in (heel_piv, toe_piv, ball_piv):
        _lock_hide_attrs(piv, ["tx","ty","tz"])

    # ユーザ要望: attr 集約 (footRoll 等) をやめ、pivot ctl (heel/ball/toe) を
    # 直接掴んで rotate する方式に。attr connect が rotate を占有していると
    # ユーザが動かせないため、connectAttr は行わない。rotate は _lock_hide_attrs
    # で translate だけロックしてあるので自由に触れる。
    # tip: heel/toe は主に rotateX、bank は heel の rotateZ で直感的に操作可。

    print(f"[{_PACKAGE}] Reverse foot: {ankle_joint} heel/ball/toe pivots "
          f"(direct rotate control, no UI attrs)")
    return {
        "heel_piv": heel_piv, "ball_piv": ball_piv, "toe_piv": toe_piv,
        "toe_ikh": toe_ikh,
    }


def _create_ui_host_ctl(label, world_pos, size, side):
    """IK/FK Switch / foot roll 等の attr を集約する option ctl。

    ユーザ意向 (v0.9.7): 「手首の近くにあるオプションコントローラーみたい
    なやつからそのへんは制御したい」→ 目立つ形状にして wrist/ankle 側に
    明確に配置。mGear の armUI 相当だが text ではなく八角+内部十字で
    ・見つけやすさ (colored bright)
    ・ここに attr が集約されてる感 (円+cross = "settings" icon 風)
    を出す。
    """
    host_name = label + "_UI_ctl"
    if cmds.objExists(host_name):
        return host_name
    import math as _mm
    s = size * 0.9  # v0.9.6 以前の井桁より大きめ、目立たせる
    # 外周: octagon (8 sides + close)
    sides = 8
    pts = [(s * _mm.cos(2*_mm.pi*i/sides),
            0,
            s * _mm.sin(2*_mm.pi*i/sides)) for i in range(sides + 1)]
    # 内部十字 (見つけやすい settings icon 感)
    cross = [(0,0,0), (s*0.7,0,0), (0,0,0),
             (-s*0.7,0,0), (0,0,0), (0,0,s*0.7),
             (0,0,0), (0,0,-s*0.7)]
    pts.extend(cross)
    host = cmds.curve(d=1, p=pts, n=host_name)
    _set_ctl_color(host, COLOR_UI)
    host_npo = cmds.group(em=True, n=label + "_UI_npo")
    cmds.parent(host, host_npo)
    cmds.xform(host_npo, ws=True, t=world_pos)
    cmds.parent(host_npo, ROOT_GROUP)
    _lock_hide_attrs(host, ["tx","ty","tz","rx","ry","rz","sx","sy","sz"])
    return host


def _resolve_chain_joints(chain_label):
    """chain_label (find_ik_chains のキー、例 'L_arm') か直接 start joint 名から
    (start, mid, end) の joint 名を動的に返す。
    """
    chains = find_ik_chains()
    # まず find_ik_chains のキー (L_arm, R_leg 等) で探す
    for lbl in (chain_label, chain_label.replace("_", "_")):
        if lbl in chains:
            return tuple(chains[lbl])
    # 直接 start joint 名で渡された場合 (arm_L, leg_R 等) → chains 値と一致確認
    for triple in chains.values():
        if triple[0] == chain_label:
            return tuple(triple)
    return None


def snap_fk_to_ik(chain_label):
    """FK ctls を現 IK 姿勢に snap。orient constraint offset 補正付き。"""
    triple = _resolve_chain_joints(chain_label)
    if not triple:
        cmds.warning(f"[attach_ctrls] snap_fk_to_ik: chain '{chain_label}' not found")
        return
    start_j, mid_j, end_j = triple
    for orig in triple:
        fk_ctl = orig + "_fk_ctl"
        if not (cmds.objExists(fk_ctl) and cmds.objExists(orig)):
            continue
        # v0.9.23 snap 誤差修正: `xform ws=True m=` は translate 軸がロック
        # されている fk_ctl に対して translation 部分が失われ、end joint
        # (wrist_L 等) で 0.7 unit ズレが発生していた。orig と fk_chain は
        # 同じ parent 階層構造なので local rotate 値を直接コピーする。
        # orientConstraint(fk_ctl -> fk_chain, mo=False) で WS rotation が
        # 同期し、parentConstraint 経由で orig に完全一致する。
        try:
            orig_rot = cmds.getAttr(orig + ".rotate")[0]
            cmds.setAttr(fk_ctl + ".rotate",
                          orig_rot[0], orig_rot[1], orig_rot[2],
                          type="double3")
        except Exception:
            # フォールバック: 旧経路
            orig_wm = cmds.xform(orig, q=True, ws=True, m=True)
            cmds.xform(fk_ctl, ws=True, m=orig_wm)
    # UI host は start joint に basicallyy 対応。start_j + "_UI_ctl" で探す
    # v0.9.12: IK_FK 直接 (master、旧 `_blend` エイリアス廃止)
    ui = start_j + "_UI_ctl"
    if cmds.objExists(ui) and cmds.attributeQuery("IK_FK", node=ui, exists=True):
        cmds.setAttr(ui + ".IK_FK", 0)
    print(f"[{_PACKAGE}] snap_fk_to_ik: {start_j} -> IK_FK=0")


def _opposite_side_name(ctl_name):
    """`arm_L_ctl` → `arm_R_ctl` 等、side を反転した ctl 名を返す。無ければ None。"""
    if cmds is None: return None
    short = ctl_name.split(":")[-1].split("|")[-1]
    # 命名バリエーション網羅 (_L / _R / L_ / R_ / _L_xxx / _R_xxx)
    candidates = []
    if "_L" in short: candidates.append(short.replace("_L", "_R", 1))
    if "_R" in short: candidates.append(short.replace("_R", "_L", 1))
    if short.startswith("L_"): candidates.append("R_" + short[2:])
    if short.startswith("R_"): candidates.append("L_" + short[2:])
    for cand in candidates:
        if cand != short and cmds.objExists(cand):
            return cand
    return None


def mirror_pose(ctls=None):
    """選択 ctl の TRS を反対側 ctl にコピーし、`invTx..invSz` で反転する。
    mGear の Mirror Pose 相当。v0.9.11 追加。

    Args:
        ctls: 対象 ctl リスト。None なら現在の selection。
    Returns:
        (mirrored_count, skipped_count)
    """
    if cmds is None: return (0, 0)
    if ctls is None:
        ctls = cmds.ls(sl=True, type="transform") or []
    n_ok = 0; n_skip = 0
    for src in ctls:
        dst = _opposite_side_name(src)
        if not dst:
            n_skip += 1; continue
        for a, inv_attr in [("translateX", "invTx"), ("translateY", "invTy"),
                             ("translateZ", "invTz"),
                             ("rotateX", "invRx"), ("rotateY", "invRy"),
                             ("rotateZ", "invRz"),
                             ("scaleX", "invSx"), ("scaleY", "invSy"),
                             ("scaleZ", "invSz")]:
            try:
                v = cmds.getAttr(src + "." + a)
                # dst の inv attr で反転判定 (mGear 慣習: dst 側の attr を参照)
                inv = False
                if cmds.attributeQuery(inv_attr, node=dst, exists=True):
                    inv = bool(cmds.getAttr(dst + "." + inv_attr))
                if inv:
                    if a.startswith("scale"):
                        # scale は 1 中心の反転 (通常は不要だが対応)
                        v = 2.0 - v if abs(v) < 5 else v
                    else:
                        v = -v
                try:
                    cmds.setAttr(dst + "." + a, v)
                except Exception:
                    pass  # locked attr 等
            except Exception:
                pass
        n_ok += 1
    print(f"[{_PACKAGE}] mirror_pose: {n_ok} mirrored, {n_skip} skipped")
    return (n_ok, n_skip)


def snap_ik_to_fk(chain_label):
    """IK ctl (+ pole vector) を現 FK 姿勢に snap。"""
    triple = _resolve_chain_joints(chain_label)
    if not triple:
        cmds.warning(f"[attach_ctrls] snap_ik_to_fk: chain '{chain_label}' not found")
        return
    start_j, mid_j, end_j = triple
    ik_ctl = start_j + "_IK_ctl"
    pv_ctl = start_j + "_PV_ctl"
    if cmds.objExists(ik_ctl) and cmds.objExists(end_j):
        # v0.9.23 snap 誤差修正: leg では IK ctl の rotation が pivot chain
        # (heel/tip/ball) 経由で foot_ikh 位置を動かして knee がわずかにズレる
        # (0.13 unit)。IK ctl は translate だけ end WS 位置に合わせ、rotation
        # は end WS rotation に合わせる場合と分けて処理する。arm は wrist
        # 向きを ik_ctl から取るため matrix 全体を転写、leg は translate のみ
        # 転写して pivot が bind orient を維持できるようにする。
        is_leg = "leg" in chain_label.lower()
        if is_leg:
            ep = cmds.xform(end_j, q=True, ws=True, t=True)
            cmds.xform(ik_ctl, ws=True, t=ep)
            # rotation は 0 に (pivot chain が bind orient で foot_ikh の
            # WS が IK ctl WS と一致するように)。
            try:
                cmds.setAttr(ik_ctl + ".rotate", 0, 0, 0, type="double3")
            except Exception:
                pass
        else:
            wm = cmds.xform(end_j, q=True, ws=True, m=True)
            cmds.xform(ik_ctl, ws=True, m=wm)
    if cmds.objExists(pv_ctl) and cmds.objExists(mid_j):
        wp = cmds.xform(mid_j, q=True, ws=True, t=True)
        cmds.xform(pv_ctl, ws=True, t=wp)
    ui = start_j + "_UI_ctl"
    if cmds.objExists(ui) and cmds.attributeQuery("IK_FK", node=ui, exists=True):
        cmds.setAttr(ui + ".IK_FK", 1)
    print(f"[{_PACKAGE}] snap_ik_to_fk: {start_j} -> IK_FK=1")


def _create_twist_segments(parent, child, count=3, prefix=None, side=None):
    """parent → child bone を count 分節する twist joint を parent の子として作成。

    各 twist joint は parent joint の DIRECT CHILD として parent→child 直線上
    に等間隔配置される (i/(count+1) の位置)。既存の parent-child 階層 (parent
    が child の親) は保持され、twist joint は sibling として並ぶ。

    naming: `<prefix>_i_<side>` (i=1..count)
    prefix 省略時: parent の base name から `_twist` を派生
    side 省略時: parent 名末尾の _L/_R/_C から判定

    Returns: 作成した twist joint 名リスト。
    """
    if cmds is None:
        return []
    if not (cmds.objExists(parent) and cmds.objExists(child)):
        return []
    if side is None:
        side = _detect_side(parent)
    if prefix is None:
        # arm_L → "arm_twist"、hand_L → "hand_twist"
        base = _base_name(parent).lower()
        prefix = base + "_twist"

    p_pos = cmds.xform(parent, q=True, ws=True, t=True)
    c_pos = cmds.xform(child, q=True, ws=True, t=True)

    created = []
    for i in range(1, count + 1):
        frac = i / (count + 1)
        pos = [p_pos[k] + (c_pos[k] - p_pos[k]) * frac for k in range(3)]
        name = f"{prefix}_{i}_{side}" if side else f"{prefix}_{i}"
        if cmds.objExists(name):
            # 名前衝突: 既存を再利用
            created.append(name)
            continue
        try:
            cmds.select(parent, r=True)
            j = cmds.joint(n=name, p=pos)
            # v0.9.25: cmds.joint(p=pos) は parent 選択下で pos を LOCAL
            # として扱う挙動があり、parent に jointOrient がある場合 twist
            # bone が期待位置から 10+ unit ズレる (PLAIN-MODEL scout 発見)。
            # WS で強制配置してズレを消す。
            try:
                cmds.xform(j, ws=True, t=pos)
            except Exception:
                pass
            # 親 (parent) の jointOrient を継承しつつ、rotate=0 にリセット
            for a in ("jointOrientX", "jointOrientY", "jointOrientZ"):
                try:
                    v = cmds.getAttr(parent + "." + a)
                    cmds.setAttr(j + "." + a, v)
                except Exception:
                    pass
            cmds.setAttr(j + ".rotate", 0, 0, 0, type="double3")
            # visual: parent と同じ radius、描画なし (viewport ノイズ回避)
            try:
                cmds.setAttr(j + ".radius", cmds.getAttr(parent + ".radius"))
            except Exception:
                pass
            try:
                cmds.setAttr(j + ".drawStyle", 2)  # None
            except Exception:
                pass
            created.append(j)
        except Exception as exc:
            cmds.warning(f"[{_PACKAGE}] create twist joint {name} failed: {exc}")
    return created


def _transfer_parent_weight_to_twist(parent, twist_bones, mesh_shape,
                                       skc, driver_axis=(1.0, 0.0, 0.0)):
    """parent bone の skin weight を twist_bones に bone 軸沿いで proportional 転送。

    各 vertex について:
      1. parent への weight を取得
      2. weight > 閾値 なら、vertex を parent-child (bone) 軸に射影して t 値取得
      3. t を twist_bones の位置範囲 [0..1] にマップ、隣接 2 bone 間で線形補間
      4. parent の weight を interpolated fractions で twist_bones に加算、
         parent の weight は 0 に

    parent の weight が全 vertex で ≒ 0 (skin されていない) なら何もしない
    (Nekotatune のように既存 twist 系が weight を持つケース)。

    Returns: 転送した vertex 数。0 なら parent skinning 無しでスキップ。
    """
    if cmds is None or not twist_bones:
        return 0
    infs = cmds.skinCluster(skc, q=True, inf=True) or []
    if parent not in infs:
        return 0
    # twist_bones を influence に追加 (未含のみ)
    for tb in twist_bones:
        if tb not in infs:
            try:
                cmds.skinCluster(skc, e=True, ai=tb,
                                  lockWeights=False, weight=0)
            except Exception:
                pass

    n_verts = cmds.polyEvaluate(mesh_shape, v=True)
    if not isinstance(n_verts, int):
        return 0

    p_pos = cmds.xform(parent, q=True, ws=True, t=True)
    # twist bones を parent → child 軸沿いで sort、t 値 (fraction) を計算
    tb_info = []
    for tb in twist_bones:
        tb_pos = cmds.xform(tb, q=True, ws=True, t=True)
        vec = [tb_pos[i] - p_pos[i] for i in range(3)]
        # child 方向の長さで正規化するため、driver_axis のスケールで t 取る
        # 実際は twist_bone 位置は既に parent→child 直線上なので、単純に
        # parent からの距離で fraction を計算 (max=1)
        d = sum(v * v for v in vec) ** 0.5
        tb_info.append((tb, tb_pos, d))
    # d 順にソート (parent から近い順)
    tb_info.sort(key=lambda x: x[2])
    max_d = tb_info[-1][2] if tb_info else 1.0
    if max_d < 1e-4:
        return 0

    n_transferred = 0
    for vi in range(n_verts):
        vtx = f"{mesh_shape}.vtx[{vi}]"
        try:
            pw = cmds.skinPercent(skc, vtx, transform=parent, q=True) or 0
        except Exception:
            continue
        if pw < 1e-4:
            continue
        # vertex 位置
        try:
            vp = cmds.pointPosition(vtx, w=True)
        except Exception:
            continue
        vec = [vp[i] - p_pos[i] for i in range(3)]
        d_v = sum(v * v for v in vec) ** 0.5
        t = min(1.0, max(0.0, d_v / max_d))
        # 隣接 2 twist bone 間で線形補間
        # tb_info の d 位置は 0..max_d、tb i の fraction = i_d / max_d
        # 適切な区間 [i, i+1] を探す
        fracs = [(tbi[2] / max_d) for tbi in tb_info]
        weights_split = {}
        placed = False
        for i in range(len(fracs) - 1):
            if fracs[i] <= t <= fracs[i + 1]:
                span = fracs[i + 1] - fracs[i]
                if span < 1e-6:
                    weights_split[tb_info[i][0]] = 1.0
                else:
                    a = (t - fracs[i]) / span
                    weights_split[tb_info[i][0]] = 1.0 - a
                    weights_split[tb_info[i + 1][0]] = a
                placed = True
                break
        if not placed:
            # 範囲外 (parent 側 or child 端側): 最近傍 1 本に集中
            if t < fracs[0]:
                weights_split[tb_info[0][0]] = 1.0
            else:
                weights_split[tb_info[-1][0]] = 1.0

        # skinPercent で weight 更新: parent → 0、各 tb に pw * frac を追加
        try:
            tv = [(parent, 0.0)]
            for tb, frac in weights_split.items():
                # 既存の tb weight に加算するため事前取得
                cur = cmds.skinPercent(skc, vtx, transform=tb, q=True) or 0
                tv.append((tb, cur + pw * frac))
            cmds.skinPercent(skc, vtx, transformValue=tv)
            n_transferred += 1
        except Exception:
            pass

    return n_transferred


def setup_twist_wiring(transfer_weights=False):
    """L/R arm/forearm にツール独自の twist chain を追加して wrist 捻り自動分配。

    v0.9.29 完全独立化: モデル固有の "_twist_" 命名 bone (arm_twist_L,
    hand_twist_1/2/3_L, dummy_/shadow_ 等) は装飾か実 twist か判定不能な
    ので **一切触らず**、ツール専用命名 `<parent>_tt_<N>_<side>` の chain
    を新規作成する。

    処理:
      1. arm 系 (arm_L→elbow_L) / hand 系 (elbow_L→wrist_L) 各 chain で
         `<parent>_tt_1/2/3_<side>` を parent 子として直線配置
      2. `wrist_<side>.rotateX × (idx/(N+1))` で wire (0.25/0.50/0.75 分配)
      3. transfer_weights=True の場合: parent (arm_L / elbow_L) に既存
         skin weight があれば、bone 軸沿いで proportional に tool bones へ
         転送。**per-vertex 処理で 86000+ verts なら数分掛かる**ため default
         は False (wire だけ張る)。plain model で mesh 変形を活かしたい
         場合のみ True で呼ぶ。

    tool bones が weight を持たない場合 (transfer skip) は mesh 変形に
    寄与しないが、wire は張られるため manually skin することで有効化可能。
    """
    if cmds is None:
        return 0

    # (chain_label, parent_joint, child_joint)
    targets = [
        ("arm_L",   "arm_L",   "elbow_L"),
        ("arm_R",   "arm_R",   "elbow_R"),
        ("hand_L",  "elbow_L", "wrist_L"),
        ("hand_R",  "elbow_R", "wrist_R"),
    ]

    n_wired = 0
    n_created = 0
    n_transferred_total = 0
    count = 3  # 分節数

    # skinCluster / mesh 対応取得
    skc_list = cmds.ls(type="skinCluster") or []
    sc_mesh = {}
    for sc in skc_list:
        geo = cmds.skinCluster(sc, q=True, g=True) or []
        if geo:
            sc_mesh[sc] = geo[0]

    for label, parent, child in targets:
        if not (cmds.objExists(parent) and cmds.objExists(child)):
            continue
        side = _detect_side(parent)
        wrist = f"wrist_{side}"
        driver = wrist if cmds.objExists(wrist) else child

        # tool 専用 chain 作成 (prefix "_tt" で既存 "_twist_" と衝突回避)
        prefix = f"{parent}_tt"
        segs = []
        already_exist_count = 0
        for i in range(1, count + 1):
            name = f"{prefix}_{i}_{side}"
            if cmds.objExists(name):
                segs.append(name)
                already_exist_count += 1
        if len(segs) < count:
            # 不足分を作成
            new = _create_twist_segments(parent, child, count=count,
                                          prefix=prefix, side=side)
            segs = new
            n_created += len(new) - already_exist_count

        # v0.9.30 twist only isolation:
        # 1. inheritsTransform=0 で parent の rotation 継承を切断
        # 2. wtAddMatrix で parent と child の worldMatrix を weighted blend
        #    (weight (1-frac, frac)) → decomposeMatrix → tool.translate に接続。
        #    parent が bend しても tool の位置は parent→child 直線上を追従
        # 3. rotation は wrist.rotateX * fraction のみ (twist only)
        # ペアレントコンストレイン不使用、node network で完結
        for idx, seg in enumerate(segs, start=1):
            frac = idx / (count + 1)
            try:
                # inheritsTransform 切断 (parent の rotation/scale 継承を止める)
                try:
                    cmds.setAttr(seg + ".inheritsTransform", 0)
                except Exception:
                    pass

                # 位置追従 node 群 (既存なら再利用)
                wt_node = seg + "_tt_pos_wt"
                dm_node = seg + "_tt_pos_dm"
                if not cmds.objExists(wt_node):
                    wt_node = cmds.createNode("wtAddMatrix", n=wt_node)
                    cmds.connectAttr(parent + ".worldMatrix[0]",
                                      wt_node + ".wtMatrix[0].matrixIn")
                    cmds.connectAttr(child + ".worldMatrix[0]",
                                      wt_node + ".wtMatrix[1].matrixIn")
                    cmds.setAttr(wt_node + ".wtMatrix[0].weightIn", 1.0 - frac)
                    cmds.setAttr(wt_node + ".wtMatrix[1].weightIn", frac)
                if not cmds.objExists(dm_node):
                    dm_node = cmds.createNode("decomposeMatrix", n=dm_node)
                    cmds.connectAttr(wt_node + ".matrixSum",
                                      dm_node + ".inputMatrix")
                # translate を tool bone.translate に接続 (world-aligned position)
                for ax in ("X", "Y", "Z"):
                    try:
                        cmds.connectAttr(dm_node + ".outputTranslate" + ax,
                                          seg + ".translate" + ax, f=True)
                    except Exception:
                        pass

                # rotation wire (wrist twist のみ)
                dst = seg + ".rotateX"
                cur = cmds.listConnections(dst, s=True, d=False, p=True) or []
                if cur:
                    continue
                md = cmds.createNode("multiplyDivide", n=seg + "_tt_mul")
                cmds.setAttr(md + ".input2X", frac)
                cmds.connectAttr(driver + ".rotateX", md + ".input1X")
                cmds.connectAttr(md + ".outputX", dst, f=True)
                n_wired += 1
            except Exception as exc:
                cmds.warning(f"[{_PACKAGE}] tt wire {driver}→{seg} failed: {exc}")

        # weight 転送: opt-in で default OFF (per-vertex 遅い)
        if transfer_weights:
            for sc, mesh in sc_mesh.items():
                infs = cmds.skinCluster(sc, q=True, inf=True) or []
                if parent not in infs:
                    continue
                try:
                    nt = _transfer_parent_weight_to_twist(parent, segs, mesh, sc)
                    if nt > 0:
                        n_transferred_total += nt
                        print(f"[{_PACKAGE}] tt weight: {label} → transferred "
                              f"{nt} verts from {parent} to tool twist bones")
                except Exception as exc:
                    cmds.warning(f"[{_PACKAGE}] tt weight transfer {parent} failed: {exc}")

    print(f"[{_PACKAGE}] setup_twist_wiring: {n_wired} tool twist wired, "
          f"{n_created} newly created, {n_transferred_total} vertex weights "
          f"transferred (existing model _twist_ bones untouched)")
    return n_wired


def setup_all_ik_fk(mapping=None):
    """検出できた L/R arm/leg 全てに IK/FK rig を構築。leg には reverse foot も。

    Args:
        mapping: dict {label: [start, mid, end]} を渡すと優先使用。
                 None なら resolve_chains_for_ikfk() で scene mapping / auto
                 detect の順に解決。UDE/HIJI/TE 等 非標準命名でも UI で
                 mapping を設定しておけばこの経路で拾える。
    """
    if mapping is None or not mapping:
        chains = resolve_chains_for_ikfk()
    else:
        # mapping は 2 形式受付:
        #   (a) {"fixed": {label: [j,j,j]}, "chains": {...}} (完全形)
        #   (b) {label: [j,j,j]} (fixed のみのフラット形、後方互換)
        if "fixed" in mapping and isinstance(mapping["fixed"], dict):
            fixed_src = mapping["fixed"]
        else:
            fixed_src = mapping
        chains = {}
        for label, joints in fixed_src.items():
            if label in FIXED_LABELS and len(joints) >= 3 \
                    and all(cmds.objExists(j) for j in joints[:3]):
                chains[label] = list(joints[:3])
    total = len(chains)
    results = []
    # setup_all_ik_fk は full_auto_setup 側で [55, 100]% を占める前提。
    # 各 chain を等分割し、chain 内の substep は setup_ik_fk / setup_reverse_foot
    # が独自区間で報告できるよう _pw_span を切り替える。
    outer_lo, outer_hi = _PW_SPAN[0], _PW_SPAN[1]
    for i, (label, chain) in enumerate(chains.items()):
        # この chain が占める outer 区間
        chain_lo = outer_lo + (outer_hi - outer_lo) * i / max(1, total)
        chain_hi = outer_lo + (outer_hi - outer_lo) * (i + 1) / max(1, total)
        _pw_span(chain_lo, chain_hi)
        _pw_sub(0.0, f"Setup IK/FK: {label}")
        # label は "arm_L"/"arm_R"/"leg_L"/"leg_R" 形式 (FIXED_LABELS)
        side = "L" if label.endswith("_L") else "R" if label.endswith("_R") else "C"
        try:
            r = setup_ik_fk(chain[0], chain[1], chain[2], side=side,
                             label=label)
            results.append(r)
            # leg なら reverse foot も試みる
            if "leg" in label.lower():
                _pw_sub(85.0, f"Reverse foot: {label}")
                rf = setup_reverse_foot(chain[2], r["ik_ctl"], r["ik_handle"], side=side)
                if rf:
                    r["reverse_foot"] = rf
        except Exception as exc:
            cmds.warning(f"[attach_ctrls] IK setup for {label} failed: {exc}")
    _pw_span(outer_lo, outer_hi)  # 復元
    _pw_sub(100.0)
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

# --- Progress display -------------------------------------------------------

# Progress は full_auto_setup 全体を [0, 100] に mapping し、内部関数は各自の
# "担当区間" 内で [0, 100] を報告する。区間は module-level state で持ち回して
# 関数シグネチャを汚さない。UI (progressWindow) が使えない batch モードでも
# try/except で silently skip する。
_PW_SPAN = [0.0, 100.0]  # 現在の担当区間 (start_pct, end_pct)
_PW_ACTIVE = False       # progress window 開いているか

def _pw_start(title="Attach Ctrls", status="Setting up...", max_v=100):
    """外側の progressWindow を開く。full_auto_setup 等トップレベルから呼ぶ。"""
    global _PW_ACTIVE, _PW_SPAN
    _PW_SPAN[:] = [0.0, 100.0]
    _PW_ACTIVE = False
    if cmds is None: return
    try:
        # 二重積み対策: 残留 progressWindow を全部 drain。
        # progressWindow が無い時は cmds.progressWindow(q=True) が例外を投げる
        # (None を返さない) ので try/except で判定する (AUDIT #6)。
        for _ in range(8):
            try:
                cmds.progressWindow(q=True, isCancelled=True)
                # 例外出ない = window 存在
                cmds.progressWindow(endProgress=True)
            except Exception:
                break  # window 無し = drain 完了
        cmds.progressWindow(title=title, status=status, progress=0,
                             min=0, max=max_v, isInterruptable=False)
        _PW_ACTIVE = True
    except Exception:
        pass  # batch mode 等で使えない環境は無視


def _pw_end():
    """外側の progressWindow を閉じる。"""
    global _PW_ACTIVE
    if not _PW_ACTIVE: return
    try: cmds.progressWindow(endProgress=True)
    except Exception: pass
    _PW_ACTIVE = False


def _pw_span(start_pct, end_pct):
    """今後の substep 報告が start_pct-end_pct 範囲に mapping されるようセット。"""
    _PW_SPAN[0] = float(start_pct)
    _PW_SPAN[1] = float(end_pct)


def _pw_sub(local_pct, status=None):
    """現区間内で [0, 100] を報告。外側 window の絶対値に換算されて反映される。"""
    if not _PW_ACTIVE: return
    lo, hi = _PW_SPAN
    outer = lo + (hi - lo) * max(0.0, min(100.0, float(local_pct))) / 100.0
    try:
        kw = {"edit": True, "progress": outer}
        if status is not None: kw["status"] = status
        cmds.progressWindow(**kw)
    except Exception:
        pass


def freeze_joint_rotations(joints=None):
    """joint の rotate 値を jointOrient に完全移動させて rotate=0 化。

    MMD/FBX import 直後の joint は rotate に値が baked in された状態で
    やってくる。この状態で ikHandle / orient constraint を掛けると RP
    solver が preferred pose を誤判定して chain が drift する、L/R で
    jointOrient 符号が非対称になり arm_R が反転する、等の重篤な問題を
    起こす。リギング前に必ず rotate → jointOrient にフリーズする。

    Args:
        joints: 対象 joint リスト。None なら scene 内全 joint。

    アルゴリズム (ユーザ提供):
      1. 現 world rotation を記録
      2. jointOrient を (0,0,0) にセット
      3. world rotation を元値に復元 (rotate に全て入る)
      4. その object-space rotate を jointOrient にコピー
      5. rotate を (0,0,0) にリセット
    結果: world rotation 不変、rotate=0、jointOrient に全て。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    if joints is None:
        joints = cmds.ls(type="joint") or []
    total = len(joints)
    n_ok = 0
    for i, jnt in enumerate(joints):
        try:
            rot = cmds.xform(jnt, q=True, ws=True, rotation=True)
            cmds.setAttr(jnt + ".jointOrient", 0, 0, 0, type="double3")
            cmds.xform(jnt, ws=True, rotation=rot)
            new_rot = cmds.xform(jnt, q=True, os=True, rotation=True)
            cmds.setAttr(jnt + ".jointOrient",
                          new_rot[0], new_rot[1], new_rot[2], type="double3")
            cmds.setAttr(jnt + ".rotate", 0, 0, 0, type="double3")
            n_ok += 1
        except Exception as exc:
            cmds.warning(f"[{_PACKAGE}] freeze_joint_rotations({jnt}) failed: {exc}")
        # 進行報告 (10% 刻み、更新回数を抑えて速度低下防止)
        if total and (i % max(1, total // 10) == 0):
            _pw_sub(100.0 * i / total, f"Freeze joint rotations {i}/{total}")
    print(f"[{_PACKAGE}] freeze_joint_rotations: {n_ok}/{len(joints)} joint(s) frozen")
    return n_ok


def symmetrize_bones_L_to_R():
    """L 側 chain の bone を mirrorJoint (mirrorBehavior=True) で正しく
    ミラーし、R 側 chain の jointOrient / translate に転写する。

    問題: MMD 由来 FBX の骨は L と R で local axis の向きが一致していない
    ことがあり、両肩を同時に `rotateX=30` しても片腕が上がって片腕が下がる、
    ような "非ミラー" 挙動になる (jointOrient が幾何的に対称でない)。
    freeze_joint_rotations は rotate → jointOrient に集約するだけで、
    jointOrient 自体の非対称は解消しない。

    対処: L root chain を duplicate → `cmds.mirrorJoint(mirrorYZ=True,
    mirrorBehavior=True)` で正しく mirror されたコピーを作り、DFS 順序
    (子は名前ソート) で R chain に jointOrient/translate をコピー。
    これで同一 rotate 入力が左右で対称モーションを生む状態になる。

    実測 (Nekotatune): before は arm.rotateX=30 で L wrist dY=+15.6 に
    対し R wrist dY=-10.4 (逆)、after は L=+15.6 / R=+15.6 (共に上昇、
    X は対称) と完全に正常化。

    Returns:
        (n_transferred, n_l_roots): 転写した joint 数と処理した L root 数。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")

    def _dfs(root):
        out = [root]
        kids = cmds.listRelatives(root, c=True, type="joint", f=False) or []
        for k in sorted(kids):
            out.extend(_dfs(k))
        return out

    # L 側最上位 root (親が L でない L joint) を全収集
    l_roots = []
    for j in cmds.ls(type="joint") or []:
        if _detect_side(j) != "L":
            continue
        parent = cmds.listRelatives(j, p=True, type="joint") or []
        if not parent or _detect_side(parent[0]) != "L":
            l_roots.append(j)

    # v0.9.19 mesh 破綻対策: R 骨の translate/jointOrient を変更する前に
    # 全 skinCluster に moveJointsMode=1 を立てて bindPreMatrix を自動更新
    # させる。これで骨の world 位置変化を mesh が追従しなくなる。
    skc_list = cmds.ls(type="skinCluster") or []
    for sc in skc_list:
        try:
            cmds.skinCluster(sc, e=True, moveJointsMode=1)
        except Exception:
            pass

    n_transferred = 0
    n_missing_r = 0
    try:
        for l_root in l_roots:
            r_root = _opposite_side_name(l_root)
            if not r_root or not cmds.objExists(r_root):
                n_missing_r += 1
                continue

            # L root を duplicate → mirrorJoint で mirror behavior 付き複製
            try:
                dup = cmds.duplicate(l_root, rr=True, rc=True)[0]
            except Exception as exc:
                cmds.warning(f"[{_PACKAGE}] symmetrize duplicate({l_root}) failed: {exc}")
                continue
            try:
                mirrored = cmds.mirrorJoint(dup, mirrorYZ=True,
                                            mirrorBehavior=True,
                                            searchReplace=("_L", "_MTMP"))
            except Exception as exc:
                cmds.warning(f"[{_PACKAGE}] mirrorJoint({dup}) failed: {exc}")
                if cmds.objExists(dup):
                    cmds.delete(dup)
                continue

            # mirrored[0] = mirror root (mirrorJoint 仕様: 引数 joint の mirror が先頭)
            mir_root = mirrored[0] if mirrored else None
            if not mir_root or not cmds.objExists(mir_root):
                if cmds.objExists(dup):
                    cmds.delete(dup)
                continue

            # DFS 同期 (子は名前ソート、L/mir/R とも同じ命名規則なので順序一致)
            mir_list = _dfs(mir_root)
            r_list = _dfs(r_root)
            if len(mir_list) != len(r_list):
                cmds.warning(f"[{_PACKAGE}] symmetrize({l_root}): mir({len(mir_list)}) "
                             f"vs R({len(r_list)}) 数不一致 → skip")
                if cmds.objExists(mir_root):
                    cmds.delete(mir_root)
                if cmds.objExists(dup):
                    cmds.delete(dup)
                continue

            for m, r in zip(mir_list, r_list):
                try:
                    jo = cmds.getAttr(m + ".jointOrient")[0]
                    t = cmds.getAttr(m + ".translate")[0]
                    cmds.setAttr(r + ".jointOrient",
                                 jo[0], jo[1], jo[2], type="double3")
                    cmds.setAttr(r + ".translate",
                                 t[0], t[1], t[2], type="double3")
                    cmds.setAttr(r + ".rotate", 0, 0, 0, type="double3")
                    n_transferred += 1
                except Exception as exc:
                    cmds.warning(f"[{_PACKAGE}] transfer {m} → {r} failed: {exc}")

            # cleanup: mirror hierarchy + duplicate をどちらも削除
            if cmds.objExists(mir_root):
                cmds.delete(mir_root)
            if cmds.objExists(dup):
                cmds.delete(dup)
    finally:
        # skinCluster の moveJointsMode を確実に戻す (例外時も)
        for sc in skc_list:
            try:
                cmds.skinCluster(sc, e=True, moveJointsMode=0)
            except Exception:
                pass

    print(f"[{_PACKAGE}] symmetrize_bones_L_to_R: {n_transferred} joint(s) "
          f"転写、L roots={len(l_roots)} (R 無し={n_missing_r})")
    return n_transferred, len(l_roots)


def merge_legD_into_leg():
    """MMD の legD 系 bone (legD/kneeD/ankleD + shadow_/dummy_ 変種) を
    削除し、skinCluster の weight を対応する main leg bone に移送する。

    MMD FBX には脚 chain と並列に "D" (Direct/Displacement) chain が
    存在し、skinCluster に含まれる場合がある。attach_ctrls の rig は
    main leg bone (leg_L/knee_L/ankle_L) を対象とするため、D chain は
    リギング上不要かつ IK 挙動と干渉する原因になる。

    処理:
      1. D bone → main bone の mapping を組む (D suffix を除去した名前)
      2. skinCluster ごとに、D bone の weight を main bone に加算転送
      3. D bone を skinCluster influence から除外
      4. D bone (と _end) を delete

    Returns:
        (n_transferred, n_deleted): 転送した D bone 数と削除した joint 数。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")

    # 対象 D bone 名 (base name)
    d_base_names = ("legD", "kneeD", "ankleD")
    # 変種 prefix
    prefixes = ("", "shadow_", "dummy_")

    def _main_of_d(d_name):
        """dummy_legD_L → leg_L 等、対応する main bone 名を返す。"""
        short = d_name.split("|")[-1].split(":")[-1]
        for pref in prefixes:
            for base in d_base_names:
                d_full = pref + base
                # base_L / base_R
                for side in ("_L", "_R"):
                    if short == d_full + side:
                        # main は base から D を除いた版
                        return base[:-1] + side  # legD → leg, kneeD → knee
        return None

    # 現在 scene 内の全 joint から D 骨を検出
    all_joints = cmds.ls(type="joint") or []
    d_joints = []
    for j in all_joints:
        main = _main_of_d(j)
        if main and cmds.objExists(main):
            d_joints.append((j, main))

    if not d_joints:
        print(f"[{_PACKAGE}] merge_legD_into_leg: D 系 bone 未検出 → skip")
        return 0, 0

    print(f"[{_PACKAGE}] merge_legD_into_leg: {len(d_joints)} D-bone 検出")

    # skinCluster 単位で weight 転送
    skcs = cmds.ls(type="skinCluster") or []
    n_transferred = 0
    for skc in skcs:
        infs = cmds.skinCluster(skc, q=True, inf=True) or []
        inf_short = [i.split("|")[-1].split(":")[-1] for i in infs]
        # このスキンに含まれる D bone だけ処理
        d_in_skin = [(d, m) for (d, m) in d_joints
                     if d.split("|")[-1].split(":")[-1] in inf_short]
        if not d_in_skin:
            continue
        geo_list = cmds.skinCluster(skc, q=True, g=True) or []
        if not geo_list:
            continue
        geo = geo_list[0]
        # 頂点数
        vtx_count = cmds.polyEvaluate(geo, v=True)
        if not isinstance(vtx_count, int):
            continue

        # main bone を influence に必ず含める (未含なら add)
        for d, m in d_in_skin:
            if m not in infs:
                try:
                    cmds.skinCluster(skc, e=True, ai=m,
                                     lockWeights=False, weight=0)
                    infs.append(m)
                except Exception as exc:
                    cmds.warning(f"[{_PACKAGE}] add influence {m} failed: {exc}")

        # 頂点 loop で weight 移送
        for vi in range(vtx_count):
            vtx = f"{geo}.vtx[{vi}]"
            for d, m in d_in_skin:
                try:
                    d_w = cmds.skinPercent(skc, vtx, transform=d, q=True) or 0
                except Exception:
                    d_w = 0
                if d_w > 1e-6:
                    try:
                        m_w = cmds.skinPercent(skc, vtx,
                                                transform=m, q=True) or 0
                        cmds.skinPercent(skc, vtx,
                                          transformValue=[(m, m_w + d_w),
                                                          (d, 0)])
                    except Exception:
                        pass
            if vi and (vi % max(1, vtx_count // 20) == 0):
                _pw_sub(100.0 * vi / vtx_count,
                         f"Merge legD weights {vi}/{vtx_count}")

        # D bone を influence から除外
        for d, m in d_in_skin:
            try:
                cmds.skinCluster(skc, e=True, ri=d)
                n_transferred += 1
            except Exception as exc:
                cmds.warning(f"[{_PACKAGE}] remove influence {d} failed: {exc}")

    # D bone (と descendants) を削除
    n_deleted = 0
    for d, _ in d_joints:
        if not cmds.objExists(d):
            continue
        try:
            # descendants も含めて削除 (D_end 等)
            cmds.delete(d)
            n_deleted += 1
        except Exception as exc:
            cmds.warning(f"[{_PACKAGE}] delete {d} failed: {exc}")

    print(f"[{_PACKAGE}] merge_legD_into_leg: {n_transferred} 影響移送、"
          f"{n_deleted} joint 削除")
    return n_transferred, n_deleted


def neutralize_leg_bind_bend():
    """脚 knee を hip-ankle 直線に完全射影して bind pose の knock-knee
    (X 内側 + Z 前方 offset) を除去する。

    問題: MMD 由来 FBX は knee joint が hip-ankle 直線から (-X inward,
    +Z forward) にオフセットして bind されている (Nekotatune 実測 dist=2.04)。
    RP solver は bind の bend 方向を preferred hint として拾い、腰下げ時の
    chain 圧縮で knee が world X 方向へ大きく変位する (Bug 2 の根源)。

    対策: knee_L / knee_R の WS position を hip-ankle 線上に射影して bind
    をストレート (dist=0) にする。ankle は knee の子なので world 位置を
    保存 → 復元。skinCluster は `moveJointsMode` で bindPreMatrix を自動
    更新し mesh を保持。射影後 freeze で新 bind を jointOrient に集約。

    こうすると RP solver は preferred bend を持たなくなり、PV 方向 (現状
    +Z forward) のみが bend を決定 → chain 圧縮で knee は素直に前方に
    曲がる。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    import math

    def _project_on_line(hip, mid, ank):
        se = [ank[i] - hip[i] for i in range(3)]
        len2 = sum(a * a for a in se)
        if len2 < 1e-9:
            return list(mid)
        t = sum((mid[i] - hip[i]) * se[i] for i in range(3)) / len2
        return [hip[i] + t * se[i] for i in range(3)]

    def _ws(n):
        return cmds.xform(n, q=True, ws=True, t=True)

    # 脚 chain 検出 (hero name: leg_L/knee_L/ankle_L)
    pairs = []
    for side in ("L", "R"):
        hip = f"leg_{side}"
        mid = f"knee_{side}"
        ank = f"ankle_{side}"
        if all(cmds.objExists(n) for n in (hip, mid, ank)):
            pairs.append((hip, mid, ank))
    if not pairs:
        print(f"[{_PACKAGE}] neutralize_leg_bind_bend: leg/knee/ankle "
              "chain 未検出 → skip")
        return 0

    # 補正前の offset 記録
    for hip, mid, ank in pairs:
        hp, mp, ap = _ws(hip), _ws(mid), _ws(ank)
        proj = _project_on_line(hp, mp, ap)
        offset = math.sqrt(sum((mp[i] - proj[i]) ** 2 for i in range(3)))
        print(f"[{_PACKAGE}] {mid} bind offset from hip-ankle line: "
              f"{offset:.3f} unit")

    # skinCluster moveJointsMode 有効化 (bindPreMatrix 自動更新)
    skc_list = cmds.ls(type="skinCluster") or []
    for sc in skc_list:
        try:
            cmds.skinCluster(sc, e=True, moveJointsMode=1)
        except Exception:
            pass

    frozen_joints = []
    for hip, mid, ank in pairs:
        ankle_save = _ws(ank)
        proj = _project_on_line(_ws(hip), _ws(mid), ankle_save)
        cmds.xform(mid, ws=True, t=proj)
        # ankle は mid の子なので追従してしまう → world 位置を強制復元
        cmds.xform(ank, ws=True, t=ankle_save)
        frozen_joints.extend([hip, mid, ank])

    # 新 bind を jointOrient に集約 (rotate=0 化)
    freeze_joint_rotations(frozen_joints)

    for sc in skc_list:
        try:
            cmds.skinCluster(sc, e=True, moveJointsMode=0)
        except Exception:
            pass

    # v0.9.22 で試行した cmds.joint(oj="xyz") による chain 再オリエントは
    # knee.jointOrient=(0,0,0) の clean 化に成功したが、primary X 軸が bone
    # 方向 (aim) になったため FK ctl の rotateX が bend ではなく twist に
    # なり functional_FK_leg テストが FAIL した。既存 FK convention と
    # 噛み合わないため revert。preferredAngle だけで Bug 2 X drift は
    # 十分小さいので現状で良しとする。将来 FK ctl 側の rotate axis
    # convention を見直す場合に再検討。

    # v0.9.21: knee に preferredAngleZ=5° を仕込む (RP solver bend 方向ヒント)。
    for hip, mid, ank in pairs:
        try:
            cmds.setAttr(mid + ".preferredAngleX", 0)
            cmds.setAttr(mid + ".preferredAngleY", 0)
            cmds.setAttr(mid + ".preferredAngleZ", 5.0)
        except Exception:
            pass

    # 補正後確認
    for hip, mid, ank in pairs:
        hp, mp, ap = _ws(hip), _ws(mid), _ws(ank)
        proj = _project_on_line(hp, mp, ap)
        offset = math.sqrt(sum((mp[i] - proj[i]) ** 2 for i in range(3)))
        print(f"[{_PACKAGE}] {mid} 補正後 offset: {offset:.4f} unit")

    return len(pairs)


def full_auto_setup(scale=1.0, skip_decoration=True, delete_junk=True,
                    mapping=None):
    """FBX 直後の状態から完全 rig setup を 1 コマンドで実行。
    1. namespace 除去
    2. joint 名 rename (fbx_renamer 経由)
    3. 不要ノード削除 (locator + 未skin _end/shadow/dummy)
    4. IK/FK chain を除いた全 joint に FK cube ctl 付与 (auto-scale)
    5. L/R arm/leg で IK/FK blend rig 構築 (mapping 優先、無ければ auto-detect)

    Args:
        scale:           auto_scale multiplier (1.0 = bone 長さ準拠)
        skip_decoration: hair/ribbon/skirt/coat/ear/tail 系を除外
        delete_junk:     不要ノード削除を実行するか
        mapping:         optional {label: [start,mid,end]} dict。命名規則が
                         非標準 (UDE/HIJI/TE 等) のキャラで setup_all_ik_fk が
                         auto-detect に失敗する場合に UI 経由で渡す。
                         None なら scene の mappingJson attr を優先し、それも
                         無ければ auto-detect fallback。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")

    # scale cache reset (毎回 fresh に mesh bbox を測定)
    _reset_scale_cache()

    _pw_start(title="Attach Ctrls - Full Auto Setup",
              status="Rename joints...", max_v=100)
    try:
        # Step 1+2: rename (0-5%)
        _pw_span(0, 5); _pw_sub(0, "Rename joints...")
        if fbx_renamer is None:
            cmds.warning("[attach_ctrls] fbx_renamer not available -- skipping rename")
        else:
            fbx_renamer.remove_all_namespaces()
            fbx_renamer.rename_all_joints()

        # Step 2.5: freeze rotate to jointOrient (5-12%)
        _pw_span(5, 12); _pw_sub(0, "Freeze joint rotations...")
        freeze_joint_rotations()

        # Step 2.6: L → R 骨対称化 (12-14%)
        # MMD FBX は L/R で local axis が非対称なことがあり、両肩の同一
        # rotateX で片腕上がって片腕下がる、といった rig 不能状態になる。
        # mirrorJoint (mirrorBehavior=True) で L chain を正しく mirror し
        # R chain の jointOrient/translate に転写して対称化する。
        _pw_span(12, 14); _pw_sub(0, "Symmetrize L->R bones...")
        try:
            symmetrize_bones_L_to_R()
        except Exception as _sym_exc:
            cmds.warning(f"[attach_ctrls] symmetrize_bones failed (continue): {_sym_exc}")

        # Step 2.7: (無効化) legD 系削除 + skin weight 移送
        # v0.9.17 で有効化したが mesh 破綻が判明したため無効化。
        # MMD の D (Direct/Displacement) bone は「mesh 用衛星骨」として
        # waistcancel_L 経由の独自 constraint chain を持ち、animation で
        # 起こる leg_L の変形から mesh を隔離する役割がある。単純に leg_L
        # へ weight 移送すると IK 駆動の leg_L 変形が直接 mesh を歪めて
        # 破綻する。関数は残置。有効化するには D bone の constraint 構造
        # を保持したまま (または全 D chain を除去して MMD 設計外の rig を
        # 覚悟して) 使う必要がある。
        # try:
        #     merge_legD_into_leg()
        # except Exception as _md_exc:
        #     cmds.warning(f"[attach_ctrls] merge_legD_into_leg failed (continue): {_md_exc}")

        # Step 2.8: knee を hip-ankle 直線に射影 (Bug 2 根本対策)
        # v0.9.17 で有効化した際 chain が rigid になる副作用があったが、
        # 真因は setup_ik_fk の stretch loop が D-family bone の translate
        # を override して waistcancel_L の hidden constraint を壊し ankle
        # を 56 unit jump させていたこと (v0.9.20 で D-family 除外して解消)。
        # v0.9.21 で再有効化して neutralize が IK bend を natural にする
        # か検証する。
        try:
            neutralize_leg_bind_bend()
        except Exception as _bend_exc:
            cmds.warning(f"[attach_ctrls] neutralize failed (continue): {_bend_exc}")

        # Step 3: cleanup (15-25%)
        _pw_span(15, 25); _pw_sub(0, "Delete unnecessary nodes...")
        if delete_junk:
            delete_unnecessary()

        # Step 3.5: joint radius を最小限に (骨の viewport 表示は ctl を邪魔しない程度)
        # ユーザー要望: 固定 0.016 (mesh scale 依存を外して常に極小)
        _pw_span(25, 28); _pw_sub(0, "Adjust joint radius...")
        diag = _scene_mesh_bbox_diag()
        joint_radius = 0.016
        for j in cmds.ls(type="joint") or []:
            try:
                cmds.setAttr(j + ".radius", joint_radius)
            except Exception:
                pass
        print(f"[{_PACKAGE}] joint radius set to {joint_radius:.4f} (diag/1000)")

        # Step 4: root/main ctls を作成 (地面のオクタゴン + 主体 box)
        #         attach_ctrls_grp の直下、他の ctl の親になる
        _pw_span(28, 30); _pw_sub(0, "Create root/main ctls...")
        diag = _scene_mesh_bbox_diag()
        if not cmds.objExists(ROOT_GROUP):
            cmds.group(em=True, name=ROOT_GROUP)
        # world ctl (地面) — 大きめオクタゴン (mGear world_ctl 相当、赤)
        world_ctl_name = "world_ctl"
        if not cmds.objExists(world_ctl_name):
            world_ctl = _make_octagon_curve(world_ctl_name, scale=diag * 0.28)
            _set_ctl_color(world_ctl, COLOR_WORLD)
            cmds.parent(world_ctl, ROOT_GROUP)
            # v0.9.11: scale は unlock して global scale ctl として使う
            _lock_hide_attrs(world_ctl, ["v"])
            # rig 全体 scale を world_ctl の scale で駆動 (main_ctl は既に
            # world_ctl の子だが、scaleConstraint で子孫全部が同期)
            try:
                for _ax in ("sx", "sy", "sz"):
                    cmds.connectAttr(world_ctl + "." + _ax,
                                      ROOT_GROUP + "." + _ax, f=True)
            except Exception:
                pass
        else:
            world_ctl = world_ctl_name
        # main ctl (体を包む) — 中サイズ box (mGear body_C0_ctl 相当、黄)
        main_ctl_name = "main_ctl"
        if not cmds.objExists(main_ctl_name):
            main_ctl = _make_flat_box_curve(main_ctl_name, scale=diag * 0.2,
                                            x_ratio=1.5, z_ratio=1.5)
            _set_ctl_color(main_ctl, COLOR_C)
            cmds.parent(main_ctl, world_ctl)
            _lock_hide_attrs(main_ctl, ["sx","sy","sz","v"])
        else:
            main_ctl = main_ctl_name

        # Step 5: attach FK ctls, exclude IK/FK chain joints (30-55%)
        _pw_span(30, 55); _pw_sub(0, "Attach FK controllers...")
        # v0.9.31 bugfix: mapping 経由で IK/FK 対象になる joint も除外に含める。
        # 従来は find_ik_chains() の naming heuristic 結果だけを exclude にして
        # いたが、UDE/HIJI/TE 等 非標準命名では heuristic が失敗して UDE_L に
        # FK cube が付き、続く Step 7 で mapping 経由 IK/FK も張られて
        # duplicate ctl + duplicate parentConstraint になっていた。
        # resolve_chains_for_ikfk(mapping) で Step 7 と同じ chain 集合を得て
        # exclude を統一する。
        ikfk_chains = resolve_chains_for_ikfk(mapping)
        exclude = set()
        for chain in ikfk_chains.values():
            exclude.update(chain)

        # v0.9.32 診断: exclude 内容と件数を print (skip_decoration=False で
        # arm ctl が生成されない bug 報告への切り分け材料)
        print(f"[{_PACKAGE}] Step5 IK/FK 除外対象 ({len(exclude)}): "
              f"{sorted(exclude)}")

        all_joints = cmds.ls(type="joint") or []
        # v0.9.24: twist joint は FK cube ctl の対象外 (auto-drive で
        # multiplyDivide 接続するため、parentConstraint と衝突しないよう
        # attach_controllers から除外)。
        def _is_twist_bone(name):
            return "twist" in name.split("|")[-1].split(":")[-1].lower()
        other = [j for j in all_joints
                 if j not in exclude
                 and not j.endswith("_end")
                 and not _is_twist_bone(j)]

        # v0.9.32 defensive: attach_controllers 内部で 1 joint が例外を起こしても
        # Step 7 の IK/FK setup は必ず走らせる。従来は Step 5 の予期せぬ例外で
        # Step 7 まで到達せず「腕/脚 ctl が生成されない」bug 報告があった。
        attach_result = {}
        try:
            attach_result = attach_controllers(joints=other, scale=scale,
                                                do_constrain=True,
                                                auto_scale=True,
                                                skip_decoration=skip_decoration)
        except Exception as _step5_exc:
            import traceback as _tb
            cmds.warning(f"[{_PACKAGE}] Step 5 attach_controllers 例外 → Step 7 は続行: "
                          f"{_step5_exc}")
            print(f"[{_PACKAGE}] Step 5 traceback:\n{_tb.format_exc()}")

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

        # Step 7: IK/FK setup (IK ctl NPO は独立世界置き = 足接地 目的) (55-97%)
        _pw_span(55, 97); _pw_sub(0, "Setup IK/FK chains...")
        ik_results = setup_all_ik_fk(mapping=mapping)

        # v0.9.32 診断: 期待した chain 数 vs 実際に生成できた IK ctl 数を突合
        got_labels = {r.get("label") for r in ik_results}
        expected_labels = set(ikfk_chains.keys())
        missing_labels = expected_labels - got_labels
        if missing_labels:
            cmds.warning(f"[{_PACKAGE}] IK/FK 未生成 chain: {sorted(missing_labels)} "
                          f"(期待 {sorted(expected_labels)}, 実際 {sorted(got_labels)})")
        missing_ctls = []
        for lbl in expected_labels:
            for suf in ("_IK_ctl", "_PV_ctl", "_UI_ctl"):
                nm = lbl + suf
                if not cmds.objExists(nm):
                    missing_ctls.append(nm)
        if missing_ctls:
            cmds.warning(f"[{_PACKAGE}] 生成失敗 ctl ({len(missing_ctls)}): "
                          f"{missing_ctls}")

        # Step 8: twist joint auto-drive 配線 (97-100%)
        _pw_span(97, 100); _pw_sub(0, "Wire twist joints...")
        try:
            setup_twist_wiring()
        except Exception as _tw_exc:
            cmds.warning(f"[attach_ctrls] twist wiring failed (continue): {_tw_exc}")

        print(f"[{_PACKAGE}] === full_auto_setup complete ===")
        print(f"  FK ctls attached: {len(attach_result)}")
        print(f"  IK/FK chains    : {len(ik_results)}")
        for r in ik_results:
            print(f"    {r['label']:15s} switch: {r['switch']}")

        return {"fk_attach": attach_result, "ik_fk": ik_results}
    finally:
        _pw_end()


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
        label="Skip:", label1="揺れもの骨 (hair/ribbon/skirt/coat/ear/tail 等)",
        value1=True, cw2=(60, 280),
        ann="揺れもの (装飾系) は attach_ctrls では ctl を付けず、専用ツール "
             "(jiggle_bones.show_ui) 側で dynamics/simulation を組む方針 "
             "(v0.9.33 デフォルト True 化)",
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

    # v0.9.31: 命名が非標準なキャラ用の手動 mapping UI ボタン
    cmds.rowLayout(nc=1, adj=1, cw=(1, 400))
    cmds.button(l="Chain Mapping…  (UDE/HIJI 等 命名非標準キャラ用)",
                h=28, c=show_mapping_ui,
                bgc=(0.35, 0.45, 0.65))
    cmds.setParent("..")

    # v0.9.34: 揺れもの (hair/skirt/tail 等) 専用 dynamics UI 起動ボタン
    # attach_ctrls では skip_decoration=True で除外される chain を、
    # jiggle_bones (hairSystem + follicle + spline IK) で dynamics 化する。
    cmds.rowLayout(nc=1, adj=1, cw=(1, 400))
    cmds.button(l="🌊 Jiggle Bones…  (hair/skirt/tail 等 揺れもの dynamics)",
                h=28, c=_ui_open_jiggle_bones,
                bgc=(0.30, 0.55, 0.70))
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
    cmds.text(l="IK/FK 切替: UI host (option ctl) の 'IK_FK' attr (0=FK, 1=IK)",
              al="left", fn="smallObliqueLabelFont")
    cmds.text(l="装飾骨は暗灰色。IK モードで waist を回すと足が接地したまま追従。",
              al="left", fn="smallObliqueLabelFont")

    # snap / reset ボタン (AUDIT #3, #10)
    cmds.separator(h=8, style="in")
    cmds.text(l="=== Snap / Reset (chain の任意 ctl を選択して実行) ===",
              al="left", fn="boldLabelFont")
    cmds.rowLayout(nc=3, adj=1, cw3=(130, 130, 130),
                   ct3=("both","both","both"), co3=(2,2,2))
    cmds.button(l="FK → IK snap", h=26, c=_ui_snap_fk_to_ik,
                bgc=(0.35, 0.55, 0.75))
    cmds.button(l="IK → FK snap", h=26, c=_ui_snap_ik_to_fk,
                bgc=(0.55, 0.35, 0.75))
    cmds.button(l="Reset all IK ctls", h=26, c=_ui_reset_ik,
                bgc=(0.60, 0.30, 0.30))
    cmds.setParent("..")

    # v0.9.11 UX: Mirror Pose ボタン
    cmds.rowLayout(nc=1, adj=1, cw=(1, 400))
    cmds.button(l="Mirror Selected (L ↔ R pose copy)", h=26,
                c=_ui_mirror_pose, bgc=(0.35, 0.65, 0.35))
    cmds.setParent("..")


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


def _ui_snap_fk_to_ik(*_):
    """selection の joint / ctl から chain を推定して snap (AUDIT #3)。"""
    sel = cmds.ls(sl=True) or []
    if not sel:
        cmds.warning("Select any joint/ctl of the target chain first")
        return
    label = sel[0].split(":")[-1].replace("_ctl", "").replace("_IK", "") \
                       .replace("_PV", "").replace("_UI", "").replace("_fk", "")
    for guess in (label, label + "_L", label + "_R"):
        try:
            snap_fk_to_ik(guess); return
        except Exception:
            continue
    cmds.warning(f"snap_fk_to_ik: could not resolve chain from {sel[0]}")


def _ui_snap_ik_to_fk(*_):
    sel = cmds.ls(sl=True) or []
    if not sel:
        cmds.warning("Select any joint/ctl of the target chain first")
        return
    label = sel[0].split(":")[-1].replace("_ctl", "").replace("_IK", "") \
                       .replace("_PV", "").replace("_UI", "").replace("_fk", "")
    for guess in (label, label + "_L", label + "_R"):
        try:
            snap_ik_to_fk(guess); return
        except Exception:
            continue
    cmds.warning(f"snap_ik_to_fk: could not resolve chain from {sel[0]}")


def _ui_mirror_pose(*_):
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Select L or R side ctls to mirror to opposite side")
        return
    n_ok, n_skip = mirror_pose(sel)
    print(f"[{_PACKAGE}] Mirror: {n_ok} mirrored, {n_skip} skipped")


def _ui_reset_ik(*_):
    """全 IK ctl の translate/rotate を bind pose (npo と同位置) にリセット。
    ユーザが誤って translate=100 等にした際の復帰手段 (AUDIT #10)。"""
    n = 0
    for ctl in cmds.ls("*_IK_ctl", type="transform") or []:
        try:
            npo = ctl.replace("_IK_ctl", "_IK_npo")
            if cmds.objExists(npo):
                m = cmds.xform(npo, q=True, ws=True, m=True)
                cmds.xform(ctl, ws=True, m=m)
                n += 1
        except Exception:
            pass
    print(f"[{_PACKAGE}] Reset IK ctls to bind: {n}")


def _ui_attach(*_):
    scale = cmds.floatSliderGrp(_UI_SCALE, q=True, value=True)
    do_constrain = cmds.checkBoxGrp(_UI_CONSTRAIN, q=True, value1=True)
    skip_dec = cmds.checkBoxGrp(_UI_SKIP_DECOR, q=True, value1=True)
    attach_controllers(scale=scale, do_constrain=do_constrain,
                        auto_scale=True, skip_decoration=skip_dec)


def _ui_delete(*_):
    delete_generated()


def _ui_open_jiggle_bones(*_):
    """揺れもの UI (jiggle_bones.show_ui) を起動。
    lazy import で attach_ctrls 単体運用時の依存を回避 (jiggle_bones.py が
    scripts フォルダに無くても attach_ctrls 本体は動作する)。"""
    try:
        import importlib, sys as _sys
        if "jiggle_bones" in _sys.modules:
            importlib.reload(_sys.modules["jiggle_bones"])
            jb = _sys.modules["jiggle_bones"]
        else:
            import jiggle_bones as jb
        jb.show_ui()
    except ImportError:
        cmds.confirmDialog(
            title="Jiggle Bones not installed",
            message="jiggle_bones.py が Maya scripts フォルダ (または sys.path) に\n"
                    "見つかりません。\n\n"
                    "GitHub raw から取得:\n"
                    "https://raw.githubusercontent.com/ogshaw03/FXtest/main/jiggle_bones.py\n"
                    "を C:/Users/<user>/Documents/maya/2023/scripts/ に保存してください。",
            button=["OK"], defaultButton="OK")
    except Exception as exc:
        cmds.warning(f"[attach_ctrls] jiggle_bones の起動に失敗: {exc}")


# =========================================================================
# Chain Mapping UI (v0.9.31 humanoid layout / v0.9.32 refactor)
# =========================================================================

MAPPING_WINDOW = "attach_ctrls_mappingWin"

# fixed chain の joint 役割ラベル (表示用)
_FIXED_ROLES = ("start", "mid", "end")
_FIXED_ROLE_JP = {
    "arm_L": ("L Shoulder", "L Elbow", "L Wrist"),
    "arm_R": ("R Shoulder", "R Elbow", "R Wrist"),
    "leg_L": ("L Hip",      "L Knee",  "L Ankle"),
    "leg_R": ("R Hip",      "R Knee",  "R Ankle"),
}
# ボタン内 abbreviation (幅 ~72px に収める)
_ROLE_ABBREV = {
    "arm_L": ("L Sho", "L Elb", "L Wri"),
    "arm_R": ("R Sho", "R Elb", "R Wri"),
    "leg_L": ("L Hip", "L Kne", "L Ank"),
    "leg_R": ("R Hip", "R Kne", "R Ank"),
}

# 人型 body diagram の joint 位置 (px offset from formLayout の左上)。
# キャラを正面から見た配置なので character の L 側は viewer から見て右側
# (x が大きい方) に置く (mirror view convention)。
# 全体 canvas は 380 x 440。
_HUMANOID_POSITIONS = {
    ("arm_R", 0): ( 68,  70),   # R shoulder (character 右肩、viewer 左)
    ("arm_R", 1): ( 32, 130),   # R elbow    (肩より外へ)
    ("arm_R", 2): (  8, 195),   # R wrist    (更に外)
    ("arm_L", 0): (238,  70),   # L shoulder (character 左肩、viewer 右)
    ("arm_L", 1): (274, 130),
    ("arm_L", 2): (298, 195),
    ("leg_R", 0): (108, 245),   # R hip
    ("leg_R", 1): (100, 320),
    ("leg_R", 2): ( 94, 395),
    ("leg_L", 0): (198, 245),   # L hip
    ("leg_L", 1): (206, 320),
    ("leg_L", 2): (212, 395),
}
_HUMANOID_FORM_W = 380
_HUMANOID_FORM_H = 428

# UI 内で state を持ち回るための global (Maya cmds UI は callback で state 参照要)
_MAP_UI_FIXED_FIELDS = {}     # {(label, role_idx): iconTextButton_name}
_MAP_UI_CHAINS_LAYOUT = None  # variable chain list の親 columnLayout
_MAP_UI_CHAIN_ROWS = {}       # {chain_name: {"name_field":..., "joints_field":...}}
_MAP_UI_ROW_COUNTER = 0       # 単調増加 ID (len(dict) だと 削除→追加で衝突する)


def _mapping_ui_reset_state():
    _MAP_UI_FIXED_FIELDS.clear()
    _MAP_UI_CHAIN_ROWS.clear()
    global _MAP_UI_CHAINS_LAYOUT, _MAP_UI_ROW_COUNTER
    _MAP_UI_CHAINS_LAYOUT = None
    _MAP_UI_ROW_COUNTER = 0


# ---- 人型 body-diagram slot 操作 ----

def _slot_get_joint(btn):
    """button の annotation に格納された joint 名を返す。空なら空文字。"""
    try:
        ann = cmds.button(btn, q=True, ann=True) or ""
    except Exception:
        return ""
    # annotation 冒頭に "joint: " を付けているので split で取り出す
    if ann.startswith("joint: "):
        return ann[len("joint: "):].strip()
    return ""


def _slot_update_visual(btn, label, role_idx):
    """slot の stored joint を読み、button の label/色/tooltip を再描画。"""
    joint = _slot_get_joint(btn)
    role_full = _FIXED_ROLE_JP[label][role_idx]
    if joint:
        # 存在チェックで色分け
        exists = cmds.objExists(joint)
        display = joint if len(joint) <= 9 else joint[:8] + "…"
        cmds.button(btn, e=True, l=display,
                     bgc=(0.30, 0.65, 0.30) if exists else (0.70, 0.35, 0.35),
                     ann=f"joint: {joint}\n{role_full}\n"
                          f"({'exists' if exists else 'NOT FOUND in scene'})\n"
                          "左click: 選択中 joint を割当  /  右click: メニュー")
    else:
        cmds.button(btn, e=True, l="+ " + _ROLE_ABBREV[label][role_idx],
                     bgc=(0.32, 0.32, 0.32),
                     ann=f"joint: \n{role_full}  (未割当)\n"
                          "左click: 選択中 joint を割当  /  右click: メニュー")


def _slot_set_joint(btn, label, role_idx, joint):
    """slot に joint 名を格納 → visual 更新。"""
    joint = (joint or "").strip()
    if joint:
        cmds.button(btn, e=True, ann=f"joint: {joint}")
    else:
        cmds.button(btn, e=True, ann="")
    _slot_update_visual(btn, label, role_idx)


def _slot_pick_from_selection(btn, label, role_idx, *_):
    sel = cmds.ls(sl=True, type="joint") or cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Select a joint in the Outliner / viewport first")
        return
    # namespace 除去 + DAG full path 対応 ("|root|arm|wrist" → "wrist")
    joint = sel[0].split("|")[-1].split(":")[-1]
    _slot_set_joint(btn, label, role_idx, joint)


def _slot_prompt_name(btn, label, role_idx, *_):
    role_full = _FIXED_ROLE_JP[label][role_idx]
    current = _slot_get_joint(btn)
    result = cmds.promptDialog(
        title=f"Enter joint name — {role_full}",
        message=f"{label} / {_FIXED_ROLES[role_idx]} の joint 名:",
        text=current,
        button=["OK", "Cancel"], defaultButton="OK",
        cancelButton="Cancel", dismissString="Cancel")
    if result != "OK":
        return
    text = cmds.promptDialog(q=True, text=True) or ""
    _slot_set_joint(btn, label, role_idx, text)


def _slot_clear(btn, label, role_idx, *_):
    _slot_set_joint(btn, label, role_idx, "")


def _slot_select_in_scene(btn, *_):
    """slot に格納された joint を Maya scene で選択する。"""
    joint = _slot_get_joint(btn)
    if joint and cmds.objExists(joint):
        cmds.select(joint, r=True)
    else:
        cmds.warning(f"[{_PACKAGE}] joint '{joint}' が scene に存在しません")


# 旧 API 互換 (variable chain 側の textField で使用)
def _mapping_pick_selection(field_name, single=True):
    """選択中の joint 名を textField に流し込む。single=False は全 selection をカンマ結合。"""
    sel = cmds.ls(sl=True, type="joint") or cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Select joint(s) in the viewport / Outliner first")
        return
    names = [s.split("|")[-1].split(":")[-1] for s in sel]
    if single:
        cmds.textField(field_name, e=True, tx=names[0])
    else:
        cmds.textField(field_name, e=True, tx=", ".join(names))


def _mapping_current_from_ui():
    """現在の UI 状態を mapping dict にまとめる。"""
    fixed = {}
    for label in FIXED_LABELS:
        joints = []
        ok = True
        for role_idx in range(3):
            btn = _MAP_UI_FIXED_FIELDS.get((label, role_idx))
            v = _slot_get_joint(btn) if btn else ""
            if not v:
                ok = False; break
            joints.append(v)
        if ok:
            fixed[label] = joints
    chains = {}
    for name, refs in _MAP_UI_CHAIN_ROWS.items():
        name_field = refs["name_field"]
        joints_field = refs["joints_field"]
        cur_name = cmds.textField(name_field, q=True, tx=True).strip() if name_field else ""
        cur_joints = cmds.textField(joints_field, q=True, tx=True).strip() if joints_field else ""
        if not cur_name or not cur_joints:
            continue
        jlist = [x.strip() for x in cur_joints.split(",") if x.strip()]
        if len(jlist) >= 2:
            chains[cur_name] = jlist
    return {"fixed": fixed, "chains": chains}


def _mapping_populate_ui(mapping):
    """mapping dict を UI に流し込む。既存の chain row は全削除して詰め直し。"""
    # fixed → humanoid slot buttons
    fixed = mapping.get("fixed") or {}
    for label in FIXED_LABELS:
        joints = fixed.get(label) or []
        for role_idx in range(3):
            btn = _MAP_UI_FIXED_FIELDS.get((label, role_idx))
            if btn:
                v = joints[role_idx] if role_idx < len(joints) else ""
                _slot_set_joint(btn, label, role_idx, v)
    # variable chains: 既存 row を全削除
    if _MAP_UI_CHAINS_LAYOUT and cmds.layout(_MAP_UI_CHAINS_LAYOUT, ex=True):
        for row_refs in list(_MAP_UI_CHAIN_ROWS.values()):
            try:
                cmds.deleteUI(row_refs["row_layout"])
            except Exception:
                pass
    _MAP_UI_CHAIN_ROWS.clear()
    chains = mapping.get("chains") or {}
    for name, joints in chains.items():
        _mapping_add_chain_row(name=name, joints=joints)


def _mapping_add_chain_row(name="", joints=None):
    """可変 chain 用の 1 行 (name field + joints field + set/del button) を追加。"""
    if _MAP_UI_CHAINS_LAYOUT is None or not cmds.layout(_MAP_UI_CHAINS_LAYOUT, ex=True):
        return
    joints = joints or []
    cmds.setParent(_MAP_UI_CHAINS_LAYOUT)
    # v0.9.31 bugfix: 単調増加カウンタで名前衝突を防ぐ
    global _MAP_UI_ROW_COUNTER
    row_key = f"chain_row_{_MAP_UI_ROW_COUNTER}"
    _MAP_UI_ROW_COUNTER += 1
    row = cmds.rowLayout(row_key, nc=4, adj=2,
                          cw4=(80, 260, 90, 40),
                          ct4=("both", "both", "both", "both"),
                          co4=(2, 2, 2, 2))
    name_field = cmds.textField(tx=name, ann="chain 名 (spine, tail, hair 等)")
    joints_field = cmds.textField(tx=", ".join(joints),
                                   ann="joint 名をカンマ区切りで、根本→末端の順")
    cmds.button(l="Set from Sel", h=22,
                ann="選択された joint 群を選択順にこの chain に割当",
                c=lambda *_: _mapping_pick_selection(joints_field, single=False))
    cmds.button(l="✕", h=22, bgc=(0.5, 0.25, 0.25),
                c=lambda *_: _mapping_delete_chain_row(row_key))
    cmds.setParent("..")
    _MAP_UI_CHAIN_ROWS[row_key] = {
        "row_layout": row,
        "name_field": name_field,
        "joints_field": joints_field,
    }


def _mapping_delete_chain_row(row_key):
    refs = _MAP_UI_CHAIN_ROWS.pop(row_key, None)
    if not refs:
        return
    try:
        cmds.deleteUI(refs["row_layout"])
    except Exception:
        pass


def _ui_mapping_auto_detect(*_):
    detected = auto_detect_mapping()
    # variable chains は auto-detect 対象外だが、現在の UI 状態は保持したいので
    # fixed のみ上書きして chains は現状維持
    current = _mapping_current_from_ui()
    detected["chains"] = current.get("chains", {})
    _mapping_populate_ui(detected)
    n_fixed = len(detected.get("fixed") or {})
    print(f"[{_PACKAGE}] auto-detect: {n_fixed}/4 fixed chains detected")


def _ui_mapping_save(*_):
    mapping = _mapping_current_from_ui()
    set_mapping(mapping)
    n_fix = len(mapping.get("fixed") or {})
    n_var = len(mapping.get("chains") or {})
    print(f"[{_PACKAGE}] mapping saved: {n_fix} fixed / {n_var} variable")


def _ui_mapping_save_and_run(*_):
    mapping = _mapping_current_from_ui()
    set_mapping(mapping)
    if cmds.window(MAPPING_WINDOW, exists=True):
        cmds.deleteUI(MAPPING_WINDOW)
    # 通常の Full Auto Setup を走らせる (mapping は scene attr 経由で拾われる)
    scale = 1.0
    skip_dec = False
    del_junk = True
    if cmds.floatSliderGrp(_UI_SCALE, ex=True):
        scale = cmds.floatSliderGrp(_UI_SCALE, q=True, value=True)
    if cmds.checkBoxGrp(_UI_SKIP_DECOR, ex=True):
        skip_dec = cmds.checkBoxGrp(_UI_SKIP_DECOR, q=True, value1=True)
    if cmds.checkBoxGrp(_UI_DELETE_JUNK, ex=True):
        del_junk = cmds.checkBoxGrp(_UI_DELETE_JUNK, q=True, value1=True)
    full_auto_setup(scale=scale, skip_decoration=skip_dec,
                    delete_junk=del_junk, mapping=mapping)


def _ui_mapping_add_chain(*_):
    _mapping_add_chain_row()


def show_mapping_ui(*_):
    """Chain mapping UI を開く。scene に既存 mapping があれば load、
    無ければ auto-detect でプリセット表示。"""
    if cmds is None:
        raise RuntimeError("show_mapping_ui() must be called inside Maya.")
    if cmds.window(MAPPING_WINDOW, exists=True):
        cmds.deleteUI(MAPPING_WINDOW)
    _mapping_ui_reset_state()

    win = cmds.window(MAPPING_WINDOW,
                      t=f"AttachCtrl Mapping  --  v{__version__}",
                      w=420, h=760, mnb=True, mxb=False, s=True)
    cmds.columnLayout(adj=True, rs=6, cat=("both", 10))

    cmds.text(l="=== Fixed IK/FK chains — 人型 body diagram ===",
              al="left", fn="boldLabelFont")
    cmds.text(l="正面向きミラー配置 (character の L 側は viewer の右)。"
                "各ドットを左click で選択中 joint を割当、右click でメニュー。",
              al="left", fn="smallObliqueLabelFont", ww=True)

    # --- 人型 body-diagram (formLayout 絶対配置) ---
    form = cmds.formLayout(w=_HUMANOID_FORM_W, h=_HUMANOID_FORM_H)

    # 静的パーツ (head + torso hint)。button では無く text で装飾のみ。
    head = cmds.text(l="◯\nHEAD", al="center", h=36,
                     fn="smallBoldLabelFont")
    cmds.formLayout(form, e=True,
                     af=[(head, "top", 8), (head, "left", 165)])
    torso = cmds.text(l="│ TORSO │", al="center", h=20,
                       fn="smallObliqueLabelFont")
    cmds.formLayout(form, e=True,
                     af=[(torso, "top", 155), (torso, "left", 140)])
    pelvis = cmds.text(l="─ PELVIS ─", al="center", h=20,
                        fn="smallObliqueLabelFont")
    cmds.formLayout(form, e=True,
                     af=[(pelvis, "top", 215), (pelvis, "left", 135)])
    side_hint = cmds.text(l="R (character 右肩・右脚)         L (character 左肩・左脚)",
                          al="center", h=16,
                          fn="smallObliqueLabelFont")
    cmds.formLayout(form, e=True,
                     af=[(side_hint, "top", 46), (side_hint, "left", 12)])

    # 12 個の joint slot button
    for (label, role_idx), (x, y) in _HUMANOID_POSITIONS.items():
        btn_name = f"map_slot_{label}_{role_idx}"
        btn = cmds.button(btn_name, l="+ " + _ROLE_ABBREV[label][role_idx],
                           w=72, h=24,
                           bgc=(0.32, 0.32, 0.32),
                           ann=f"{_FIXED_ROLE_JP[label][role_idx]} (未割当)")
        # left-click: pick from selection。late-binding 対策で default args capture
        cmds.button(btn, e=True,
                     c=lambda _x=None, _b=btn, _l=label, _r=role_idx:
                         _slot_pick_from_selection(_b, _l, _r))
        # right-click menu
        pu = cmds.popupMenu(p=btn, button=3)
        cmds.menuItem(l="Pick from selection",
                      c=lambda _x=None, _b=btn, _l=label, _r=role_idx:
                          _slot_pick_from_selection(_b, _l, _r))
        cmds.menuItem(l="Enter joint name…",
                      c=lambda _x=None, _b=btn, _l=label, _r=role_idx:
                          _slot_prompt_name(_b, _l, _r))
        cmds.menuItem(l="Select in scene",
                      c=lambda _x=None, _b=btn: _slot_select_in_scene(_b))
        cmds.menuItem(divider=True)
        cmds.menuItem(l="Clear",
                      c=lambda _x=None, _b=btn, _l=label, _r=role_idx:
                          _slot_clear(_b, _l, _r))
        _MAP_UI_FIXED_FIELDS[(label, role_idx)] = btn
        cmds.formLayout(form, e=True,
                         af=[(btn, "left", x), (btn, "top", y)])

    cmds.setParent("..")   # exit formLayout, back to outer columnLayout

    cmds.text(l="灰 = 未割当  /  緑 = OK  /  赤 = joint 不在 (rename か再割当)",
              al="center", fn="smallObliqueLabelFont")

    cmds.separator(h=8, style="in")
    cmds.text(l="=== Variable chains (spine / tail / hair 等、可変長) ===",
              al="left", fn="boldLabelFont")
    cmds.text(l="Set from Sel: 選択された joint を根本→末端の順で登録。",
              al="left", fn="smallObliqueLabelFont")

    cmds.rowLayout(nc=1, adj=1, cw=(1, 200))
    cmds.button(l="+ Add chain", h=22, c=_ui_mapping_add_chain,
                bgc=(0.30, 0.55, 0.30))
    cmds.setParent("..")

    global _MAP_UI_CHAINS_LAYOUT
    _MAP_UI_CHAINS_LAYOUT = cmds.columnLayout(adj=True, rs=2)
    cmds.setParent("..")

    cmds.separator(h=10, style="in")
    cmds.rowLayout(nc=3, adj=3, cw3=(140, 140, 200),
                   ct3=("both", "both", "both"), co3=(4, 4, 4))
    cmds.button(l="Auto-detect names", h=28, c=_ui_mapping_auto_detect)
    cmds.button(l="Save to scene", h=28, c=_ui_mapping_save,
                bgc=(0.35, 0.55, 0.75))
    cmds.button(l="Save & Run Full Auto", h=28, c=_ui_mapping_save_and_run,
                bgc=(0.90, 0.55, 0.10))
    cmds.setParent("..")

    # 初期表示: scene に mapping があれば load、無ければ auto-detect
    existing = get_mapping()
    if (existing.get("fixed") or existing.get("chains")):
        _mapping_populate_ui(existing)
    else:
        _mapping_populate_ui(auto_detect_mapping())

    cmds.showWindow(win)
    return win


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
