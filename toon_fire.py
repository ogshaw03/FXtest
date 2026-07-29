"""toon_fire -- Maya 2023 セルルック炎エフェクト (ポリゴン + トゥーン)

アニメ調 (2〜3階調フラット + アウトライン) の炎エフェクトを生成する
シングルファイル ツール。install.py と同居して配布し、シェルフから
起動 / GitHub 更新できる。

構成:
  - outline : 反転法線シェル (黒)                       [最外]
  - outer   : 外炎 (濃いオレンジ)
  - middle  : 中炎 (オレンジ)
  - core    : 芯 (黄〜白)                                [最内]

アニメーション:
  各レイヤーに time ベースの sin 表現式で scale/rotate を付与し、
  レイヤーごとに位相をずらして「揺らぎ」を表現。
"""

from __future__ import annotations

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore


__version__ = "0.1.1"


WINDOW = "toon_fireWin"

# --- CUSTOMIZE -----------------------------------------------------------
_GITHUB_OWNER = "ogshaw03"
_GITHUB_REPO = "FXtest"
_GITHUB_BRANCH = "main"
_PACKAGE = "toon_fire"
# --- END CUSTOMIZE -------------------------------------------------------

_GITHUB_API = f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}"
_GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{_GITHUB_OWNER}/{_GITHUB_REPO}"


GROUP_NAME = "toonFire_grp"


# =========================================================================
# Update-from-GitHub flow  (patterns doc §1-7, §1-8, §1-9)  -- 触らない
# =========================================================================

def _resolve_latest_sha() -> str:
    """SHA-pinned URLs are the only reliable cache-buster for
    raw.githubusercontent.com. Ask the API for main's tip commit."""
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
    """UI button callback. Immediately returns -- the actual work runs
    on the next Maya idle so we don't tear down the window that owns
    this callback while the callback is still on the stack."""
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
# Fire effect generation
# =========================================================================

def _make_flame_mesh(name, height=3.0, radius=1.0, subdiv_axis=16):
    """炎シルエットのプロファイル曲線を Y 軸に revolve → ポリゴン化。"""
    profile_pts = [
        (0.65, 0.00, 0.0),
        (1.00, 0.15, 0.0),
        (0.95, 0.40, 0.0),
        (0.75, 0.65, 0.0),
        (0.45, 0.85, 0.0),
        (0.00, 1.00, 0.0),
    ]
    scaled = [(x * radius, y * height, z) for x, y, z in profile_pts]

    curve = cmds.curve(d=3, p=scaled, name=name + "_profile")

    nurbs_surface = cmds.revolve(
        curve,
        ch=False, po=0,
        ssw=0, esw=360,
        ax=(0, 1, 0),
        degree=3, s=subdiv_axis,
        name=name + "_nurbs",
    )[0]

    poly_result = cmds.nurbsToPoly(
        nurbs_surface,
        mnd=1, ch=False,
        f=2, pt=1, pc=300,
        chr=0.9, ft=0.01,
        mel=0.001, d=0.1,
        ut=1, un=subdiv_axis,
        vt=1, vn=6,
        uch=0, ucr=0, cht=0.2, es=0,
        ntr=0, mrt=0, mel1=0.001,
        name=name + "_mesh",
    )
    poly_mesh = poly_result[0]

    cmds.delete(curve, nurbs_surface)
    cmds.xform(poly_mesh, ws=True, rp=(0, 0, 0), sp=(0, 0, 0))
    return poly_mesh


def _make_flat_shader(name, color):
    shader = cmds.shadingNode("surfaceShader", asShader=True, name=name + "_SS")
    sg = cmds.sets(
        renderable=True, noSurfaceShader=True, empty=True,
        name=name + "_SG",
    )
    cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
    cmds.setAttr(shader + ".outColor", *color, type="double3")
    return shader, sg


def _assign_shader(mesh, sg):
    cmds.sets(mesh, edit=True, forceElement=sg)


def _make_outline_shell(src_mesh, name, thickness=0.05):
    dup = cmds.duplicate(src_mesh, name=name)[0]
    cmds.polyNormal(dup, normalMode=0, userNormalMode=0, ch=False)
    cmds.setAttr(dup + ".scale", 1 + thickness, 1 + thickness, 1 + thickness)
    return dup


def _add_flicker_expression(transform, phase=0.0, speed=6.0,
                            amp_scale=0.08, amp_rot=6.0):
    expr = (
        "float $t = time * {speed} + {phase};\n"
        "{n}.scaleX = 1 + sin($t)         * {a_s};\n"
        "{n}.scaleZ = 1 + sin($t + 1.7)   * {a_s};\n"
        "{n}.scaleY = 1 + sin($t * 0.8)   * ({a_s} * 0.5);\n"
        "{n}.rotateY = sin($t * 0.5)      * {a_r};\n"
        "{n}.translateX = sin($t * 1.3)   * 0.05;\n"
        "{n}.translateZ = sin($t * 1.1 + 0.7) * 0.05;\n"
    ).format(
        n=transform, speed=speed, phase=phase,
        a_s=amp_scale, a_r=amp_rot,
    )
    cmds.expression(s=expr, name=transform + "_flicker_EXPR", ae=True, uc="all")


