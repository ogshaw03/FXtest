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


__version__ = "0.1.0"


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


def attach_controllers(joints=None, scale=1.0, do_constrain=True):
    """選択された joint に mGear 風のコントローラを一括セットアップ。

    Args:
        joints:        処理対象 joint リスト。None なら現在の selection を使う。
        scale:         キューブ ctl のスケール (1.0 = 1 unit)。
        do_constrain:  True なら joint を ctl に parentConstraint する。

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

        side = _detect_side(base)
        color = {"L": COLOR_L, "R": COLOR_R, "C": COLOR_C}[side]

        ctl = _make_cube_curve(ctl_name, scale=scale)
        _set_ctl_color(ctl, color)

        npo = cmds.group(em=True, name=npo_name)
        cmds.parent(ctl, npo)

        cmds.matchTransform(npo, jnt, pos=True, rot=True)
        cmds.parent(npo, ROOT_GROUP)

        _lock_hide_attrs(ctl, ["sx", "sy", "sz", "v"])

        jnt_to_ctl[jnt] = (npo, ctl)
        created_ctls.append(ctl_name)

    # Pass 2: 骨階層に合わせて npo を親子付け直し
    for jnt, (npo, ctl) in jnt_to_ctl.items():
        parents = cmds.listRelatives(jnt, p=True, type="joint") or []
        if parents and parents[0] in jnt_to_ctl:
            parent_ctl = jnt_to_ctl[parents[0]][1]
            try:
                cmds.parent(npo, parent_ctl)
            except Exception as exc:
                cmds.warning(f"[attach_ctrls] parent {npo} -> {parent_ctl} failed: {exc}")

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
    """attach_ctrls_grp と付随する constraint をまとめて削除。"""
    if cmds is None:
        return
    n = 0
    if cmds.objExists(ROOT_GROUP):
        cmds.delete(ROOT_GROUP)
        n += 1
    # constraint も掃除
    for con in cmds.ls("*_parentConstraint*", type="parentConstraint") or []:
        try:
            cmds.delete(con)
            n += 1
        except Exception:
            pass
    print(f"[{_PACKAGE}] Deleted generated nodes: {n}")


# =========================================================================
# UI
# =========================================================================

_UI_SCALE = "attach_ctrls_ui_scale"
_UI_CONSTRAIN = "attach_ctrls_ui_constrain"


def _build_body() -> None:
    cmds.separator(h=4, style="none")
    cmds.text(l="1) Outliner か viewport で骨を選択",
              al="left")
    cmds.text(l="2) 下のパラメータを調整して [Attach] を押す",
              al="left")
    cmds.separator(h=6, style="none")

    cmds.floatSliderGrp(
        _UI_SCALE,
        label="Ctrl Scale", field=True,
        min=0.1, max=10.0, fieldMinValue=0.01, fieldMaxValue=100.0,
        value=1.0, cw3=(80, 60, 120),
        ann="キューブコントローラーのサイズ (unit)",
    )
    cmds.checkBoxGrp(
        _UI_CONSTRAIN,
        label="Constrain", label1="parentConstraint (joint follows ctl)",
        value1=True, cw2=(80, 260),
    )
    cmds.separator(h=6, style="none")

    cmds.rowLayout(nc=2, adj=1, cw2=(240, 120),
                   ct2=("both", "both"), co2=(4, 4))
    cmds.button(l="Attach controllers to selected", h=36, c=_ui_attach,
                bgc=(0.20, 0.55, 0.95))
    cmds.button(l="Delete generated", h=36, c=_ui_delete,
                bgc=(0.55, 0.20, 0.20))
    cmds.setParent("..")

    cmds.separator(h=6, style="none")
    cmds.text(l="側判定: 左/右 (MMD), L_/R_, _L/_R, left/right → 該当なしは C(黄)",
              al="left", fn="smallObliqueLabelFont")


def _ui_attach(*_):
    scale = cmds.floatSliderGrp(_UI_SCALE, q=True, value=True)
    do_constrain = cmds.checkBoxGrp(_UI_CONSTRAIN, q=True, value1=True)
    attach_controllers(scale=scale, do_constrain=do_constrain)


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
