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
__version__ = "0.1.0"
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

def _make_face_curve(name, size=1.0):
    """顔マーク: 外側円 (顔輪郭) + 目 2 個 (小円) + 口 (弧)。"""
    import math
    r = size
    # 顔輪郭 (円)
    seg = 32
    face = [(r * math.cos(2 * math.pi * i / seg), 0,
             r * math.sin(2 * math.pi * i / seg))
            for i in range(seg + 1)]
    face_c = cmds.curve(d=1, p=face)
    # 左目
    er = r * 0.15
    eye_l = [(er * math.cos(2 * math.pi * i / 16) - r * 0.35, 0,
              er * math.sin(2 * math.pi * i / 16) + r * 0.25)
             for i in range(17)]
    eye_l_c = cmds.curve(d=1, p=eye_l)
    # 右目
    eye_r = [(er * math.cos(2 * math.pi * i / 16) + r * 0.35, 0,
              er * math.sin(2 * math.pi * i / 16) + r * 0.25)
             for i in range(17)]
    eye_r_c = cmds.curve(d=1, p=eye_r)
    # 口 (下向き弧)
    mouth = []
    for i in range(9):
        a = math.pi + (math.pi / 8) * i    # 180° -> 360° range
        mouth.append((r * 0.35 * math.cos(a), 0,
                       r * 0.35 * math.sin(a) - r * 0.15))
    mouth_c = cmds.curve(d=1, p=mouth)
    # combine shapes to face_c transform
    for c in (eye_l_c, eye_r_c, mouth_c):
        for s in cmds.listRelatives(c, s=True, type="nurbsCurve") or []:
            cmds.parent(s, face_c, s=True, r=True)
        cmds.delete(c)
    return cmds.rename(face_c, name)


def _make_eye_curve(name, size=1.0):
    """目 ctl: アーモンド型 (楕円)。"""
    import math
    pts = []
    for i in range(33):
        a = 2 * math.pi * i / 32
        pts.append((size * math.cos(a), 0, size * 0.4 * math.sin(a)))
    return cmds.curve(d=1, p=pts, n=name)


def _make_mouth_curve(name, size=1.0):
    """口 ctl: 唇型 (下弧 + 上弧)。"""
    import math
    pts = []
    # 上唇 (上弧)
    for i in range(17):
        a = math.pi * i / 16
        pts.append((size * math.cos(a), 0, size * 0.3 * math.sin(a)))
    # 下唇 (下弧、戻り)
    for i in range(17):
        a = math.pi + math.pi * i / 16
        pts.append((size * math.cos(a), 0,
                     -size * 0.15 * math.sin(a - math.pi)))
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
    face_ctl = _make_face_curve(FACE_CTL_NAME, size=size)
    _set_ctl_color(face_ctl, 17)   # 黄
    face_npo = cmds.group(em=True, n=FACE_NPO_NAME)
    cmds.parent(face_ctl, face_npo)
    cmds.xform(face_npo, ws=True, t=head_position)
    # 顔を 正面向きに (X 軸周り 90° で XZ flat → 前向き 平面)
    cmds.xform(face_npo, ro=(90, 0, 0))

    # 目 ctl (face の 上位置)
    eye_ctl = _make_eye_curve(EYE_CTL_NAME, size=size * 0.35)
    _set_ctl_color(eye_ctl, 14)   # 緑
    eye_npo = cmds.group(em=True, n=EYE_NPO_NAME)
    cmds.parent(eye_ctl, eye_npo)
    cmds.parent(eye_npo, face_ctl)
    cmds.setAttr(f"{eye_npo}.translate", 0, 0.25 * size, 0, type="double3")
    cmds.setAttr(f"{eye_npo}.rotate", 0, 0, 0, type="double3")

    # 口 ctl (face の 下位置)
    mouth_ctl = _make_mouth_curve(MOUTH_CTL_NAME, size=size * 0.4)
    _set_ctl_color(mouth_ctl, 13)   # 赤
    mouth_npo = cmds.group(em=True, n=MOUTH_NPO_NAME)
    cmds.parent(mouth_ctl, mouth_npo)
    cmds.parent(mouth_npo, face_ctl)
    cmds.setAttr(f"{mouth_npo}.translate", 0, -0.15 * size, 0, type="double3")
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
    """左 選択 ctl + Channel Box で選択中 の blendShape attr を紐付け。"""
    left_sel = cmds.textScrollList(_UI_LEFT_LIST, q=True, si=True) or []
    if not left_sel:
        cmds.warning("先に左 list から ctl を 選択してください")
        return
    ctl = left_sel[0]
    # Channel Box で 選択中 attr を取得
    cb = "mainChannelBox"
    sel_attrs = cmds.channelBox(cb, q=True, sma=True) or []
    if not sel_attrs:
        cmds.warning("Channel Box で blendShape の attr を 選択してください")
        return
    # 選択中 node (Channel Box に映ってる)
    cb_nodes = cmds.channelBox(cb, q=True, mol=True) or []
    if not cb_nodes:
        cmds.warning("Channel Box で blendShape node を 選択してください")
        return
    bs_node = None
    for n in cb_nodes:
        if cmds.objExists(n) and cmds.nodeType(n) == "blendShape":
            bs_node = n
            break
    if not bs_node:
        cmds.warning("選択中 に blendShape node が無い")
        return
    for attr in sel_attrs:
        link_bs_attr(ctl, bs_node, attr)
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
