"""FBX Renamer -- Maya 2023 Script Editor 貼り付け用

MMD -> FBX -> Maya import で発生する骨名 (および任意ノード名) の
乱れを整理する。install.py 経由の配布はしない。

処理順:
  1. namespace を除去 (foo:bar:baz -> baz)
  2. FBXASC### を実文字に decode
     MMD FBX exporter は Japanese 1 文字 (3 UTF-8 bytes) を
     FBXASC229FBXASC133FBXASC168 のように 1 byte ずつ吐き出す。
     連続する FBXASC### 群を bytes として集めて UTF-8 decode する。
  3. MMD 標準骨名を英語に翻訳 (全ての親 -> root_parent, 左腕 -> arm_L 等)
  4. マップに無い残存 Japanese は u<hex> escape (Maya 2023 は Japanese
     を node name に受け付けないため必須)
  5. 無効文字を "_" に置換 (space, dot, paren, /, \\, -, :, |, . 等)
  6. 連続 "_" を単一化、先頭/末尾 "_" を除去
  7. 名前衝突は "_1", "_2" ... サフィックスで回避

--------------------------------------------------------------------
使い方 (Script Editor Python タブに全文貼り付け -> Ctrl+Enter):

  # A. 選択した joint だけ (安全)
  rename_selected_joints()

  # B. シーン内すべての joint
  rename_all_joints()

  # C. joint + mesh + transform 全部
  rename_all(types=["joint", "transform"])

  # D. dry-run (rename せず 元 -> 新 のみ print)
  rename_all_joints(dry_run=True)

  # E. namespace 自体をシーンから抜く
  remove_all_namespaces()

推奨フロー:
  remove_all_namespaces()           # 1. namespace ノードを掃除
  rename_all_joints(dry_run=True)   # 2. rename 予定を確認
  rename_all_joints()               # 3. 本番実行
--------------------------------------------------------------------
"""

import re
import maya.cmds as cmds


# =========================================================================
# regex / sanitize
# =========================================================================

_FBXASC_RE = re.compile(r"FBXASC(\d{3})")
# Maya 識別子: 英数, "_" のみ許可 (Japanese は事前に翻訳/エスケープ)
_INVALID_ASCII_RE = re.compile(r"[^A-Za-z0-9_]")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


# =========================================================================
# MMD 標準骨名 -> 英語 マッピング
# ------------------------------------------------------------------------
# MMD (PMD/PMX) では日本語で骨に命名する。FBX 経由で Maya に持ち込むと
# Japanese はそのまま扱えない (Maya 2023 は node name に非 ASCII 不可) ので
# 定番の英語表記に置き換える。ここに無いものは u<hex> escape に fall back。
#
# 命名規則:
# - side prefix (左/右/前/後) は EXTRACTED して suffix "_L"/"_R" 化。
# - 順序: 「左腕」-> 左 抽出 -> "腕" -> "arm" -> "arm_L"
# - 「肩P」「肩C」等の派生は明示エントリを持つ。
# =========================================================================

_MMD_SIDE_MAP = {
    "左": "_L",
    "右": "_R",
    "前": "_F",
    "後": "_B",
}

