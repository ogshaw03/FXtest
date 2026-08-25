"""facial_ctrls.py — 顔 表情 controller + blendShape 紐付け UI

Maya 2023、Script Editor 貼付 or install.py 経由 sys.path 追加。

## 機能
1. 顔マーク ctl + 目 ctl + 口 ctl を head 付近に作成 (作成 API + UI ボタン)
2. UI で 各 ctl と blendShape attr を紐付け:
   - 左リスト: 登録済 facial ctl 一覧
   - 右リスト: 選択 ctl に 紐付いている blendShape attr 一覧
   - 選択 ctl + Channel Box で blendShape attr 選択 → "+ 紐付け" ボタン
3. 紐付け方式: Maya proxy attribute (ctl.<attr> ↔ blendShape.<attr>)
   → animator は ctl 選択 → Channel Box で アニメ できる

## 使い方
```python
import facial_ctrls as fc, importlib
importlib.reload(fc)
fc.show_ui()   # UI 起動
```

または 直接 API:
```python
fc.create_facial_ctls(head_position=(0, 15, 0), size=1.0)
fc.link_bs_attr("eye_ctl", "faceBS", "eyeSmile")
```
"""
__version__ = "0.1.4"
__package__ = "facial_ctrls"

try:
    import maya.cmds as cmds
except ImportError:
    cmds = None


# =========================================================================
# 定数 / 命名
# =========================================================================

FACE_CTL_NAME  = "face_ctl"
FACE_NPO_NAME  = "face_ctl_npo"
EYE_CTL_NAME   = "eye_ctl"
EYE_NPO_NAME   = "eye_ctl_npo"
MOUTH_CTL_NAME = "mouth_ctl"
MOUTH_NPO_NAME = "mouth_ctl_npo"

WINDOW = "facialCtrlsWin"
_UI_LEFT_LIST  = "facial_ui_leftList"
_UI_RIGHT_LIST = "facial_ui_rightList"


# =========================================================================
# Curve shape 生成
# =========================================================================

def _make_face_outline(name, size=1.0):
    """v0.1.2: 顔輪郭 のみ (外側 円 1 個)。"""
    import math
    r = size
    seg = 32
    pts = [(r * math.cos(2 * math.pi * i / seg),
            r * math.sin(2 * math.pi * i / seg), 0)
           for i in range(seg + 1)]
    return cmds.curve(d=1, p=pts, n=name)


def _make_face_eyes(name, size=1.0):
    """v0.1.2: 目 (顔マーク の 2 つの 目) を 1 transform 下 に。
    face_ctl と組み合わせた時 顔の 目位置 に配置される寸法。"""
    import math
    r = size
    er = r * 0.15   # 目 半径
    # 左目
    eye_l = [(er * math.cos(2 * math.pi * i / 16) - r * 0.35,
              er * math.sin(2 * math.pi * i / 16) + r * 0.25, 0)
             for i in range(17)]
    tr = cmds.curve(d=1, p=eye_l, n=name)
    # 右目 shape を tr に merge
    eye_r = [(er * math.cos(2 * math.pi * i / 16) + r * 0.35,
              er * math.sin(2 * math.pi * i / 16) + r * 0.25, 0)
             for i in range(17)]
    r_curve = cmds.curve(d=1, p=eye_r)
    for s in cmds.listRelatives(r_curve, s=True, type="nurbsCurve") or []:
        cmds.parent(s, tr, s=True, r=True)
    cmds.delete(r_curve)
    return tr


def _make_face_mouth(name, size=1.0):
    """v0.1.2: 口 (顔マーク の smile 弧)。"""
    import math
    r = size
    pts = []
    for i in range(9):
        a = math.pi + (math.pi / 8) * i   # 180° - 360° (下半円)
        pts.append((r * 0.35 * math.cos(a),
                     r * 0.35 * math.sin(a) - r * 0.1, 0))
    return cmds.curve(d=1, p=pts, n=name)


def _set_ctl_color(ctl, color_idx):
    for s in cmds.listRelatives(ctl, s=True) or []:
        try:
            cmds.setAttr(s + ".overrideEnabled", 1)
            cmds.setAttr(s + ".overrideColor", color_idx)
        except Exception:
            pass


# =========================================================================
# 作成 API
# =========================================================================

