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

__version__ = "0.2.1"
WINDOW = "jiggleBonesWin"
JB_GROUP = "jiggle_bones_grp"
NUCLEUS_NAME = "jb_nucleus"

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


def _create_rest_curve_from_chain(chain):
    """chain の joint 位置に沿って NURBS curve を作り、親骨に parentConstraint する。"""
    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in chain]
    degree = 3 if len(pts) >= 4 else max(1, len(pts) - 1)
    curve = cmds.curve(d=degree, p=pts, n=_rest_curve_name(chain))
    # 親骨 (chain の start joint の親)
    parent = cmds.listRelatives(chain[0], p=True, type="joint") or []
    if parent:
        try:
            cmds.parentConstraint(parent[0], curve, mo=True,
                                    n=curve + "_pc")
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] rest curve parentConstraint failed: {exc}")
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
    """chain (親→末端 joint list) に hairSystem dynamics を組む。"""
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    if not chain or len(chain) < 2:
        cmds.warning(f"[jiggle_bones] chain too short: {chain}")
        return None
    if category is None:
        category = _classify(chain[0]) or "hair"

    # 既存 jiggle があれば先に remove (二重張り防止)
    if cmds.objExists(_ik_handle_name(chain)):
        remove_jiggle_for_chain(chain)

    _ensure_jb_group()
    hs_xform, hs_shape = _get_or_create_hair_system(category)

    rest_curve = _create_rest_curve_from_chain(chain)
    dyn_shape = _add_follicle_to_hair_system(rest_curve, hs_shape, chain)
    ikh = _create_spline_ik(chain, dyn_shape)

    print(f"[jiggle_bones] setup {_chain_id(chain)} → category={category}, "
          f"joints={len(chain)}")
    return {
        "chain":         chain,
        "category":      category,
        "rest_curve":    rest_curve,
        "dynamic_curve": dyn_shape,
        "follicle":      _follicle_name(chain),
        "ik_handle":     ikh,
        "hair_system":   hs_xform,
    }


def remove_jiggle_for_chain(chain):
    """create_jiggle_for_chain で作った node を掃除する。"""
    if cmds is None:
        return
    for nm in (_ik_handle_name(chain),
               _dyn_curve_name(chain),
               _follicle_name(chain),
               _rest_curve_name(chain) + "_pc",
               _rest_curve_name(chain)):
        if cmds.objExists(nm):
            try:
                cmds.delete(nm)
            except Exception as exc:
                cmds.warning(f"[jiggle_bones] delete {nm} failed: {exc}")


def is_chain_active(chain):
    """spline IK handle があれば active とみなす。"""
    return cmds.objExists(_ik_handle_name(chain))


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
    cmds.text(l=f"Jiggle Bones  v{__version__}  —  hairSystem dynamics",
              al="left", fn="boldLabelFont", p=header)
    cmds.text(l="使い方: ①collider を選択して Add  ②各カテゴリで params 調整  "
                "③chain 単位 [Setup] or 底部 [Setup All Enabled]  "
                "④timeline scrub で確認",
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
    cmds.button(l="⚡ Setup All Enabled", h=34, c=_ui_setup_all_enabled,
                bgc=(0.90, 0.55, 0.10), p=footer_row)
    cmds.button(l="Remove All", h=34, c=_ui_remove_all,
                bgc=(0.55, 0.30, 0.30), p=footer_row)
    cmds.button(l="Refresh Detection", h=34, c=lambda *_: show_ui(),
                bgc=(0.35, 0.55, 0.75), p=footer_row)

    # ---- BODY (scroll 領域: collider + カテゴリ) ----
    body_scroll = cmds.scrollLayout("jbBody", cr=True, p=form,
                                     horizontalScrollBarThickness=0)
    body_col = cmds.columnLayout(adj=True, rs=6, cat=("both", 10),
                                  p=body_scroll)

    # collider セクション
    cmds.text(l="① Collider mesh(es)", al="left",
              fn="boldLabelFont", p=body_col)
    cmds.text(l="body/leg 等の mesh を選んで Add。skirt vs 脚 の衝突判定に使う。",
              al="left", fn="smallObliqueLabelFont", ww=True, p=body_col,
              w=490)
    cmds.textScrollList(_UI_COLLIDER_LIST, numberOfRows=4, h=80,
                         allowMultiSelection=True, p=body_col)
    col_row = cmds.rowLayout(nc=2, adj=1, p=body_col,
                              cw2=(200, 200),
                              ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="Add from Selection", h=26, p=col_row,
                c=_ui_collider_add_from_sel, bgc=(0.30, 0.55, 0.30))
    cmds.button(l="Remove Selected", h=26, p=col_row,
                c=_ui_collider_remove_sel, bgc=(0.55, 0.30, 0.30))

    cmds.separator(h=8, style="in", p=body_col)
    cmds.text(l="②/③ Chains by category  —  params は各枠内、setup は右列ボタン",
              al="left", fn="boldLabelFont", p=body_col)

    try:
        detected = find_jiggle_chains()
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] detection failed: {exc}")
        detected = {}

    if not detected:
        cmds.text(l="(揺れもの chain が検出されませんでした。"
                     "scene に hair_*/skirt_*/tail_* 等の joint がありますか?)",
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
    frame_kwargs = dict(l=f"  {category}  ({len(chain_list)} chain)",
                         cll=True, cl=False, mw=6, mh=4,
                         bgc=(0.22, 0.24, 0.28))
    if parent:
        frame_kwargs["p"] = parent
    frame = cmds.frameLayout(**frame_kwargs)
    inner = cmds.columnLayout(adj=True, rs=3, p=frame)

    # param sliders (per-category)
    for attr in _PARAM_ATTRS:
        v = defaults.get(attr, 0.0)
        fld = cmds.floatFieldGrp(numberOfFields=1, label=attr + ":",
                                   value1=v, cw2=(150, 80), p=inner)
        _UI_CATEGORY_FIELDS[(category, attr)] = fld

    cmds.button(l=f"Apply {category} params to existing hairSystem",
                h=22, c=lambda _x=None, _c=category: _ui_apply_category_params(_c),
                bgc=(0.30, 0.45, 0.55), p=inner)
    cmds.separator(h=6, style="none", p=inner)

    # chain rows
    for chain in chain_list:
        cid = _chain_id(chain)
        active = is_chain_active(chain)
        row = cmds.rowLayout(nc=4, adj=2, p=inner,
                              cw4=(28, 260, 80, 80),
                              ct4=("both", "left", "both", "both"),
                              co4=(2, 4, 2, 2))
        cb = cmds.checkBox(l="", v=not active, p=row)
        _UI_CHAIN_CHECKS[(category, cid)] = cb
        status = "  [ACTIVE]" if active else ""
        cmds.text(l=f"{cid}  ({len(chain)} → {_short(chain[-1])}){status}",
                   al="left", fn="smallPlainLabelFont", p=row)
        cmds.button(l="Setup", h=22, p=row,
                    c=lambda _x=None, _c=category, _ch=chain:
                        _ui_setup_chain(_c, _ch),
                    bgc=(0.30, 0.55, 0.30))
        cmds.button(l="Remove", h=22, p=row,
                    c=lambda _x=None, _ch=chain: _ui_remove_chain(_ch),
                    bgc=(0.55, 0.30, 0.30))
