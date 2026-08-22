"""Jiggle Bones v0.2.0 — 揺れもの (hair/skirt/ribbon/tail 等) 専用 dynamics

## 位置づけ
attach_ctrls は arm/leg/spine 等の主 rig を担当し、揺れもの骨は
skip_decoration=True (default v0.9.33+) で除外される。本モジュールは
その除外されている chain だけを対象に、hairSystem ベースの dynamics
を組む。

## 実装方針 (ユーザー合意事項)
- **Method**: hairSystem + follicle + spline IK (Maya 純正 nHair)
- **Nucleus**: シーン共有 (1 個)、collision solver
- **hairSystem 粒度**: **カテゴリごとに 1 個** (hair 系は 1 個、skirt 系は 1 個…)
  → per-category パラメータ (stiffness/damp/gravity 等)
- **Rest curve**: **親骨に parentConstraint** — キャラの動きに rest 位置が
  追従する (bind pose 固定ではない)
- **Live dynamics**: timeline scrub でリアルタイム揺れ、bake 不要
- **Collider**: **手動選択** (UI で body mesh を選ぶ)

## API
    import jiggle_bones as jb

    # 検出
    chains = jb.find_jiggle_chains()

    # per-chain dynamic setup
    r = jb.create_jiggle_for_chain(chain_joints, category="hair")
    # → dict {rest_curve, dynamic_curve, follicle, ik_handle, hair_system}

    # remove (undo)
    jb.remove_jiggle_for_chain(chain_joints)

    # collider 追加/除去
    jb.add_collider("body_geo")
    jb.remove_collider("body_geo")

    # カテゴリ params
    jb.set_category_params("hair", stiffness=0.15, damp=0.20,
                            startCurveAttract=0.10)
    jb.get_category_params("hair")

    # UI
    jb.show_ui()

## Scene organization
すべての生成物は `jiggle_bones_grp` transform 下に集約:
    jiggle_bones_grp
    ├── jb_nucleus
    ├── jb_hairSystem_hair
    ├── jb_hairSystem_skirt
    ├── jb_rest_H1_crv      (親骨に parentConstraint 済み)
    ├── jb_dyn_H1_crv       (hairSystem output、spline IK 参照)
    ├── jb_foll_H1
    ├── jb_ikh_H1           (spline IK handle)
    ├── jb_collider_body_geo
    └── ...
"""
import maya.cmds as cmds
import maya.mel as mel

__version__ = "0.3.4"
WINDOW = "jiggleBonesWin"
JB_GROUP = "jiggle_bones_grp"
NUCLEUS_NAME = "jb_nucleus"

# UI 表示用の日本語ラベル (API 用 identifier は英語のまま維持)
_CATEGORY_JP = {
    "hair":    "髪",
    "skirt":   "スカート",
    "ribbon":  "リボン",
    "sleeve":  "袖",
    "necktie": "ネクタイ",
    "coat":    "コート",
    "ear":     "耳",
    "tail":    "しっぽ",
}
_PARAM_LABEL_JP = {
    "stiffness":         "硬さ (stiffness)",
    "damp":              "減衰 (damp)",
    "startCurveAttract": "元形状復元 (attract)",
    "mass":              "質量 (mass)",
}

# =========================================================================
# Chain classification / detection (v0.1.0 と同一)
# =========================================================================

_JIGGLE_TOKENS = {
    "hair":    ("hair", "front_hair", "back_hair", "side_hair"),
    "skirt":   ("skirt",),
    "ribbon":  ("ribbon",),
    "sleeve":  ("sleeve",),
    "necktie": ("necktie",),
    "coat":    ("coat",),
    "ear":     ("cat_ear", "ear"),
    "tail":    ("tail",),
}

# カテゴリ別 default params (経験則の初期値、UI で dial 可)
DEFAULT_PARAMS_BY_CATEGORY = {
    "hair":    {"stiffness": 0.15, "damp": 0.30, "startCurveAttract": 0.10,
                "mass": 1.0},
    "skirt":   {"stiffness": 0.10, "damp": 0.15, "startCurveAttract": 0.05,
                "mass": 1.5},
    "ribbon":  {"stiffness": 0.05, "damp": 0.10, "startCurveAttract": 0.15,
                "mass": 0.4},
    "sleeve":  {"stiffness": 0.10, "damp": 0.20, "startCurveAttract": 0.10,
                "mass": 0.8},
    "necktie": {"stiffness": 0.20, "damp": 0.25, "startCurveAttract": 0.15,
                "mass": 0.5},
    "coat":    {"stiffness": 0.20, "damp": 0.25, "startCurveAttract": 0.10,
                "mass": 1.5},
    "ear":     {"stiffness": 0.40, "damp": 0.30, "startCurveAttract": 0.20,
                "mass": 0.5},
    "tail":    {"stiffness": 0.10, "damp": 0.15, "startCurveAttract": 0.05,
                "mass": 1.0},
}


def _short(name):
    return name.split("|")[-1].split(":")[-1]


def _classify(joint):
    lo = _short(joint).lower()
    core = lo
    for suf in ("_l", "_r", "_c", "_end"):
        if core.endswith(suf):
            core = core[:-len(suf)]
    s = _short(joint)
    if len(s) >= 2 and s[0] in ("H", "h") and s[1:].split("_")[0].isdigit():
        return "hair"
    for tag, tokens in _JIGGLE_TOKENS.items():
        for t in tokens:
            if t in core:
                return tag
    return None


def _walk_chain(root, tag):
    def _longest(node):
        kids = cmds.listRelatives(node, c=True, type="joint") or []
        same = [k for k in kids if _classify(k) == tag]
        if not same:
            return [node]
        best = []
        for k in same:
            sub = _longest(k)
            if len(sub) > len(best):
                best = sub
        return [node] + best
    return _longest(root)


