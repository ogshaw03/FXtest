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

__version__ = "0.5.32"


def _cleanup_mcd_junk_inline():
    """v0.5.13: setup 途中で呼ぶ内部版 (print 抑制、失敗も静か)。"""
    n = 0
    for t in cmds.ls(assemblies=True) or []:
        if not cmds.objExists(t):
            continue
        if t.startswith("jb_"):
            continue
        # 名前パターン: hairSystemNOutputCurves 系 / transformN 系 /
        # nucleusN 系 (自動生成された余計 nucleus) / follicleN 系 (empty)
        looks_junk = (
            t.startswith("hairSystem") or
            (t.startswith("transform") and t[9:].isdigit()) or
            (t.startswith("nucleus") and t[7:].isdigit()) or
            (t.startswith("follicle") and t[8:].isdigit())
        )
        if not looks_junk:
            continue
        # 中身: 空 or shape も transform 子も無い場合のみ削除
        kids = cmds.listRelatives(t, c=True) or []
        if kids:
            continue
        try:
            cmds.delete(t)
            n += 1
        except Exception:
            pass
    return n


def cleanup_mcd_junk():
    """v0.5.12: 既存 scene に散らかった MCD 副産物を掃除。

    対象: root 直下 (assemblies) の transform で
      - 名前が hairSystem*OutputCurves / transform<n>
      - 子が無い (空 group)
      - jb_ prefix ではない

    Returns: 削除した node 数。"""
    n = 0
    for t in cmds.ls(assemblies=True) or []:
        if not cmds.objExists(t):
            continue
        if t.startswith("jb_"):
            continue
        if not (t.startswith("hairSystem") or
                (t.startswith("transform") and t[9:].isdigit())):
            continue
        kids = cmds.listRelatives(t, c=True) or []
        if kids:
            continue
        try:
            cmds.delete(t)
            n += 1
        except Exception:
            pass
    print(f"[jiggle_bones] cleanup_mcd_junk: {n} node(s) deleted")
    return n
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
# v0.5.14: SplineIK の 子方向軸 (dForwardAxis)
# MMD 系は Y+、Maya default は X+、rig によっては Z+ など様々。
# 合ってないと SplineIK が joint を 90/180 度 twist して skinning が
# 裏返る現象 ("normal ひっくり返り") の原因になる。
_AIM_AXIS_CHOICES = ("X+", "X-", "Y+", "Y-", "Z+", "Z-")
_AIM_AXIS_TO_DFA = {"X+": 0, "Y+": 1, "Z+": 2, "X-": 3, "Y-": 4, "Z-": 5}
_DEFAULT_AIM_AXIS = "X+"   # 従来動作 (Maya default)

# 保存先: scene attribute (chain per-category に格納、なければ default)
_AIM_AXIS_ATTR = "jbAimAxis"   # jiggle_bones_grp に string で保存

# module-level cache: create_jiggle_for_chain 実行中に _create_spline_ik から
# 読む。UI ボタン → set_current_aim_axis(...) → create_jiggle_for_chain の順。
_current_setup_aim_axis = None

