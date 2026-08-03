# FXtest attach_ctrls.py 引き継ぎ

**最終更新**: 2026-08-04  **現行**: v0.9.31 (mapping UI 追加、Bug 2 完全解決は未達)

Repo: https://github.com/ogshaw03/FXtest

---

## クイック起動

```bash
"C:/Program Files/Autodesk/Maya2023/bin/mayapy.exe" E:/OG_Tools/FXtest/_test_full_setup.py
```
→ `OVERALL VERDICT: PASS` (24/24) を確認。BEHAV5 補足:

```bash
"C:/Program Files/Autodesk/Maya2023/bin/mayapy.exe" E:/OG_Tools/FXtest/_behav5_diag.py
```
→ `E:/OG_Tools/FXtest/_behav5_result.json` の `verdict: OK`。

---

## ユーザ方針
- **mGear 完全撤去、pure maya.cmds のみ** (v0.9.12 で達成)
- **汎用ツール化**: MMD/plain 両対応。モデル依存の推定は避ける
- **subagent 作業は必ず [mission-control](E:/OG_Tools/Claude_visual/ops.js) 経由**
- **install.py の hot-update 配布** (§5-A テンプレ) 準拠、ユーザは `Update from GitHub` で最新反映
- 発生ドキュメント: プロジェクトルート `HANDOFF.md`、memory `attach-ctrls-current-state`
- **既存 skinCluster の mesh 破壊は最優先で回避**。joint 移動時は `moveJointsMode=1/0` で挟む

---

## 開発環境
- Maya 2023 / mayapy 3.9.7 / Windows 11
- ユーザ Maya scripts folder: `C:/Users/ogush/Documents/maya/2023/scripts/attach_ctrls.py`
  - **編集後は必ず sync**: `cp E:/OG_Tools/FXtest/attach_ctrls.py C:/Users/ogush/Documents/maya/2023/scripts/attach_ctrls.py`
- テスト FBX: `E:/OG_Tools/FXtest/Nekotatune_Path_mode_on.fbx` (274 joints、MMD、.gitignore 済)

---

## 主要 commit 履歴 (v0.9.12 → v0.9.30)

| ver | commit | 内容 |
|---|---|---|
| 0.9.12 | cfbc6bf | mGear 完全撤去、24 統合テスト PASS |
| 0.9.13 | bc8d5ab | **Bug 1 完全解決**: hero joint blend を `parentConstraint mo=True + skipTranslate` に置換 (Maya の orientConstraint は per-target offset なし、parentConstraint はあり)。arm/leg mid_delta 0.386 → 0.0000 |
| 0.9.16 | 251c5c6 | **骨対称化**: `symmetrize_bones_L_to_R()` で L→R mirror (`cmds.mirrorJoint mirrorBehavior=True`)。両肩 rotateX=30 で両腕上がる rig-ready 化 |
| 0.9.19 | 7afc9aa | mesh 破綻対策: symmetrize を moveJointsMode で保護 (mesh vertex max_shift 0.0000) |
| 0.9.20 | 4e0903d | **リバースフット修復**: stretch loop から D-family bone 除外。ball pivot 挙動 42.58→5.12 unit |
| 0.9.21 | ae55dd8 | **Bug 2 X drift ±0.3 unit 以内**: neutralize_leg_bind_bend (knee を hip-ankle 直線に射影) + preferredAngleZ=5° hint。9.80 → 0.26 (37× 改善) |
| 0.9.23 | 005303e | snap 精度: arm IK→FK wrist ズレ 0.69→0.002 unit (300× 改善) |
| 0.9.24 | 6d2fb8a | twist joint auto-drive 配線 (`setup_twist_wiring`) |
| 0.9.28 | 5b3b952 | twist wiring 対象を master のみに (numbered は装飾骨として除外) |
| 0.9.29 | f49eca0 | **ツール専用 twist chain 生成**: `<parent>_tt_<N>_<side>` (既存 `_twist_` 骨 untouched、汎用対応) |
| 0.9.30 | 42b2987 | **tool twist bone を twist のみに応答**: inheritsTransform=0 + wtAddMatrix + decomposeMatrix で node network 位置追従 (ペアレントコンストレイン不使用) |
| 0.9.31 | b4aaa64 | **Chain Mapping UI** 追加。UDE/HIJI/TE 等 命名非標準キャラで user が手動で joint を割当てて確実に rig を組める。Bug 2 dyn PV 検討結果を design comment に残置 (functional 変更なし) |