def find_jiggle_chains():
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    all_joints = cmds.ls(type="joint") or []
    tagged = {}
    for j in all_joints:
        t = _classify(j)
        if t:
            tagged[j] = t
    result = {tag: [] for tag in _JIGGLE_TOKENS.keys()}
    for j, tag in tagged.items():
        parent = cmds.listRelatives(j, p=True, type="joint") or []
        parent_tag = _classify(parent[0]) if parent else None
        if parent_tag == tag:
            continue
        chain = _walk_chain(j, tag)
        if len(chain) >= 2:
            result[tag].append(chain)
    return {tag: chains for tag, chains in result.items() if chains}


# =========================================================================
# Nucleus / hairSystem / follicle / spline IK setup
# =========================================================================

def _ensure_jb_group():
    if not cmds.objExists(JB_GROUP):
        cmds.group(em=True, n=JB_GROUP, w=True)
    return JB_GROUP


def _parent_to_jb(node):
    try:
        parents = cmds.listRelatives(node, p=True) or []
        if not parents or parents[0] != JB_GROUP:
            cmds.parent(node, JB_GROUP)
    except Exception:
        pass


def _get_or_create_nucleus():
    """シーン共有の nucleus を返す。無ければ作って time に接続。"""
    _ensure_jb_group()
    if cmds.objExists(NUCLEUS_NAME):
        return NUCLEUS_NAME
    n = cmds.createNode("nucleus", n=NUCLEUS_NAME)
    try:
        cmds.connectAttr("time1.outTime", n + ".currentTime", f=True)
    except Exception:
        pass
    try:
        cmds.setAttr(n + ".startFrame",
                      cmds.playbackOptions(q=True, min=True))
    except Exception:
        pass
    # nucleus は shape なので transform を取得
    parents = cmds.listRelatives(n, p=True) or []
    top = parents[0] if parents else n
    if top != NUCLEUS_NAME and cmds.objExists(top):
        try:
            top = cmds.rename(top, NUCLEUS_NAME)
        except Exception:
            pass
    _parent_to_jb(top)
    return n


def _next_multi_index(node, multi_attr):
    """multi attr の未使用 index を返す。"""
    existing = cmds.getAttr(f"{node}.{multi_attr}", mi=True) or []
    return (max(existing) + 1) if existing else 0


def _get_or_create_hair_system(category):
    """カテゴリごとに 1 個の hairSystem を返す。無ければ作って nucleus に接続。"""
    _ensure_jb_group()
    hs_xform = f"jb_hairSystem_{category}"
    hs_shape = hs_xform + "Shape"
    if cmds.objExists(hs_shape):
        return hs_xform, hs_shape

    # hairSystem shape を作る
    hs_shape = cmds.createNode("hairSystem", n=hs_shape)
    xf_parents = cmds.listRelatives(hs_shape, p=True) or []
    if xf_parents:
        hs_xform = cmds.rename(xf_parents[0], hs_xform)
    # rebuild shape name after xform rename
    shape_now = cmds.listRelatives(hs_xform, s=True) or []
    if shape_now:
        hs_shape = shape_now[0]

    # nucleus 接続
    nucleus = _get_or_create_nucleus()
    try:
        cmds.connectAttr("time1.outTime", hs_shape + ".currentTime", f=True)
    except Exception:
        pass
    idx = _next_multi_index(nucleus, "inputActive")
    try:
        # nucleus attr: inputActive / inputActiveStart / outputObjects
        # (initial 誤: inputStart は存在しない — v0.2.0 実測で修正)
        cmds.connectAttr(hs_shape + ".currentState",
                          f"{nucleus}.inputActive[{idx}]", f=True)
        cmds.connectAttr(hs_shape + ".startState",
                          f"{nucleus}.inputActiveStart[{idx}]", f=True)
        cmds.connectAttr(f"{nucleus}.outputObjects[{idx}]",
                          hs_shape + ".nextState", f=True)
        cmds.connectAttr(f"{nucleus}.startFrame",
                          hs_shape + ".startFrame", f=True)
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] nucleus 接続失敗: {exc}")

    # default params
    for attr, val in DEFAULT_PARAMS_BY_CATEGORY.get(category, {}).items():
        try:
            cmds.setAttr(f"{hs_shape}.{attr}", val)
        except Exception:
            pass

    _parent_to_jb(hs_xform)
    return hs_xform, hs_shape


# ---- per-chain 名前規則 (remove 時の逆引きに使う) ----
def _chain_id(chain):
    """chain の識別子 (rest_curve 等の suffix に使う)。"""
    return _short(chain[0])


def _rest_curve_name(chain):
    return f"jb_rest_{_chain_id(chain)}_crv"


def _dyn_curve_name(chain):
    return f"jb_dyn_{_chain_id(chain)}_crv"


def _follicle_name(chain):
    return f"jb_foll_{_chain_id(chain)}"


def _ik_handle_name(chain):
    return f"jb_ikh_{_chain_id(chain)}"


# v0.3.0 追加: FK ctl / dual chain 用の命名
def _fk_joint_name(orig_joint):
    return _short(orig_joint) + "_jbFK"


def _dyn_joint_name(orig_joint):
    return _short(orig_joint) + "_jbDYN"


def _fk_ctl_name(orig_joint):
    return _short(orig_joint) + "_jbCtl"


def _fk_npo_name(orig_joint):
    return _short(orig_joint) + "_jbNpo"


def _blend_pc_name(orig_joint):
    return _short(orig_joint) + "_jbPC"


def _dynrev_name(chain):
    return f"jb_dynrev_{_chain_id(chain)}"


def _root_pc_name(chain):
    return f"jb_rootpc_{_chain_id(chain)}"


