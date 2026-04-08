---
name: github-digest
description: "GitHub リポジトリの最新 Issue/PR アクティビティを取得し、日本語サマリーを Markdown に保存するスキル。「github」「GitHub ダイジェスト」「リポジトリの動向」「issue まとめ」「PR まとめ」「今日の更新」などで発動する。"
allowed-tools:
  - "Bash(uv run python */github-digest/scripts/fetch_github.py*)"
  - "Bash(mkdir -p */github-digest)"
  - Read
  - Write
---

# GitHub リポジトリ・ダイジェスト

監視対象の GitHub リポジトリについて、直近24時間に更新された Issue と PR のアクティビティを取得し、リポジトリごとに日本語で要約して Markdown ファイルとして保存する。

## 設定ファイル

`github-digest/repos.json` に監視対象リポジトリを記載する。

```json
{
  "repos": [
    "owner/repo1",
    "owner/repo2"
  ]
}
```

ファイルが存在しない場合はユーザーに監視対象リポジトリを確認し、作成する。

## ワークフロー

### Step 1: 設定ファイルの確認

`github-digest/repos.json` を読み込む。存在しない場合は、ユーザーに監視したいリポジトリを確認して作成する。

### Step 2: データ取得（リポジトリ並列）

repos.json に含まれる各リポジトリについて **subagent (model: opus) を並列起動** し、それぞれ `fetch_github.py --repo {repo}` を実行して JSON を取得する。各 subagent は1リポジトリ分のデータ取得と JSON パースを担当する。

```
Agent(model=opus, repo="owner/repo1") ──→ JSON1
Agent(model=opus, repo="owner/repo2") ──→ JSON2   ← 並列実行
Agent(model=opus, repo="owner/repo3") ──→ JSON3
```

各 subagent は以下を実行する:

```bash
uv run python <skill-dir>/scripts/fetch_github.py --repo {owner/repo}
```

- 標準出力に JSON が出力される（リポジトリごとにグループ化された Issue/PR データ）
- 各 Issue/PR には以下が含まれる:
  - `number`, `title`, `html_url`, `state`, `type` (issue/pull_request)
  - `body` (全文)
  - `user` (作成者)
  - `labels`
  - `comments` (直近24時間のコメント一覧。各コメントは `user`, `body`(全文), `created_at`)
  - PRの場合: `pr_details` (merged, additions, deletions, changed_files, base_branch, head_branch)
  - PRの場合: `files` (変更ファイル一覧)、`reviews`、`review_comments`
- GitHub API のレート制限に配慮し、リポジトリ間に1秒の待機が入る
- エラーが発生したリポジトリは `error` フィールドに記録される

### Step 3: 日本語サマリー生成（Issue/PR 並列）

各リポジトリ内の Issue/PR について **subagent (model: opus) を並列起動** し、それぞれ1件ずつ日本語サマリーを生成する。

各 subagent は以下を出力する:

1. **タイトル** — 原題をそのまま保持（原語のまま）
2. **概要**（日本語） — 2〜3文でこの Issue/PR が何を扱っているか簡潔にまとめる
3. **直近のアクティビティ**（日本語） — コメント・レビューで誰が何を議論したかの要約。議論のポイント、主要な指摘や提案を具体的に書く
4. **変更ファイル概要**（PRのみ、日本語） — どのファイル/モジュールが影響を受けるか、追加・削除行数
5. **現在のステータスと次のアクション**（日本語） — open/closed/merged の状態と、次に何が必要か

要約を作る際の注意点:
- Issue/PR の body とコメントの内容に忠実に書く。推測や外部知識で補わない
- 技術用語・固有名詞はそのまま使う
- コメントが0件の場合は「直近24時間のコメントなし」と記載する

### Step 4: 校正（Issue/PR 並列）

生成された各サマリーについて **subagent (model: opus) を並列起動** し、校正を行う:
- 日本語の自然さ・可読性
- 事実関係の正確性（元データとの整合）
- 誤字脱字

校正後、全サマリーを統合して用語の表記統一を確認する。

### Step 5: 構成・編集（リポジトリ並列）

各リポジトリのセクションを **subagent (model: opus) で並列生成** する:
- リポジトリごとの傾向（導入文1〜2文）
- 更新日時順に並べ替え（最新の更新が上）

統合後、ハイライトセクション（注目アクティビティ 3〜5件）を作成する。選定基準:
- 活発な議論が行われている（コメント数が多い）
- 大きな機能追加やブレーキングチェンジに関わる
- セキュリティやパフォーマンスに影響する
- マイルストーンやリリースに関連する

> **注: 全ての subagent は `model: "opus"` (Opus 4.6, 1M context) を使用する。**

### Step 6: Markdown ファイルの生成と保存

以下のフォーマットで Markdown ファイルを生成する:

種別アイコンとして GitHub Octicons の SVG を使う:
- PR: `![PR](icons/git-pull-request.svg)` (紫 #8250df)
- Issue: `![Issue](icons/issue-opened.svg)` (緑 #1a7f37)

アイコンファイルは `github-digest/icons/` に配置済み。

```markdown
# GitHub ダイジェスト — YYYY-MM-DD

## 本日のハイライト

1. ![PR](icons/git-pull-request.svg) **[{タイトル(原語)}]({html_url})** ({owner/repo}) — {選定理由(日本語)}
2. ![Issue](icons/issue-opened.svg) **[{タイトル(原語)}]({html_url})** ({owner/repo}) — {選定理由(日本語)}
...

---

## {owner/repo}

> {リポジトリの活動傾向(日本語)}

### ![PR](icons/git-pull-request.svg) [{タイトル(原語)}]({html_url}) `#{number}`
**ステータス:** Open/Closed/Merged | **ラベル:** {labels}

**概要:** {2〜3文の要約(日本語)}

**直近のアクティビティ:** {コメント・レビューの要約(日本語)}

**変更ファイル:** {PRのみ: 変更ファイルの概要(日本語)}

**次のアクション:** {必要な対応(日本語)}

---

### ![Issue](icons/issue-opened.svg) [{次のタイトル(Issue)}](...) `#{number}` ...

## {次のリポジトリ} ...
```

保存先: リポジトリの `github-digest/YYYY-MM-DD.md`
- 同名ファイルが既にある場合は上書きする

### Step 7: 完了報告

生成が完了したら、以下を報告する:
- 保存したファイルパス
- リポジトリごとの Issue/PR 数
- 取得できなかったリポジトリがあればその旨

## エラーハンドリング

- `gh` CLI が認証されていない場合は、`gh auth login` の実行をユーザーに案内する
- 特定のリポジトリの取得に失敗した場合、そのリポジトリをスキップして他を続行する
- アクティビティが0件のリポジトリは「直近24時間の更新はありません」と記載する
- API レート制限に達した場合は、取得できた分までで生成し、残りをスキップした旨を報告する