---

## 現在の rig 挙動 (v0.9.30)

**24 統合テスト全 PASS**、BEHAV5 verdict OK。

| 機能 | 状態 |
|---|---|
| **Bug 1** (FK↔IK drift) | 完全解決、残差 e-05 unit (視覚不可視) |
| **Bug 2** (waist ty=-20 knee X drift) | ±0.3 unit 以内、9.80 → 0.26 (37× 改善) |
| 骨対称化 (両肩 rotateX=30 で両腕上がる) | ✓ mirror behavior |
| リバースフット (heel/ball/tip pivot) | ✓ 地面配置、rotate proper |
| snap arm IK→FK wrist | 0.002 unit (300× 改善) |
| snap leg FK→IK knee | 0.0002 unit (v0.9.29 の副次改善で 3 桁向上) |
| mesh vertex shift | 0.0000 unit (moveJointsMode 保護) |
| twist joint 自動配線 (`arm_L_tt_1/2/3_L` etc.) | ✓ wrist.rotateX × 0.25/0.5/0.75 分配、parent bend 継承なし |
| legD 系削除 | v0.9.18 で無効化 (D bone は "mesh 用衛星骨" で削除すると破綻) |
| Global scale / stretch / volume / Mirror Pose | v0.9.11-12 実装維持 |

---

## Chain Mapping API (v0.9.31)

命名規則が特殊なキャラ (UDE/HIJI/TE、bip01_L_UpperArm 等) でも rig を組めるようにする手動 mapping 機構。

**データ**: `attach_ctrls_grp.mappingJson` に JSON で保存
```json
{
  "fixed":  {"arm_L": ["UDE_L","HIJI_L","TE_L"], "arm_R":[...], "leg_L":[...], "leg_R":[...]},
  "chains": {"spine":["waist","upper_body","chest"], "tail":["tail_1","tail_2",...]}
}
```
- `fixed`: 4 スロット固定 (arm/leg × L/R)、IK/FK rig 対象
- `chains`: 可変長、選択順で登録。現状 API のみ (spline IK 等の消費先は今後)

**API**:
- `get_mapping()` / `set_mapping(dict)` — scene attr との IO
- `auto_detect_mapping()` — 命名 heuristic で fixed をプリセット埋め
- `resolve_chains_for_ikfk(mapping=None)` — mapping → auto-detect の順で解決
- `full_auto_setup(..., mapping=None)` — 明示 override 可
- `setup_all_ik_fk(mapping=None)` — 同上
- `setup_ik_fk(start, mid, end, side, label=None)` — canonical `label` 引数追加。UDE_L を指定しても ctl は `arm_L_IK_ctl` etc で生成される

**UI**: `show_mapping_ui()` (Main UI の "Chain Mapping…" ボタンから起動)
- Fixed section: 12 個の textField + "Pick Sel" ボタン
- Variable section: "+ Add chain" で行追加 → 選択順に "Set from Sel" で流し込み
- "Save to scene" / "Save & Run Full Auto" / "Auto-detect names"

## twist joint 実装 (v0.9.29+30)

**設計思想**:
- 既存 `_twist_` 命名 bone (Nekotatune の arm_twist_1/2/3_L / dummy_/shadow_ etc.) は装飾か実 twist か判定不能 → **一切触らない**
- ツール専用命名 `<parent>_tt_<N>_<side>` の chain を新規作成 → tool が制御
- twist のみに応答 (parent の bend rotation は継承しない)

**node network** (per tool bone):
```
parent.worldMatrix ─┐
                    ├→ wtAddMatrix (w=1-frac / frac) → matrixSum
child.worldMatrix  ─┘         → decomposeMatrix → outputTranslate → tool.translate

wrist.rotateX → multiplyDivide (input2X = idx/(N+1)) → tool.rotateX

tool.inheritsTransform = 0  (parent 継承切断)
```

**API**:
- `setup_twist_wiring(transfer_weights=False)` — full_auto_setup で自動呼出
- `transfer_weights=True` で parent (arm_L/elbow_L) の既存 weight を tool bones に proportional 分配 (per-vertex、86000 verts で ~70s、default OFF)

---