_MMD_BONE_MAP = {
    # --- 体幹 ---
    "全ての親": "root_parent",
    "センター": "center",
    "グルーブ": "groove",
    "グループ": "group",
    "腰": "waist",
    "上半身": "upper_body",
    "上半身2": "upper_body_2",
    "下半身": "lower_body",
    "首": "neck",
    "頭": "head",

    # --- 目 ---
    "両目": "eyes",
    "目": "eye",
    "目球": "eyeball",
    "目玉": "eyeball",

    # --- 腕 ---
    "肩P": "shoulder_P",
    "肩C": "shoulder_C",
    "肩": "shoulder",
    "腕捩1": "arm_twist_1",
    "腕捩2": "arm_twist_2",
    "腕捩3": "arm_twist_3",
    "腕捩": "arm_twist",
    "腕": "arm",
    "ひじ": "elbow",
    "肘": "elbow",
    "手捩1": "hand_twist_1",
    "手捩2": "hand_twist_2",
    "手捩3": "hand_twist_3",
    "手捩": "hand_twist",
    "手首": "wrist",
    "手先": "hand_tip",
    "手指": "hand_finger",
    "手": "hand",

    # --- 指 ---
    "親指0": "thumb_0",
    "親指1": "thumb_1",
    "親指2": "thumb_2",
    "親指": "thumb",
    "人指1": "index_1",
    "人指2": "index_2",
    "人指3": "index_3",
    "人指": "index",
    "中指1": "middle_1",
    "中指2": "middle_2",
    "中指3": "middle_3",
    "中指": "middle",
    "薬指1": "ring_1",
    "薬指2": "ring_2",
    "薬指3": "ring_3",
    "薬指": "ring",
    "小指1": "pinky_1",
    "小指2": "pinky_2",
    "小指3": "pinky_3",
    "小指": "pinky",
    "母指": "thumb",
    "指": "finger",

    # --- 脚 ---
    "つま先IK": "toe_ik",
    "つま先": "toe",
    "足IK親": "leg_ik_parent",
    "足IK": "leg_ik",
    "足首": "ankle",
    "足操作中心": "leg_op_center",
    "足操作": "leg_op",
    "足": "leg",
    "ひざ": "knee",
    "膝": "knee",

    # --- お尻 / 尻尾 (お尻 は 尻 より先) ---
    "お尻": "butt",
    "尻尾": "tail",
    "しっぽ": "tail",
    "尻": "butt",

    # --- 髪 ---
    "前髪": "front_hair",
    "後髪": "back_hair",
    "サイド髪": "side_hair",
    "横髪": "side_hair",
    "髪": "hair",

    # --- 服飾 (Nekotatune 含む一般的なもの) ---
    "リボン": "ribbon",
    "スカート": "skirt",
    "袖": "sleeve",
    "胸": "chest",
    "ネクタイ": "necktie",
    "コート": "coat",

    # --- 動物パーツ (cat girl model) ---
    "猫耳": "cat_ear",
    "耳": "ear",
    "猫": "cat",

    # --- MMD 標準の cancel bone / 補助ラベル ---
    "キャンセル": "cancel",
    "ダミー": "dummy",
    "補助": "aux",

    # --- 汎用ラベル ---
    "先": "tip",
    "操作中心": "op_center",
    "操作": "op",
    "中心": "center",
    "半身": "body_half",
    "作": "make",
    "球": "ball",
    "IK": "IK",
    "EX": "EX",
}

# 長い pattern から先にマッチさせる (「上半身2」を「上半身」より先に)
_SORTED_BONE_KEYS = sorted(_MMD_BONE_MAP.keys(), key=len, reverse=True)


# =========================================================================
# name transform helpers
# =========================================================================

def _strip_namespace(name):
    """foo:bar:baz -> baz. Short name 前提。"""
    return name.split(":")[-1]


_FULLWIDTH_DIGIT_MAP = {chr(0xFF10 + i): str(i) for i in range(10)}
_FULLWIDTH_TRANSLATE = str.maketrans(_FULLWIDTH_DIGIT_MAP)


def _normalize_fullwidth(name):
    """全角数字 (U+FF10-U+FF19) を半角 0-9 に正規化。
    MMD 骨名で「親指１」(全角) と「親指1」(半角) が混在するのでマップ検索前に統一。"""
    return name.translate(_FULLWIDTH_TRANSLATE)


def _decode_fbxasc(name):
    """FBXASC### を実文字に戻す。

    MMD FBX exporter は 1 Japanese char (3 UTF-8 bytes) を
    FBXASC229FBXASC133FBXASC168 のように 1 byte ずつエンコードする。
    従って連続 FBXASC### を byte 配列として集めて UTF-8 decode する。
    """
    parts = []
    buf = bytearray()
    pos = 0

    def _flush():
        if not buf:
            return
        try:
            parts.append(bytes(buf).decode("utf-8"))
        except UnicodeDecodeError:
            parts.append(bytes(buf).decode("latin-1", errors="replace"))
        buf.clear()

    for m in _FBXASC_RE.finditer(name):
        if m.start() > pos:
            _flush()
            parts.append(name[pos:m.start()])
        code = int(m.group(1))
        if 0 <= code <= 255:
            buf.append(code)
        else:
            _flush()
            parts.append(chr(code))
        pos = m.end()

    _flush()
    if pos < len(name):
        parts.append(name[pos:])
    return "".join(parts)


def _translate_mmd(name):
    """MMD 標準骨名を英語に翻訳する。
    1. 先頭の 左/右/前/後 を抽出して suffix 化 (_L / _R / _F / _B)
    2. 長い bone pattern から順に substring 置換
    3. まだ残っている 左/右/前/後 も置換 (中位置ケース)
    """
    side_suffix = ""
    for jp, en in _MMD_SIDE_MAP.items():
        if name.startswith(jp):
            side_suffix = en
            name = name[len(jp):]
            break

    for jp in _SORTED_BONE_KEYS:
        if jp in name:
            name = name.replace(jp, _MMD_BONE_MAP[jp])

    for jp, en in _MMD_SIDE_MAP.items():
        if jp in name:
            name = name.replace(jp, en)

    return name + side_suffix