def create_facial_ctls(head_position=None, size=None):
    """顔マーク + 目 + 口 ctl を作成、階層化。
    face_ctl_npo (world 置き) > face_ctl
                              > eye_ctl_npo (face の 目位置) > eye_ctl
                              > mouth_ctl_npo (face の 口位置) > mouth_ctl

    Args:
        head_position: world pos (x,y,z)。 None なら 選択 joint or (0, 15, 0)
        size: ctl の サイズ。 None なら auto (mesh bbox 参照)

    Returns: (face_ctl, eye_ctl, mouth_ctl) の name tuple
    """
    if cmds is None:
        raise RuntimeError("run inside Maya")

    # head_position 決定
    if head_position is None:
        sel = cmds.ls(sl=True) or []
        if sel:
            head_position = cmds.xform(sel[0], q=True, ws=True, t=True)
        else:
            head_position = (0, 15, 0)
    # size 決定
    if size is None:
        try:
            bbox = cmds.exactWorldBoundingBox(cmds.ls(type="mesh")) or None
            if bbox:
                dy = bbox[4] - bbox[1]
                size = max(0.5, dy * 0.05)
            else:
                size = 1.0
        except Exception:
            size = 1.0

    # 既存 削除
    for old in (FACE_NPO_NAME, EYE_NPO_NAME, MOUTH_NPO_NAME):
        if cmds.objExists(old):
            try: cmds.delete(old)
            except: pass

    # 顔マーク
    # v0.1.2: 顔マークを 3 パーツ (輪郭 / 目 / 口) の 別 transform に分割。
    # 全て 同じ 黄色 で、見た目 は 1 つの 顔だが 選択は 別々。
    # face_ctl (輪郭) = 顔全体 移動、 eye_ctl = 目 表情 attr、
    # mouth_ctl = 口 表情 attr 用。
    face_ctl = _make_face_outline(FACE_CTL_NAME, size=size)
    _set_ctl_color(face_ctl, 17)   # 黄
    face_npo = cmds.group(em=True, n=FACE_NPO_NAME)
    cmds.parent(face_ctl, face_npo)
    cmds.xform(face_npo, ws=True, t=head_position)

    # 目 (顔輪郭 の 中 に 目位置で 2 つの 小円)
    eye_ctl = _make_face_eyes(EYE_CTL_NAME, size=size)
    _set_ctl_color(eye_ctl, 17)   # 黄 (face と 同色)
    eye_npo = cmds.group(em=True, n=EYE_NPO_NAME)
    cmds.parent(eye_ctl, eye_npo)
    cmds.parent(eye_npo, face_ctl)
    cmds.setAttr(f"{eye_npo}.translate", 0, 0, 0, type="double3")
    cmds.setAttr(f"{eye_npo}.rotate", 0, 0, 0, type="double3")

    # 口 (顔輪郭 の 中 に smile 弧)
    mouth_ctl = _make_face_mouth(MOUTH_CTL_NAME, size=size)
    _set_ctl_color(mouth_ctl, 17)   # 黄
    mouth_npo = cmds.group(em=True, n=MOUTH_NPO_NAME)
    cmds.parent(mouth_ctl, mouth_npo)
    cmds.parent(mouth_npo, face_ctl)
    cmds.setAttr(f"{mouth_npo}.translate", 0, 0, 0, type="double3")
    cmds.setAttr(f"{mouth_npo}.rotate", 0, 0, 0, type="double3")

    # face_npo を ctrl group / world_ctl 下に (attach_ctrls 有れば)
    for cand in ("ctrl", "main_ctl", "world_ctl"):
        if cmds.objExists(cand):
            try: cmds.parent(face_npo, cand)
            except: pass
            break

    print(f"[{__package__}] created {face_ctl}, {eye_ctl}, {mouth_ctl} "
          f"at {head_position} size={size}")
    return face_ctl, eye_ctl, mouth_ctl


# =========================================================================
# BlendShape 紐付け
# =========================================================================