## 残課題 / 次回検討

| 優先 | 項目 | 詳細 |
|---|---|---|
| 高 | **診断ファイル整理** (pending task #2) | `_audit*/_behav*/_diff*/_sym*/_mirror*/_kneebind*/_spring_poc*/_tt_*` 等が untracked 状態。`.gitignore` に追加 or `.diag/` フォルダに移動 |
| 中 | tt_2 (中央 tool bone) の weight 偏り | `_transfer_parent_weight_to_twist` の `max_d` が最後 bone 位置 (0.75) 基準 → 中央区間 [0.333, 0.667] しか tt_2 に配分されない。修正案: `max_d = child 位置 (1.0)` (attach_ctrls.py:2339 付近) |
| 中 | Bug 2 全 depth 完全解決 (現在 ±0.3 unit 残) | **v0.9.31 で simple midpoint PV (hip↔ik_ctl 0.5/0.5 pointConstraint) を検証済**。dy=-35 で 0.63→0.08 に激減する一方 dy=-20 で 0.26→0.43 と ±0.3 spec を逸脱するため未採用。全 depth 解決には compression 依存 blend (condition + multiplyDivide) + キャラ別 tuning が必須。attach_ctrls.py:1084 のコメントに設計メモ残置 |
| 中 | leg chain jointOrient cleanup | ユーザ提案の `cmds.joint(oj="xyz")` は FK ctl rotateX が twist 化して破綻 (v0.9.22 revert)。FK ctl 側 rotate axis convention 見直しが先 |
| 低 | tool bone 命名重複 | `arm_L_tt_1_L` (prefix suffix 両方に _L)、動作影響なし視認性のみ |
| 低 | arm_R Bug 1 残差非対称 | leg_L/R = e-05、arm_R = 1.4e-05 で 30× 差。symmetrize 由来、閾値内 |
| 低 | 自作 popupMenu (RMENU scout 設計案) | ユーザ「無くなる」了承済のため優先度低 |

---

## 管制室 (mission-control)

- サーバー: `http://localhost:5173/` (常時起動)
- ops.js: `E:/OG_Tools/Claude_visual/ops.js`
- **subagent 起動は必ず ops.js 経由** (feedback_use_mission_control)
- パターン:
  ```bash
  node "E:/OG_Tools/Claude_visual/ops.js" start --session <name> --label "..." --task "..." --force
  node "E:/OG_Tools/Claude_visual/ops.js" spawn scout-X --name "SHORT" --task "..." --session <name>
  # Agent tool で scout 実際に起動、run_in_background=true
  ```

---

## 主要 test / diag スクリプト

| ファイル | 用途 |
|---|---|
| `_test_full_setup.py` | 24 統合テスト。full_auto_setup 実行、全機能 PASS/FAIL 判定 |
| `_behav5_diag.py` | Bug 1/2 + snap + mirror + revfoot 挙動測定、`_behav5_result.json` 書出 |
| scratchpad `_twist_*.py` | v0.9.24-30 twist 関連の PoC/検証 |
| scratchpad `_snap_*.py` | v0.9.23 snap 精度検証 |

---

## 引き継ぎ prompt テンプレ

新セッションで作業再開するときの推奨 prompt (これをそのまま貼る):

```
attach_ctrls.py の作業を再開します。

E:/OG_Tools/FXtest/HANDOFF.md と memory の attach-ctrls-current-state を
読んで現状を把握してください。

最新 v0.9.30 (commit 42b2987、push 済) の状態で、以下の残課題があります:
1. 診断ファイル整理 (pending)
2. tool twist bone の weight 中央偏り修正
3. Bug 2 全 depth 完全解決 (dynamic PV)
その他は HANDOFF.md 参照。

今回は [具体的な作業内容] をお願いします。

編集後は必ず C:/Users/ogush/Documents/maya/2023/scripts/attach_ctrls.py
にも sync してください (mayapy 起動時こちらが読まれる)。

subagent が必要な作業は必ず mission-control (ops.js) 経由で立ち上げて
ください。
```

---

## 参照

- `install.py` — hot-update installer (§5-A テンプレ)
- `fbx_renamer.py` — FBX/MMD 名 clean
- 元 rig 参照: `E:/OG_Tools/FXtest/Spiderman.ma` (mGear、参考のみ、準拠しない)