_PARAM_LABEL_JP = {
    "stiffness":         "硬さ (stiffness)",
    "damp":              "減衰 (damp)",
    "startCurveAttract": "元形状復元 (attract)",
    "mass":              "質量 (mass)",
    # v0.5.4: 過減衰対策で追加。「1 回で止まる」→ これらを下げると
    # 自然な振動が復活する。
    "drag":              "空気抵抗 (drag)",
    "motionDrag":        "運動抵抗 (motionDrag)",
    "attractionDamp":    "復元減衰 (attractionDamp)",
    "bendResistance":    "曲げ抵抗 (bendResistance)",
    # v0.5.9: 当たり判定の見た目広さ調整 (mesh 表面から N unit 外側まで)
    "collideWidthOffset": "衝突厚み (collideWidthOffset)",
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
    # v0.5.4: 「1 回で止まる」= 過減衰 の対策で drag / motionDrag /
    # attractionDamp を明示的に低めに設定 (Maya default では motionDrag=0.1、
    # attractionDamp=0 だが setup 時にリセットされない事があるので明示)。
    # drag は 空気抵抗、motionDrag は 動作抵抗、attractionDamp は 復元力の
    # 減衰。この 3 つを下げると 衝突後 数回 振動してから停止する自然挙動に。
    # v0.5.6: 「root 動作 → hair が overshoot する」 過反発対策。
    # attractionDamp を 0.0 → 0.15 前後に上げる。これは attract 力による
    # 復元が生む振動を "critical damp" で抑える attr。 damp より overshoot
    # を狙って殺せる (damp は 全体の速度に効くので上げすぎると mushy に)。
    # damp も僅かに上げて root-follow 時の 慣性余韻を短縮。
    # v0.5.7: user 調整結果を default 化 (screenshot 準拠)。
    #   attractionDamp を 1.0 に上げて overshoot を完全に殺し、
    #   mass を 0.2 に下げて 慣性遅れを最小化、
    #   stiffness を 0.1 まで下げて 引き戻し速度を穏やかに。
    # v0.5.9: collideWidthOffset を param 化 (0.05 mesh すれすれ default)。
    "hair":    {"stiffness": 0.10, "damp": 0.02, "startCurveAttract": 0.15,
                "mass": 0.2, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 1.0, "bendResistance": 0.1089,
                "collideWidthOffset": 0.05},
    "skirt":   {"stiffness": 0.20, "damp": 0.04, "startCurveAttract": 0.10,
                "mass": 1.5, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 0.10, "bendResistance": 0.1,
                "collideWidthOffset": 0.05},
    "ribbon":  {"stiffness": 0.15, "damp": 0.03, "startCurveAttract": 0.20,
                "mass": 0.4, "drag": 0.02, "motionDrag": 0.0,
                "attractionDamp": 0.15, "bendResistance": 0.05,
                "collideWidthOffset": 0.05},
    "sleeve":  {"stiffness": 0.20, "damp": 0.04, "startCurveAttract": 0.15,
                "mass": 0.8, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 0.12, "bendResistance": 0.1,
                "collideWidthOffset": 0.05},
    "necktie": {"stiffness": 0.30, "damp": 0.05, "startCurveAttract": 0.20,
                "mass": 0.5, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 0.15, "bendResistance": 0.1,
                "collideWidthOffset": 0.05},
    "coat":    {"stiffness": 0.30, "damp": 0.05, "startCurveAttract": 0.15,
                "mass": 1.5, "drag": 0.08, "motionDrag": 0.0,
                "attractionDamp": 0.15, "bendResistance": 0.15,
                "collideWidthOffset": 0.05},
    "ear":     {"stiffness": 0.50, "damp": 0.06, "startCurveAttract": 0.30,
                "mass": 0.5, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 0.20, "bendResistance": 0.15,
                "collideWidthOffset": 0.05},
    "tail":    {"stiffness": 0.20, "damp": 0.04, "startCurveAttract": 0.10,
                "mass": 1.0, "drag": 0.05, "motionDrag": 0.0,
                "attractionDamp": 0.10, "bendResistance": 0.1,
                "collideWidthOffset": 0.05},
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
    """v0.3.x までの命名 heuristic 検出 (v0.4.0 以降は主 flow から外れ、
    「auto-detect して registry に追加」ボタン用の補助関数として残置)。"""
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
# Chain registry (v0.4.0) — user が pick で追加した chain を scene に永続化
# =========================================================================
#
# `jiggle_bones_grp.chainRegistry` に JSON で保存:
#   [
#     {"category": "hair",  "chain": ["hair1","hair2",...]},
#     {"category": "skirt", "chain": ["skirt_L_1",...]},
#     ...
#   ]
#
# 命名 heuristic (find_jiggle_chains) と切り離し、user が明示的に選択して
# 登録した chain だけを扱う。

REGISTRY_ATTR = "chainRegistry"


def _read_registry():
    import json
    if not cmds.objExists(JB_GROUP):
        return []
    if not cmds.attributeQuery(REGISTRY_ATTR, node=JB_GROUP, exists=True):
        return []
    raw = cmds.getAttr(f"{JB_GROUP}.{REGISTRY_ATTR}") or ""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_registry(entries):
    import json
    _ensure_jb_group()
    if not cmds.attributeQuery(REGISTRY_ATTR, node=JB_GROUP, exists=True):
        cmds.addAttr(JB_GROUP, ln=REGISTRY_ATTR, dt="string")
    cmds.setAttr(f"{JB_GROUP}.{REGISTRY_ATTR}",
                  json.dumps(entries, ensure_ascii=False, indent=2),
                  type="string")


def get_registered_chains():
    """{'hair': [chain, chain, ...], 'skirt': [...]}, category ごと分類。"""
    entries = _read_registry()
    out = {}
    for e in entries:
        cat = e.get("category") or "hair"
        chain = e.get("chain") or []
        if len(chain) >= 2:
            out.setdefault(cat, []).append(chain)
    return out


def add_registered_chain(chain, category=None):
    """chain を registry に追加 (chain[0] 名で重複除去)。
    category=None なら _classify で自動判定 (無ければ "hair" fallback)。"""
    if not chain or len(chain) < 2:
        cmds.warning("[jiggle_bones] chain must have at least 2 joints")
        return None
    if category is None:
        category = _classify(chain[0]) or "hair"
    entries = [e for e in _read_registry()
                if (e.get("chain") or [None])[0] != chain[0]]
    entries.append({"category": category, "chain": list(chain)})
    _write_registry(entries)
    print(f"[jiggle_bones] registered chain: {chain[0]} ({len(chain)} 個) "
          f"as '{category}'")
    return {"category": category, "chain": list(chain)}


def remove_registered_chain(chain_or_root):
    """registry から chain を除去 (rig は tear down しない)。"""
    root = chain_or_root[0] if isinstance(chain_or_root, list) \
                              else chain_or_root
    entries = [e for e in _read_registry()
                if (e.get("chain") or [None])[0] != root]
    _write_registry(entries)


def _dfs_longest_chain(node):
    """node から下 (子) へ single-child chain を最深方向に辿る。"""
    kids = cmds.listRelatives(node, c=True, type="joint") or []
    if not kids:
        return [node]
    best = []
    for k in kids:
        sub = _dfs_longest_chain(k)
        if len(sub) > len(best):
            best = sub
    return [node] + best


def build_chains_from_selection():
    """v0.5.11: 選択から 複数 chain を構築 (multi-root 対応)。

    ルール:
      - 選択された joint のうち、"親も選択されている" ものは skip
        (親が同 chain の先頭として処理される)
      - 残った各 joint を root として DFS で単一 chain を作る
      - つまり:
        * 髪の 3 本 root を並列選択 → 3 chain 返す
        * 1 本 chain の途中まで選択 → その top を root として 1 chain
        * 1 joint 選択 → 1 chain (DFS)

    Returns: [chain, chain, ...]  (各 chain は joint 名 list, 長さ ≥ 2)
             選択なし / 有効 chain 0 → []
    """
    sel = cmds.ls(sl=True, type="joint") \
          or cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Outliner / viewport で joint(s) を選択してください")
        return []
    # short name に統一
    sel_short = [s.split("|")[-1].split(":")[-1] for s in sel]
    sel_set = set(sel_short)
    # 親が同じ選択集合内なら skip → 残ったのが root 候補
    roots = []
    for name in sel_short:
        parent = cmds.listRelatives(name, p=True, type="joint") or []
        parent_short = parent[0].split("|")[-1].split(":")[-1] if parent else None
        if parent_short and parent_short in sel_set:
            continue   # 親が選択されてる → chain の途中、skip
        roots.append(name)
    # 各 root から DFS で chain 作成
    chains = []
    for r in roots:
        chain = _dfs_longest_chain(r)
        if len(chain) >= 2:
            chains.append(chain)
        else:
            cmds.warning(f"[jiggle_bones] {r} の子 joint が無い → skip")
    return chains


def build_chain_from_selection():
    """v0.5.11: 後方互換 wrapper。build_chains_from_selection() の 最初 1 個を
    返す (単一 chain を期待する古い呼び出し用)。

    以前の「複数選択 = そのまま linear chain」用途は廃止。もし手で順序指定して
    chain を作りたい場合は 1 joint 選択 → DFS で長い chain を建てるか、
    直接 create_jiggle_for_chain(joint_list) を呼ぶ。
    """
    chains = build_chains_from_selection()
    if not chains:
        return None
    return chains[0]


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
    # v0.4.2 貫通対策: default より高精度化
    #   subSteps: 3 → 6 (frame 間の計算刻み倍増)
    #   maxCollisionIterations: 4 → 8 (衝突ペア反復)
    #   spaceScale: 1.0 のまま (キャラサイズ依存なので UI で調整予定)
    for attr, val in (("subSteps", 6),
                       ("maxCollisionIterations", 8)):
        try:
            cmds.setAttr(f"{n}.{attr}", val)
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


def _hair_system_name_for_chain(chain, category):
    """v0.5.0: chain 1 本ごとに 1 hairSystem。名前は `jb_hs_<cat>_<chainId>`。"""
    return f"jb_hs_{category}_{_chain_id(chain)}"


def _get_or_create_hair_system(category, chain=None):
    """v0.5.0: chain を渡すと per-chain hairSystem を返す (article-verbatim)。
    chain=None (旧 API) の場合は per-category shared にフォールバック。"""
    _ensure_jb_group()
    if chain is not None:
        hs_xform = _hair_system_name_for_chain(chain, category)
    else:
        hs_xform = f"jb_hairSystem_{category}"
    hs_shape = hs_xform + "Shape"
    if cmds.objExists(hs_shape):
        # v0.5.2: 既存 HS を返す時も active=1 を再保証。
        # 何かの副作用 (makeCurvesDynamic 実行時 / connect 時) で
        # False に戻される事があるため。
        try:
            cmds.setAttr(hs_shape + ".active", 1)
        except Exception:
            pass
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

    # v0.4.1 bugfix: hairSystem の collide default は OFF なので、コライダー
    # を追加しても衝突判定が効かない (hair が mesh を貫通する) 現象があった。
    # nRigid を nucleus に登録しても、hair 側で collide=1 にしないと反応
    # しない仕様なので明示 setAttr する。
    # v0.4.2 貫通対策強化: iterations と collide サンプリング数を増やす
    #   iterations: 1 → 3 (毎フレーム solver 反復)
    #   collideOverSample: 1 → 4 (frame 間の collide 検出回数)
    #   collideWidthOffset: 0 → 0.1 (少しマージン)
    # v0.4.7 記事準拠 (backbone-studio.com/blog-nhairrig):
    #   collideWidthOffset は「値を上げる」ことで初めて衝突判定が有効化する。
    #   default 0 は実質 collision OFF。1.0 前後を明示。solverDisplay=1 で
    #   Collision Thickness を viewport に可視化 (デバッグ用)。
    # v0.5.1 犯人発見: hairSystem.active default が False → sim 全体休眠 →
    #   nucleus/hair/nRigid の connection が全部正しくても collision 評価
    #   されない。実 scene dump で `.active = False` 確認 → 手動 setAttr で
    #   collide 確認済。makeCurvesDynamic / createNode 経由でも default OFF
    #   なので明示的に True にする。
    # v0.5.8: collideWidthOffset を 1.0 → 0.05 に。
    #   v0.4.7 で貫通対策のため大きめ 1.0 にしたが、これは "mesh 表面から
    #   1 unit 外側まで衝突判定" の意味 → 見た目に「当たり判定が広い」隙間。
    #   v0.5.x で他の貫通対策 (subSteps↑ / iterations↑ / active=1 fix / etc.)
    #   が入って collideWidthOffset に頼らなくても貫通しなくなったので小さく。
    for coll_attr, val in (("active", 1),
                            ("collide", 1),
                            ("collideStrength", 1.0),
                            ("iterations", 3),
                            ("collideOverSample", 4),
                            ("collideWidthOffset", 0.05),
                            ("selfCollide", 0),
                            ("displayColor", (1.0, 1.0, 0.5)),
                            ):
        try:
            if isinstance(val, tuple):
                cmds.setAttr(f"{hs_shape}.{coll_attr}", *val, type="double3")
            else:
                cmds.setAttr(f"{hs_shape}.{coll_attr}", val)
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
    """v0.4.6: Maya 標準 `makeCurvesDynamic` MEL に完全依存する版。

    自分で follicle/rebuildCurve を組む方式 (v0.4.5 まで) は視覚上 collision が
    発火しない問題があった。MEL に丸投げして Maya-blessed 接続を得る。
    その後 hairSystem を我々のカテゴリ hairSystem に付け替え、余計な
    自動生成 hairSystem を削除する。

    Args:
        rest_curve: 元の rest curve transform (cluster で FK chain 追従済み)
        hs_shape:   我々のカテゴリ hairSystem shape (jb_hairSystem_<cat>Shape)
        chain:      元 joint chain (id 生成用)

    Returns:
        dyn_shape:  spline IK 用の dynamic curve shape 名
    """
    _ensure_jb_group()
    nucleus = _get_or_create_nucleus()

    # nHair plugin ロード必須
    try:
        if not cmds.pluginInfo("nHair", q=True, l=True):
            cmds.loadPlugin("nHair", quiet=True)
    except Exception:
        pass

    # 既存の hairSystem / follicle を記録 → 新規生成分を差分検出するため
    hs_before = set(cmds.ls(type="hairSystem"))
    foll_before = set(cmds.ls(type="follicle"))
    curves_before = set(cmds.ls(type="nurbsCurve"))
    nuc_before = set(cmds.ls(type="nucleus"))
    # v0.5.12: MCD が root に散らかす副産物 transform も差分検出
    transforms_before = set(cmds.ls(assemblies=True))

    # 現在 nucleus を我々のものに (makeCurvesDynamic はこの nucleus を使う)
    try:
        mel.eval(f'setActiveNucleusNode "{nucleus}"')
    except Exception:
        pass

    # makeCurvesDynamic 実行 (curve を選択して MEL 呼出)
    sel_before = cmds.ls(sl=True)
    cmds.select(rest_curve, r=True)
    try:
        # args: hairSystemMode ({1:existing,2:new}), flags:
        #   0: attachToSelected 0/1
        #   1: createRestCurves 0/1
        #   2: createOutputCurves 1
        #   3: overwriteExisting 1
        #   4: static 0
        mel.eval('makeCurvesDynamic 2 { "0", "0", "1", "1", "0" };')
    except Exception as exc:
        cmds.select(sel_before or None, r=True)
        raise RuntimeError(f"makeCurvesDynamic failed: {exc}")
    cmds.select(sel_before or None, r=True)

    # 差分で新規 node 検出
    new_hs = list(set(cmds.ls(type="hairSystem")) - hs_before)
    new_foll = list(set(cmds.ls(type="follicle")) - foll_before)
    new_curves = list(set(cmds.ls(type="nurbsCurve")) - curves_before)
    new_nuc = list(set(cmds.ls(type="nucleus")) - nuc_before)

    if not new_foll:
        raise RuntimeError("makeCurvesDynamic did not create follicle")

    new_foll_shape = new_foll[0]
    new_hs_shape = new_hs[0] if new_hs else None

    # dyn curve = follicle.outCurve → nurbsCurve.create
    dyn_shape = None
    for c in new_curves:
        conns = cmds.listConnections(c + ".create", s=True, d=False,
                                       type="follicle") or []
        if conns:
            dyn_shape = c
            break

    # 自動生成された余計な nucleus を削除 (我々の jb_nucleus に統一する)
    for n in new_nuc:
        n_xform_parents = cmds.listRelatives(n, p=True) or []
        target = n_xform_parents[0] if n_xform_parents else n
        try:
            cmds.delete(target)
        except Exception:
            pass

    # 我々のカテゴリ hairSystem に follicle を移動:
    #   自動生成 hairSystem を切断 → jb hairSystem に接続 → 自動生成 hairSystem 削除
    if new_hs_shape and new_hs_shape != hs_shape:
        # 切断 (outHair, currentPosition の両方向)
        for c in cmds.listConnections(new_foll_shape + ".outHair",
                                        s=False, d=True, plugs=True) or []:
            try: cmds.disconnectAttr(new_foll_shape + ".outHair", c)
            except Exception: pass
        for c in cmds.listConnections(new_foll_shape + ".currentPosition",
                                        s=True, d=False, plugs=True) or []:
            try: cmds.disconnectAttr(c, new_foll_shape + ".currentPosition")
            except Exception: pass
        # 我々の hairSystem に再接続
        idx = _next_multi_index(hs_shape, "inputHair")
        cmds.connectAttr(new_foll_shape + ".outHair",
                          f"{hs_shape}.inputHair[{idx}]", f=True)
        cmds.connectAttr(f"{hs_shape}.outputHair[{idx}]",
                          new_foll_shape + ".currentPosition", f=True)
        # 自動生成 hairSystem を削除
        hs_xform_parents = cmds.listRelatives(new_hs_shape, p=True) or []
        target = hs_xform_parents[0] if hs_xform_parents else new_hs_shape
        try:
            cmds.delete(target)
        except Exception:
            pass

    # jiggle 用に pointLock=1 (base only、tip 自由) を明示 (MCD default は 3)
    try:
        cmds.setAttr(new_foll_shape + ".pointLock", 1)
    except Exception:
        pass

    # 命名を jb_ 規則に統一
    foll_xform = cmds.listRelatives(new_foll_shape, p=True)[0]
    try:
        foll_xform = cmds.rename(foll_xform, _follicle_name(chain))
        new_foll_shape = cmds.listRelatives(foll_xform, s=True)[0]
    except Exception:
        pass
    if dyn_shape:
        dyn_xform_parents = cmds.listRelatives(dyn_shape, p=True) or []
        if dyn_xform_parents:
            try:
                new_dyn_xform = cmds.rename(dyn_xform_parents[0],
                                              _dyn_curve_name(chain))
                dyn_shape = cmds.listRelatives(new_dyn_xform, s=True)[0]
            except Exception:
                pass

    # jb_group 配下に整理 (dyn curve は follicle 下ではなく独立に残る)
    _parent_to_jb(foll_xform)
    if dyn_shape:
        dyn_xf = cmds.listRelatives(dyn_shape, p=True)
        if dyn_xf:
            _parent_to_jb(dyn_xf[0])

    # v0.5.12: MCD が root に散らかす副産物 (hairSystemNOutputCurves 空 group、
    # transform1/2/3 の 中身抽出済み空 transform) を片付ける。
    # 差分で見つかった 新規 root 直下 transform のうち、
    #   ・子が無い (empty group)
    #   ・shape も無い
    #   ・name が hairSystem*OutputCurves か transform<n>
    # を対象に削除。 jb_bones_grp や 明示 rename 済み jb_* 名は除外。
    transforms_after = set(cmds.ls(assemblies=True))
    new_root_transforms = transforms_after - transforms_before
    _mcd_junk_patterns = ("hairSystem", "transform")
    for t in new_root_transforms:
        if not cmds.objExists(t):
            continue
        if t.startswith("jb_"):
            continue
        # 名前パターン確認
        if not any(t.startswith(p) for p in _mcd_junk_patterns):
            continue
        # 中身確認: 子 transform も shape も無ければ削除、または 子が全部
        # 別階層へ移動済で empty なら削除
        kids = cmds.listRelatives(t, c=True) or []
        if not kids:
            try:
                cmds.delete(t)
            except Exception:
                pass

    return dyn_shape


def _create_spline_ik(chain, dynamic_curve_shape):
    """chain (親→末端) に spline IK を張り、dynamic curve に追従させる。

    v0.4.7 記事準拠: ikHandle 作成後、明示的に
    `dynamic_curve.worldSpace[0] → ikHandle.inCurve` を再接続する。
    Maya version によっては `.local` が接続され、follicle 変位が反映
    されない可能性があるため。
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

    # v0.5.28: SplineIK advanced twist を完全設定。
    # v0.5.14 は dTwistControlEnable + dForwardAxis のみ設定していたが、
    # dWorldUpType/dWorldUpAxis/dWorldUpMatrix が未設定だと solver が
    # default (Scene Up = world Y) を使う → 骨の実 orientation と不整合
    # → 90/180 度の twist が入る (user 報告 "骨が拗じられる"の原因)。
    #
    # 修正: dWorldUpType=4 (Object Rotation Up Start/End) を使い、
    # start/end joint 自身の orientation を twist reference にする。
    # これで joint の 実 local 軸 と 完全整合、余計な twist 無し。
    aim = _current_setup_aim_axis or _DEFAULT_AIM_AXIS
    dfa = _AIM_AXIS_TO_DFA.get(aim, 0)
    # aim 軸に垂直な 軸を "up" として選ぶ:
    #   aim=Y+ → up=X+ (dWorldUpAxis=2)
    #   aim=X+ → up=Y+ (dWorldUpAxis=0)
    #   aim=Z+ → up=X+ (dWorldUpAxis=2)
    up_axis_for_aim = {"X+": 0, "X-": 0, "Y+": 2, "Y-": 2, "Z+": 2, "Z-": 2}
    dwua = up_axis_for_aim.get(aim, 2)
    try:
        cmds.setAttr(ikh + ".dTwistControlEnable", 1)
        cmds.setAttr(ikh + ".dForwardAxis", dfa)
        # Object Rotation Up (Start/End) — start & end joint の orientation を
        # twist 参照に (chain[0] と chain[-1] の worldMatrix を渡す)
        cmds.setAttr(ikh + ".dWorldUpType", 4)
        cmds.setAttr(ikh + ".dWorldUpAxis", dwua)
        # start / end joint の worldMatrix を connect
        try:
            cmds.connectAttr(chain[0] + ".worldMatrix[0]",
                              ikh + ".dWorldUpMatrix", f=True)
        except Exception:
            pass
        try:
            cmds.connectAttr(chain[-1] + ".worldMatrix[0]",
                              ikh + ".dWorldUpMatrixEnd", f=True)
        except Exception:
            pass
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] spline IK advanced twist set failed: {exc}")

    # v0.4.7: worldSpace[0] を明示接続 (記事準拠)
    try:
        # 既存 inCurve 接続を切って worldSpace[0] を張り直す
        cur = cmds.listConnections(ikh + ".inCurve", s=True, d=False,
                                     plugs=True) or []
        for c in cur:
            try: cmds.disconnectAttr(c, ikh + ".inCurve")
            except Exception: pass
        cmds.connectAttr(dynamic_curve_shape + ".worldSpace[0]",
                          ikh + ".inCurve", f=True)
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] spline IK worldSpace connect failed: {exc}")

    return ikh


def set_current_aim_axis(axis):
    """v0.5.14: 次の create_jiggle_for_chain で使う子方向軸をセット。
    UI dropdown → set → create_jiggle_for_chain の流れで使う。"""
    global _current_setup_aim_axis
    if axis and axis in _AIM_AXIS_CHOICES:
        _current_setup_aim_axis = axis
    else:
        _current_setup_aim_axis = None


# =========================================================================
# Joint orient tool (v0.5.15) — weight を保ったまま jointOrient を直す
# =========================================================================

# aim/up の組合せ → Maya `joint -oj` 引数
_ORIENT_MAPPING = {
    "X+ (Y up)": "xyz",   # Maya default
    "Y+ (Z up)": "yzx",   # MMD 系
    "Z+ (X up)": "zxy",
    "X- (Y up)": "xyz",   # 反転は sao で対応、簡易実装は xyz と同じ
    "Y- (Z up)": "yzx",
    "Z- (X up)": "zxy",
}


def _collect_chain_from_root(root):
    """root から下 の 全 joint を DFS で 返す (root 含む、末端まで)。"""
    out = [root]
    for k in cmds.listRelatives(root, c=True, type="joint") or []:
        out.extend(_collect_chain_from_root(k))
    return out


def _find_skin_clusters_for_joints(joints):
    """joints のいずれかに 影響されている skinCluster を全部集める。"""
    skins = set()
    for j in joints:
        for plug in (".worldMatrix[0]", ".worldMatrix"):
            for c in (cmds.listConnections(j + plug, s=False, d=True,
                                              type="skinCluster") or []):
                skins.add(c)
    return skins


def _joint_index_in_skin(sc, joint):
    """skinCluster sc の .matrix[N] の どれが joint に繋がってるか index を返す。"""
    conns = cmds.listConnections(sc + ".matrix", s=True, d=False,
                                   plugs=True, c=True) or []
    for i in range(0, len(conns), 2):
        dst_plug = conns[i]
        src_plug = conns[i + 1]
        src_node = src_plug.split(".")[0]
        if src_node == joint:
            try:
                return int(dst_plug.split("[")[1].rstrip("]"))
            except Exception:
                pass
    return None


def _force_dg_eval_recursive(node):
    """v0.5.25: node と 全 descendant の worldMatrix を getAttr で 強制評価。
    setAttr 後に stale cache が残るのを防ぐ (Maya parallel eval の遅延対策)。"""
    try:
        cmds.getAttr(node + ".worldMatrix[0]")
        cmds.getAttr(node + ".worldInverseMatrix[0]")
        for d in cmds.listRelatives(node, ad=True, type="transform") or []:
            try:
                cmds.getAttr(d + ".worldMatrix[0]")
                cmds.getAttr(d + ".worldInverseMatrix[0]")
            except Exception:
                pass
    except Exception:
        pass


def _manual_orient_joint_to_child(joint, aim="yzx", verbose=False):
    """v0.5.24: joint の 最初の 子 に aim 軸を向けるように jointOrient を計算。
    Verbose ON で 詳細計算経過を print (デバッグ用)。

    aim: "xyz"/"yzx"/"zxy" のいずれか (Maya `-oj` 形式)。
         最初文字が aim 軸 (X/Y/Z)。
    """
    child_list = cmds.listRelatives(joint, c=True, type="joint") or []
    if not child_list:
        try: cmds.setAttr(joint + ".jointOrient", 0, 0, 0)
        except: pass
        return
    try:
        import maya.api.OpenMaya as om
        import math
        child = child_list[0]
        j_ws = cmds.xform(joint, q=True, ws=True, t=True)
        c_ws = cmds.xform(child, q=True, ws=True, t=True)
        parent_list = cmds.listRelatives(joint, p=True) or []
        if parent_list:
            pm = cmds.getAttr(parent_list[0] + ".worldMatrix[0]")
            p_wim = om.MMatrix(pm).inverse()
        else:
            p_wim = om.MMatrix()
        aim_world = om.MVector(c_ws[0]-j_ws[0], c_ws[1]-j_ws[1], c_ws[2]-j_ws[2])
        if aim_world.length() < 1e-8:
            if verbose: print(f"    manual({joint}): 子と同位置、skip")
            return
        # v0.5.24: direction 変換は MMatrix.setToProduct ではなく
        # MVector * MMatrix を使う (row-vector 慣習)。 direction なので
        # translate 成分は無視される (MVector 自体が w=0 相当)。
        aim_vec = (aim_world * p_wim).normalize()
        up_world_1 = om.MVector(0, 1, 0)
        up_vec = (up_world_1 * p_wim).normalize()
        if abs(aim_vec * up_vec) > 0.95:
            up_world_2 = om.MVector(0, 0, 1)
            up_vec = (up_world_2 * p_wim).normalize()
        side_axis = (aim_vec ^ up_vec).normalize()
        up_axis = (side_axis ^ aim_vec).normalize()
        aim_axis = aim_vec

        axes_map = {
            "xyz": (aim_axis, up_axis, side_axis),
            "yzx": (side_axis, aim_axis, up_axis),
            "zxy": (up_axis, side_axis, aim_axis),
        }
        rows = axes_map.get(aim, axes_map["yzx"])
        m = om.MMatrix((
            rows[0].x, rows[0].y, rows[0].z, 0.0,
            rows[1].x, rows[1].y, rows[1].z, 0.0,
            rows[2].x, rows[2].y, rows[2].z, 0.0,
            0.0, 0.0, 0.0, 1.0,
        ))
        e = om.MTransformationMatrix(m).rotation(asQuaternion=False)
        new_jo = (math.degrees(e.x), math.degrees(e.y), math.degrees(e.z))
        if verbose:
            print(f"    manual({joint}):")
            print(f"      child={child}")
            print(f"      j.WS={[round(x,2) for x in j_ws]}")
            print(f"      c.WS={[round(x,2) for x in c_ws]}")
            print(f"      aim_world={[round(aim_world[i],3) for i in range(3)]} (len={aim_world.length():.3f})")
            print(f"      aim_local (parent space)={[round(aim_vec[i],3) for i in range(3)]}")
            print(f"      side_axis={[round(side_axis[i],3) for i in range(3)]}")
            print(f"      up_axis={[round(up_axis[i],3) for i in range(3)]}")
            print(f"      → jointOrient = {[round(x,2) for x in new_jo]}")
        cmds.setAttr(joint + ".jointOrient", new_jo[0], new_jo[1], new_jo[2])
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] manual orient failed on {joint}: {exc}")
        import traceback; traceback.print_exc()


def _freeze_rotate_to_jointOrient(joint):
    """v0.5.23: joint.rotate を jointOrient に吸収させて rotate=0 に。
    world rotation は 完全維持。 matrix decomposition ベース。

    やり方:
      1. joint の 現在 worldMatrix WM を採取 (rotate 反映済)
      2. parent の worldMatrix PM を採取
      3. local = WM * PM.inverse  (row-vector convention)
      4. local から rotation 部分を euler で抽出
      5. これを jointOrient に set、rotate=0 に

    利点: quaternion order の 仮定不要、Maya の 実 world matrix と 一致。"""
    try:
        import maya.api.OpenMaya as om
        import math
        wm = om.MMatrix(cmds.getAttr(joint + ".worldMatrix[0]"))
        parent_list = cmds.listRelatives(joint, p=True) or []
        if parent_list:
            pm_inv = om.MMatrix(cmds.getAttr(parent_list[0]
                                               + ".worldMatrix[0]")).inverse()
        else:
            pm_inv = om.MMatrix()   # identity
        # row-vector: local = wm * pm_inv
        local = wm * pm_inv
        # rotate 部分だけ抽出 (translate は無視 — jointOrient に translate 成分は無い)
        e = om.MTransformationMatrix(local).rotation(asQuaternion=False)
        cmds.setAttr(joint + ".jointOrient",
                      math.degrees(e.x),
                      math.degrees(e.y),
                      math.degrees(e.z))
        cmds.setAttr(joint + ".rotate", 0, 0, 0)
        return True
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] freeze rotate failed on {joint}: {exc}")
        return False


def orient_joints_preserving_weights(roots, aim="yzx", sao="yup"):
    """v0.5.15: root joint list を渡すと、各 chain を joint orient し直し、
    かつ skinCluster の bindPreMatrix を新 WM に合わせて更新 → weight 維持。

    Args:
        roots: root joint 名 list (各々を chain の root として扱う)
        aim:   Maya `joint -oj` 引数 ("xyz" / "yzx" / "zxy" 等)。
               "yzx" は Y 軸を子方向に (MMD 系典型)
        sao:   secondaryAxisOrient ("xup"/"yup"/"zup"/... と対応する down)

    Returns:
        oriented joint 数
    """
    if isinstance(roots, str):
        roots = [roots]

    # 1. chain 展開
    all_joints = []
    for r in roots:
        if not cmds.objExists(r):
            cmds.warning(f"[jiggle_bones] orient skip: {r} 存在しない")
            continue
        all_joints.extend(_collect_chain_from_root(r))
    if not all_joints:
        return 0

    # v0.5.27: jiggle setup が active な chain で orient するのは NG。
    # SplineIK / parentConstraint 経由で joint rotate が driven されており
    # 静的 jointOrient を書いても solver に上書きされる → 見た目 mesh が
    # 破綻する (実際 user 報告の "ノーマルおかしい" の原因)。
    # jbCtl / SplineIK 存在チェックして warning → abort。
    for r in roots:
        for j in _collect_chain_from_root(r):
            ctl_name = _short(j) + "_jbCtl"
            fk_name = _short(j) + "_jbFK"
            ikh_name = _ik_handle_name([j])
            if (cmds.objExists(ctl_name) or cmds.objExists(fk_name)
                    or cmds.objExists(ikh_name)):
                msg = (f"[jiggle_bones] {r} に jiggle setup が active です。\n"
                       f"orient を実行する前に:\n"
                       f"  1. UI で 該当 chain の 「削除」ボタン、または \n"
                       f"     「全 chain 削除」ボタンで jiggle setup を除去\n"
                       f"  2. orient を実行\n"
                       f"  3. 再度 jiggle setup\n"
                       f"の順で行ってください (SplineIK 等が joint を driving 中は "
                       f"orient が正しく反映されません)。")
                cmds.confirmDialog(t="jiggle setup active", m=msg, b=["OK"])
                cmds.warning(msg)
                return 0

    # 2. 影響 skinCluster 集める + joint→index map 保存
    skins = _find_skin_clusters_for_joints(all_joints)
    joint_indices = {}   # sc -> {joint: idx}
    for sc in skins:
        m = {}
        for j in all_joints:
            idx = _joint_index_in_skin(sc, j)
            if idx is not None:
                m[j] = idx
        if m:
            joint_indices[sc] = m
    print(f"[jiggle_bones] orient: {len(all_joints)} joint(s), "
          f"{len(skins)} skinCluster(s) 影響")

    # 3. envelope 一時 OFF (視覚 flicker 防止)
    saved_env = {}
    for sc in skins:
        try:
            saved_env[sc] = cmds.getAttr(sc + ".envelope")
            cmds.setAttr(sc + ".envelope", 0)
        except Exception:
            pass

    try:
        # v0.5.23: snapshot は 最初、freeze も orient も 何もする前 に採取。
        # (v0.5.22 まで freeze 後に WM snapshot → freeze による WM 変化が
        # delta 計算に含まれず bindPreMatrix 補正が不完全だった。 実際には
        # 数学的に freeze 前後で world matrix は同じはずだが、浮動小数点や
        # 実装の副作用で微妙にズレる可能性。 最も安全な起点で snapshot する)
        all_ws_snapshot = {}
        all_wm_before = {}
        for r in roots:
            if not cmds.objExists(r):
                continue
            for j in _collect_chain_from_root(r):
                try:
                    all_ws_snapshot[j] = cmds.xform(j, q=True, ws=True, t=True)
                    all_wm_before[j] = cmds.getAttr(j + ".worldMatrix[0]")
                except Exception:
                    pass
        all_bpm_before = {}
        for sc, ji in joint_indices.items():
            for j, idx in ji.items():
                try:
                    all_bpm_before[(sc, j)] = cmds.getAttr(
                        f"{sc}.bindPreMatrix[{idx}]")
                except Exception:
                    pass

        # v0.5.16: 4. 各 root chain の rotate を jointOrient に freeze
        # v0.5.23: freeze は WM snapshot の後に実行 (snapshot が freeze 影響を
        #          受けないよう)
        for r in roots:
            if not cmds.objExists(r):
                continue
            for j in _collect_chain_from_root(r):
                r_vals = cmds.getAttr(j + ".rotate")[0]
                if any(abs(v) > 1e-6 for v in r_vals):
                    _freeze_rotate_to_jointOrient(j)

        # v0.5.26: 内部反復 (最大 5 pass、変化無くなったら早期終了)。
        # v0.5.25 の DG eval 強化でもまだ 3-4 回実行が必要な症状があった。
        # snapshot は最初に 1 回のみ (元 world 形状を維持する ground truth)、
        # orient loop を multi-pass で回して収束させる。
        MAX_ORIENT_PASSES = 5
        CONVERGE_EPS = 0.01   # jointOrient 変化 (degrees) 閾値
        for pass_i in range(MAX_ORIENT_PASSES):
            any_changed = False
            for r in roots:
                if not cmds.objExists(r):
                    continue
                chain_joints = _collect_chain_from_root(r)
                if pass_i == 0:
                    print(f"[jiggle_bones] orient chain root={r}, "
                          f"{len(chain_joints)} joints")
                for j in chain_joints:
                    has_child = bool(cmds.listRelatives(j, c=True, type="joint"))
                    jo_before = cmds.getAttr(j + ".jointOrient")[0]

                    if has_child:
                        # pass 1 だけ verbose (2 回目以降うるさいので)
                        _manual_orient_joint_to_child(j, aim=aim,
                                                       verbose=(pass_i == 0))
                    else:
                        try:
                            cmds.setAttr(j + ".jointOrient", 0, 0, 0)
                        except Exception:
                            pass
                    _force_dg_eval_recursive(j)

                    # descendants の WS を最初の snapshot に復元
                    descendants = cmds.listRelatives(j, ad=True,
                                                       type="transform") or []
                    for d in descendants:
                        if d in all_ws_snapshot:
                            try:
                                cmds.xform(d, ws=True, t=all_ws_snapshot[d])
                            except Exception:
                                pass
                    _force_dg_eval_recursive(j)

                    jo_after = cmds.getAttr(j + ".jointOrient")[0]
                    joint_changed = any(abs(jo_before[i] - jo_after[i]) > CONVERGE_EPS
                                        for i in range(3))
                    if joint_changed:
                        any_changed = True
                    if pass_i == 0:
                        mark = " ★" if joint_changed else " (unchanged)"
                        print(f"    {j}: jo {[round(x,1) for x in jo_before]}"
                              f" → {[round(x,1) for x in jo_after]}{mark}")
            print(f"[jiggle_bones] orient pass {pass_i+1}: "
                  f"{'変化あり (continue)' if any_changed else '収束 (break)'}")
            if not any_changed:
                break

        # v0.5.22: bindPreMatrix を delta-based で更新。
        # 従来 (v0.5.15-v0.5.21) は BPM_new = WM_new.inverse として
        # "bind pose を今にリセット" していたが、これは original BPM が
        # 特殊値 (offset 込み) だと skinning が壊れる。
        #
        # 数式 (Maya row-vector 慣習):
        #   skinning: output = input * BPM * WM
        #   出力不変 (output = input * BPM_old * WM_old = input * BPM_new * WM_new)
        #   → BPM_new = BPM_old * WM_old * WM_new.inverse
        #
        # これで original BPM の特殊値も 保存されつつ 新 WM に追従する。
        try:
            import maya.api.OpenMaya as om
        except Exception:
            om = None
        n_updated = 0
        for sc, ji in joint_indices.items():
            for j, idx in ji.items():
                try:
                    wm_new = cmds.getAttr(j + ".worldMatrix[0]")
                    if om and (sc, j) in all_bpm_before and j in all_wm_before:
                        m_bpm_old = om.MMatrix(all_bpm_before[(sc, j)])
                        m_wm_old = om.MMatrix(all_wm_before[j])
                        m_wm_new_inv = om.MMatrix(wm_new).inverse()
                        m_bpm_new = m_bpm_old * m_wm_old * m_wm_new_inv
                        bpm_flat = [m_bpm_new.getElement(r, c)
                                     for r in range(4) for c in range(4)]
                        cmds.setAttr(f"{sc}.bindPreMatrix[{idx}]", bpm_flat,
                                      type="matrix")
                    else:
                        # fallback: 従来動作
                        wm_inv = cmds.getAttr(j + ".worldInverseMatrix[0]")
                        cmds.setAttr(f"{sc}.bindPreMatrix[{idx}]", wm_inv,
                                      type="matrix")
                    n_updated += 1
                except Exception as exc:
                    cmds.warning(f"[jiggle_bones] bindPreMatrix update "
                                  f"{sc}[{idx}] {j}: {exc}")
        print(f"[jiggle_bones] orient: bindPreMatrix updated {n_updated} slot(s) "
              f"(delta-based)")

        # v0.5.16: 7. bindPose reset は 元 pose に joint が居ない場合
        # "not in the pose" エラーが大量に出るので、影響 joint を bindPose
        # の member に含んでるやつだけ狙って reset。
        bp_nodes = cmds.ls(type="dagPose") or []
        joint_set = set(all_joints)
        for bp in bp_nodes:
            try:
                members = cmds.dagPose(bp, q=True, members=True) or []
            except Exception:
                members = []
            members_short = set(m.split("|")[-1].split(":")[-1]
                                 for m in members)
            relevant = [j for j in all_joints
                        if j.split("|")[-1].split(":")[-1] in members_short]
            if not relevant:
                continue
            for j in relevant:
                try:
                    cmds.dagPose(j, reset=True, name=bp)
                except Exception:
                    pass

    finally:
        # 7. envelope 復元
        for sc, env in saved_env.items():
            try:
                cmds.setAttr(sc + ".envelope", env)
            except Exception:
                pass

    return len(all_joints)


def _ui_run_orient(*_):
    """UI ボタン: 選択 root(s) を対象に joint orient を実行 (weight 保護)。
    v0.5.16: 各ステップに verbose 出力を追加、silent 失敗の原因追跡用。"""
    print(f"[jiggle_bones] _ui_run_orient called")
    # joint type だけでなく transform も受ける (joint 判定ゆるく)
    sel_all = cmds.ls(sl=True) or []
    sel = []
    for s in sel_all:
        if cmds.nodeType(s) == "joint" or cmds.objectType(s, isa="joint"):
            sel.append(s)
    print(f"[jiggle_bones] selection: {sel_all}  (joints: {sel})")
    if not sel:
        cmds.warning("Outliner / viewport で root joint(s) を選択してください "
                     "(joint type であること)")
        return
    aim = "yzx"
    if cmds.optionMenu(_UI_ORIENT_AIM_MENU, ex=True):
        raw = cmds.optionMenu(_UI_ORIENT_AIM_MENU, q=True, v=True)
        print(f"[jiggle_bones] orient aim UI value: {raw!r}")
        if raw and "(" in raw:
            axis_key = raw.split("(")[0].strip().rstrip("+-").strip()
            aim_map = {"X": "xyz", "Y": "yzx", "Z": "zxy"}
            aim = aim_map.get(axis_key, "yzx")
    print(f"[jiggle_bones] resolved aim = {aim}")
    result = cmds.confirmDialog(
        t="Joint Orient (weight 保護)",
        m=f"選択 {len(sel)} root chain に対して joint orient を実行します。\n"
          f"aim = {aim}\n\n"
          f"影響 skinCluster の bindPreMatrix を更新するので\n"
          f"weight/見た目は保持されます (envelope は一時 OFF)。\n\n"
          f"元に戻せません (Undo 可)。実行しますか?",
        b=["実行", "Cancel"], defaultButton="実行",
        cancelButton="Cancel", dismissString="Cancel")
    print(f"[jiggle_bones] confirmDialog result: {result!r}")
    if result != "実行":
        print(f"[jiggle_bones] cancelled by user")
        return
    try:
        n = orient_joints_preserving_weights(sel, aim=aim)
        print(f"[jiggle_bones] orient 完了: {n} joint(s)")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        cmds.warning(f"[jiggle_bones] orient エラー: {exc}")


# =========================================================================
# Ctl shape replacer (v0.5.29) — 選択カーブを他 ctl の shape に差し替え
# =========================================================================

def replace_ctl_curves(source, targets, preserve_color=True):
    """v0.5.29: source curve の shape を targets 各々に コピー、既存 shape を
    差し替える (rig 構造は 保持、見た目 のみ変える)。

    Args:
        source:  shape コピー元 (curve transform)
        targets: 差し替え対象 の ctl transform list
        preserve_color: True なら target の 既存 color 継承 (overrideColor 等)

    使い方例:
        replace_ctl_curves("myCustomCube", ["skirt_00_jbCtl", "skirt_01_jbCtl"])
    """
    if isinstance(targets, str):
        targets = [targets]
    if not (source and cmds.objExists(source)):
        cmds.warning(f"[jiggle_bones] source curve 存在しない: {source}")
        return 0
    src_shapes = cmds.listRelatives(source, s=True, type="nurbsCurve") or []
    if not src_shapes:
        cmds.warning(f"[jiggle_bones] {source} に nurbsCurve shape が無い")
        return 0

    n_replaced = 0
    for tgt in targets:
        if not cmds.objExists(tgt):
            cmds.warning(f"[jiggle_bones] target 存在しない: {tgt}")
            continue
        # 既存 color を保存
        old_shapes = cmds.listRelatives(tgt, s=True, type="nurbsCurve") or []
        saved_color = None
        if preserve_color and old_shapes:
            try:
                if cmds.getAttr(old_shapes[0] + ".overrideEnabled"):
                    saved_color = {
                        "enabled": True,
                        "rgb": cmds.getAttr(old_shapes[0] + ".overrideRGBColors"),
                        "colorIdx": cmds.getAttr(old_shapes[0] + ".overrideColor"),
                        "colorR": cmds.getAttr(old_shapes[0] + ".overrideColorR"),
                        "colorG": cmds.getAttr(old_shapes[0] + ".overrideColorG"),
                        "colorB": cmds.getAttr(old_shapes[0] + ".overrideColorB"),
                    }
            except Exception:
                pass
        # 既存 shape 削除
        for s in old_shapes:
            try: cmds.delete(s)
            except Exception: pass
        # source shape を duplicate → target に parent
        dup = cmds.duplicate(source, rc=True)[0]
        dup_shapes = cmds.listRelatives(dup, s=True, type="nurbsCurve") or []
        for i, ds in enumerate(dup_shapes):
            try:
                new_name = f"{tgt}Shape" if i == 0 else f"{tgt}Shape{i}"
                ds = cmds.rename(ds, new_name)
                cmds.parent(ds, tgt, s=True, r=True)
                # color 復元
                if saved_color:
                    try:
                        cmds.setAttr(ds + ".overrideEnabled", 1)
                        cmds.setAttr(ds + ".overrideRGBColors",
                                      saved_color["rgb"])
                        if saved_color["rgb"]:
                            cmds.setAttr(ds + ".overrideColorR",
                                          saved_color["colorR"])
                            cmds.setAttr(ds + ".overrideColorG",
                                          saved_color["colorG"])
                            cmds.setAttr(ds + ".overrideColorB",
                                          saved_color["colorB"])
                        else:
                            cmds.setAttr(ds + ".overrideColor",
                                          saved_color["colorIdx"])
                    except Exception:
                        pass
            except Exception as exc:
                cmds.warning(f"[jiggle_bones] shape parent {ds}→{tgt}: {exc}")
        # duplicate transform を掃除
        try: cmds.delete(dup)
        except Exception: pass
        n_replaced += 1
        print(f"[jiggle_bones] replaced ctl shape: {tgt} ← {source}")
    print(f"[jiggle_bones] replace_ctl_curves: {n_replaced}/{len(targets)} done")
    return n_replaced


def _ui_replace_ctl_curves(*_):
    """UI: 選択の 最初 = source curve、残り = target ctl(s)。"""
    sel = cmds.ls(sl=True) or []
    if len(sel) < 2:
        cmds.warning("最初に source curve、その後に target ctl(s) を "
                     "Shift+選択してください (計 2 個以上)")
        return
    source = sel[0]
    targets = sel[1:]
    result = cmds.confirmDialog(
        t="Ctl 差し替え確認",
        m=f"Source curve: {source}\n"
          f"Target ctl(s): {len(targets)} 個\n  {', '.join(targets[:5])}"
          f"{'…' if len(targets) > 5 else ''}\n\n"
          f"target の 既存 shape を削除 → source shape でコピー差し替え。\n"
          f"color / rig 構造 (parent/constraint) は 保持。 実行しますか?",
        b=["実行", "Cancel"], defaultButton="実行",
        cancelButton="Cancel", dismissString="Cancel")
    if result != "実行":
        return
    n = replace_ctl_curves(source, targets)
    print(f"[jiggle_bones] {n} ctl(s) 差し替え完了")


# =========================================================================
# Master ctl (v0.5.30) — 足元 に per-category ON/OFF ctl を配置
# =========================================================================

MASTER_CTL_NAME = "jb_master_ctl"
MASTER_CTL_NPO = "jb_master_ctl_npo"


def _make_master_curve(name, size=1.0):
    """v0.5.32: "Jiggle" テキスト curve で master shape 生成。
    Maya `textCurves` で "Jiggle" を各文字 nurbsCurve として生成 →
    全 shape を 1 transform 下に combine。 XZ 平面 flat (Y=0)、上から
    読める向き。 サイズは 全体 size で調整。"""
    # 1. textCurves 生成 (default font)
    try:
        result = cmds.textCurves(f="Arial", t="Jiggle", ch=False)
    except Exception:
        # Font 指定 fail 時は default font へ fallback
        result = cmds.textCurves(t="Jiggle", ch=False)
    text_top = result[0]

    # 2. 各文字 transform (curve shape を持つ) を集める
    all_desc = cmds.listRelatives(text_top, ad=True, type="transform") or []
    letter_xforms = [x for x in all_desc
                     if cmds.listRelatives(x, s=True, type="nurbsCurve")]

    # 3. 各文字 transform を world unparent → makeIdentity (freeze) →
    #    CV が world 座標に固定 (親 translate 分が 焼き込まれる)
    freed = []
    for lx in letter_xforms:
        try:
            lx_w = cmds.parent(lx, world=True)[0]
            cmds.makeIdentity(lx_w, apply=True, t=True, r=True, s=True)
            freed.append(lx_w)
        except Exception:
            pass

    # 4. 元 textCurves ツリー掃除
    try: cmds.delete(text_top)
    except Exception: pass

    # 5. 新 master transform に 全 shape を combine
    master = cmds.createNode("transform", n=name)
    for lx in freed:
        shapes = cmds.listRelatives(lx, s=True, type="nurbsCurve") or []
        for s in shapes:
            try:
                cmds.parent(s, master, s=True, r=True)
            except Exception:
                pass
        try: cmds.delete(lx)
        except Exception: pass

    # 6. rotate → freeze → scale → freeze → center → freeze
    # (scale 前に centering しても pivot が原点で無いと scale で ずれるので
    #  scale 完了後に 最終 bbox から center 算出。)
    cmds.xform(master, ro=(-90, 0, 0))
    cmds.makeIdentity(master, apply=True, r=True)
    if size != 1.0:
        # pivot を 原点 に明示
        cmds.xform(master, sp=(0, 0, 0), rp=(0, 0, 0))
        cmds.xform(master, s=(size, size, size))
        cmds.makeIdentity(master, apply=True, s=True)
    # 最終 bbox から center を原点に
    bbox = cmds.exactWorldBoundingBox(master)
    cx = (bbox[0] + bbox[3]) / 2.0
    cy = (bbox[1] + bbox[4]) / 2.0
    cz = (bbox[2] + bbox[5]) / 2.0
    cmds.xform(master, t=(-cx, -cy, -cz))
    cmds.makeIdentity(master, apply=True, t=True)
    return master


def create_or_get_master_ctl(position=None, size=None):
    """v0.5.30: 足元 (or 指定位置) に per-category master ctl を作る/取得。

    Args:
        position: world position (x,y,z)。None なら 世界原点。
        size: curve size。None なら auto (mesh bbox 参照)。

    Returns: master ctl transform 名
    """
    if cmds.objExists(MASTER_CTL_NAME):
        return MASTER_CTL_NAME

    # size 決定
    if size is None:
        try:
            bbox = cmds.exactWorldBoundingBox(cmds.ls(type="mesh")) or None
            if bbox:
                dx = bbox[3] - bbox[0]
                dz = bbox[5] - bbox[2]
                size = max(1.0, min(dx, dz) * 0.15)
            else:
                size = 3.0
        except Exception:
            size = 3.0

    # position 決定
    if position is None:
        position = (0, 0, 0)

    # NPO + ctl
    if cmds.objExists(MASTER_CTL_NPO):
        cmds.delete(MASTER_CTL_NPO)
    ctl = _make_master_curve(MASTER_CTL_NAME, size=size)
    # 色 (青紫、目立たせる)
    shapes = cmds.listRelatives(ctl, s=True) or []
    for s in shapes:
        try:
            cmds.setAttr(s + ".overrideEnabled", 1)
            cmds.setAttr(s + ".overrideColor", 15)   # dark purple / blue
        except Exception:
            pass
    npo = cmds.group(em=True, n=MASTER_CTL_NPO)
    cmds.parent(ctl, npo)
    cmds.xform(npo, ws=True, t=position)

    # attach_ctrls の main_ctl か 世界直下 に置く
    if cmds.objExists("main_ctl"):
        try: cmds.parent(npo, "main_ctl")
        except Exception: pass
    _ensure_jb_group()
    if not cmds.listRelatives(npo, p=True):
        try: cmds.parent(npo, "jiggle_bones_grp")
        except Exception: pass

    # 各 category に対して bool attr 追加 (0=off, 1=on)、default 1
    for cat in _JIGGLE_TOKENS.keys():
        if not cmds.attributeQuery(cat, node=ctl, exists=True):
            cmds.addAttr(ctl, ln=cat, at="bool", dv=1, k=True)

    # 全 category one-shot ON/OFF 便利 attr
    if not cmds.attributeQuery("allSim", node=ctl, exists=True):
        cmds.addAttr(ctl, ln="allSim", at="bool", dv=1, k=True)

    print(f"[jiggle_bones] master ctl 作成: {ctl} (size={size}, "
          f"attrs={list(_JIGGLE_TOKENS.keys())})")
    return ctl


def wire_master_to_chains():
    """v0.5.30: 全 chain の hairSystem.active を master ctl の 該当 category
    attr で drive する。 既に接続済 の hairSystem は skip。

    condition node で:
      hairSystem.active = master.allSim AND master.<category>
    (両方 1 で active、どちらか 0 で off)
    """
    if not cmds.objExists(MASTER_CTL_NAME):
        cmds.warning(f"[jiggle_bones] {MASTER_CTL_NAME} 未生成、"
                     "create_or_get_master_ctl() を先に")
        return 0

    n_wired = 0
    for cat in _JIGGLE_TOKENS.keys():
        hs_list = _hair_systems_for_category(cat)
        for hs_xform in hs_list:
            shapes = cmds.listRelatives(hs_xform, s=True,
                                          type="hairSystem") or []
            if not shapes:
                continue
            hs = shapes[0]
            # 既存 driver を切断 (二重防止)
            for c in cmds.listConnections(hs + ".active", s=True, d=False,
                                            plugs=True) or []:
                try: cmds.disconnectAttr(c, hs + ".active")
                except Exception: pass
            # multiplyDivide で AND (allSim × cat_attr)
            md_name = f"{hs}_activeGate"
            if cmds.objExists(md_name):
                try: cmds.delete(md_name)
                except: pass
            md = cmds.createNode("multiplyDivide", n=md_name)
            cmds.setAttr(md + ".operation", 1)   # multiply
            cmds.connectAttr(f"{MASTER_CTL_NAME}.allSim",
                              md + ".input1X", f=True)
            cmds.connectAttr(f"{MASTER_CTL_NAME}.{cat}",
                              md + ".input2X", f=True)
            cmds.connectAttr(md + ".outputX", hs + ".active", f=True)
            n_wired += 1
    print(f"[jiggle_bones] wire_master_to_chains: {n_wired} hairSystem(s) wired")
    return n_wired


def _ui_setup_master(*_):
    """UI: 選択位置に master ctl を作成 (無ければ世界原点)、全 chain wire。"""
    pos = None
    sel = cmds.ls(sl=True) or []
    if sel:
        try:
            pos = cmds.xform(sel[0], q=True, ws=True, t=True)
            print(f"[jiggle_bones] master 位置 = {sel[0]} の world pos {pos}")
        except Exception:
            pass
    create_or_get_master_ctl(position=pos)
    wire_master_to_chains()


def set_aim_axis_all(axis):
    """v0.5.14: 既存の全 jb SplineIK の dForwardAxis を一括変更。
    setup し直さずに 子方向軸を修正できる。"""
    if axis not in _AIM_AXIS_TO_DFA:
        cmds.warning(f"[jiggle_bones] invalid axis {axis}, choose from {_AIM_AXIS_CHOICES}")
        return 0
    dfa = _AIM_AXIS_TO_DFA[axis]
    up_axis_for_aim = {"X+": 0, "X-": 0, "Y+": 2, "Y-": 2, "Z+": 2, "Z-": 2}
    dwua = up_axis_for_aim.get(axis, 2)
    n = 0
    for ikh in cmds.ls("jb_ikh_*", type="ikHandle") or []:
        try:
            cmds.setAttr(ikh + ".dTwistControlEnable", 1)
            cmds.setAttr(ikh + ".dForwardAxis", dfa)
            # v0.5.28: WorldUp も設定して twist 完全制御
            cmds.setAttr(ikh + ".dWorldUpType", 4)
            cmds.setAttr(ikh + ".dWorldUpAxis", dwua)
            # start/end joint の worldMatrix を connect
            sj = cmds.ikHandle(ikh, q=True, sj=True)
            # end joint は effector 経由で 遡る
            ee = cmds.ikHandle(ikh, q=True, ee=True)
            ee_j = cmds.listConnections(ee + ".translate", s=True, d=False) or []
            end_j = ee_j[0] if ee_j else None
            if sj:
                try:
                    cmds.connectAttr(sj + ".worldMatrix[0]",
                                      ikh + ".dWorldUpMatrix", f=True)
                except Exception:
                    pass
            if end_j:
                try:
                    cmds.connectAttr(end_j + ".worldMatrix[0]",
                                      ikh + ".dWorldUpMatrixEnd", f=True)
                except Exception:
                    pass
            n += 1
        except Exception:
            pass
    set_current_aim_axis(axis)
    print(f"[jiggle_bones] set_aim_axis_all: {n} ikHandle(s) → {axis} "
          f"(dFA={dfa}, dWorldUpAxis={dwua})")
    return n


def create_jiggle_for_chain(chain, category=None, aim_axis=None):
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

    # v0.5.14: 明示指定 aim_axis があれば cache に (無ければ module cache 維持)
    if aim_axis is not None:
        set_current_aim_axis(aim_axis)

    # 既存があれば先に remove (二重張り防止)
    if is_chain_active(chain):
        remove_jiggle_for_chain(chain)

    _ensure_jb_group()
    # v0.5.0: per-chain hairSystem (per-category shared 廃止)
    hs_xform, hs_shape = _get_or_create_hair_system(category, chain=chain)

    # ---- 1. FK ctls (各 joint 位置に nested cube) ----
    fk_ctls, fk_npos = _create_fk_ctls_for_chain(chain)
    root_ctl = fk_ctls[0]

    # ---- 2. FK duplicate chain (FK ctls が rotate 駆動 → rest curve に伝達) ----
    fk_chain = _dup_chain(chain, _fk_joint_name)
    _parent_to_jb(fk_chain[0])
    for ctl, fkj in zip(fk_ctls, fk_chain):
        cmds.orientConstraint(ctl, fkj, mo=False, n=fkj + "_oc")
    cmds.pointConstraint(root_ctl, fk_chain[0], mo=False,
                          n=fk_chain[0] + "_pc")

    # ---- 3. Rest curve: joint 位置で作成 → CV を FK chain に cluster 束縛 ----
    #    Option B (hybrid): FK ctl を回すと rest curve が変形 →
    #    sim の "attract target" が ctl 追従する
    rest_curve = _create_rest_curve_from_chain(chain)
    _bind_curve_cvs_to_joints(rest_curve, fk_chain)

    # ---- 4. makeCurvesDynamic on rest curve → hairSystem + follicle + curve2 ----
    dyn_shape = _add_follicle_to_hair_system(rest_curve, hs_shape, chain)

    # ---- 5. SplineIK を ORIGINAL chain 直接に張る (v0.5.0 core、article verbatim) ----
    #    curve2.worldSpace[0] → ikHandle.inCurve
    #    → collision force が減衰なく直接 ORIGINAL rotate を駆動
    ikh = _create_spline_ik(chain, dyn_shape)

    # ---- 6. dynBlend attr (legacy) + startCurveAttract を UI 直接制御に ----
    #    v0.5.5: v0.5.0-v0.5.4 の `dynBlend → reverse → startCurveAttract`
    #    ネットワークを廃止。理由:
    #      - dynBlend=1 だと reverse.outputX = 0 = attract 0
    #      - attract 0 は rest curve への引き戻し力ゼロ → hair は重力で漂う
    #        だけになり、UI で damp / stiffness をどう弄っても "戻り" が
    #        発生しない ("全体的に動きが遅い" 報告の根本原因)
    #    v0.5.5:
    #      - reverse ノード作らない、attract は UI slider から直接 setAttr
    #      - dynBlend 属性は root_ctl に残す (旧 anim curve 互換のため) が、
    #        新規 setup では 何にも接続されない (legacy 表示)
    if not cmds.attributeQuery("dynBlend", node=root_ctl, exists=True):
        cmds.addAttr(root_ctl, ln="dynBlend", at="float",
                      min=0.0, max=1.0, dv=1.0, k=True)

    # ---- 6b. active=1 を再保証 (v0.5.2) ----
    #    makeCurvesDynamic 実行や inputHair connect の副作用で hairSystem.active
    #    が False に戻る事例あり (実 scene dump で確認)。setup の最終段で
    #    改めて 1 に。
    try:
        cmds.setAttr(hs_shape + ".active", 1)
    except Exception:
        pass

    # ---- 7. root joint translate (world-parented chain のみ、SplineIK は rotate 制御) ----
    parent_j = cmds.listRelatives(chain[0], p=True, type="joint") or []
    if not parent_j:
        try:
            cmds.pointConstraint(root_ctl, chain[0], mo=True,
                                    n=_root_pc_name(chain))
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] root translate constraint failed: {exc}")

    # v0.5.13: chain setup 完了時に scene 全体で MCD 副産物掃除。
    try:
        _cleanup_mcd_junk_inline()
    except Exception:
        pass

    # v0.5.30: master ctl があれば その新 chain の hairSystem を wire。
    # (master ctl は 別 UI ボタン or 手動で作成、無ければ skip)
    if cmds.objExists(MASTER_CTL_NAME):
        try:
            wire_master_to_chains()
        except Exception:
            pass

    print(f"[jiggle_bones] v0.5.0 setup {_chain_id(chain)} → "
          f"category={category}, joints={len(chain)}, "
          f"hairSystem={hs_xform}, root_ctl={root_ctl}")
    return {
        "chain":         chain,
        "category":      category,
        "fk_chain":      fk_chain,
        "fk_ctls":       fk_ctls,
        "root_ctl":      root_ctl,
        "rest_curve":    rest_curve,
        "dynamic_curve": dyn_shape,
        "follicle":      _follicle_name(chain),
        "ik_handle":     ikh,
        "hair_system":   hs_xform,
        "dynamics_attr": f"{root_ctl}.dynBlend",
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
    # (v0.4.6: rebuildCurve は MCD 自動生成分は connection 破棄で参照解除、
    #  遺る場合は cmds.ls で clean up)
    for nm in (_dyn_curve_name(chain), _follicle_name(chain),
               _rest_curve_name(chain)):
        if cmds.objExists(nm):
            try: cmds.delete(nm)
            except Exception: pass
    # 名前規則不定の rebuildCurve / hair intermediate curve を掃除
    chain_id = _chain_id(chain)
    for pat in (f"*{chain_id}*rebuild*", f"*{chain_id}*Rebuilt*"):
        for n in cmds.ls(pat) or []:
            try: cmds.delete(n)
            except Exception: pass
    # clusters: CV 数だけ (実際の CV 数は curve が既に消えているので、
    # 元 chain の joint 数を上限に片っ端から検索)
    for i in range(len(chain) + 4):  # 少し余裕を持たせて
        clu = _cluster_name(chain, i)
        if cmds.objExists(clu):
            try: cmds.delete(clu)
            except Exception: pass

    # 5. FK ctls + npo + FK chain + (旧 v0.4.x の DYN chain も掃除)
    for orig in chain:
        for nm in (_fk_ctl_name(orig), _fk_npo_name(orig),
                    _fk_joint_name(orig), _dyn_joint_name(orig)):
            if cmds.objExists(nm):
                try: cmds.delete(nm)
                except Exception: pass

    # 6. v0.5.0: per-chain hairSystem を削除 (旧 per-category shared は共有なので残す)
    chain_id = _chain_id(chain)
    # 各カテゴリ名を試してマッチする per-chain hairSystem を探す
    for cat in list(_JIGGLE_TOKENS.keys()) + ["hair"]:
        hs_xform = f"jb_hs_{cat}_{chain_id}"
        if cmds.objExists(hs_xform):
            try: cmds.delete(hs_xform)
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

    v0.4.7 記事準拠: Maya 標準の `makeCollideNCloth` MEL を優先使用
    (FX > nCloth > Create Passive Collider 相当)。mayapy 等で MEL 未使用の
    fallback として直接 createNode 経路を残す。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    if not cmds.objExists(mesh):
        cmds.warning(f"[jiggle_bones] mesh not found: {mesh}")
        return None
    mesh_shapes = cmds.listRelatives(mesh, s=True, type="mesh") or []
    if not mesh_shapes:
        cmds.warning(f"[jiggle_bones] {mesh} に mesh shape が無い")
        return None
    mesh_shape = mesh_shapes[0]

    nucleus = _get_or_create_nucleus()

    # nCloth plugin load
    try:
        if not cmds.pluginInfo("nCloth", q=True, l=True):
            cmds.loadPlugin("nCloth", quiet=True)
    except Exception:
        pass

    nr_before = set(cmds.ls(type="nRigid"))
    created_via_mel = False
    nr_shape = None

    # ------ Route A: Maya 標準 MEL (interactive Maya) ------
    try:
        mel.eval(f'setActiveNucleusNode "{nucleus}"')
        sel_before = cmds.ls(sl=True)
        cmds.select(mesh, r=True)
        mel.eval("makeCollideNCloth;")
        cmds.select(sel_before or None, r=True)
        new_nr = list(set(cmds.ls(type="nRigid")) - nr_before)
        if new_nr:
            nr_shape = new_nr[0]
            created_via_mel = True
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] makeCollideNCloth MEL failed → "
                      f"fallback to manual: {exc}")

    # ------ Route B: 手組み fallback (mayapy standalone 用) ------
    if nr_shape is None:
        shape_name = _collider_name(mesh) + "Shape"
        nr_shape = cmds.createNode("nRigid", n=shape_name)
        try:
            cmds.connectAttr(mesh_shape + ".worldMesh[0]",
                              nr_shape + ".inputMesh", f=True)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] mesh 接続失敗: {exc}")
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

    # transform を jb 規則で rename
    nr_xform_parents = cmds.listRelatives(nr_shape, p=True) or []
    if nr_xform_parents:
        old_xform = nr_xform_parents[0]
        target_name = _collider_name(mesh)
        if old_xform != target_name and not cmds.objExists(target_name):
            try:
                cmds.rename(old_xform, target_name)
                nr_shape = cmds.listRelatives(target_name, s=True)[0]
            except Exception:
                pass
    nr_xform = cmds.listRelatives(nr_shape, p=True)[0]

    # v0.4.2 貫通対策 (v0.4.3 改良): nRigid.thickness の default (0.1) は
    # 薄すぎて MMD 系キャラ (Y=20+) では実質透明。
    # 平面 mesh (minDim=0) にも対応するため MAX 辺基準に変更。
    # 目安: 最大辺の 1% (最小 0.2、最大 5.0 でクランプ)
    try:
        bb = cmds.exactWorldBoundingBox(mesh)   # [xmin,ymin,zmin, xmax,ymax,zmax]
        dims = [bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]]
        max_dim = max(dims)
        thickness = max(0.2, min(5.0, max_dim * 0.01))
    except Exception:
        thickness = 0.5
    for a, v in (("thickness", thickness),
                  ("pushOut", thickness * 0.5),
                  ("pushOutRadius", thickness * 4.0),
                  ("bounce", 0.0),
                  ("friction", 0.2),
                  ("stickiness", 0.0),
                  ("collisionFlag", 4)):   # 4 = mesh face (default 3 = surface)
        try:
            cmds.setAttr(f"{nr_shape}.{a}", v)
        except Exception:
            pass
    print(f"[jiggle_bones] nRigid thickness={thickness:.3f} (mesh bbox 最短辺 "
          f"の 2%、貫通しづらい厚みに自動設定)")

    _parent_to_jb(nr_xform)

    # v0.4.1: コライダーを追加した瞬間に全 hairSystem の collide=1 も張って
    # 「add した瞬間に効く」ようにする。既存 setup (v0.4.0 以前) にも安全。
    enable_collision_on_all_hair_systems()

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


def enable_collision_on_all_hair_systems():
    """既存の jb_hairSystem_* すべてに collide=1 + 高精度パラメータを強制セット。
    v0.4.0 以前で作った setup を v0.4.2+ の collision 対応に upgrade する。
    さらに nucleus と全 nRigid も高精度化 (subSteps / thickness auto)。
    Returns: 更新した hairSystem 数。"""
    n = 0
    # hairSystem
    for xf in cmds.ls("jb_hairSystem_*", type="transform") or []:
        shapes = cmds.listRelatives(xf, s=True, type="hairSystem") or []
        for sh in shapes:
            for coll_attr, val in (("collide", 1),
                                    ("collideStrength", 1.0),
                                    ("iterations", 3),
                                    ("collideOverSample", 4),
                                    ("collideWidthOffset", 1.0)):
                try:
                    cmds.setAttr(f"{sh}.{coll_attr}", val)
                except Exception:
                    pass
            n += 1
    # nucleus
    if cmds.objExists(NUCLEUS_NAME):
        for attr, val in (("subSteps", 6), ("maxCollisionIterations", 8)):
            try:
                cmds.setAttr(f"{NUCLEUS_NAME}.{attr}", val)
            except Exception:
                pass
    # nRigid collider の thickness を mesh bbox から再算出
    for coll_xf in cmds.ls("jb_collider_*", type="transform") or []:
        # jb_collider_<meshName> → 元 mesh 名を復元して bbox 取得
        mesh_name = coll_xf[len("jb_collider_"):]
        if not cmds.objExists(mesh_name):
            continue
        try:
            bb = cmds.exactWorldBoundingBox(mesh_name)
            dims = [bb[3]-bb[0], bb[4]-bb[1], bb[5]-bb[2]]
            max_dim = max(dims)
            thickness = max(0.2, min(5.0, max_dim * 0.01))
        except Exception:
            thickness = 0.5
        nr_shapes = cmds.listRelatives(coll_xf, s=True, type="nRigid") or []
        for sh in nr_shapes:
            for a, v in (("thickness", thickness),
                          ("pushOut", thickness * 0.5),
                          ("pushOutRadius", thickness * 4.0),
                          ("collisionFlag", 4)):
                try:
                    cmds.setAttr(f"{sh}.{a}", v)
                except Exception:
                    pass
    if n:
        print(f"[jiggle_bones] enabled collision on {n} hairSystem(s), "
              f"boosted nucleus + nRigid params")
    return n


_NUCLEUS_ATTRS = ("spaceScale", "subSteps", "maxCollisionIterations",
                   "gravity")
_COLLIDER_ATTRS = ("thickness", "pushOut", "pushOutRadius",
                    "friction", "bounce")


def get_nucleus_params():
    """nucleus の主要 attr を dict で返す (無ければ空)。"""
    if not cmds.objExists(NUCLEUS_NAME):
        return {}
    out = {}
    for a in _NUCLEUS_ATTRS:
        try:
            out[a] = cmds.getAttr(f"{NUCLEUS_NAME}.{a}")
        except Exception:
            pass
    return out


def set_nucleus_params(**params):
    """nucleus に param を適用 (existing のみ、無ければ何もしない)。"""
    if not cmds.objExists(NUCLEUS_NAME):
        return
    for a, v in params.items():
        try:
            cmds.setAttr(f"{NUCLEUS_NAME}.{a}", v)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] nucleus.{a}={v} failed: {exc}")


def get_collider_params(mesh_or_collider):
    """指定 collider の thickness/pushOut 等を dict で返す。
    mesh_or_collider は元 mesh 名 or `jb_collider_<mesh>` transform 名。"""
    coll = mesh_or_collider
    if not coll.startswith("jb_collider_"):
        coll = _collider_name(mesh_or_collider)
    if not cmds.objExists(coll):
        return {}
    shapes = cmds.listRelatives(coll, s=True, type="nRigid") or []
    if not shapes:
        return {}
    sh = shapes[0]
    out = {}
    for a in _COLLIDER_ATTRS:
        try:
            out[a] = cmds.getAttr(f"{sh}.{a}")
        except Exception:
            pass
    return out


def set_collider_params(mesh_or_collider, **params):
    """指定 collider の nRigid attr を上書き。"""
    coll = mesh_or_collider
    if not coll.startswith("jb_collider_"):
        coll = _collider_name(mesh_or_collider)
    if not cmds.objExists(coll):
        cmds.warning(f"[jiggle_bones] collider not found: {coll}")
        return
    shapes = cmds.listRelatives(coll, s=True, type="nRigid") or []
    if not shapes:
        return
    sh = shapes[0]
    for a, v in params.items():
        try:
            cmds.setAttr(f"{sh}.{a}", v)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] {sh}.{a}={v} failed: {exc}")


def diagnose_collision():
    """collision setup の状態を console に dump (貫通するときのデバッグ用)。"""
    print("=" * 70)
    print("[jiggle_bones] COLLISION DIAGNOSIS")
    print("=" * 70)
    # nucleus
    if not cmds.objExists(NUCLEUS_NAME):
        print("  [MISSING] nucleus (jb_nucleus)")
        return
    print(f"  nucleus: {NUCLEUS_NAME}")
    for a in ("enable", "spaceScale", "subSteps", "maxCollisionIterations",
              "gravity", "startFrame"):
        try:
            print(f"    .{a} = {cmds.getAttr(f'{NUCLEUS_NAME}.{a}')}")
        except Exception:
            pass
    # hairSystems
    hs_list = cmds.ls("jb_hairSystem_*", type="transform") or []
    if not hs_list:
        print("  [MISSING] no jb_hairSystem_*")
    for hs_xf in hs_list:
        shapes = cmds.listRelatives(hs_xf, s=True, type="hairSystem") or []
        if not shapes:
            continue
        sh = shapes[0]
        print(f"  hairSystem: {hs_xf}")
        for a in ("collide", "selfCollide", "collideStrength",
                  "collideOverSample", "collideWidthOffset",
                  "iterations", "stiffness", "damp", "mass"):
            try:
                print(f"    .{a} = {cmds.getAttr(f'{sh}.{a}')}")
            except Exception:
                pass
        # follicle 経由 nucleus 接続確認
        conns = cmds.listConnections(sh + ".currentState",
                                       d=True, s=False,
                                       type="nucleus") or []
        print(f"    → nucleus: {conns}")
    # nRigid colliders
    coll_list = cmds.ls("jb_collider_*", type="transform") or []
    if not coll_list:
        print("  [MISSING] no jb_collider_*")
    for coll_xf in coll_list:
        shapes = cmds.listRelatives(coll_xf, s=True, type="nRigid") or []
        if not shapes:
            continue
        sh = shapes[0]
        print(f"  collider: {coll_xf}")
        for a in ("thickness", "pushOut", "pushOutRadius",
                  "friction", "bounce", "collisionFlag"):
            try:
                print(f"    .{a} = {cmds.getAttr(f'{sh}.{a}')}")
            except Exception:
                pass
        conns = cmds.listConnections(sh + ".currentState",
                                       d=True, s=False,
                                       type="nucleus") or []
        print(f"    → nucleus: {conns}")
        # input mesh source
        mesh_src = cmds.listConnections(sh + ".inputMesh",
                                          s=True, d=False, sh=True) or []
        print(f"    input mesh: {mesh_src}")
    # follicles
    foll_list = cmds.ls("jb_foll_*", type="transform") or []
    for foll_xf in foll_list[:3]:  # 最初 3 個だけ dump
        shapes = cmds.listRelatives(foll_xf, s=True, type="follicle") or []
        if shapes:
            sh = shapes[0]
            print(f"  follicle: {foll_xf}")
            for a in ("simulationMethod", "pointLock", "restPose"):
                try:
                    print(f"    .{a} = {cmds.getAttr(f'{sh}.{a}')}")
                except Exception:
                    pass
    print("=" * 70)


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

_PARAM_ATTRS = ("stiffness", "damp", "startCurveAttract", "mass",
                # v0.5.4: 過減衰対策 (「1 回で止まる」を「自然に振動する」に)
                "drag", "motionDrag", "attractionDamp", "bendResistance",
                # v0.5.9: 当たり判定の張り出し幅 (小さいと mesh ぴったり、
                #         大きいと遠くから collision)
                "collideWidthOffset")


def _hair_systems_for_category(category):
    """v0.5.0: 該当 category の全 hairSystem transform 名を返す。
    per-chain (jb_hs_<cat>_<chain>) と 旧 per-category shared
    (jb_hairSystem_<cat>) の両方を拾う。"""
    out = []
    for pat in (f"jb_hs_{category}_*", f"jb_hairSystem_{category}"):
        for x in cmds.ls(pat, type="transform") or []:
            if x not in out:
                out.append(x)
    return out


def get_category_params(category):
    """category の 最初の hairSystem の現在 param を dict で返す。
    無ければ default 経験則。v0.5.0: per-chain hairSystem 対応。"""
    hs_list = _hair_systems_for_category(category)
    if not hs_list:
        return dict(DEFAULT_PARAMS_BY_CATEGORY.get(category, {}))
    shape = cmds.listRelatives(hs_list[0], s=True)[0]
    out = {}
    for a in _PARAM_ATTRS:
        try:
            out[a] = cmds.getAttr(f"{shape}.{a}")
        except Exception:
            pass
    return out


def _disconnect_incoming(plug):
    """plug に来ている全 incoming connection を切る (何も無ければ何もしない)。
    v0.5.5: startCurveAttract を UI で直接制御するために、旧 scene に残ってる
    reverse ノード等の 接続を setAttr 前に自動で外す。"""
    srcs = cmds.listConnections(plug, s=True, d=False, plugs=True) or []
    for s in srcs:
        try:
            cmds.disconnectAttr(s, plug)
        except Exception:
            pass


def set_category_params(category, **params):
    """category の 全 hairSystem に param を反映 (v0.5.0: per-chain 全部 loop)。
    v0.5.5: startCurveAttract は setAttr 前に incoming connection を外す
    (旧 scene の dynBlend→reverse 網を無効化して UI 値を反映させる)。"""
    hs_list = _hair_systems_for_category(category)
    n = 0
    for hs_xform in hs_list:
        shapes = cmds.listRelatives(hs_xform, s=True, type="hairSystem") or []
        if not shapes:
            continue
        shape = shapes[0]
        for a, v in params.items():
            plug = f"{shape}.{a}"
            # v0.5.5: startCurveAttract は接続を先に切って直接 setAttr
            if a == "startCurveAttract":
                _disconnect_incoming(plug)
            try:
                cmds.setAttr(plug, v)
            except Exception as exc:
                cmds.warning(f"[jiggle_bones] set {a}={v} on {shape} failed: {exc}")
        n += 1
    if n > 1:
        print(f"[jiggle_bones] set_category_params: {n} hairSystem(s) updated")


# =========================================================================
# UI (v0.2.0)
# =========================================================================

_UI_COLLIDER_LIST = "jbColliderList"
_UI_ADD_CATEGORY_MENU = "jbAddCategoryMenu"
_UI_AIM_AXIS_MENU = "jbAimAxisMenu"   # v0.5.14
_UI_ORIENT_AIM_MENU = "jbOrientAimMenu"   # v0.5.15
_UI_NUCLEUS_FIELDS = {}      # {attr: fieldGrp}
_UI_COLLIDER_FIELDS = {}     # {attr: fieldGrp}
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
    """UI の param field 値を hairSystem に反映。
    v0.5.3: floatSliderGrp と (旧) floatFieldGrp の両方を受ける。"""
    params = {}
    for attr in _PARAM_ATTRS:
        fld = _UI_CATEGORY_FIELDS.get((category, attr))
        if not fld:
            continue
        v = None
        if cmds.floatSliderGrp(fld, ex=True):
            v = cmds.floatSliderGrp(fld, q=True, value=True)
        elif cmds.floatFieldGrp(fld, ex=True):
            v = cmds.floatFieldGrp(fld, q=True, value1=True)
        if v is not None:
            params[attr] = v
    if params:
        set_category_params(category, **params)
        print(f"[jiggle_bones] {category} params updated: {params}")


def _ui_apply_nucleus_params(*_):
    """UI の nucleus fields を jb_nucleus に反映。"""
    if not cmds.objExists(NUCLEUS_NAME):
        cmds.warning("[jiggle_bones] jb_nucleus が未生成 (Setup を先に)")
        return
    params = {}
    for attr, fld in _UI_NUCLEUS_FIELDS.items():
        if not fld or not (cmds.floatFieldGrp(fld, ex=True)
                            or cmds.intFieldGrp(fld, ex=True)):
            continue
        if cmds.intFieldGrp(fld, ex=True):
            v = cmds.intFieldGrp(fld, q=True, value1=True)
        else:
            v = cmds.floatFieldGrp(fld, q=True, value1=True)
        params[attr] = v
    set_nucleus_params(**params)
    print(f"[jiggle_bones] nucleus params: {params}")


def _ui_apply_collider_params(*_):
    """UI の collider fields をリスト選択中の collider(s) に反映。"""
    items = cmds.textScrollList(_UI_COLLIDER_LIST, q=True, si=True) or []
    if not items:
        cmds.warning("collider リストで対象を選択してください")
        return
    params = {}
    for attr, fld in _UI_COLLIDER_FIELDS.items():
        if fld and cmds.floatFieldGrp(fld, ex=True):
            params[attr] = cmds.floatFieldGrp(fld, q=True, value1=True)
    # pushOutRadius は pushOut の 8x を目安に自動追従
    if "pushOut" in params:
        params["pushOutRadius"] = params["pushOut"] * 8.0
    for mesh in items:
        set_collider_params(mesh, **params)
    print(f"[jiggle_bones] applied to {len(items)} collider(s): {params}")


def _ui_setup_chain(category, chain, *_):
    create_jiggle_for_chain(chain, category=category)
    # 即座に category param を適用 (新規 hairSystem に UI 値を反映)
    _ui_apply_category_params(category)


def _ui_setup_all_enabled(*_):
    """v0.4.0: registry から chain を取得して checked のみセットアップ。"""
    n = 0
    detected = get_registered_chains()
    for (category, chain_id), cb in _UI_CHAIN_CHECKS.items():
        if not cmds.checkBox(cb, q=True, v=True):
            continue
        for chain in detected.get(category, []):
            if _chain_id(chain) == chain_id:
                create_jiggle_for_chain(chain, category=category)
                n += 1
                break
        _ui_apply_category_params(category)
    print(f"[jiggle_bones] Setup {n} enabled chain(s)")


def _ui_remove_all(*_):
    """v0.4.0: registry の全 chain を tear down + registry も空に。"""
    n = 0
    for cat_chain_list in get_registered_chains().values():
        for chain in cat_chain_list:
            if is_chain_active(chain):
                remove_jiggle_for_chain(chain)
                n += 1
    _write_registry([])
    print(f"[jiggle_bones] Removed {n} active setup(s) + cleared registry")


def _ui_remove_chain(chain, *_):
    """v0.4.0: rig tear down + registry からも除去。"""
    remove_jiggle_for_chain(chain)
    remove_registered_chain(chain)


def _ui_on_aim_axis_change(new_val, *_):
    """v0.5.14: UI dropdown 変更時に module cache へ反映。"""
    set_current_aim_axis(new_val)


def _ui_add_from_selection(*_):
    """v0.5.11: 複数 root 選択 → 各 root から chain を独立に建てて 全部 setup。

    ルール (build_chains_from_selection):
      - 選択 joint のうち 親も選択されてるのは skip (chain 途中)
      - 残った root それぞれから DFS で 単一 chain を作る
      - 各 chain を registry 登録 + create_jiggle_for_chain 実行

    エラーは try で包む: 途中の chain が失敗しても他の chain は続行。
    show_ui() は最後に 1 回だけ (rebuild を頻発させない)。"""
    chains = build_chains_from_selection()
    if not chains:
        return
    # カテゴリ dropdown 選択値を取得
    cat_choice = None
    if cmds.optionMenu(_UI_ADD_CATEGORY_MENU, ex=True):
        raw = cmds.optionMenu(_UI_ADD_CATEGORY_MENU, q=True, value=True)
        if raw and not raw.startswith("auto"):
            cat_choice = raw.split()[0]

    # v0.5.14: aim axis を dropdown から反映
    if cmds.optionMenu(_UI_AIM_AXIS_MENU, ex=True):
        try:
            set_current_aim_axis(
                cmds.optionMenu(_UI_AIM_AXIS_MENU, q=True, v=True))
        except Exception:
            pass

    n_ok = 0
    n_fail = 0
    print(f"[jiggle_bones] {len(chains)} chain(s) をセットアップします...")
    for chain in chains:
        try:
            add_registered_chain(chain, category=cat_choice)
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] registry 追加失敗 {chain[0]}: {exc}")
        try:
            create_jiggle_for_chain(chain, category=cat_choice)
            n_ok += 1
            print(f"  ✓ {chain[0]} → {len(chain)} joints")
        except Exception as exc:
            cmds.warning(f"[jiggle_bones] setup 失敗 {chain[0]}: {exc}")
            n_fail += 1
    print(f"[jiggle_bones] 完了: {n_ok} 成功 / {n_fail} 失敗 / 計 {len(chains)}")

    # 最後に UI 再構築 (エラー時も window は残す)
    try:
        show_ui()
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] UI 再構築失敗: {exc}")


def _select_bones_from_list(bone_list_name, chain, *_):
    """textScrollList で選ばれた bone (無ければ chain 全体) を scene 側で選択。"""
    items = cmds.textScrollList(bone_list_name, q=True, si=True) or []
    if items:
        # 表示は "  ✓  hair1" → 末尾の joint 名だけ抽出
        joints = [i.strip().split()[-1] for i in items]
    else:
        joints = list(chain)
    joints = [j for j in joints if cmds.objExists(j)]
    if joints:
        cmds.select(joints, r=True)


def _ui_populate_registry_from_names(*_):
    """命名 heuristic で自動検出した chain を registry に追加 (補助機能)。"""
    detected = find_jiggle_chains()
    n_added = 0
    for cat, chain_list in detected.items():
        for chain in chain_list:
            add_registered_chain(chain, category=cat)
            n_added += 1
    print(f"[jiggle_bones] auto-detect: {n_added} chain(s) registered")
    show_ui()


def _show_error_ui(msg):
    """UI 構築失敗時のフォールバック — 最小 window に Reload ボタンだけ出す。
    v0.5.10: show_ui() 途中で例外が出ると window が消えたまま残る症状の対策。"""
    if cmds.window(WINDOW, exists=True):
        try:
            cmds.deleteUI(WINDOW)
        except Exception:
            pass
    win = cmds.window(WINDOW, t=f"Jiggle Bones v{__version__} [ERROR]",
                      w=500, h=200)
    cmds.columnLayout(adj=True, rs=6, cat=("both", 10))
    cmds.text(l="UI 構築失敗", fn="boldLabelFont", al="left")
    cmds.text(l=str(msg), ww=True, w=480, al="left")
    cmds.separator(h=8, style="in")
    cmds.button(l="🔄 Reload UI", h=32,
                c=lambda *_: show_ui(), bgc=(0.35, 0.55, 0.35))
    cmds.text(l="上を押しても駄目なら Script Editor に traceback が出ています。",
              fn="smallObliqueLabelFont", al="left", ww=True, w=480)
    cmds.showWindow(win)


def show_ui():
    """v0.5.10: try/except で包み、エラー時は _show_error_ui() でフォールバック。
    途中で失敗しても window が完全に消えたままになる事は無い。"""
    if cmds is None:
        raise RuntimeError("show_ui() must be called inside Maya.")
    try:
        _show_ui_impl()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        cmds.warning(f"[jiggle_bones] UI 構築失敗: {exc}")
        try:
            _show_error_ui(exc)
        except Exception:
            pass


def _show_ui_impl():
    """揺れもの dynamics UI (v0.2.1 header/body/footer レイアウト)。

    formLayout で:
      - header (title + 使い方) を上端固定
      - footer (Setup All / Remove All / Refresh) を下端固定
      - body (collider + カテゴリ) を残り領域に scrollLayout で配置
    こうすると window resize しても footer の action ボタンが常に見える。
    """
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
    cmds.text(l="使い方:  ①コライダー mesh を追加  "
                "②scene で joint を選択 → 「選択から chain 追加」 "
                "(v0.5.10: 追加と同時に自動セットアップ、FK cube ctl 生成) "
                "③カテゴリごとにパラメータ slider で調整 → 「反映」ボタン "
                "④タイムライン再生で確認",
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
    cmds.button(l="すべて削除 (rig + 登録)", h=34, c=_ui_remove_all,
                bgc=(0.55, 0.30, 0.30), p=footer_row)
    cmds.button(l="UI 更新", h=34, c=lambda *_: show_ui(),
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

    # v0.4.1: 既存 setup (v0.4.0 以前) で hairSystem.collide=0 のままの
    # ケース用に collide 強制有効化ボタンを露出
    coll_fix_row = cmds.rowLayout(nc=2, adj=1, p=body_col,
                                    cw2=(280, 200),
                                    ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="全 hairSystem の衝突判定を強制 ON", h=22, p=coll_fix_row,
                bgc=(0.55, 0.55, 0.30),
                c=lambda *_: enable_collision_on_all_hair_systems(),
                ann="v0.4.0 以前の setup で貫通する時に押す")
    cmds.button(l="🩺 collision 診断 (script editor に出力)",
                h=22, p=coll_fix_row, bgc=(0.35, 0.55, 0.75),
                c=lambda *_: diagnose_collision(),
                ann="nucleus / hairSystem / nRigid / follicle の現在値を"
                     "console に dump。貫通の原因調査用。")

    # v0.4.3: nucleus + collider tuning UI
    cmds.separator(h=6, style="in", p=body_col)
    cmds.text(l="⚙ nucleus (全 chain 共通の物理 solver 設定)",
              al="left", fn="boldLabelFont", p=body_col)
    nuc = get_nucleus_params()
    _UI_NUCLEUS_FIELDS.clear()
    _UI_NUCLEUS_FIELDS["spaceScale"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Space Scale :", cw2=(120, 80),
        value1=nuc.get("spaceScale", 1.0), p=body_col,
        ann="キャラの単位系。1 unit = 1m 想定なので MMD (1 unit ≒ 0.08m) は "
             "0.05〜0.1 に。default 1.0")
    _UI_NUCLEUS_FIELDS["subSteps"] = cmds.intFieldGrp(
        numberOfFields=1, label="Sub Steps :", cw2=(120, 80),
        value1=int(nuc.get("subSteps", 6)), p=body_col,
        ann="frame 間の計算刻み。高速動作で貫通するなら 10〜20 に上げる。"
             "default 6 (v0.4.2 で強化)")
    _UI_NUCLEUS_FIELDS["maxCollisionIterations"] = cmds.intFieldGrp(
        numberOfFields=1, label="Max Collision Iter :", cw2=(120, 80),
        value1=int(nuc.get("maxCollisionIterations", 8)), p=body_col,
        ann="衝突ペアの反復回数。default 8。多い chain + collider 時は上げる")
    _UI_NUCLEUS_FIELDS["gravity"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Gravity :", cw2=(120, 80),
        value1=nuc.get("gravity", 9.8), p=body_col,
        ann="重力の大きさ。default 9.8 (m/s^2 想定)。0 で無重力")
    cmds.button(l="nucleus パラメータを反映", h=22, p=body_col,
                c=_ui_apply_nucleus_params, bgc=(0.30, 0.45, 0.55))

    # collider tuning (選択中 collider に対して)
    cmds.separator(h=4, style="none", p=body_col)
    cmds.text(l="⚙ 選択中 collider の tuning (上のリストで選択)",
              al="left", fn="smallBoldLabelFont", p=body_col)
    _UI_COLLIDER_FIELDS.clear()
    _UI_COLLIDER_FIELDS["thickness"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Thickness :", cw2=(120, 80),
        value1=0.5, p=body_col,
        ann="collider の厚み。薄すぎると貫通、厚すぎると mesh 表面から浮く")
    _UI_COLLIDER_FIELDS["pushOut"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Push Out :", cw2=(120, 80),
        value1=0.25, p=body_col,
        ann="衝突時の分離力。thickness の半分くらいが目安")
    _UI_COLLIDER_FIELDS["friction"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Friction :", cw2=(120, 80),
        value1=0.2, p=body_col,
        ann="摩擦係数 0..1。高いと hair が張り付く")
    _UI_COLLIDER_FIELDS["bounce"] = cmds.floatFieldGrp(
        numberOfFields=1, label="Bounce :", cw2=(120, 80),
        value1=0.0, p=body_col,
        ann="反発係数 0..1。0 で沈み込む、1 で跳ね返る")
    cmds.button(l="選択 collider にパラメータを反映", h=22, p=body_col,
                c=_ui_apply_collider_params, bgc=(0.30, 0.45, 0.55))

    cmds.separator(h=8, style="in", p=body_col)
    cmds.text(l="② chain 登録 (選択ピック方式)", al="left",
              fn="boldLabelFont", p=body_col)
    cmds.text(l="Outliner か viewport で joint を選んで「選択から chain 追加」。"
                "1 個選択なら子孫を DFS で辿って自動 chain 化、"
                "複数選択ならその順序を chain として使う。",
              al="left", fn="smallObliqueLabelFont", ww=True, p=body_col,
              w=490)

    add_row = cmds.rowLayout(nc=3, adj=2, p=body_col,
                              cw3=(70, 220, 180),
                              ct3=("left", "left", "both"),
                              co3=(4, 4, 2))
    cmds.text(l="カテゴリ:", al="right", p=add_row)
    cmds.optionMenu(_UI_ADD_CATEGORY_MENU, p=add_row)
    cmds.menuItem(l="auto (命名から自動判定)")
    for cat_key in _JIGGLE_TOKENS.keys():
        jp = _CATEGORY_JP.get(cat_key, cat_key)
        cmds.menuItem(l=f"{cat_key} ({jp})")
    cmds.button(l="⚡ 選択 root(s) から一括セットアップ", h=26, p=add_row,
                c=_ui_add_from_selection, bgc=(0.30, 0.60, 0.30),
                ann="v0.5.11: 複数 root を選択すると 各々を chain の 起点として "
                     "独立に setup。 例: 髪の 3 本 root を Shift+選択 → 3 chain "
                     "作成。親が同時選択されてる場合は skip (chain 途中扱い)。"
                     "1 joint 選択なら DFS で 単一 chain を作る従来動作。")

    # v0.5.14: 子方向軸 (SplineIK dForwardAxis) 選択
    axis_row = cmds.rowLayout(nc=3, adj=3, p=body_col,
                               cw3=(150, 100, 240),
                               ct3=("left", "left", "left"),
                               co3=(4, 4, 4))
    cmds.text(l="子を向いてる軸 (aim):", al="right", p=axis_row)
    cmds.optionMenu(_UI_AIM_AXIS_MENU, p=axis_row,
                     cc=_ui_on_aim_axis_change)
    for ax in _AIM_AXIS_CHOICES:
        cmds.menuItem(l=ax)
    # default に合わせる
    cur = _current_setup_aim_axis or _DEFAULT_AIM_AXIS
    try:
        cmds.optionMenu(_UI_AIM_AXIS_MENU, e=True, v=cur)
    except Exception:
        pass
    cmds.text(l="MMD 系は Y+、Maya default は X+  (裏返る時に変更)",
              al="left", fn="smallObliqueLabelFont", p=axis_row)

    # v0.5.15: joint orient tool (setup 前に model を整える)
    cmds.separator(h=4, style="none", p=body_col)
    orient_row = cmds.rowLayout(nc=3, adj=3, p=body_col,
                                 cw3=(150, 120, 220),
                                 ct3=("left", "left", "left"),
                                 co3=(4, 4, 4))
    cmds.text(l="orient aim:", al="right", p=orient_row)
    cmds.optionMenu(_UI_ORIENT_AIM_MENU, p=orient_row)
    for label in ("X+ (Y up)", "Y+ (Z up)", "Z+ (X up)"):
        cmds.menuItem(l=label)
    try:
        cmds.optionMenu(_UI_ORIENT_AIM_MENU, e=True, v="Y+ (Z up)")
    except Exception:
        pass
    cmds.button(l="🔧 選択 root(s) に joint orient (weight 保持)",
                h=24, p=orient_row, c=_ui_run_orient,
                bgc=(0.55, 0.40, 0.60),
                ann="v0.5.15: root joint(s) 選択後、下位 chain を joint orient し直す。"
                     " skinCluster.bindPreMatrix を新 WM.inverse で更新するので "
                     "見た目 (weight 反映) は完全に維持される。setup 前に この tool で"
                     " model の 軸を整えるのが推奨フロー。")

    # v0.5.29: Ctl shape 差し替え
    cmds.separator(h=4, style="none", p=body_col)
    replace_row = cmds.rowLayout(nc=1, adj=1, p=body_col, cw=(1, 500))
    cmds.button(l="🎨 選択 curve → 残り ctl(s) の shape を差し替え",
                h=24, p=replace_row, c=_ui_replace_ctl_curves,
                bgc=(0.45, 0.55, 0.35),
                ann="v0.5.29: 選択の 1 番目 = source curve、2 番目以降 = "
                     "target ctl(s)。 target の 既存 shape を削除して source curve の "
                     "shape でコピー差し替え。 color / rig 構造 は 保持。 "
                     "使い方: 好みの curve shape を先に選択 → Shift+ で "
                     "差し替えたい *_jbCtl を追加選択 → ボタンクリック")

    # v0.5.30: 揺れ物 Master ctl (per-category ON/OFF)
    cmds.separator(h=4, style="none", p=body_col)
    master_row = cmds.rowLayout(nc=1, adj=1, p=body_col, cw=(1, 500))
    cmds.button(l="👑 揺れ物 Master ctl 作成/更新 (per-category ON/OFF)",
                h=24, p=master_row, c=_ui_setup_master,
                bgc=(0.55, 0.35, 0.55),
                ann="v0.5.30: 足元に 揺れ物 master ctl を作成。 hair/skirt/tail 等 "
                     "各 category 毎に bool attr (ON/OFF)。 allSim で全カテゴリ 一括 mute。"
                     " 何か 1 個 joint / ctl を選択して押すと その world 位置に作成、"
                     "無選択なら 世界原点。 attach_ctrls の main_ctl があれば その下に "
                     "parent。 全 hairSystem.active を master と wire 済。")

    # 補助: 命名 heuristic で一括登録
    aux_row = cmds.rowLayout(nc=1, adj=1, p=body_col, cw=(1, 490))
    cmds.button(l="(補助) 命名 heuristic で自動検出して一括登録",
                h=22, c=_ui_populate_registry_from_names,
                bgc=(0.35, 0.45, 0.55), p=aux_row,
                ann="hair/skirt/tail/coat/ear 等の名前を持つ joint chain を "
                     "自動検出して registry に追加 (旧 v0.3.x の挙動)")

    cmds.separator(h=6, style="in", p=body_col)
    cmds.text(l="③ 登録済み chain (カテゴリ別グループ + パラメータ + bone リスト)",
              al="left", fn="boldLabelFont", p=body_col)

    registered = get_registered_chains()

    if not registered:
        cmds.text(l="(まだ chain が登録されていません。上の「選択から chain "
                     "追加」で chain を登録してください)",
                  al="left", fn="smallObliqueLabelFont", ww=True,
                  p=body_col, w=490)
    else:
        for category, chain_list in sorted(registered.items()):
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
    # v0.5.3: floatSliderGrp に切替 (0.01 単位のスライダー + 精度 4 桁の
    #        数値入力 field)。微調整が直感的にできる。
    src = live_values if live_values is not None else defaults
    _param_range = {
        "stiffness":         (0.0, 1.0, 0.0, 1.0),   # (fmin, fmax, smin, smax)
        "damp":              (0.0, 1.0, 0.0, 0.5),
        "startCurveAttract": (0.0, 1.0, 0.0, 1.0),
        "mass":              (0.01, 20.0, 0.1, 5.0),
        # v0.5.4: 過減衰対策 4 param
        "drag":              (0.0, 1.0, 0.0, 0.3),   # 空気抵抗
        "motionDrag":        (0.0, 1.0, 0.0, 0.3),   # 動作抵抗
        "attractionDamp":    (0.0, 1.0, 0.0, 1.0),   # 復元力の減衰
        "bendResistance":    (0.0, 5.0, 0.0, 1.0),   # 曲げ抵抗
        # v0.5.9: 当たり判定の張り出し幅
        # 0 なら mesh 表面すれすれ、0.5 で MMD scale の 4cm ぶん外側
        "collideWidthOffset": (0.0, 5.0, 0.0, 0.5),
    }
    for attr in _PARAM_ATTRS:
        v = src.get(attr, defaults.get(attr, 0.0))
        label_jp = _PARAM_LABEL_JP.get(attr, attr) + " :"
        fmin, fmax, smin, smax = _param_range.get(attr, (0.0, 10.0, 0.0, 1.0))
        fld = cmds.floatSliderGrp(
            label=label_jp, field=True, value=v,
            precision=4, cw3=(140, 70, 100),
            fieldMinValue=fmin, fieldMaxValue=fmax,
            minValue=smin, maxValue=smax,
            step=0.001, sliderStep=0.01,
            p=inner)
        _UI_CATEGORY_FIELDS[(category, attr)] = fld

    cmds.button(l=f"{cat_jp} のパラメータを反映",
                h=22, c=lambda _x=None, _c=category: _ui_apply_category_params(_c),
                bgc=(0.30, 0.45, 0.55), p=inner,
                ann=f"上記の値を既存の hairSystem ({category}) に上書き適用")
    cmds.separator(h=6, style="none", p=inner)

    # chain rows: 各 chain に対して 見出し行 + bone リスト行 の 2 段構造
    for chain in chain_list:
        cid = _chain_id(chain)
        active = is_chain_active(chain)

        # 見出し行 (checkbox + 名前 + セットアップ/削除)
        head_row = cmds.rowLayout(nc=4, adj=2, p=inner,
                                    cw4=(28, 260, 90, 70),
                                    ct4=("both", "left", "both", "both"),
                                    co4=(2, 4, 2, 2))
        cb = cmds.checkBox(l="", v=not active, p=head_row,
                            ann="チェックを入れると「一括セットアップ」の対象")
        _UI_CHAIN_CHECKS[(category, cid)] = cb
        status = "  [適用中]" if active else ""
        cmds.text(l=f"{cid}  ({len(chain)} 個){status}",
                   al="left", fn="smallPlainLabelFont", p=head_row)
        cmds.button(l="セットアップ", h=22, p=head_row,
                    c=lambda _x=None, _c=category, _ch=chain:
                        _ui_setup_chain(_c, _ch),
                    bgc=(0.30, 0.55, 0.30),
                    ann="この chain に FK ctl + hairSystem dynamics を構築")
        cmds.button(l="削除", h=22, p=head_row,
                    c=lambda _x=None, _ch=chain: _ui_remove_chain(_ch),
                    bgc=(0.55, 0.30, 0.30),
                    ann="この chain の jiggle 関連 node を全削除 + registry から除去")

        # bone リスト行 (chain 内の joint 名を全表示、scene で選択できるように)
        bone_list_name = f"jbBones_{category}_{cid}"
        # 既存があれば削除 (rebuild 対策)
        if cmds.textScrollList(bone_list_name, ex=True):
            cmds.deleteUI(bone_list_name)
        list_row = cmds.rowLayout(nc=2, adj=1, p=inner,
                                    cw2=(320, 130),
                                    ct2=("left", "left"), co2=(30, 4))
        cmds.textScrollList(bone_list_name, numberOfRows=min(len(chain), 6),
                             h=min(len(chain), 6) * 18 + 4,
                             allowMultiSelection=True,
                             p=list_row)
        for j in chain:
            marker = "✓" if cmds.objExists(j) else "✗"
            cmds.textScrollList(bone_list_name, e=True,
                                 append=f"  {marker}  {j}")
        cmds.button(l="scene 側を選択", h=22, p=list_row,
                    c=lambda _x=None, _bn=bone_list_name, _ch=chain:
                        _select_bones_from_list(_bn, _ch),
                    bgc=(0.35, 0.45, 0.55),
                    ann="上のリストで選んだ bone を scene 側で選択する "
                         "(なにも選ばなければ chain 全体)")

        cmds.separator(h=4, style="in", p=inner)