def link_bs_attr(ctl, bs_node, attr_name):
    """ctl に proxy attr を追加、blendShape.<attr> と接続。

    Args:
        ctl:       追加先 controller transform
        bs_node:   blendShape node 名
        attr_name: blendShape の attr 名 (weight[N] の alias 名 e.g. "smile")

    Returns: True 成功 / False 失敗
    """
    if not cmds.objExists(ctl):
        cmds.warning(f"[{__package__}] ctl 存在しない: {ctl}")
        return False
    if not cmds.objExists(bs_node):
        cmds.warning(f"[{__package__}] blendShape 存在しない: {bs_node}")
        return False
    src_plug = f"{bs_node}.{attr_name}"
    try:
        _ = cmds.getAttr(src_plug)
    except Exception:
        cmds.warning(f"[{__package__}] attr {src_plug} 無効")
        return False
    # 既存 attr が有れば skip (二重防止)
    if cmds.attributeQuery(attr_name, node=ctl, exists=True):
        print(f"[{__package__}] {ctl}.{attr_name} 既存、skip")
        return True
    # proxy attribute で 追加 (Maya 2019+)
    try:
        cmds.addAttr(ctl, ln=attr_name, at="float", proxy=src_plug, k=True)
        print(f"[{__package__}] linked {ctl}.{attr_name} ↔ {src_plug}")
        return True
    except Exception:
        # fallback: 通常 addAttr + connectAttr
        try:
            cmds.addAttr(ctl, ln=attr_name, at="float",
                          min=0.0, max=1.0, k=True)
            cmds.connectAttr(f"{ctl}.{attr_name}", src_plug, f=True)
            print(f"[{__package__}] linked (direct) {ctl}.{attr_name} → {src_plug}")
            return True
        except Exception as exc:
            cmds.warning(f"[{__package__}] link failed: {exc}")
            return False


def unlink_bs_attr(ctl, attr_name):
    """ctl の attr を削除 (proxy or direct)。"""
    if not cmds.objExists(ctl):
        return False
    if not cmds.attributeQuery(attr_name, node=ctl, exists=True):
        return False
    try:
        cmds.deleteAttr(f"{ctl}.{attr_name}")
        print(f"[{__package__}] unlinked {ctl}.{attr_name}")
        return True
    except Exception as exc:
        cmds.warning(f"[{__package__}] unlink failed: {exc}")
        return False


def get_linked_bs_attrs(ctl):
    """ctl に有る blendShape 由来の attr 一覧 (proxy or connected)。
    Returns: [(attr_name, source_plug), ...]"""
    if not cmds.objExists(ctl):
        return []
    out = []
    # ユーザー attr 全部
    user_attrs = cmds.listAttr(ctl, ud=True) or []
    for a in user_attrs:
        plug = f"{ctl}.{a}"
        src = None
        # proxy check (attributeQuery で は 取れないので listConnections で)
        try:
            conns = cmds.listConnections(plug, s=True, d=False,
                                            plugs=True) or []
            for c in conns:
                if cmds.nodeType(c.split(".")[0]) == "blendShape":
                    src = c
                    break
            # 逆方向 (proxy でも これで拾える事あり)
            if not src:
                outs = cmds.listConnections(plug, s=False, d=True,
                                             plugs=True) or []
                for c in outs:
                    if cmds.nodeType(c.split(".")[0]) == "blendShape":
                        src = c
                        break
        except Exception:
            pass
        if src:
            out.append((a, src))
    return out


# =========================================================================
# UI
# =========================================================================

# scene に facial ctl として登録した list を jb_face_grp のカスタム attr に保存
_REGISTRY_ATTR = "facialCtls"   # string, "|" 区切り


def _load_registered_ctls():
    """scene から 登録済 ctl list を読む。 デフォルトで face/eye/mouth
    ctl があれば 自動追加。"""
    reg = []
    if cmds.objExists("facial_registry_grp"):
        if cmds.attributeQuery(_REGISTRY_ATTR, node="facial_registry_grp",
                                 exists=True):
            raw = cmds.getAttr("facial_registry_grp." + _REGISTRY_ATTR) or ""
            reg = [x for x in raw.split("|") if x.strip()]
    # 存在確認 + default 追加
    reg = [c for c in reg if cmds.objExists(c)]
    for default in (FACE_CTL_NAME, EYE_CTL_NAME, MOUTH_CTL_NAME):
        if cmds.objExists(default) and default not in reg:
            reg.append(default)
    return reg


def _save_registered_ctls(ctls):
    """scene に登録 list を save。"""
    if not cmds.objExists("facial_registry_grp"):
        cmds.createNode("transform", n="facial_registry_grp")
    if not cmds.attributeQuery(_REGISTRY_ATTR, node="facial_registry_grp",
                                 exists=True):
        cmds.addAttr("facial_registry_grp", ln=_REGISTRY_ATTR, dt="string")
    cmds.setAttr("facial_registry_grp." + _REGISTRY_ATTR,
                 "|".join(ctls), type="string")


