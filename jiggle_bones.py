"""Jiggle Bones v0.1.0 — 揺れもの (hair/skirt/ribbon/tail 等) 専用ツール

## 位置づけ
attach_ctrls は arm/leg/spine 等の主 rig を担当し、揺れもの骨は
skip_decoration=True (default v0.9.33+) で除外される。本モジュールは
その除外されている chain だけを対象に、dynamics / simulation を組む。

## ステータス
**v0.1.0 = scaffold (chain 検出 + UI 骨組みのみ)**。simulation 実装は
ユーザーとの仕様確認後。以下の質問を経て本実装:

  1. シミュレーション方式の優先順位
      (a) Constraint ベース (aim/orient + spring pseudo-dynamics)
      (b) hairSystem (nHair follicle + curve + spline IK)
      (c) nCloth (骨に proxy mesh を張って cloth simulation → matrix 逆算)
      (d) Custom expression (spring-damper 自作、bake 前提)
  2. 出力形式: 常駐 dynamics / bake 済 keyframe / どちらか選択可
  3. ヘア/スカート/リボン等でパラメータ変えたい (stiffness/damping/gravity)
  4. UI 側: 検出結果を chain list で並べて toggle + per-chain 数値入力
  5. 干渉: skirt vs leg 等の collision 有無、collider 自動生成

## 現状 API (chain 検出のみ)
    import jiggle_bones
    chains = jiggle_bones.find_jiggle_chains()
    # → {"hair": [["H1","H2","H3","H4","H5"], ...],
    #    "skirt": [["skirt_L_1","skirt_L_2",...], ...],
    #    "ribbon": [...],
    #    "tail": [...]}
    jiggle_bones.show_ui()   # scaffold UI (現状は list 表示のみ)
"""
import maya.cmds as cmds

__version__ = "0.1.0"
WINDOW = "jiggleBonesWin"

# 揺れもの分類 (attach_ctrls._DECORATION_TOKENS とほぼ同じだが独立管理)
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


def _short(name):
    return name.split("|")[-1].split(":")[-1]


def _classify(joint):
    """joint 名から分類 tag を返す。該当なしは None。"""
    lo = _short(joint).lower()
    # 末尾 side/suffix を剥がして token マッチ
    core = lo
    for suf in ("_l", "_r", "_c", "_end"):
        if core.endswith(suf):
            core = core[:-len(suf)]
    # H1..H99 hair pattern (完全一致は別処理)
    s = _short(joint)
    if len(s) >= 2 and s[0] in ("H", "h") and s[1:].split("_")[0].isdigit():
        return "hair"
    for tag, tokens in _JIGGLE_TOKENS.items():
        for t in tokens:
            if t in core:
                return tag
    return None


def _walk_chain(root, tag):
    """root から同分類 (同 tag) の子孫だけを辿った chain を返す (親→末端順)。
    枝分かれがある場合は最も長い branch を優先。"""
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
    """scene 内の全 joint から揺れもの chain を抽出。

    戻り値: { tag: [chain, chain, ...] } の dict。
      chain は joint 名 list (親→末端の順、長さ ≥ 2)。
      root は同 tag の親 joint を持たない joint (chain の "起点")。
    """
    if cmds is None:
        raise RuntimeError("Must run inside Maya.")
    all_joints = cmds.ls(type="joint") or []
    tagged = {}
    for j in all_joints:
        t = _classify(j)
        if t:
            tagged[j] = t

    result = {tag: [] for tag in _JIGGLE_TOKENS.keys()}

    # chain root = 同 tag の親を持たない tagged joint
    for j, tag in tagged.items():
        parent = cmds.listRelatives(j, p=True, type="joint") or []
        parent_tag = _classify(parent[0]) if parent else None
        if parent_tag == tag:
            continue    # 中間 joint、root ではない
        chain = _walk_chain(j, tag)
        if len(chain) >= 2:
            result[tag].append(chain)

    # 空 tag は除外して返す
    return {tag: chains for tag, chains in result.items() if chains}


# =========================================================================
# UI scaffold (chain 検出結果の表示のみ、simulation は本実装後)
# =========================================================================

_UI_LIST = "jbChainList"
_UI_METHOD = "jbMethod"


def _ui_refresh(*_):
    if not cmds.textScrollList(_UI_LIST, ex=True):
        return
    cmds.textScrollList(_UI_LIST, e=True, ra=True)
    try:
        chains = find_jiggle_chains()
    except Exception as exc:
        cmds.warning(f"[jiggle_bones] detect failed: {exc}")
        return
    total = 0
    for tag, chain_list in sorted(chains.items()):
        for chain in chain_list:
            label = f"[{tag}]  {chain[0]}  →  ({len(chain)} joints)  →  {chain[-1]}"
            cmds.textScrollList(_UI_LIST, e=True, append=label)
            total += 1
    print(f"[jiggle_bones] detected {total} chains: "
          f"{ {t: len(c) for t, c in chains.items()} }")


def _ui_apply(*_):
    method = cmds.optionMenu(_UI_METHOD, q=True, value=True)
    cmds.warning(f"[jiggle_bones] '{method}' simulation は v0.1.0 では未実装。"
                  "仕様確認後に本実装します。")


def show_ui():
    """揺れもの UI (scaffold: chain list + method 選択、simulation 適用は未実装)。"""
    if cmds is None:
        raise RuntimeError("show_ui() must be called inside Maya.")
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    win = cmds.window(WINDOW, t=f"Jiggle Bones  v{__version__}  (scaffold)",
                      w=460, h=520, mnb=True, mxb=False, s=True)
    cmds.columnLayout(adj=True, rs=6, cat=("both", 10))

    cmds.text(l="揺れもの (hair/skirt/ribbon/tail 等) 専用 dynamics ツール",
              al="left", fn="boldLabelFont")
    cmds.text(l="v0.1.0: chain 検出と UI 骨組みのみ。simulation は仕様確認後に本実装。",
              al="left", fn="smallObliqueLabelFont", ww=True)

    cmds.separator(h=6, style="in")
    cmds.text(l="=== 検出された揺れもの chain ===",
              al="left", fn="boldLabelFont")
    cmds.textScrollList(_UI_LIST, numberOfRows=14, allowMultiSelection=True,
                         h=280)
    cmds.button(l="Refresh detection", h=24, c=_ui_refresh,
                bgc=(0.35, 0.55, 0.75))

    cmds.separator(h=6, style="in")
    cmds.text(l="=== Simulation method (要件確認中) ===",
              al="left", fn="boldLabelFont")
    cmds.optionMenu(_UI_METHOD, label="Method:")
    cmds.menuItem(l="(a) Constraint spring pseudo-dynamics")
    cmds.menuItem(l="(b) hairSystem (nHair + spline IK)")
    cmds.menuItem(l="(c) nCloth (proxy mesh)")
    cmds.menuItem(l="(d) Custom expression spring-damper")

    cmds.separator(h=6, style="in")
    cmds.button(l="Apply (未実装)", h=32, c=_ui_apply,
                bgc=(0.60, 0.55, 0.30))

    cmds.showWindow(win)
    _ui_refresh()
    return win
