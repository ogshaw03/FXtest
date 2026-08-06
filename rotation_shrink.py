"""Rotation Shrink v0.1.0 — Euler filter 亜種

概要:
  大きな回転値 (720°, -1080° 等) を、角度を保ったまま等価な小さい値
  (0°, -360° 等) に置換する。playback range 全体を per-frame bake し、
  各 axis ごとに 360° 整数倍の定数オフセットを引くため、animation は
  完全に保たれる (見た目 & 補間 curve 形状が同一)。

想定用途:
  - FBX/mocap import で rotate 値が数千度になってしまったキャラを
    人間が数値編集できる範囲に戻す
  - Euler filter 前後の後処理 (Euler は 360° flip を解決するが、
    全体オフセット (例 -1080°) は残す)

使い方:
  1. Maya 2024 の Script Editor Python タブに全文貼り付けて実行
     (関数 + UI が定義される)
  2. 対象 transform を選択
  3. `rotation_shrink.show_ui()` で UI を起動、または関数を直接呼ぶ:

     shrink_rotations(cmds.ls(sl=True),
                      axes=("rotateX","rotateY","rotateZ"),
                      time_range=None,   # None なら playback range
                      bake=True,
                      apply_euler_filter=True)

アルゴリズム:
  各 (object, axis) について:
    1. (option) filterCurve euler で 360° flip を先に解決
    2. keyframe values の (min+max)/2 を midpoint とし、
       offset = round(midpoint / 360) * 360 を計算
    3. 全 keyframe に -offset を relative 加算
       (定数オフセットなので curve 形状は不変、角度も 360° 単位で同じ)
"""
import maya.cmds as cmds

__version__ = "0.1.0"
WINDOW = "rotationShrinkWin"

# UI element 名前 (state を持ち回るため module global)
_UI_AX     = "rsAxes"
_UI_RANGE  = "rsRange"
_UI_BAKE   = "rsBake"
_UI_EULER  = "rsEuler"
_UI_SEL_H  = "rsSelHier"


# =========================================================================
# Core logic
# =========================================================================

def _compute_offset(values):
    """values の 360° 整数倍オフセットで最大絶対値を最小化する offset。

    midpoint = (min + max) / 2 を 360° 最寄りに丸めるだけの簡易法。
    animation 全体を [-180, +180] 近辺に centered する典型ケースで最適。
    """
    if not values:
        return 0.0
    midpoint = (min(values) + max(values)) / 2.0
    return round(midpoint / 360.0) * 360.0


def _valid_plugs(objects, axes):
    """存在する (object, axis) の plug 名 list を返す。"""
    plugs = []
    for o in objects:
        if not cmds.objExists(o):
            continue
        for a in axes:
            if cmds.attributeQuery(a, node=o, exists=True):
                plugs.append(f"{o}.{a}")
    return plugs


def shrink_rotations(objects, axes=("rotateX", "rotateY", "rotateZ"),
                     time_range=None, bake=True, apply_euler_filter=True,
                     include_hierarchy=False):
    """objects の rotate 値を等価な小さい値に置換 (playback range で bake)。

    Args:
        objects:             transform 名の list
        axes:                対象 axis の list (default 全 3 軸)
        time_range:          (start, end) or None (None → playback range)
        bake:                True なら per-frame bake を先に実行
        apply_euler_filter:  True なら filterCurve euler で 360° flip 補正
        include_hierarchy:   True なら objects の全 descendant transform も対象

    Returns:
        dict {obj: {attr: offset_applied}}  適用された offset 値の一覧
    """
    if not objects:
        cmds.warning("[rotation_shrink] no objects")
        return {}

    if include_hierarchy:
        expanded = set(objects)
        for o in objects:
            for d in cmds.listRelatives(o, ad=True, type="transform") or []:
                expanded.add(d)
        objects = sorted(expanded)

    if time_range is None:
        time_range = (cmds.playbackOptions(q=True, min=True),
                       cmds.playbackOptions(q=True, max=True))
    time_range = (float(time_range[0]), float(time_range[1]))

    axes = list(axes)
    plugs = _valid_plugs(objects, axes)
    if not plugs:
        cmds.warning("[rotation_shrink] no valid rotate attrs on selection")
        return {}

    if bake:
        # 対象 axis のみ per-frame bake。preserveOutsideKeys=True で範囲外の
        # keyframe を壊さない。simulation=True は constraint / expression 経由
        # の駆動を毎フレーム評価するために必要。
        try:
            cmds.bakeResults(
                objects, t=time_range, sampleBy=1,
                attribute=axes, simulation=True,
                preserveOutsideKeys=True,
                sparseAnimCurveBake=False,
                removeBakedAttributeFromLayer=False,
                disableImplicitControl=True,
                bakeOnOverrideLayer=False,
                minimizeRotation=False,
            )
        except Exception as exc:
            cmds.warning(f"[rotation_shrink] bakeResults failed: {exc}")
            return {}

    if apply_euler_filter and plugs:
        # filterCurve euler は 3 軸連動で 360° flip を解決する。partial 軸
        # (X のみ等) だと期待動作にならないので、対象 object の 3 軸まとめて
        # filter する。
        all_axes = ("rotateX", "rotateY", "rotateZ")
        full_plugs = _valid_plugs(objects, all_axes)
        try:
            cmds.filterCurve(*full_plugs, filter="euler")
        except Exception as exc:
            cmds.warning(f"[rotation_shrink] filterCurve failed: {exc}")

    results = {}
    for obj in objects:
        obj_res = {}
        for attr in axes:
            if not cmds.attributeQuery(attr, node=obj, exists=True):
                continue
            plug = f"{obj}.{attr}"
            values = cmds.keyframe(plug, q=True, valueChange=True) or []
            if not values:
                # 静的 (無 key) 値 → 直接 setAttr
                try:
                    cur = cmds.getAttr(plug)
                except Exception:
                    continue
                offset = round(cur / 360.0) * 360.0
                if abs(offset) >= 0.5:
                    try:
                        cmds.setAttr(plug, cur - offset)
                        obj_res[attr] = offset
                    except Exception as exc:
                        cmds.warning(f"[rotation_shrink] setAttr {plug} "
                                      f"failed: {exc}")
                continue
            offset = _compute_offset(values)
            if abs(offset) < 0.5:
                continue
            # 全 key に -offset を relative 加算 (curve 形状不変)
            try:
                cmds.keyframe(plug, edit=True, relative=True,
                               valueChange=-offset)
                obj_res[attr] = offset
            except Exception as exc:
                cmds.warning(f"[rotation_shrink] keyframe edit {plug} "
                              f"failed: {exc}")
        if obj_res:
            results[obj] = obj_res

    n_obj = len(results)
    n_axis = sum(len(v) for v in results.values())
    print(f"[rotation_shrink] shrunk {n_axis} axis(es) on {n_obj} object(s), "
          f"range {time_range[0]:g}-{time_range[1]:g}")
    for obj, offs in results.items():
        pretty = ", ".join(f"{a}={o:+.0f}" for a, o in offs.items())
        print(f"  {obj}: {pretty}")
    return results