def _ui_refresh_left(*_):
    """左 list を registry から更新。"""
    if not cmds.textScrollList(_UI_LEFT_LIST, ex=True):
        return
    cmds.textScrollList(_UI_LEFT_LIST, e=True, removeAll=True)
    for c in _load_registered_ctls():
        cmds.textScrollList(_UI_LEFT_LIST, e=True, append=c)


def _ui_refresh_right(*_):
    """左 選択の ctl の 紐付き blendShape attr を 右 list に表示。"""
    if not cmds.textScrollList(_UI_RIGHT_LIST, ex=True):
        return
    cmds.textScrollList(_UI_RIGHT_LIST, e=True, removeAll=True)
    sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    if not sel:
        return
    ctl = sel[0]
    for attr_name, src_plug in get_linked_bs_attrs(ctl):
        cmds.textScrollList(_UI_RIGHT_LIST, e=True,
                             append=f"{attr_name}  ← {src_plug}")


def _ui_on_left_select(*_):
    """左 list 選択時: scene も選択 + 右 list 更新。"""
    sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    if sel and cmds.objExists(sel[0]):
        cmds.select(sel[0], r=True)
    _ui_refresh_right()


def _ui_create_all_facial(*_):
    """顔 ctl 3 個を作成、左 list に登録。"""
    face, eye, mouth = create_facial_ctls()
    reg = _load_registered_ctls()
    for c in (face, eye, mouth):
        if c not in reg:
            reg.append(c)
    _save_registered_ctls(reg)
    _ui_refresh_left()


def _ui_add_ctl_from_selection(*_):
    """選択 curve を 左 list に追加。"""
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("scene で curve ctl を 選択してください")
        return
    reg = _load_registered_ctls()
    for c in sel:
        if c not in reg:
            reg.append(c)
    _save_registered_ctls(reg)
    _ui_refresh_left()


def _ui_remove_selected_ctl(*_):
    """左 list 選択の ctl を registry から削除 (scene の ctl は残す)。"""
    sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    if not sel:
        return
    reg = _load_registered_ctls()
    for c in sel:
        if c in reg:
            reg.remove(c)
    _save_registered_ctls(reg)
    _ui_refresh_left()
    _ui_refresh_right()


def _ui_link_selected_bs(*_):
    """v0.1.4: 左 選択 ctl + Channel Box 選択中 blendShape attr を紐付け。
    Channel Box 全 section 走査 + scene selection の history からも
    blendShape を検索 (verbose 出力付き)。"""
    left_sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    if not left_sel:
        cmds.warning("先に左 list から ctl を 選択してください")
        return
    ctl = left_sel[0]
    print(f"[{__package__}] === 紐付け 実行、target ctl = {ctl} ===")
    cb = "mainChannelBox"

    # 全 section から attr 収集
    all_attrs = []
    all_nodes = []
    for attr_flag, obj_flag in (("sma", "mol"), ("ssa", "sol"),
                                  ("sha", "hol"), ("soa", "ool")):
        try:
            attrs = cmds.channelBox(cb, q=True, **{attr_flag: True}) or []
            nodes = cmds.channelBox(cb, q=True, **{obj_flag: True}) or []
        except Exception as e:
            continue
        if attrs:
            all_attrs.extend(attrs)
            print(f"  section {attr_flag}: attrs={attrs} nodes={nodes}")
        if nodes:
            all_nodes.extend(nodes)

    if not all_attrs:
        cmds.warning("Channel Box で attr が選択されていません "
                     "(INPUTS の blendShape 内 attr を クリック)")
        return

    # blendShape node 候補を集める:
    #   (a) Channel Box object list から blendShape type のもの
    #   (b) scene selection の 各 mesh の history から辿る blendShape
    bs_nodes = []
    for n in all_nodes:
        try:
            if cmds.objExists(n) and cmds.nodeType(n) == "blendShape":
                bs_nodes.append(n)
        except Exception:
            pass
    # selection 経由
    for sel in cmds.ls(sl=True) or []:
        try:
            hist = cmds.listHistory(sel, type="blendShape") or []
            for h in hist:
                if h not in bs_nodes:
                    bs_nodes.append(h)
        except Exception:
            pass
    # 全 blendShape scene 内 (最後の手段)
    if not bs_nodes:
        bs_nodes = cmds.ls(type="blendShape") or []
        if bs_nodes:
            print(f"  fallback: scene 全 blendShape {bs_nodes}")

    if not bs_nodes:
        cmds.warning("blendShape node が見つかりません")
        return

    # attr が どの blendShape に属するか判定 (attributeQuery で存在確認)
    linked_count = 0
    for attr in all_attrs:
        for bs in bs_nodes:
            if cmds.attributeQuery(attr, node=bs, exists=True):
                if link_bs_attr(ctl, bs, attr):
                    linked_count += 1
                break
        else:
            cmds.warning(f"  attr {attr}: 対応 blendShape 無し")
    print(f"[{__package__}] linked: {linked_count} / {len(all_attrs)} attr(s)")
    _ui_refresh_right()


