---
name: github-digest
description: "GitHub リポジトリの最新 Issue/PR アクティビティを取得し、日本語サマリーを Markdown に保存するスキル。「github」「GitHub ダイジェスト」「リポジトリの動向」「issue まとめ」「PR まとめ」「今日の更新」などで発動する。"
allowed-tools:
  - "Bash(uv run python */github-digest/scripts/fetch_github.py*)"
  - "Bash(mkdir -p */github-digest)"
  - Read
  - Write
  - Agent
---

# GitHub リポジトリ・ダイジェスト

監視対象の GitHub リポジトリについて、直近24時間に更新された Issue（最新50件）と PR（最新50件）のアクティビティを取得し、リポジトリごとに日本語で要約して Markdown ファイルとして保存する。

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

repos.json に含まれる各リポジトリについて **subagent (model: opus) を並列起動** し、それぞれ `fetch_github.py --repo {repo}` を実行して JSON を取得する。

```
Agent(model=opus, repo="owner/repo1") ──→ JSON1
Agent(model=opus, repo="owner/repo2") ──→ JSON2   ← 並列実行
Agent(model=opus, repo="owner/repo3") ──→ JSON3
```

> **全ての subagent は `model: "opus"` を指定すること。**

各 subagent は以下を実行する:

```bash
uv run python <skill-dir>/scripts/fetch_github.py --repo {owner/repo}
```

- リポジトリあたり Issue 最大50件、PR 最大50件を取得（updated_at 降順）
- 各 Issue/PR には title, body, comments, labels, state が含まれる
- PR にはさらに pr_details (merged, additions, deletions), files, reviews が含まれる
- エラーが発生したリポジトリは `error` フィールドに記録される

### Step 3: サマリー生成・構成・保存

全リポジトリの JSON データを統合し、以下を一括で生成する:

1. **各 Issue/PR の日本語サマリー** — 以下の項目を含む:
   - タイトル（原語のまま）
   - 概要（2〜3文、日本語）
   - 直近のアクティビティ（コメント・レビューのやり取り要約）
   - 変更ファイル概要（PR のみ）
   - ステータスと次のアクション

2. **リポジトリごとの傾向** — 導入文1〜2文

3. **ハイライトセクション** — 全リポジトリから注目アクティビティを3〜5件選定。選定基準:
   - 活発な議論（コメント数が多い）
   - 大きな機能追加やブレーキングチェンジ
   - セキュリティやパフォーマンスへの影響
   - マイルストーンやリリースに関連

要約の注意点:
- body とコメントの内容に忠実に書く。推測や外部知識で補わない
- 技術用語・固有名詞はそのまま使う
- コメントが0件の場合は「直近24時間のコメントなし」と記載

### Step 4: Markdown ファイルの保存

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

### ![Issue](icons/issue-opened.svg) [{タイトル(原語)}]({html_url}) `#{number}`
**ステータス:** Open/Closed | **ラベル:** {labels}

**概要:** {2〜3文の要約(日本語)}

**直近のアクティビティ:** {コメント・レビューの要約(日本語)}

**次のアクション:** {必要な対応(日本語)}

---

## {次のリポジトリ} ...
```

保存先: `github-digest/YYYY-MM-DD.md`
- 同名ファイルが既にある場合は上書きする

### Step 5: 完了報告

生成が完了したら、以下を報告する:
- 保存したファイルパス
- リポジトリごとの Issue/PR 数
- 取得できなかったリポジトリがあればその旨

## エラーハンドリング

- `gh` CLI が認証されていない場合は、`gh auth login` の実行をユーザーに案内する
- 特定のリポジトリの取得に失敗した場合、そのリポジトリをスキップして他を続行する
- アクティビティが0件のリポジトリは「直近24時間の更新はありません」と記載する
- API レート制限に達した場合は、取得できた分までで生成し、残りをスキップした旨を報告する
