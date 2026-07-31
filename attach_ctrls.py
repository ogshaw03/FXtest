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


__version__ = "0.9.11"


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
    """mGear の右クリック context menu を発火させる。
    VPMENU scout が mGear 5.0 の `dagmenu.py:742` を実解析した結果:

    1. **primary trigger**: `cmds.objExists("<sel>.isCtl")` bool attr
    2. **Maya 標準 controller tag** (`cmds.controller`) — pick-walk 連携
    3. **`rig_controllers_grp` objectSet 登録** — mGear の menu fill() が
       `cmds.sets(cmds.ls(cmds.listConnections(ctl), type='objectSet'), q=True)`
       を叩き、空 list だと TypeError で menu 構築が途中失敗する。純正
       shifter は必ずここに add する。**未登録が「右クリメニュー出ない」真因**
    """
    # 1. isCtl bool (mGear の唯一の gate)
    try:
        if not cmds.attributeQuery("isCtl", node=ctl, exists=True):
            cmds.addAttr(ctl, ln="isCtl", at="bool", dv=True, k=False)
        cmds.setAttr(ctl + ".isCtl", channelBox=False)
    except Exception:
        pass
    # 2. Maya 2019+ controller tag
    try:
        cmds.controller(ctl)
    except Exception:
        pass
    # 3. mGear が要求する rig_controllers_grp objectSet に登録
    try:
        set_name = "rig_controllers_grp"
        if not cmds.objExists(set_name):
            cmds.sets(name=set_name, empty=True)
        if not cmds.sets(ctl, isMember=set_name):
            cmds.sets(ctl, add=set_name)
    except Exception:
        pass
    # 4. v0.9.11 Mirror Pose 対応: `invTx..invSz` 9 bool attr。
    # side が R の ctl は default で invTx=1, invRy=1, invRz=1 (YZ 平面で
    # ミラーする mGear 慣習)。L/C は全 0。
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
    """override color + mGear/Maya の controller marker を一括で付ける。
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
    for pat in ("*_ikfk_oc", "*_fk_oc", "*_ik_orient_oc"):
        for con in cmds.ls(pat, type="orientConstraint") or []:
            _safe_del(con, "orient constraint")
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
    # dual chain: <joint>_ik / <joint>_fk / <joint>_mth の joint
    # (_mth = mGear IK/FK match target、AUDIT #2 で削除漏れが判明)
    # 加えて reverse foot 用 helper (<ankle>_rfBallBone / _rfToeBone)
    for suf in ("_ik", "_fk", "_mth", "_rfBallBone", "_rfToeBone"):
        for j in cmds.ls("*" + suf, type="joint") or []:
            if cmds.objExists(j):
                _safe_del(j, "generated joint")
    # attach_ctrls 起源 ctl (isCtl marker + Pass 2 で親 joint 下に移動されて
    # ROOT_GROUP 削除で残った *_ctl / *_npo) を全部掃討する (AUDIT2 NEW: 2回目
    # setup で 90+ warning "already exists; skipping" の原因)。
    for ctl in cmds.ls("*_ctl", type="transform") or []:
        if not cmds.objExists(ctl):
            continue
        try:
            has_marker = cmds.attributeQuery("isCtl", node=ctl, exists=True)
        except Exception:
            has_marker = False
        if has_marker:
            _safe_del(ctl, "orphan ctl")
    # 対応する npo も掃討
    for npo in cmds.ls("*_npo", type="transform") or []:
        if cmds.objExists(npo):
            _safe_del(npo, "orphan npo")
    # rig_controllers_grp / attach_ctrls 起源の objectSet も片付け
    if cmds.objExists("rig_controllers_grp"):
        _safe_del("rig_controllers_grp", "controllers set")
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
    # visual radius を hero と同じに (0.016 想定)
    try:
        cmds.setAttr(n + ".radius", cmds.getAttr(orig + ".radius"))
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
    # mGear dagmenu が探す `<label>_blend` サフィックスを追加 (MENU scout)。
    # `_blend` を master、`IK_FK` は driven にする (mGear の IK/FK Switch は
    # setAttr `_blend` を叩くので、以前の逆方向接続だと "locked or connected"
    # エラーが出て切替失敗していた)。
    blend_attr = label + "_blend"
    if not cmds.attributeQuery(blend_attr, node=ui_host, exists=True):
        cmds.addAttr(ui_host, ln=blend_attr, at="float",
                     min=0.0, max=1.0, dv=1.0, k=True)
    # _blend → IK_FK: mGear が _blend を叩けば IK_FK が追従し既存の内部配線
    # (orient blend / stretch gate 等) が反応する
    try:
        if not cmds.isConnected(ui_host + "." + blend_attr, ui_host + ".IK_FK"):
            cmds.connectAttr(ui_host + "." + blend_attr,
                             ui_host + ".IK_FK", f=True)
    except Exception:
        pass
    # BEHAV-A: IK_FK / ikVis / fkVis は driven (locked) なので Channel Box
    # から setAttr するとエラー。keyable/channelBox 両方 False にして
    # ユーザは `_blend` のみを触る運用に統一。
    for driven in ("IK_FK", "ikVis", "fkVis"):
        try:
            cmds.setAttr(ui_host + "." + driven, k=False, channelBox=False)
        except Exception:
            pass
    # コンポーネント配下 ctl リスト用 message array (mGear が
    # `<label>_id0_ctl_cnx` を叩いて全メンバーを取得する)
    ctl_cnx_attr = label + "_id0_ctl_cnx"
    if not cmds.attributeQuery(ctl_cnx_attr, node=ui_host, exists=True):
        cmds.addAttr(ui_host, ln=ctl_cnx_attr, at="message",
                     multi=True, im=False)
    # mth joints (snap target = bind pose 保持 duplicate)
    mth_joints = []
    for orig in (start, mid, end):
        mth_name = orig + "_mth"
        if not cmds.objExists(mth_name):
            try:
                m = _dup_hero_joint(orig, "_mth", new_parent=orig)
                try: cmds.setAttr(m + ".drawStyle", 2)
                except Exception: pass
                mth_joints.append(m)
            except Exception:
                mth_joints.append(None)
        else:
            mth_joints.append(mth_name)

    def _tag_mgear_ctl(ctl, role, mth_joint):
        for a in ("ctl_role", "uiHost"):
            if not cmds.attributeQuery(a, node=ctl, exists=True):
                try: cmds.addAttr(ctl, ln=a, dt="string")
                except Exception: pass
        try:
            cmds.setAttr(ctl + ".ctl_role", role, type="string")
            cmds.setAttr(ctl + ".uiHost", ui_host, type="string")
        except Exception:
            pass
        if not cmds.attributeQuery("match_ref", node=ctl, exists=True):
            try: cmds.addAttr(ctl, ln="match_ref", at="message")
            except Exception: pass
        if mth_joint and cmds.objExists(mth_joint):
            try:
                cmds.connectAttr(mth_joint + ".message", ctl + ".match_ref", f=True)
            except Exception:
                pass
        try:
            idx = cmds.getAttr(ui_host + "." + ctl_cnx_attr, size=True) or 0
            cmds.connectAttr(ctl + ".message",
                             ui_host + "." + ctl_cnx_attr + "[" + str(idx) + "]",
                             f=True)
        except Exception:
            pass

    # v0.9.7: ik_ctl.IK_FK proxy を削除 (SPIDERMAN scout + ユーザ意向:
    # 「IKFKSwitch が色んな controller に紐付いててややこしい」)。
    # switch は UI host (option ctl) 一本に集約、UI host 選択 → 右クリ →
    # mGear IK/FK Switch という一貫フローに統一。

    rev = cmds.createNode("reverse", n=label + "_ikfk_rev")
    cmds.connectAttr(ui_host + ".IK_FK", rev + ".inputX")

    # --- 7. Blend original hero joints between IK chain and FK chain ---
    # orientConstraint mo=True で local space 差異を初期化時に吸収。
    # twist bones は元 hero joint の child なので、hero joint 回転すれば自然追従。
    # IK solver は clean chain だけ動かし、twist bones を直接触らない。
    for orig, ikj, fkj in zip([start, mid, end], ik_chain, fk_chain):
        # end joint (wrist/ankle) は IK solver が rotate を制御しないので、
        # ikj (wrist_ik) 経由でなく ik_ctl を直接 orient source に使う。
        # これで wrist_ik 経由の local-space 不整合が消え、TWIST scout 報告の
        # 手首捻れ (arm_R wrist 127°) が解消する。
        ikj_source = ik_ctl if orig == end else ikj
        cons = cmds.orientConstraint(fkj, ikj_source, orig, mo=True,
                                     n=orig + "_ikfk_oc")[0]
        wal = cmds.orientConstraint(cons, q=True, wal=True)  # [fkW, ikW]
        cmds.connectAttr(rev + ".outputX",  cons + "." + wal[0])
        cmds.connectAttr(ui_host + ".IK_FK", cons + "." + wal[1])
    # 従来の orientConstraint(ik_ctl, wrist_ik, mo=True) は削除
    # (wrist_ik を driven する必要が無くなった)

    # mGear dagmenu 用に各 ctl を tag (MENU scout 優先度A: IK/FK Switch 発火)
    _tag_mgear_ctl(ik_ctl, "ik", mth_joints[2])
    _tag_mgear_ctl(pv_ctl, "upv", mth_joints[1])
    for (_, fk_ctl), mth, role in zip(fk_ctls, mth_joints, ["fk0","fk1","fk2"]):
        _tag_mgear_ctl(fk_ctl, role, mth)

    # --- 8. Pole vector constraint (orient blend 構築の後で張る) ---
    # 判定は world X 軸 (bone 軸) の acos で行う。start+mid+end 3 joint の合算 drift
    # を最小化する twist を選ぶ (FOOTROT scout 発見: start だけだと elbow/wrist の
    # 163°/127° flip を見逃す)。
    import math as _math
    def _bone_axis_diff(j, ref_matrix):
        m = cmds.xform(j, q=True, ws=True, m=True) or [0]*16
        dot = m[0]*ref_matrix[0] + m[1]*ref_matrix[1] + m[2]*ref_matrix[2]
        dot = max(-1.0, min(1.0, dot))
        return _math.degrees(_math.acos(dot))

    bind_matrices = {
        j: cmds.xform(j, q=True, ws=True, m=True) for j in (start, mid, end)
    }

    def _total_drift():
        return sum(_bone_axis_diff(j, bind_matrices[j]) for j in bind_matrices)

    cmds.poleVectorConstraint(pv_ctl, ik_handle)

    # --- 8.5. Twist 自動補正 (RP solver plane flip 対策) ---
    # AUDIT #11: v0.9.0 の freeze_joint_rotations で bind pose が identity 化
    # された後、全 chain で twist=0° が最適解に収束する (実測)。73 候補 × 4
    # chain の総当りは 10-15% の実行時間を消費するだけで無駄なので、初期解
    # が既に十分小さければスキップ。念のため fallback として drift > 5° の
    # 場合のみ限定的に探索 (v0.9.0 前の pose データ想定)。
    best_twist = 0.0
    best_drift = _total_drift()
    _pw_sub(80.0, f"Solve twist ({label})")
    if best_drift > 5.0:
        for twist_try in range(-180, 181, 15):  # 25 候補に絞る
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
            for h in hero_bones:
                if not cmds.objExists(h):
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

    # AUDIT #13: 古い `switch=<ik_ctl>.IK_FK` print を UI host 参照に更新
    print(f"[{_PACKAGE}] IK/FK rig: {label}  IK={ik_ctl}  PV={pv_ctl}  "
          f"FK={[c for _, c in fk_ctls]}  switch={ui_host}.{blend_attr}")

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
        below = candidates
    ground_y = min(v[1] for v in below)

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
        # orig の現 world matrix を fk_ctl に転写。
        # fk_ctl は fk_npo の子なので、fk_ctl.matrix = orig_wm * inv(fk_npo_wm)
        # cmds.xform ws=True m=... はこれを自動計算してくれる。
        orig_wm = cmds.xform(orig, q=True, ws=True, m=True)
        cmds.xform(fk_ctl, ws=True, m=orig_wm)
    # UI host は start joint に basicallyy 対応。start_j + "_UI_ctl" で探す
    # v0.9.8: _blend (master) を叩く。IK_FK は driven で locked (BEHAV-B)。
    ui = start_j + "_UI_ctl"
    blend_attr = start_j + "_blend"
    if cmds.objExists(ui) and cmds.attributeQuery(blend_attr, node=ui, exists=True):
        cmds.setAttr(ui + "." + blend_attr, 0)
    print(f"[{_PACKAGE}] snap_fk_to_ik: {start_j} -> {blend_attr}=0")


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
        wm = cmds.xform(end_j, q=True, ws=True, m=True)
        cmds.xform(ik_ctl, ws=True, m=wm)
    if cmds.objExists(pv_ctl) and cmds.objExists(mid_j):
        wp = cmds.xform(mid_j, q=True, ws=True, t=True)
        cmds.xform(pv_ctl, ws=True, t=wp)
    ui = start_j + "_UI_ctl"
    blend_attr = start_j + "_blend"
    if cmds.objExists(ui) and cmds.attributeQuery(blend_attr, node=ui, exists=True):
        cmds.setAttr(ui + "." + blend_attr, 1)
    print(f"[{_PACKAGE}] snap_ik_to_fk: {start_j} -> {blend_attr}=1")


def setup_all_ik_fk():
    """検出できた L/R arm/leg 全てに IK/FK rig を構築。leg には reverse foot も。"""
    chains = find_ik_chains()
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
        side = "L" if label.startswith("L") else "R"
        try:
            r = setup_ik_fk(chain[0], chain[1], chain[2], side=side)
            results.append(r)
            # leg なら reverse foot も試みる
            if "leg" in label:
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

        # Step 2.5: freeze rotate to jointOrient (5-15%)
        _pw_span(5, 15); _pw_sub(0, "Freeze joint rotations...")
        freeze_joint_rotations()

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

        # Step 7: IK/FK setup (IK ctl NPO は独立世界置き = 足接地 目的) (55-100%)
        _pw_span(55, 100); _pw_sub(0, "Setup IK/FK chains...")
        ik_results = setup_all_ik_fk()

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
    cmds.text(l="IK/FK 切替: UI host (option ctl) の '<label>_blend' attr (0=FK, 1=IK)",
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