def _ui_unlink_selected_bs(*_):
    """右 list 選択の attr を ctl から削除。"""
    left_sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    right_sel = cmds.textScrollList(_UI_RIGHT_LIST, q=True, si=True) or []
    if not left_sel or not right_sel:
        return
    ctl = left_sel[0]
    for entry in right_sel:
        attr_name = entry.split("  ←")[0].strip()
        unlink_bs_attr(ctl, attr_name)
    _ui_refresh_right()


def show_ui():
    """facial ctl + blendShape 紐付け UI 起動。"""
    if cmds is None:
        raise RuntimeError("run inside Maya")
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    win = cmds.window(WINDOW,
                      t=f"Facial Ctrls  v{__version__}",
                      w=680, h=540, mnb=True, mxb=True, s=True)
    cmds.columnLayout(adj=True, rs=4, cat=("both", 6))
    cmds.text(l=f"Facial Ctrls  v{__version__}",
              al="left", fn="boldLabelFont")
    cmds.text(l="① 顔 ctl 作成 → ② 左 list で ctl 選択 → "
                 "③ Channel Box で blendShape attr 選択 → ④「+ 紐付け」",
              al="left", fn="smallObliqueLabelFont", ww=True)

    cmds.separator(h=6, style="in")
    cmds.rowLayout(nc=2, adj=1, cw2=(300, 300),
                   ct2=("both", "both"), co2=(4, 4))
    cmds.button(l="👤 顔 ctl 3 個 作成 (face + eye + mouth)", h=28,
                c=_ui_create_all_facial, bgc=(0.40, 0.55, 0.35))
    cmds.button(l="Refresh", h=28, c=lambda *_: (_ui_refresh_left(),
                                                    _ui_refresh_right()))
    cmds.setParent("..")

    cmds.separator(h=6, style="in")
    # 2 リスト
    cmds.rowLayout(nc=2, adj=1, cw2=(330, 330),
                   ct2=("both", "both"), co2=(4, 4))
    # 左
    cmds.columnLayout(adj=True, rs=3)
    cmds.text(l="① Facial Ctls  (登録済 controllers)",
              al="left", fn="boldLabelFont")
    cmds.textScrollList(_UI_LEFT_LIST, h=340, ams=False,
                          sc=_ui_on_left_select)
    cmds.rowLayout(nc=2, adj=1, cw2=(160, 160),
                   ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="+ 選択 curve 追加", h=24,
                c=_ui_add_ctl_from_selection)
    cmds.button(l="- Registry 削除", h=24,
                c=_ui_remove_selected_ctl,
                bgc=(0.55, 0.35, 0.35))
    cmds.setParent("..")
    cmds.setParent("..")
    # 右
    cmds.columnLayout(adj=True, rs=3)
    cmds.text(l="② Linked BlendShape Attrs  (左選択 ctl の紐付け一覧)",
              al="left", fn="boldLabelFont")
    cmds.textScrollList(_UI_RIGHT_LIST, h=340, ams=True)
    cmds.rowLayout(nc=2, adj=1, cw2=(160, 160),
                   ct2=("both", "both"), co2=(2, 2))
    cmds.button(l="+ 紐付け  (CB選択→追加)", h=24,
                c=_ui_link_selected_bs, bgc=(0.30, 0.55, 0.30),
                ann="Channel Box で blendShape の attr を選択した状態で "
                     "押すと 左選択 ctl と紐付け (proxy attr 経由)")
    cmds.button(l="- 紐付け解除", h=24,
                c=_ui_unlink_selected_bs, bgc=(0.55, 0.35, 0.35))
    cmds.setParent("..")
    cmds.setParent("..")
    cmds.setParent("..")

    cmds.separator(h=8, style="in")
    cmds.text(l="Tips: proxy attribute で紐付け → ctl 選択 → Channel Box に "
                 "blendShape attr が現れる → animator は ctl だけ触れば OK",
              al="left", fn="smallObliqueLabelFont", ww=True)

    cmds.showWindow(win)
    _ui_refresh_left()
    _ui_refresh_right()
    return win