def _root_offset_pc_name(chain):
    return f"jb_rootoff_{_chain_id(chain)}"


def _cluster_name(chain, cv_idx):
    return f"jb_clu_{_chain_id(chain)}_cv{cv_idx}"


# =========================================================================
# v0.3.0 helpers: FK ctl / dual chain / rest curve binding
# =========================================================================

def _make_cube_curve(name, size=1.0):
    """簡易 wireframe cube (単一 nurbsCurve、d=1)。attach_ctrls と同形。"""
    s = size * 0.5
    pts = [(-s, s, s), (s, s, s), (s, -s, s), (-s, -s, s), (-s, s, s),
           (-s, s, -s), (s, s, -s), (s, s, s), (s, s, -s), (s, -s, -s),
           (s, -s, s), (s, -s, -s), (-s, -s, -s), (-s, -s, s),
           (-s, -s, -s), (-s, s, -s)]
    knots = list(range(len(pts)))
    return cmds.curve(d=1, p=pts, k=knots, n=name)


def _set_ctl_color(ctl, index):
    """override color index を curve shape に設定。"""
    shapes = cmds.listRelatives(ctl, s=True, type="nurbsCurve") or []
    for sh in shapes:
        try:
            cmds.setAttr(sh + ".overrideEnabled", 1)
            cmds.setAttr(sh + ".overrideColor", index)
        except Exception:
            pass


def _lock_hide_attrs(node, attrs):
    for a in attrs:
        try:
            cmds.setAttr(f"{node}.{a}", l=True, k=False, cb=False)
        except Exception:
            pass


def _chain_segment_length(chain):
    """chain 内 joint 間の平均距離 (ctl scale 計算用)。"""
    import math
    if len(chain) < 2:
        return 1.0
    dists = []
    for a, b in zip(chain, chain[1:]):
        pa = cmds.xform(a, q=True, ws=True, t=True)
        pb = cmds.xform(b, q=True, ws=True, t=True)
        dists.append(math.sqrt(sum((x - y) ** 2 for x, y in zip(pa, pb))))
    return sum(dists) / len(dists) if dists else 1.0


def _dup_chain(orig_chain, suffix_fn):
    """orig_chain と同 world 位置の clean duplicate chain を作って返す。
    元 chain の親子関係を保つ (親→子順)。twist bones や子骨は含めない。"""
    new = []
    for j in orig_chain:
        nm = suffix_fn(j)
        if cmds.objExists(nm):
            cmds.delete(nm)
        # po=True で children を含めず duplicate
        dup = cmds.duplicate(j, po=True, n=nm)[0]
        # 一旦 world に出す (再親子付けのため)
        try:
            cmds.parent(dup, world=True)
        except Exception:
            pass
        new.append(dup)
    # 親子付け (world 位置は自動保持)
    for i in range(1, len(new)):
        cmds.parent(new[i], new[i - 1])
    return new


def _create_fk_ctls_for_chain(orig_chain):
    """各 orig joint 位置に FK cube ctl を配置。
    root ctl は translate + rotate 自由、子 ctl は rotate のみ。
    root ctl の npo は jiggle_bones_grp 下 (親骨があれば parentConstraint 追従)。
    子 ctl の npo は親 ctl の下 (nested hierarchy)。

    Returns: (ctls, npos)  ← 各 list は root → tip 順
    """
    seg = _chain_segment_length(orig_chain)
    ctl_size = max(seg * 0.4, 0.1)
    ctls, npos = [], []
    prev_ctl = None
    for i, j in enumerate(orig_chain):
        ctl_name = _fk_ctl_name(j)
        npo_name = _fk_npo_name(j)
        # 既存があれば削除 (再セットアップ対応)
        for nm in (ctl_name, npo_name):
            if cmds.objExists(nm):
                cmds.delete(nm)
        ctl = _make_cube_curve(ctl_name, size=ctl_size)
        # color: root = orange(21), child = light-blue(28)
        _set_ctl_color(ctl, 21 if i == 0 else 28)
        npo = cmds.group(em=True, n=npo_name)
        cmds.parent(ctl, npo)
        cmds.matchTransform(npo, j, pos=True, rot=True)
        if i == 0:
            _parent_to_jb(npo)
            # 元 chain root に親骨があれば、その親骨に npo を parentConstraint
            # (キャラの動きに ctl 自体が追従、animator は local offset を打つ形)
            parent_j = cmds.listRelatives(j, p=True, type="joint") or []
            if parent_j:
                try:
                    cmds.parentConstraint(parent_j[0], npo, mo=True,
                                            n=_root_offset_pc_name(orig_chain))
                except Exception:
                    pass
            # root ctl: translate + rotate 開放、scale + visibility ロック
            _lock_hide_attrs(ctl, ["sx", "sy", "sz", "v"])
        else:
            cmds.parent(npo, prev_ctl)
            # 子 ctl: rotate のみ (translate/scale ロック)
            _lock_hide_attrs(ctl, ["tx", "ty", "tz", "sx", "sy", "sz", "v"])
        prev_ctl = ctl
        ctls.append(ctl)
        npos.append(npo)
    return ctls, npos


def _bind_curve_cvs_to_joints(curve, joints):
    """curve の各 CV を対応 joint に cluster で固定 (CV 数 == joints 数 想定)。
    joint が動くと curve CV が同期して動く → rest curve が FK 追従。
    Returns: cluster transform 名の list。"""
    shape = cmds.listRelatives(curve, s=True, type="nurbsCurve") or []
    if not shape:
        return []
    shape = shape[0]
    n_cv = cmds.getAttr(shape + ".spans") + cmds.getAttr(shape + ".degree")
    clusters = []
    for i in range(min(n_cv, len(joints))):
        clu = cmds.cluster(f"{curve}.cv[{i}]",
                            n=_cluster_name([joints[0]], i))
        # returned = [clusterNode, clusterHandle]
        handle = clu[1]
        # cluster handle を joint 下に parent (joint 動くと CV も動く)
        try:
            cmds.parent(handle, joints[i])
            # cluster handle の visibility off (viewport 邪魔なので)
            cmds.setAttr(handle + ".visibility", 0)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] cluster parent failed: {exc}")
        clusters.append(handle)
    return clusters


