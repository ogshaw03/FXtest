# FXtest — Maya 2023 リギング/エフェクトツール

現在のツール:
- **attach_ctrls** — 既存 skinned joint に mGear 風コントローラを一括セットアップ (install.py 配布)
- **fbx_renamer** — FBX import 由来の骨名 (FBXASC / namespace / 無効文字) を整理 (Script Editor 貼り付け型)

`attach_ctrls` は `maya-hot-update-patterns` 準拠の hot-update 配布。
`fbx_renamer` は shelf 化せず、必要な時だけ GitHub raw をコピペして使う。

## 開発環境

- Maya 2023 / mayapy 3.9.7 / Windows 11
- Repo: https://github.com/ogshaw03/FXtest

## リポジトリ構成

```
FXtest/
├── install.py        # インストーラー (attach_ctrls 用、§5-A テンプレ)
├── attach_ctrls.py   # ツール本体 (v0.1.0)
├── fbx_renamer.py    # Script Editor 貼り付け型ユーティリティ
├── CLAUDE.md
└── .gitignore
```

## エンドユーザー導線

1. https://raw.githubusercontent.com/ogshaw03/FXtest/main/install.py をブラウザで保存
2. Maya 2023 のビューポートにドラッグ
3. シェルフに `AttachCtrl` ボタンが追加される
4. 左クリック → UI 起動
5. 右クリック → `Update from GitHub`

## fbx_renamer (Script Editor 貼り付け型)

https://raw.githubusercontent.com/ogshaw03/FXtest/main/fbx_renamer.py を開いて全文コピー → Maya の Script Editor **Python タブ** に貼り付け → 実行。関数だけ定義されるので、以下を別行で呼ぶ:

```python
remove_all_namespaces()               # namespace 除去
rename_all_joints(dry_run=True)       # rename 予定を確認
rename_all_joints()                   # 本番実行
```

内部処理: namespace 除去 → `FBXASC###` decode → 無効文字を `_` 置換 → 連続 `_` 単一化 → 名前衝突は `_1`, `_2` 回避。日本語 (漢字/ひらがな/カタカナ) は保持。

## attach_ctrls 仕様

- **対象**: 選択された joint (複数可)、MMD 由来 Japanese 骨名 (`左腕` 等) に対応
- **生成物**: `<joint>_ctl` (キューブ NURBS カーブ) + `<joint>_npo` (offset グループ)
- **階層**: 骨階層をミラー、root は `attach_ctrls_grp`
- **側判定**: 左/右 (MMD), L_/R_, _L/_R, left/right → 該当なしは C (黄)
- **カラー**: L=blue(6), R=red(13), C=yellow(17)
- **制約**: parentConstraint (mo=False)
- **UI**: Scale, Constrain checkbox, Attach / Delete generated ボタン
