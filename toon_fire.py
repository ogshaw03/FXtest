"""toon_fire -- Maya 2023 セルルック炎エフェクト (ポリゴン + トゥーン)

アニメ調 (2〜3階調フラット) の炎エフェクトを生成する
シングルファイル ツール。install.py と同居して配布し、シェルフから
起動 / GitHub 更新できる。

構成:
  - outer   : 外炎 (濃いオレンジ)                        [最外]
  - middle  : 中炎 (オレンジ)
  - core    : 芯 (黄〜白)                                [最内]

アニメーション:
  各レイヤーに time ベースの noise() 多オクターブ表現式で
  scale/rotate/translate を付与。scaleY は上方向 (>1) に
  バイアスをかけ、炎が「舐め上がる」動きを再現する。
"""

from __future__ import annotations

try:
    from maya import cmds  # type: ignore
except ImportError:  # pragma: no cover
    cmds = None  # type: ignore


__version__ = "0.2.0"


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
        ch=False,
        n=name + "_mesh",
        f=2,          # format: Quads
        pt=1,         # polygonType: Count method
        pc=300,       # target polygon count
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


def _add_flicker_expression(transform, phase=0.0, speed=6.0,
                            amp_scale=0.08, amp_rot=6.0,
                            up_bias=0.60):
    """炎の揺らぎを付与する MEL 表現式。

    - noise() を 2 オクターブ重ねて有機的な揺らぎを作る。
    - scaleY は上方向 (>1) にバイアスをかけ「舐め上がる」動きに。
    - translate は 0.15 unit 以下に抑え、位置は安定させる。
    - rotate はゆっくりした横揺れ (sway) のみ。

    Args:
        up_bias: 0..1  scaleY を 1 より上に押し上げる比率 (fire licks up).
    """
    # noise(x) は Maya 表現式で ~-1..1 の smooth pseudo-random を返す。
    # 2 オクターブ (低周波の大うねり + 高周波のちらつき) を混ぜる。
    expr = (
        "float $t   = time * {speed} + {phase};\n"
        "float $t2  = $t * 2.3 + 5.1;\n"
        "// --- 2-octave noise per axis ---\n"
        "float $nx  = noise($t)        * 0.65 + noise($t2)        * 0.35;\n"
        "float $nz  = noise($t + 11.0) * 0.65 + noise($t2 + 7.0)  * 0.35;\n"
        "float $ny  = noise($t * 0.8 + 3.0) * 0.70 "
        "+ noise($t * 2.6 + 13.0) * 0.30;\n"
        "float $nr  = noise($t * 0.45 + 21.0);\n"
        "// --- upward-biased scaleY (fire licks up) ---\n"
        "// remap ny (~-1..1) so it sits mostly above 1.\n"
        "float $sy_raw = ($ny + 1.0) * 0.5;   // 0..1\n"
        "float $sy_bias = $sy_raw * (1.0 - {up_bias}) + {up_bias};\n"
        "{n}.scaleY = 1.0 + $sy_bias * ({a_s} * 1.6);\n"
        "{n}.scaleX = 1.0 + $nx * {a_s};\n"
        "{n}.scaleZ = 1.0 + $nz * {a_s};\n"
        "// --- slow sway rotation, no spin ---\n"
        "{n}.rotateY = $nr * {a_r};\n"
        "// --- tiny drift (< 0.15 unit) ---\n"
        "{n}.translateX = $nx * 0.06;\n"
        "{n}.translateZ = $nz * 0.06;\n"
    ).format(
        n=transform, speed=speed, phase=phase,
        a_s=amp_scale, a_r=amp_rot, up_bias=up_bias,
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

    # (name, h_scale, r_scale, color, phase, speed, amp_s, up_bias)
    # outer は広くゆっくり、core は速く鋭く。amp/up_bias で先端の伸びを演出。
    layers = [
        ("fire_outer", 1.00, 1.00, (0.95, 0.30, 0.03), 0.0, 3.5, 0.12, 0.55),
        ("fire_mid",   0.80, 0.70, (1.00, 0.60, 0.10), 1.7, 5.5, 0.14, 0.65),
        ("fire_core",  0.60, 0.38, (1.00, 0.95, 0.55), 3.4, 8.0, 0.16, 0.75),
    ]

    for name, hs, rs, color, phase, speed, amp_s, up_bias in layers:
        mesh = _make_flame_mesh(name, height=height * hs, radius=radius * rs)
        _shader, sg = _make_flat_shader(name, color)
        _assign_shader(mesh, sg)
        _add_flicker_expression(
            mesh, phase=phase, speed=speed * speed_mult,
            amp_scale=amp_s, up_bias=up_bias,
        )
        cmds.parent(mesh, group)

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