def _create_rest_curve_from_chain(chain):
    """chain の joint 位置に沿って NURBS curve を作る (親追従は呼び出し側で設定)。"""
    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in chain]
    degree = 3 if len(pts) >= 4 else max(1, len(pts) - 1)
    # 既存を削除して作り直し (再セットアップ対応)
    curve_name = _rest_curve_name(chain)
    if cmds.objExists(curve_name):
        cmds.delete(curve_name)
    curve = cmds.curve(d=degree, p=pts, n=curve_name)
    _parent_to_jb(curve)
    return curve


def _add_follicle_to_hair_system(rest_curve, hs_shape, chain):
    """rest_curve を follicle 経由で hairSystem に接続し、output dynamic curve を返す。"""
    _ensure_jb_group()
    rest_shape = cmds.listRelatives(rest_curve, s=True, type="nurbsCurve") or []
    if not rest_shape:
        raise RuntimeError(f"rest curve {rest_curve} に nurbsCurve shape が無い")
    rest_shape = rest_shape[0]

    foll_shape = cmds.createNode("follicle", n=_follicle_name(chain) + "Shape")
    foll_xform = cmds.listRelatives(foll_shape, p=True)[0]
    foll_xform = cmds.rename(foll_xform, _follicle_name(chain))
    foll_shape = cmds.listRelatives(foll_xform, s=True)[0]

    # curve → follicle 接続 (start position)
    cmds.connectAttr(rest_shape + ".worldMatrix[0]",
                      foll_shape + ".startPositionMatrix", f=True)
    cmds.connectAttr(rest_shape + ".local",
                      foll_shape + ".startPosition", f=True)

    # follicle を hairSystem に登録
    idx = _next_multi_index(hs_shape, "inputHair")
    cmds.connectAttr(foll_shape + ".outHair",
                      f"{hs_shape}.inputHair[{idx}]", f=True)
    cmds.connectAttr(f"{hs_shape}.outputHair[{idx}]",
                      foll_shape + ".currentPosition", f=True)

    # output dynamic curve を生成
    dyn_shape_name = _dyn_curve_name(chain)
    dyn_shape = cmds.createNode("nurbsCurve", n=dyn_shape_name + "Shape",
                                 p=foll_xform)
    cmds.connectAttr(foll_shape + ".outCurve", dyn_shape + ".create", f=True)
    # dyn curve は follicle の transform 下に居るが、UI 上判別しやすいよう
    # 独立 transform 化 (parent hair system group 下に飛ばす)
    dyn_xform = cmds.rename(foll_xform, foll_xform)  # keep

    # follicle の default 設定: dynamic + hair sim
    try:
        cmds.setAttr(foll_shape + ".simulationMethod", 2)   # 0=static 1=static+collide 2=dynamic
        cmds.setAttr(foll_shape + ".pointLock", 1)          # 0=nothing 1=base (根元固定)
        cmds.setAttr(foll_shape + ".restPose", 1)           # 1=start curve
    except Exception:
        pass

    _parent_to_jb(foll_xform)
    return dyn_shape


def _create_spline_ik(chain, dynamic_curve_shape):
    """chain (親→末端) に spline IK を張り、dynamic curve に追従させる。

    ikHandle は ccv=False で「existing curve を使う」モードにすると返り値は
    (handleName, effectorName) の 2 要素 (curve 引数は既存指定なので入らない)。
    """
    ikh_name = _ik_handle_name(chain)
    ret = cmds.ikHandle(
        sj=chain[0], ee=chain[-1],
        sol="ikSplineSolver",
        ccv=False, pcv=False,
        c=dynamic_curve_shape,
        n=ikh_name,
    )
    ikh = ret[0] if isinstance(ret, (list, tuple)) else ret
    _parent_to_jb(ikh)
    return ikh


