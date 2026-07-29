# FXtest — Maya 2023 エフェクト開発

Maya 2023 用のエフェクト（VFX）を作成するプロジェクト。
配布/更新は「install.py を Maya にドラッグ → シェルフ追加 → 以後 UI or シェルフ右クリックから GitHub 最新版に更新」パターン (`maya-hot-update-patterns`) を採用。

## 開発環境

- **Maya**: 2023 (`C:\Program Files\Autodesk\Maya2023`)
- **mayapy**: Python 3.9.7
- **PyMEL**: Maya 2023 用インストール済み
- **System Python**: 3.10（補助スクリプト用）
- **OS**: Windows 11 / PowerShell
- **リポジトリ**: https://github.com/ogshaw03/FXtest

## リポジトリ構成

```
FXtest/
├── install.py       # 配布用インストーラー (§5-A テンプレ準拠)
├── toon_fire.py     # ツール本体 (§5-B テンプレ + セルルック炎ロジック)
├── CLAUDE.md
└── .gitignore
```

**単一ツール構成**（`_MODULE = "toon_fire"`）。将来ツールが増える場合は `_MODULE` を変えた別リポにするか、テンプレ §6-5 に従ってパッケージ化する。

## エンドユーザー導線

1. GitHub raw の [install.py](install.py) をブラウザで保存
2. Maya 2023 のビューポートにドラッグ
3. シェルフに `ToonFire` ボタンが追加される
4. **左クリック** → UI 起動（Height/Radius/Speed → Create Fire）
5. **右クリック** → `Update from GitHub` / UI 内の `GitHub から更新` ボタン

## 開発フロー

1. `toon_fire.py` を編集
2. `__version__` を bump（例: `0.1.0` → `0.1.1`）
3. commit & push
4. Maya 内で `GitHub から更新` を押す → 再起動なしで反映

### ローカル iteration（GitHub push 前に試す）

環境変数 `TOON_FIRE_USE_LOCAL=1` を立てて Maya を起動すれば、`install.py` は GitHub でなくローカルファイルからコピーする（テンプレ §5-A `_fetch_module` の分岐）。

## コーディング規約

- Maya API は原則 `maya.cmds`。パフォーマンスが必要な場合のみ `maya.api.OpenMaya`（API 2.0）。
- PyMEL は必要な場合のみ（起動が遅くなるため）。
- 配布インフラ部分（`install.py`, `toon_fire.py` の Update 関連関数と `show()` の外枠）は `maya-hot-update-patterns` §7 rule 4 に従い**触らない**。
- 機能追加は原則 `_build_body()` と `# Fire effect generation` セクション内に閉じる。

## §8 オプション拡張について

`maya-hot-update-patterns` §8 の以下は **明示的な依頼があるまで実装しない**:
- バージョンごとのロールバック（tag / releases / versions フォルダ）
- 新版通知ダイアログ（起動時チェック / 定期チェック）
- Update available バッジ、Changelog 表示、無視バージョン記憶 等

## 参照

- 配布パターンの原典: `~/Downloads/maya-hot-update-patterns.md`（社内ナレッジ、リポには含めない）