# =========================================================================
# UI (cmds)
# =========================================================================

def _ui_get_playback(*_):
    s = cmds.playbackOptions(q=True, min=True)
    e = cmds.playbackOptions(q=True, max=True)
    cmds.floatFieldGrp(_UI_RANGE, e=True, value1=s, value2=e)


def _ui_execute(*_):
    sel = cmds.ls(sl=True, type="transform") or []
    if not sel:
        cmds.warning("Select transform(s) first")
        return
    check3 = cmds.checkBoxGrp(_UI_AX, q=True, valueArray3=True)
    axes = [a for a, v in zip(("rotateX", "rotateY", "rotateZ"), check3) if v]
    if not axes:
        cmds.warning("Select at least one axis")
        return
    s = cmds.floatFieldGrp(_UI_RANGE, q=True, value1=True)
    e = cmds.floatFieldGrp(_UI_RANGE, q=True, value2=True)
    bake = cmds.checkBoxGrp(_UI_BAKE, q=True, value1=True)
    euler = cmds.checkBoxGrp(_UI_EULER, q=True, value1=True)
    incl_h = cmds.checkBoxGrp(_UI_SEL_H, q=True, value1=True)

    # Undo 単位を 1 chunk にまとめる (Execute 1 回 = Ctrl+Z 1 回)
    cmds.undoInfo(openChunk=True, chunkName="rotation_shrink")
    try:
        shrink_rotations(sel, axes=axes, time_range=(s, e),
                          bake=bake, apply_euler_filter=euler,
                          include_hierarchy=incl_h)
    finally:
        cmds.undoInfo(closeChunk=True)


def show_ui():
    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)
    win = cmds.window(WINDOW, title=f"Rotation Shrink  v{__version__}",
                      w=380, h=280, mnb=True, mxb=False, s=True)
    cmds.columnLayout(adj=True, rs=6, cat=("both", 10))

    cmds.text(l="rotate 値を等価な最小値に置換 (Euler filter 亜種)",
              al="left", fn="boldLabelFont")
    cmds.text(l="選択 transform の rotateX/Y/Z を playback range で per-frame "
                "bake し、360° 整数倍オフセットで数値を最小化。角度・curve 形状は不変。",
              al="left", fn="smallObliqueLabelFont", ww=True)

    cmds.separator(h=8, style="in")
    cmds.checkBoxGrp(_UI_AX, numberOfCheckBoxes=3,
                      labelArray3=("rotateX", "rotateY", "rotateZ"),
                      valueArray3=(True, True, True),
                      cw3=(90, 90, 90))

    s = cmds.playbackOptions(q=True, min=True)
    e = cmds.playbackOptions(q=True, max=True)
    cmds.floatFieldGrp(_UI_RANGE, numberOfFields=2, label="Range:",
                        value1=s, value2=e,
                        cw3=(60, 80, 80))
    cmds.rowLayout(nc=1, adj=1, cw=(1, 200))
    cmds.button(l="Get from Playback range", h=22, c=_ui_get_playback)
    cmds.setParent("..")

    cmds.separator(h=4, style="none")
    cmds.checkBoxGrp(_UI_BAKE, label="Bake:",
                      label1="per-frame bake first (推奨)",
                      value1=True, cw2=(60, 250))
    cmds.checkBoxGrp(_UI_EULER, label="Euler:",
                      label1="filterCurve で 360° flip 補正",
                      value1=True, cw2=(60, 250))
    cmds.checkBoxGrp(_UI_SEL_H, label="Sel:",
                      label1="子孫 transform も対象 (階層フルベイク)",
                      value1=False, cw2=(60, 300))

    cmds.separator(h=8, style="in")
    cmds.button(l="⚡ Execute", h=34, c=_ui_execute,
                bgc=(0.90, 0.55, 0.10))
    cmds.text(l="Ctrl+Z で 1 手戻り可能 (undo 1 chunk)",
              al="center", fn="smallObliqueLabelFont")

    cmds.showWindow(win)
    return win


# --------------------------------------------------------------------------
# Script Editor 貼り付け実行時のショートカット (末尾に 1 行呼ぶだけで UI)
# --------------------------------------------------------------------------
# 貼り付け後:
#   rotation_shrink.show_ui()
# または直接:
#   rotation_shrink.shrink_rotations(cmds.ls(sl=True))