def create_jiggle_for_chain(chain, category=None):
    """chain (親→末端 joint list) に FK ctls + hairSystem dynamics overlay を組む。

    ## v0.3.0 挙動 (通常セットアップ + dynamics overlay)
      1. FK ctls を各 joint 位置に配置 (root は translate + rotate、子は rotate のみ)
      2. FK duplicate chain: FK ctls が rotate を駆動
      3. DYN duplicate chain: hairSystem 経由の spline IK が rotate を駆動
      4. 元 joint の rotate = parentConstraint(fk_j, dyn_j, orig, mo=True, st=(...))
         weight は root FK ctl の `dynamics` attr (0..1) で blend
         (dynamics=0 → FK 100%、dynamics=1 → DYN 100%)
      5. chain root が world-parented (親骨無し) の場合、root ctl → chain[0]
         への parentConstraint (translate のみ) で animator が chain 全体を
         移動できるように

    Args:
        chain:    元 joint 名 list (親→末端)
        category: hair/skirt/tail 等 (None なら _classify で自動判定)
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    if not chain or len(chain) < 2:
        cmds.warning(f"[jiggle_bones] chain too short: {chain}")
        return None
    if category is None:
        category = _classify(chain[0]) or "hair"

    # 既存があれば先に remove (二重張り防止)
    if is_chain_active(chain):
        remove_jiggle_for_chain(chain)

    _ensure_jb_group()
    hs_xform, hs_shape = _get_or_create_hair_system(category)

    # ---- 1. FK ctls (各 joint 位置に nested cube) ----
    fk_ctls, fk_npos = _create_fk_ctls_for_chain(chain)
    root_ctl = fk_ctls[0]

    # ---- 2. FK duplicate chain (FK ctls が rotate 駆動) ----
    fk_chain = _dup_chain(chain, _fk_joint_name)
    _parent_to_jb(fk_chain[0])
    for ctl, fkj in zip(fk_ctls, fk_chain):
        # orientConstraint: ctl の world rotate → fk joint の rotate
        cmds.orientConstraint(ctl, fkj, mo=False,
                                n=fkj + "_oc")
    # FK chain root は root ctl の WS transform に完全追従させる (translate も)
    cmds.pointConstraint(root_ctl, fk_chain[0], mo=False,
                          n=fk_chain[0] + "_pc")

    # ---- 3. DYN duplicate chain (spline IK が rotate 駆動) ----
    dyn_chain = _dup_chain(chain, _dyn_joint_name)
    _parent_to_jb(dyn_chain[0])
    # DYN chain root も root ctl に位置追従 (spline IK は rotate 駆動、
    # 起点位置は curve root から取るが、root joint も揃えておく)
    cmds.pointConstraint(root_ctl, dyn_chain[0], mo=False,
                          n=dyn_chain[0] + "_pc")

    # ---- 4. Rest curve: joint 位置に沿って作り、CV を FK chain に cluster 束縛 ----
    rest_curve = _create_rest_curve_from_chain(chain)
    _bind_curve_cvs_to_joints(rest_curve, fk_chain)

    # ---- 5. hairSystem follicle + dynamic curve + spline IK on DYN chain ----
    dyn_shape = _add_follicle_to_hair_system(rest_curve, hs_shape, chain)
    ikh = _create_spline_ik(dyn_chain, dyn_shape)

    # ---- 6. dynamics attr を root FK ctl に追加 (0..1、0=手付け FK / 1=full dyn) ----
    if not cmds.attributeQuery("dynBlend", node=root_ctl, exists=True):
        cmds.addAttr(root_ctl, ln="dynBlend", at="float",
                      min=0.0, max=1.0, dv=1.0, k=True)

    # ---- 7. 元 joint に FK/DYN rotate blend の parentConstraint (translate skip) ----
    rev = cmds.createNode("reverse", n=_dynrev_name(chain))
    cmds.connectAttr(f"{root_ctl}.dynBlend", rev + ".inputX", f=True)
    for orig, fkj, dynj in zip(chain, fk_chain, dyn_chain):
        pc = cmds.parentConstraint(
            fkj, dynj, orig, mo=True,
            st=("x", "y", "z"),   # translate は元階層 or 別 constraint に任せる
            n=_blend_pc_name(orig),
        )[0]
        wal = cmds.parentConstraint(pc, q=True, wal=True)  # [fkW, dynW]
        try:
            cmds.setAttr(pc + ".interpType", 2)  # shortest, euler flip 回避
        except Exception:
            pass
        cmds.connectAttr(f"{root_ctl}.dynBlend", pc + "." + wal[1], f=True)
        cmds.connectAttr(rev + ".outputX",       pc + "." + wal[0], f=True)

    # ---- 8. root joint に translate 用の parentConstraint (root_ctl → chain[0]) ----
    # 親骨があれば chain[0].translate は元 hierarchy 任せで OK。無ければ
    # root ctl の world 移動を chain[0] に反映する必要がある。
    parent_j = cmds.listRelatives(chain[0], p=True, type="joint") or []
    if not parent_j:
        try:
            cmds.pointConstraint(root_ctl, chain[0], mo=True,
                                    n=_root_pc_name(chain))
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] root translate constraint failed: {exc}")

    print(f"[jiggle_bones] setup {_chain_id(chain)} → category={category}, "
          f"joints={len(chain)}, root ctl={root_ctl} (dynamics attr on this)")
    return {
        "chain":         chain,
        "category":      category,
        "fk_chain":      fk_chain,
        "dyn_chain":     dyn_chain,
        "fk_ctls":       fk_ctls,
        "root_ctl":      root_ctl,
        "rest_curve":    rest_curve,
        "dynamic_curve": dyn_shape,
        "follicle":      _follicle_name(chain),
        "ik_handle":     ikh,
        "hair_system":   hs_xform,
        "dynamics_attr": f"{root_ctl}.dynBlend",  # UI や API に露出する blend attr
    }


def remove_jiggle_for_chain(chain):
    """create_jiggle_for_chain で作った node を掃除する (v0.3.0 dual chain 対応)。"""
    if cmds is None:
        return
    # 1. constraint 系 (元 joint への blend PC、root PC/offset PC、rev)
    for orig in chain:
        pc = _blend_pc_name(orig)
        if cmds.objExists(pc):
            try: cmds.delete(pc)
            except Exception: pass
    for nm in (_dynrev_name(chain), _root_pc_name(chain),
               _root_offset_pc_name(chain)):
        if cmds.objExists(nm):
            try: cmds.delete(nm)
            except Exception: pass

    # 2. dynamics attr (root FK ctl から除去)
    root_ctl_name = _fk_ctl_name(chain[0])
    if cmds.objExists(root_ctl_name) \
            and cmds.attributeQuery("dynBlend", node=root_ctl_name, exists=True):
        try:
            cmds.deleteAttr(root_ctl_name + ".dynBlend")
        except Exception:
            pass

    # 3. spline IK handle + effector
    ikh = _ik_handle_name(chain)
    if cmds.objExists(ikh):
        try: cmds.delete(ikh)
        except Exception: pass

    # 4. follicle + dyn curve + rest curve + clusters
    for nm in (_dyn_curve_name(chain), _follicle_name(chain),
               _rest_curve_name(chain)):
        if cmds.objExists(nm):
            try: cmds.delete(nm)
            except Exception: pass
    # clusters: CV 数だけ (実際の CV 数は curve が既に消えているので、
    # 元 chain の joint 数を上限に片っ端から検索)
    for i in range(len(chain) + 4):  # 少し余裕を持たせて
        clu = _cluster_name(chain, i)
        if cmds.objExists(clu):
            try: cmds.delete(clu)
            except Exception: pass

    # 5. FK ctls + npo + FK chain + DYN chain
    for orig in chain:
        for nm in (_fk_ctl_name(orig), _fk_npo_name(orig),
                    _fk_joint_name(orig), _dyn_joint_name(orig)):
            if cmds.objExists(nm):
                try: cmds.delete(nm)
                except Exception: pass


def is_chain_active(chain):
    """v0.3.0: root FK ctl があれば active とみなす (spline IK も併存)。"""
    return cmds.objExists(_fk_ctl_name(chain[0])) \
        or cmds.objExists(_ik_handle_name(chain))


# =========================================================================
# Collider (nRigid) 管理
# =========================================================================

def _collider_name(mesh):
    return f"jb_collider_{_short(mesh)}"


def add_collider(mesh):
    """指定 mesh を nRigid collider として nucleus に登録。

    Maya の `makeCollideNCloth` MEL は plugin 未 load の環境や mayapy standalone
    で `setActiveNucleusNode` が見つからないケースがあるため、node を直接
    createNode + connectAttr で組み立てる (依存少・移植性高)。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    if not cmds.objExists(mesh):
        cmds.warning(f"[jiggle_bones] mesh not found: {mesh}")
        return None
    # mesh shape 取得
    mesh_shapes = cmds.listRelatives(mesh, s=True, type="mesh") or []
    if not mesh_shapes:
        cmds.warning(f"[jiggle_bones] {mesh} に mesh shape が無い")
        return None
    mesh_shape = mesh_shapes[0]

    nucleus = _get_or_create_nucleus()

    # nRigid shape 作成 (transform も自動で作られる)
    shape_name = _collider_name(mesh) + "Shape"
    nr_shape = cmds.createNode("nRigid", n=shape_name)
    nr_xform_parents = cmds.listRelatives(nr_shape, p=True) or []
    if nr_xform_parents:
        try:
            cmds.rename(nr_xform_parents[0], _collider_name(mesh))
        except Exception:
            pass
    # rebuild shape name after rename
    nr_xform = _collider_name(mesh)
    shape_now = cmds.listRelatives(nr_xform, s=True) or []
    if shape_now:
        nr_shape = shape_now[0]

    # mesh → nRigid: worldMesh を inputMesh に接続
    try:
        cmds.connectAttr(mesh_shape + ".worldMesh[0]",
                          nr_shape + ".inputMesh", f=True)
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] mesh 接続失敗: {exc}")

    # nucleus に passive collider として登録
    idx = _next_multi_index(nucleus, "inputPassive")
    try:
        cmds.connectAttr(nr_shape + ".currentState",
                          f"{nucleus}.inputPassive[{idx}]", f=True)
        cmds.connectAttr(nr_shape + ".startState",
                          f"{nucleus}.inputPassiveStart[{idx}]", f=True)
        cmds.connectAttr(f"{nucleus}.startFrame",
                          nr_shape + ".startFrame", f=True)
        cmds.connectAttr("time1.outTime",
                          nr_shape + ".currentTime", f=True)
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] nucleus 接続失敗: {exc}")

    _parent_to_jb(nr_xform)
    print(f"[jiggle_bones] collider added: {mesh} → {nr_xform}")
    return nr_xform


