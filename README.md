# personal-notify-tool

個人利用の定期チェック・通知自動化ツールです。
5分間隔でGitHub Actions上で実行され、条件を満たした場合にLINEへ通知します。

監視対象や条件はリポジトリのSecretsで管理しており、コードには含まれていません。

## Secrets

`Settings` → `Secrets and variables` → `Actions` に以下を登録してください。

| Name | 内容 |
|---|---|
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Messaging APIのチャネルアクセストークン(長期) |
| `MONITOR_URLS_JSON` | 監視対象URLのJSON配列(例: `["https://example.com/"]`) |
| `KEYWORDS_JSON` | 監視対象キーワードのJSON配列(例: `["キーワード"]`) |

## 動作確認

Actionsタブから手動実行(`test_notify` をオンにする)で、LINEにテスト通知が届くか確認できます。