def create(height=3.0, radius=1.0, speed_mult=1.0):
    """Generate the toon fire in the current scene.

    Args:
        height:     炎全体の高さ (unit).
        radius:    炎全体の幅 (unit).
        speed_mult: 揺らぎ速度倍率 (1.0 = デフォルト).
    """
    if cmds is None:
        raise RuntimeError("create() must be called inside Maya.")

    if cmds.objExists(GROUP_NAME):
        cmds.delete(GROUP_NAME)

    group = cmds.group(em=True, name=GROUP_NAME)

    layers = [
        # (name,      h_scale, r_scale, color,             phase, speed, amp_s)
        ("fire_outer", 1.00,   1.00,   (0.95, 0.35, 0.05), 0.0,  5.0,  0.10),
        ("fire_mid",   0.83,   0.75,   (1.00, 0.65, 0.10), 1.1,  6.0,  0.09),
        ("fire_core",  0.67,   0.45,   (1.00, 0.95, 0.55), 2.3,  7.0,  0.08),
    ]

    for name, hs, rs, color, phase, speed, amp_s in layers:
        mesh = _make_flame_mesh(name, height=height * hs, radius=radius * rs)
        _shader, sg = _make_flat_shader(name, color)
        _assign_shader(mesh, sg)
        _add_flicker_expression(
            mesh, phase=phase, speed=speed * speed_mult, amp_scale=amp_s,
        )
        cmds.parent(mesh, group)

    outline_mesh = _make_flame_mesh(
        "fire_outline_src", height=height * 1.02, radius=radius * 1.02,
        subdiv_axis=16,
    )
    outline_final = _make_outline_shell(outline_mesh, "fire_outline", thickness=0.02)
    cmds.delete(outline_mesh)
    _shader, sg = _make_flat_shader("fire_outline", (0.02, 0.02, 0.02))
    _assign_shader(outline_final, sg)
    _add_flicker_expression(
        outline_final, phase=0.05, speed=5.0 * speed_mult, amp_scale=0.10,
    )
    cmds.parent(outline_final, group)

    cmds.playbackOptions(min=1, max=120, ast=1, aet=120)
    cmds.currentTime(1)

    print(f"[{_PACKAGE}] Created group: {group}  "
          f"(h={height}, r={radius}, speed x{speed_mult})")
    return group


def delete_fire():
    if cmds is None:
        return
    if cmds.objExists(GROUP_NAME):
        cmds.delete(GROUP_NAME)
        print(f"[{_PACKAGE}] Deleted {GROUP_NAME}")


# =========================================================================
# UI
# =========================================================================

_UI_HEIGHT = "toon_fire_ui_height"
_UI_RADIUS = "toon_fire_ui_radius"
_UI_SPEED = "toon_fire_ui_speed"


def _build_body() -> None:
    cmds.separator(h=4, style="none")
    cmds.text(l="炎エフェクト パラメータ", al="left", fn="boldLabelFont")
    cmds.separator(h=4, style="none")

    cmds.floatSliderGrp(
        _UI_HEIGHT,
        label="Height", field=True,
        min=0.5, max=10.0, fieldMinValue=0.1, fieldMaxValue=100.0,
        value=3.0, cw3=(60, 60, 120),
        ann="炎の高さ (unit)",
    )
    cmds.floatSliderGrp(
        _UI_RADIUS,
        label="Radius", field=True,
        min=0.1, max=5.0, fieldMinValue=0.05, fieldMaxValue=50.0,
        value=1.0, cw3=(60, 60, 120),
        ann="炎の幅 (unit)",
    )
    cmds.floatSliderGrp(
        _UI_SPEED,
        label="Speed", field=True,
        min=0.1, max=3.0, fieldMinValue=0.05, fieldMaxValue=10.0,
        value=1.0, cw3=(60, 60, 120),
        ann="揺らぎ速度倍率",
    )
    cmds.separator(h=6, style="none")

    cmds.rowLayout(nc=2, adj=1, cw2=(200, 100), ct2=("both", "both"),
                   co2=(4, 4))
    cmds.button(l="Create Fire", h=32, c=_ui_create,
                bgc=(0.95, 0.55, 0.15))
    cmds.button(l="Delete", h=32, c=_ui_delete)
    cmds.setParent("..")


def _ui_create(*_):
    h = cmds.floatSliderGrp(_UI_HEIGHT, q=True, value=True)
    r = cmds.floatSliderGrp(_UI_RADIUS, q=True, value=True)
    s = cmds.floatSliderGrp(_UI_SPEED, q=True, value=True)
    create(height=h, radius=r, speed_mult=s)


def _ui_delete(*_):
    delete_fire()


def show() -> str:
    if cmds is None:
        raise RuntimeError("show() must be called inside Maya.")

    if cmds.window(WINDOW, exists=True):
        cmds.deleteUI(WINDOW)

    win = cmds.window(WINDOW,
                      t=f"ToonFire  --  v{__version__}",
                      w=380, h=240, mnb=True, mxb=False, s=True)
    cmds.columnLayout(adj=True, rs=8, cat=("both", 10))

    _build_body()

    cmds.separator(h=10, style="in")
    cmds.rowLayout(nc=2, adj=1, cw2=(200, 150))
    cmds.text(l=f"{_PACKAGE}  v{__version__}",
              al="left", fn="smallObliqueLabelFont")
    cmds.button(l="GitHub から更新", h=24, c=update_from_github)
    cmds.setParent("..")

    cmds.showWindow(win)
    return win