def remove_collider(mesh):
    """add_collider で登録した nRigid を除去 (mesh 自体には触れない)。"""
    nm = _collider_name(mesh)
    if cmds.objExists(nm):
        try:
            cmds.delete(nm)
            print(f"[jiggle_bones] collider removed: {mesh}")
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] delete collider {nm} failed: {exc}")


def list_colliders():
    """登録済み collider mesh 名の list を返す。"""
    out = []
    for c in cmds.ls(f"jb_collider_*", type="transform") or []:
        # jb_collider_<mesh> から元 mesh 名を復元
        original = c[len("jb_collider_"):]
        out.append(original)
    return out


# =========================================================================
# Category params
# =========================================================================

_PARAM_ATTRS = ("stiffness", "damp", "startCurveAttract", "mass")


def get_category_params(category):
    """category hairSystem の現在 param を dict で返す。無ければ default。"""
    hs_xform = f"jb_hairSystem_{category}"
    if not cmds.objExists(hs_xform):
        return dict(DEFAULT_PARAMS_BY_CATEGORY.get(category, {}))
    shape = cmds.listRelatives(hs_xform, s=True)[0]
    out = {}
    for a in _PARAM_ATTRS:
        try:
            out[a] = cmds.getAttr(f"{shape}.{a}")
        except Exception:
            pass
    return out


def set_category_params(category, **params):
    """category hairSystem の param を更新 (無ければ何もしない)。"""
    hs_xform = f"jb_hairSystem_{category}"
    if not cmds.objExists(hs_xform):
        return
    shape = cmds.listRelatives(hs_xform, s=True)[0]
    for a, v in params.items():
        try:
            cmds.setAttr(f"{shape}.{a}", v)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] set {a}={v} failed: {exc}")


# =========================================================================
# UI (v0.2.0)
# =========================================================================

_UI_COLLIDER_LIST = "jbColliderList"
_UI_CAT_PREFIX = "jbCatSection"
_UI_CHAIN_PREFIX = "jbChainRow"

