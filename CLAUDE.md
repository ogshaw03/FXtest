# FXtest — Maya 2023 リギング/エフェクトツール

現在のツール: **attach_ctrls** (既存 skinned joint に mGear 風コントローラを一括セットアップ)

配布は `maya-hot-update-patterns` 準拠 (install.py ドラッグ → shelf → GitHub 更新)。

## 開発環境

- Maya 2023 / mayapy 3.9.7 / Windows 11
- Repo: https://github.com/ogshaw03/FXtest

## リポジトリ構成

```
FXtest/
├── install.py        # インストーラー (§5-A テンプレ)
├── attach_ctrls.py   # ツール本体 (v0.1.0)
├── CLAUDE.md
└── .gitignore
```

## エンドユーザー導線

1. https://raw.githubusercontent.com/ogshaw03/FXtest/main/install.py をブラウザで保存
2. Maya 2023 のビューポートにドラッグ
3. シェルフに `AttachCtrl` ボタンが追加される
4. 左クリック → UI 起動
5. 右クリック → `Update from GitHub`

## ツール仕様

- **対象**: 選択された joint (複数可)、MMD 由来 Japanese 骨名 (`左腕` 等) に対応
- **生成物**: `<joint>_ctl` (キューブ NURBS カーブ) + `<joint>_npo` (offset グループ)
- **階層**: 骨階層をミラー、root は `attach_ctrls_grp`
- **側判定**: 左/右 (MMD), L_/R_, _L/_R, left/right → 該当なしは C (黄)
- **カラー**: L=blue(6), R=red(13), C=yellow(17)
- **制約**: parentConstraint (mo=False)
- **UI**: Scale, Constrain checkbox, Attach / Delete generated ボタン
