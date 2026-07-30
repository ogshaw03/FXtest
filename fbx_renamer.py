"""FBX Renamer -- Maya 2023 Script Editor 貼り付け用

MMD -> FBX -> Maya import で発生する骨名 (および任意ノード名) の
乱れを整理する。install.py 経由の配布はしない。使い方:

  1. このファイル全体を Script Editor の Python タブに貼り付け
  2. Ctrl+Enter で実行 (関数定義が読み込まれる、この時点では何もしない)
  3. 下部の 使い方 セクションの関数を呼ぶ

処理順:
  1. namespace を除去 (foo:bar:baz -> baz)
  2. FBXASC### を実文字に decode (FBXASC032 -> " ", FBXASC046 -> "." 等)
  3. Maya identifier に沿わない文字を "_" に置換
     (space, dot, paren, /, \\, -, :, | 等)
     ※ 日本語 (漢字/ひらがな/カタカナ) はそのまま保持
  4. 連続 "_" を単一化、先頭/末尾 "_" を除去
  5. 名前衝突は "_1", "_2" ... サフィックスで回避
  6. 元名 -> 新名 の対応を print

--------------------------------------------------------------------
使い方 (関数を呼ぶ、Script Editor の Python タブで):

  # A. 選択した joint だけ (安全)
  rename_selected_joints()

  # B. シーン内すべての joint
  rename_all_joints()

  # C. joint + mesh + transform 全部
  rename_all(types=["joint", "transform"])

  # D. dry-run (実際には rename せず 元 -> 新 のみ print)
  rename_selected_joints(dry_run=True)
  rename_all_joints(dry_run=True)

  # E. namespace 自体をシーンから抜く (rename の前後どちらでも可)
  remove_all_namespaces()

推奨フロー:
  remove_all_namespaces()           # 1. namespace ノードを消す
  rename_all_joints(dry_run=True)   # 2. rename 予定を確認
  rename_all_joints()               # 3. 問題なければ本番実行
--------------------------------------------------------------------
"""

import re
import maya.cmds as cmds


# --- 判定用 regex ------------------------------------------------------
_FBXASC_RE = re.compile(r"FBXASC(\d{3})")

# 許可文字: 英数, "_", 漢字 (U+4E00-U+9FFF), ひらがな+カタカナ (U+3040-U+30FF)
# これ以外は "_" に置換される。 (unicode escape でコピペ時のエンコード事故回避)
_INVALID_CHAR_RE = re.compile(
    "[^A-Za-z0-9_"
    "぀-ヿ"   # Hiragana + Katakana
    "一-鿿"   # CJK Unified Ideographs (kanji)
    "]"
)
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


# --- name transform helpers -------------------------------------------

def _strip_namespace(name):
    """foo:bar:baz -> baz. Short name 前提。"""
    return name.split(":")[-1]


def _decode_fbxasc(name):
    """FBXASC### を chr(###) に戻す。"""
    def repl(m):
        try:
            return chr(int(m.group(1)))
        except Exception:
            return m.group(0)
    return _FBXASC_RE.sub(repl, name)


def _sanitize(name):
    out = _INVALID_CHAR_RE.sub("_", name)
    out = _MULTI_UNDERSCORE_RE.sub("_", out)
    out = out.strip("_")
    if not out:
        out = "unnamed"
    if out[0].isdigit():
        out = "n_" + out
    return out


def clean_name(name):
    """短名 1 個を通しで整形して返す (rename は行わない)。"""
    n = name
    n = _strip_namespace(n)
    n = _decode_fbxasc(n)
    n = _sanitize(n)
    return n


def _unique_name(desired, existing_names):
    if desired not in existing_names:
        return desired
    i = 1
    while f"{desired}_{i}" in existing_names:
        i += 1
    return f"{desired}_{i}"


# --- 実 rename ---------------------------------------------------------

def rename_nodes(nodes, dry_run=False):
    """任意ノード list を rename。子から親の順で処理。"""
    # DAG path 深い順 = 子から先に (親の long path 変動を避ける)
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
        mapping.append((short, new))
        if not dry_run:
            try:
                actual = cmds.rename(full_path, new)
                all_current.discard(short)
                all_current.add(actual)
            except Exception as exc:
                print(f"[FBXRenamer] rename FAILED  {short} -> {new}  ({exc})")
                continue

    tag = "DRY-RUN" if dry_run else "RENAMED"
    print(f"[FBXRenamer] {tag}: {len(mapping)} node(s)")
    for old, new in mapping:
        print(f"  {old!r:40s} -> {new!r}")
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


# --- namespace 整理 ---------------------------------------------------

def remove_all_namespaces(dry_run=False):
    """シーン内すべての namespace を root(:) に merge して削除。
    UI / shared など Maya システム namespace は保護。"""
    ns_all = cmds.namespaceInfo(":", listOnlyNamespaces=True, recurse=True) or []
    ns_all = [n for n in ns_all if n not in ("UI", "shared")]
    # 深い namespace から (子から) 処理
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


# --- 貼り付け直後は何も実行しない (関数だけ定義) ----------------------
# 実行したい関数を上の 使い方 セクションから選んで別行で呼ぶ。
print("[FBXRenamer] loaded. Call rename_selected_joints() / rename_all_joints() etc.")