# runtime state (UI 内で share)
_UI_CATEGORY_FIELDS = {}   # {(category, attr): floatFieldGrp name}
_UI_CHAIN_CHECKS = {}      # {(category, chain_id): checkBox name}


def _ui_reset_state():
    _UI_CATEGORY_FIELDS.clear()
    _UI_CHAIN_CHECKS.clear()


def _ui_refresh_colliders(*_):
    if not cmds.textScrollList(_UI_COLLIDER_LIST, ex=True):
        return
    cmds.textScrollList(_UI_COLLIDER_LIST, e=True, ra=True)
    for c in list_colliders():
        cmds.textScrollList(_UI_COLLIDER_LIST, e=True, append=c)


def _ui_collider_add_from_sel(*_):
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Select a mesh transform in the viewport first")
        return
    for m in sel:
        # mesh shape を持つ transform だけを対象に
        shapes = cmds.listRelatives(m, s=True, type="mesh") or []
        if not shapes:
            cmds.warning(f"skip {m}: no mesh shape")
            continue
        add_collider(m)
    _ui_refresh_colliders()


def _ui_collider_remove_sel(*_):
    items = cmds.textScrollList(_UI_COLLIDER_LIST, q=True, si=True) or []
    for m in items:
        remove_collider(m)
    _ui_refresh_colliders()


def _ui_apply_category_params(category, *_):
    """UI の param field 値を hairSystem に反映。"""
    params = {}
    for attr in _PARAM_ATTRS:
        fld = _UI_CATEGORY_FIELDS.get((category, attr))
        if fld and cmds.floatFieldGrp(fld, ex=True):
            v = cmds.floatFieldGrp(fld, q=True, value1=True)
            params[attr] = v
    if params:
        set_category_params(category, **params)
        print(f"[jiggle_bones] {category} params updated: {params}")


def _ui_setup_chain(category, chain, *_):
    create_jiggle_for_chain(chain, category=category)
    # 即座に category param を適用 (新規 hairSystem に UI 値を反映)
    _ui_apply_category_params(category)


def _ui_remove_chain(chain, *_):
    remove_jiggle_for_chain(chain)


def _ui_setup_all_enabled(*_):
    n = 0
    for (category, chain_id), cb in _UI_CHAIN_CHECKS.items():
        if not cmds.checkBox(cb, q=True, v=True):
            continue
        # chain データは button に annotation で埋め込んだ id → chains dict 再検索
        chains = find_jiggle_chains().get(category, [])
        for chain in chains:
            if _chain_id(chain) == chain_id:
                create_jiggle_for_chain(chain, category=category)
                n += 1
                break
        _ui_apply_category_params(category)
    print(f"[jiggle_bones] Setup {n} enabled chain(s)")


def _ui_remove_all(*_):
    for cat_chain_list in find_jiggle_chains().values():
        for chain in cat_chain_list:
            if is_chain_active(chain):
                remove_jiggle_for_chain(chain)
    print(f"[jiggle_bones] Removed all jiggle setups")


def show_ui():
    """揺れもの dynamics UI (v0.2.1 header/body/footer レイアウト)。

    formLayout で:
      - header (title + 使い方) を上端固定
      - footer (Setup All / Remove All / Refresh) を下端固定
      - body (collider + カテゴリ) を残り領域に scrollLayout で配置
    こうすると window resize しても footer の action ボタンが常に見える。
    """
    if cmds is None:
        raise RuntimeError("show_ui() must be called inside Maya.")
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    _ui_reset_state()

    win = cmds.window(WINDOW, t=f"Jiggle Bones  v{__version__}",
                      w=540, h=680, mnb=True, mxb=True, s=True)

    form = cmds.formLayout("jbForm", w=540, h=680)

    # ---- HEADER (title + 使い方 workflow) ----
    header = cmds.columnLayout("jbHeader", adj=True, rs=3,
                                cat=("both", 10), p=form)
    cmds.text(l=f"Jiggle Bones  v{__version__}  —  FK コントローラー + "
                 "hairSystem オーバーレイ",
              al="left", fn="boldLabelFont", p=header)
    cmds.text(l="使い方:  ①コライダー mesh を追加  ②各カテゴリで"
                "シミュレーションパラメータを調整  ③chain ごとに「セット"
                "アップ」→ 全 joint に FK cube ctl が生成される (root ctl は"
                "移動+回転可、子 ctl は回転のみ)。 ④root ctl の "
                "dynBlend アトリビュートで FK(0) ↔ dynamics(1) をブレンド。"
                " ⑤タイムラインを再生してシミュレーション確認",
              al="left", fn="smallObliqueLabelFont", ww=True, p=header,
              w=520)
    cmds.separator(h=6, style="in", p=header)

    # ---- FOOTER (常に見える action バー) ----
    footer = cmds.columnLayout("jbFooter", adj=True, rs=3,
                                cat=("both", 10), p=form)
    cmds.separator(h=6, style="in", p=footer)
    footer_row = cmds.rowLayout(nc=3, adj=1, p=footer,
                                 cw3=(200, 130, 160),
                                 ct3=("both", "both", "both"),
                                 co3=(4, 4, 4))
    cmds.button(l="⚡ チェック済 chain を一括セットアップ", h=34,
                c=_ui_setup_all_enabled, bgc=(0.90, 0.55, 0.10), p=footer_row)
    cmds.button(l="すべて削除", h=34, c=_ui_remove_all,
                bgc=(0.55, 0.30, 0.30), p=footer_row)
    cmds.button(l="chain 再検出", h=34, c=lambda *_: show_ui(),
                bgc=(0.35, 0.55, 0.75), p=footer_row)

    # ---- BODY (scroll 領域: collider + カテゴリ) ----
    body_scroll = cmds.scrollLayout("jbBody", cr=True, p=form,
                                     horizontalScrollBarThickness=0)
    body_col = cmds.columnLayout(adj=True, rs=6, cat=("both", 10),
                                  p=body_scroll)

    # collider セクション
    cmds.text(l="① コライダー (衝突対象 mesh)", al="left",
              fn="boldLabelFont", p=body_col)
    cmds.text(l="体・脚など mesh を選んで「選択から追加」。スカート ⇄ 脚の"
                "衝突判定に使います。",
              al="left", fn="smallObliqueLabelFont", ww=True, p=body_col,
              w=490)
    cmds.textScrollList(_UI_COLLIDER_LIST, numberOfRows=4, h=80,
                         allowMultiSelection=True, p=body_col)
    col_row = cmds.rowLayout(nc=2, adj=1, p=body_col,
                              cw2=(200, 200),
                              ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="選択から追加", h=26, p=col_row,
                c=_ui_collider_add_from_sel, bgc=(0.30, 0.55, 0.30))
    cmds.button(l="選択を削除", h=26, p=col_row,
                c=_ui_collider_remove_sel, bgc=(0.55, 0.30, 0.30))

    cmds.separator(h=8, style="in", p=body_col)
    cmds.text(l="②/③ カテゴリ別 chain — シミュレーションパラメータは各枠内、"
                "セットアップ/削除は右列のボタン",
              al="left", fn="boldLabelFont", p=body_col)

    try:
        detected = find_jiggle_chains()
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] detection failed: {exc}")
        detected = {}

    if not detected:
        cmds.text(l="(揺れもの chain が検出されませんでした。"
                     "scene に hair_*/skirt_*/tail_*/coat_*/ear_* 等の "
                     "joint がありますか?)",
                  al="left", fn="smallObliqueLabelFont", ww=True,
                  p=body_col, w=490)
    else:
        for category, chain_list in sorted(detected.items()):
            _build_category_section(category, chain_list, parent=body_col)

    # ---- formLayout で header / body / footer を配置 ----
    cmds.formLayout(form, e=True,
        af=[
            (header, "top",    0),
            (header, "left",   0),
            (header, "right",  0),
            (footer, "bottom", 0),
            (footer, "left",   0),
            (footer, "right",  0),
            (body_scroll, "left",  0),
            (body_scroll, "right", 0),
        ],
        ac=[
            (body_scroll, "top",    2, header),
            (body_scroll, "bottom", 2, footer),
        ])

    _ui_refresh_colliders()
    cmds.showWindow(win)
    return win


