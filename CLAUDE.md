# FXtest — Maya 2023 エフェクト開発

Maya 2023 用のエフェクト（VFX）を作成するプロジェクト。

## 開発環境

- **Maya**: 2023 (`C:\Program Files\Autodesk\Maya2023`)
- **mayapy**: Python 3.9.7 (`C:\Program Files\Autodesk\Maya2023\bin\mayapy.exe`)
- **PyMEL**: Maya 2023 用インストール済み
- **System Python**: 3.10 (補助スクリプト用)
- **OS**: Windows 11 / PowerShell
- **リポジトリ**: https://github.com/ogshaw03/FXtest

## スクリプト実行

Maya の Python 環境で実行する場合:

```bash
"/c/Program Files/Autodesk/Maya2023/bin/mayapy.exe" script.py
```

Maya 内 Script Editor で実行する場合は、Python タブに直接貼り付けるか、`execfile()` / `import` を使用。

## コーディング規約

- Maya API は原則 `maya.cmds`（コマンド）を使用。ノード操作でパフォーマンスが必要な場合のみ `maya.api.OpenMaya`（API 2.0）を使う。
- PyMEL は必要な場合のみ（起動が遅くなるため）。
- エフェクト系（パーティクル/nParticle/Bifrost/MASH 等）で使用したモジュールは各スクリプト冒頭にコメントで明示。

## ディレクトリ構成（想定）

```
FXtest/
├── scripts/     # Python スクリプト（生成・セットアップ）
├── scenes/      # Maya シーンファイル (.ma / .mb)
├── shaders/     # シェーダー・テクスチャ
└── refs/        # 参考資料
```

必要になったタイミングで作成。