def _ascii_safe(name):
    """残存 non-ASCII (未知の Japanese 等) を u<hex> にエスケープ。
    MMD map を通した後の最後の手段。"""
    out = []
    for c in name:
        o = ord(c)
        if o < 128:
            out.append(c)
        else:
            out.append("u{:04x}".format(o))
    return "".join(out)


def _sanitize(name):
    """Maya identifier に沿わせる (英数 + _ 以外は _ に置換)。"""
    out = _INVALID_ASCII_RE.sub("_", name)
    out = _MULTI_UNDERSCORE_RE.sub("_", out)
    out = out.strip("_")
    if not out:
        out = "unnamed"
    if out[0].isdigit():
        out = "n_" + out
    return out


def clean_name(name):
    """短名 1 個を通しで整形して返す (rename は行わない)。"""
    n = _strip_namespace(name)
    n = _decode_fbxasc(n)
    n = _normalize_fullwidth(n)   # 全角数字 -> 半角 (MMD map ヒット率向上)
    n = _translate_mmd(n)
    n = _ascii_safe(n)
    n = _sanitize(n)
    return n


def _unique_name(desired, existing_names):
    if desired not in existing_names:
        return desired
    i = 1
    while f"{desired}_{i}" in existing_names:
        i += 1
    return f"{desired}_{i}"


# =========================================================================
# 実 rename
# =========================================================================

def rename_nodes(nodes, dry_run=False):
    """任意ノード list を rename。子から親の順で処理。"""
    scored = [(n.count("|"), n) for n in nodes if cmds.objExists(n)]
    scored.sort(reverse=True)
    ordered = [n for _, n in scored]

    all_current = set(cmds.ls())
    mapping = []

    for full_path in ordered:
        if not cmds.objExists(full_path):
            continue
        short = full_path.split("|")[-1]
        new = clean_name(short)
        if new == short:
            continue
        new = _unique_name(new, all_current)
        if dry_run:
            mapping.append((short, new))
            continue
        try:
            actual = cmds.rename(full_path, new)
            actual_short = actual.split("|")[-1] if actual else new
            all_current.discard(short)
            all_current.add(actual_short)
            mapping.append((short, actual_short))
        except Exception as exc:
            print(f"[FBXRenamer] rename FAILED  {short} -> {new}  ({exc})")
            continue

    tag = "DRY-RUN" if dry_run else "RENAMED"
    print(f"[FBXRenamer] {tag}: {len(mapping)} node(s)")
    for old, new in mapping[:80]:
        old_disp = old if len(old) < 60 else old[:57] + "..."
        print(f"  {old_disp!r:60s} -> {new!r}")
    if len(mapping) > 80:
        print(f"  ... ({len(mapping) - 80} more)")
    return mapping


def rename_selected_joints(dry_run=False):
    sel = cmds.ls(sl=True, type="joint", long=True) or []
    if not sel:
        print("[FBXRenamer] No joints selected.")
        return []
    return rename_nodes(sel, dry_run=dry_run)


def rename_all_joints(dry_run=False):
    joints = cmds.ls(type="joint", long=True) or []
    return rename_nodes(joints, dry_run=dry_run)


def rename_all(types=None, dry_run=False):
    """types 指定の全ノード。types=None なら joint のみ。"""
    types = types or ["joint"]
    nodes = cmds.ls(type=types, long=True) or []
    return rename_nodes(nodes, dry_run=dry_run)


# =========================================================================
# namespace 整理
# =========================================================================

def remove_all_namespaces(dry_run=False):
    """シーン内すべての namespace を root(:) に merge して削除。
    UI / shared は保護。"""
    ns_all = cmds.namespaceInfo(":", listOnlyNamespaces=True, recurse=True) or []
    ns_all = [n for n in ns_all if n not in ("UI", "shared")]
    ns_all.sort(key=lambda n: n.count(":"), reverse=True)

    removed = 0
    for ns in ns_all:
        if not cmds.namespace(exists=ns):
            continue
        print(f"[FBXRenamer] {'DRY-RUN' if dry_run else 'MERGE  '} :{ns} -> :")
        if dry_run:
            continue
        try:
            cmds.namespace(force=True, moveNamespace=(ns, ":"))
            cmds.namespace(removeNamespace=ns)
            removed += 1
        except Exception as exc:
            print(f"  FAILED: {exc}")
    tag = "DRY-RUN" if dry_run else "REMOVED"
    print(f"[FBXRenamer] {tag} namespaces: {removed}")


# --- 貼り付け直後は何も実行しない (関数定義のみ) ----------------------
print("[FBXRenamer] loaded. Call rename_selected_joints() / rename_all_joints() etc.")