def _build_category_section(category, chain_list, parent=None):
    """1 カテゴリ (hair / skirt 等) の param field + chain checkbox 群を作る。"""
    defaults = DEFAULT_PARAMS_BY_CATEGORY.get(category, {})
    # v0.3.4: 既存 hairSystem があれば現在値を優先表示 (無ければ default)
    hs_xform = f"jb_hairSystem_{category}"
    live_values = None
    if cmds.objExists(hs_xform):
        try:
            live_values = get_category_params(category)
        except Exception:
            live_values = None
    cat_jp = _CATEGORY_JP.get(category, category)
    hs_marker = "  [構築済]" if live_values is not None else ""
    frame_kwargs = dict(
        l=f"  {cat_jp}  ({category} / {len(chain_list)} 本){hs_marker}",
        cll=True, cl=False, mw=6, mh=4,
        bgc=(0.22, 0.24, 0.28),
    )
    if parent:
        frame_kwargs["p"] = parent
    frame = cmds.frameLayout(**frame_kwargs)
    inner = cmds.columnLayout(adj=True, rs=3, p=frame)

    # シミュレーションパラメータ (per-category)
    # 表示値の優先順位: 既存 hairSystem の live 値 > default 経験則
    src = live_values if live_values is not None else defaults
    for attr in _PARAM_ATTRS:
        v = src.get(attr, defaults.get(attr, 0.0))
        label_jp = _PARAM_LABEL_JP.get(attr, attr) + " :"
        fld = cmds.floatFieldGrp(numberOfFields=1, label=label_jp,
                                   value1=v, cw2=(180, 80), p=inner)
        _UI_CATEGORY_FIELDS[(category, attr)] = fld

    cmds.button(l=f"{cat_jp} のパラメータを反映",
                h=22, c=lambda _x=None, _c=category: _ui_apply_category_params(_c),
                bgc=(0.30, 0.45, 0.55), p=inner,
                ann=f"上記の値を既存の hairSystem ({category}) に上書き適用")
    cmds.separator(h=6, style="none", p=inner)

    # chain rows
    for chain in chain_list:
        cid = _chain_id(chain)
        active = is_chain_active(chain)
        row = cmds.rowLayout(nc=4, adj=2, p=inner,
                              cw4=(28, 260, 90, 70),
                              ct4=("both", "left", "both", "both"),
                              co4=(2, 4, 2, 2))
        cb = cmds.checkBox(l="", v=not active, p=row,
                            ann="チェックを入れると「一括セットアップ」の対象")
        _UI_CHAIN_CHECKS[(category, cid)] = cb
        status = "  [適用中]" if active else ""
        cmds.text(l=f"{cid}  ({len(chain)} 個 → {_short(chain[-1])}){status}",
                   al="left", fn="smallPlainLabelFont", p=row)
        cmds.button(l="セットアップ", h=22, p=row,
                    c=lambda _x=None, _c=category, _ch=chain:
                        _ui_setup_chain(_c, _ch),
                    bgc=(0.30, 0.55, 0.30),
                    ann="この chain に FK ctl + hairSystem dynamics を構築")
        cmds.button(l="削除", h=22, p=row,
                    c=lambda _x=None, _ch=chain: _ui_remove_chain(_ch),
                    bgc=(0.55, 0.30, 0.30),
                    ann="この chain の jiggle 関連 node を全削除")
