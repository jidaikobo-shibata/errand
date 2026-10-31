# 開発と検証

以下のコマンドはリポジトリのルートで実行します。
利用者向けの機能・操作・承認方法は [README](README.md) を参照してください。

## 共通チャット基盤への移行

設計と移行手順は [.codex/shared-chat-design.md](.codex/shared-chat-design.md) にあります。
製品名・案内・指示・設定保存先は `errand/product.py` の定義から渡します。
Errandの既存入口と保存先は維持します。現在は移行初期で、会話制御・ランタイム等には
Errand固有の前提が残っています。製品別に共通処理をコピーせず、段階的に分離してください。

Markdown・数式・MCPフォームの本体は `chat_foundation/core` と `chat_foundation/gtk` に
あります。`errand` 側の同名モジュールは互換エイリアスです。共通部品の修正は本体へ入れ、
製品固有のimportを追加しないでください。共通コードを追加した配布物では、このパッケージも
必ず収録します。会話・ログイン・準備の生成は `errand/services.py` で組み立てます。

Codexの会話制御・ログインの本体は `chat_foundation/codex` にあります。
共通Sessionには指示文・要約指示・接続情報を明示的に渡し、接続には検証用のcommand、
またはCodex実行ファイルの探索関数を渡します。共通Loginにも接続情報と接続方法を渡します。
共通側は製品の保存先や指示を探しません。`errand/session.py` と `errand/login.py` は
従来の呼び出しにErrandの既定値を補う薄い層です。`errand/protocol.py` は実行ファイル探索と
共通通信の再公開を担い、取得・配置・manifestは引き続きErrandの配布処理で管理します。

JSONLの双方向プロセス通信・子プロセス終了は`chat_foundation/core/process_rpc.py`へ分離しました。
CodexのAppServerは薄い互換層で、JSON-RPCバージョンフィールドを付けない従来の通信を維持します。
ACPでは同じ通信に`jsonrpc: "2.0"`と専用の環境を渡します。共通層へエージェント名・製品設定を
探す処理を追加しないでください。

ACP v1の会話・モデル・進捗・今回限りの承認・中断・要約は`chat_foundation/acp/session.py`です。
起動関数からcommand/cwd/environment、任意の認証要求・接続情報・モデル除外を渡します。
モデル選択がないエージェントは既定のモデルだけで使い、モードの強制は製品側から指定します。
依頼文をCLIのスラッシュ・シェルコマンドへ変換しません。未知の要求や常時許可しかない要求は
拒否し、中断・終了では保留中の確認をキャンセルします。ファイル参照のURI変換も注入できます。

Gemini CLIの探索・一時設定・キー取得・MCP設定の選択は`errand/gemini_cli.py`にあります。
APIキーは`errand/secret_store.py`で製品IDごとにOSの保管庫から取得し、未保存なら環境変数・
指定ファイル・既定ファイルへフォールバックします。保管庫がロックされた場合は停止します。
Ubuntuはlibsecret GI、macOSは`macos_secret_store.py`のSecurity.framework（ctypes）を利用し、
キーを引数に渡すプロセスや平文ファイルは使いません。OSアクセスはGUIスレッド外で行います。
APIキーはACPの認証要求で渡し、子プロセス環境や設定ファイルへ保存しません。
CLIの起動・再接続ごとに新規runtimeを作り、workとprofileを兄弟ディレクトリに分離します。
以前のworkをcwdに再利用せず、.gemini/.envや.gemini/settings.jsonの持ち越しを防ぎます。
新しいworkには空の.envを作り、親ディレクトリのdotenv探索も遮断します。
旧workの設定や中間ファイルを新workへコピーしないでください。
`scripts/gemini_security_smoke.py`は実CLIのfetchをループバック疑似APIへ接続し、
ネットワーク層で外部接続を拒否して、植え込んだ設定の非継承と明示MCPの動作を確認します。
中断は依頼IDを捕捉し、古い中断処理が次の依頼・プロセスを止めないようにします。
既存のCLIホームを上書きせず、タブ専用ホーム・既定承認モード・非恒久的な許可を使います。
CLIのOSサンドボックスをCodexと同じものとして扱わないでください。
共通画面は`agent/permission`を表示し、エージェント別の分岐を持ちません。
approval_modes指定時はCLI固有の承認方法を選択します。任意のinitial_approval_modeと
save_approval_modeを注入すると製品側の保存済み値・保存処理を使えます。Geminiでは
gemini-approval-modeへ保存し、Codexのapproval-reviewerから分離します。要約には引き継ぎません。supports_auto_review=Falseは
Codexの保存済み承認設定を引き継がず、CLI固有モード未指定の場合は手動承認に固定します。
supports_file_references=Trueならsendのfiles引数へ絶対パスを渡し、従来のCodex送信は維持します。

```bash
python3 -m unittest discover -s tests -p 'test_acp.py' -v
env GSETTINGS_BACKEND=memory python3 scripts/acp_smoke.py
python3 scripts/acp_native_smoke.py
```

ACP画面検証も独立Wayland・D-Bus・一時HOME/設定で行います。native_smokeはインストール済み
Gemini CLIを使いますが、ダミーキー・一時ファイル・ループバックの疑似APIのみを使い、
Nodeの外部ネットワーク接続を遮断します。CLI本体やOSの設定を変更しません。
この検証は実モデルの応答速度・品質や実MCP接続を確認するものではありません。
保管庫の単体テストはモック/isolatedメモリを使い、通常のキーリングに触れません。
実libsecretを検証する場合は別D-Bus・一時HOME/設定/データと専用キーリングでダミーキーを使います。
既存ユーザーの保管庫や実キーを自動テストに使わないでください。

ErrandのGemini接続はCLI/ACPに一本化しています。旧設定`gemini`は`gemini-cli`へ正規化します。
`chat_foundation/gemini`のAPI直接接続は共通基盤の再利用用に残し、Errandからは選択しません。
キー読み込みは両方式で共有します。製品は `errand/services.py` の `AIServices` で接続先を選び、キー設定ファイルの場所・
指示を注入します。共通コードは製品・GTK・Codex実装に依存しません。
画面は任意の `assistant_name`、`notice`、`supports_files`、`supports_approvals`、
`supports_reasoning` 属性を参照します。未指定なら従来のCodex表示・機能を維持します。
CLIのモデル一覧はCLIから取得します。共通基盤のAPI直接接続のみローカル定義です。
実キーの読み出しやGoogleへの送信をテストに含めないでください。
共通基盤のAPI直接接続のtransportは本文チャンクのコールバックを受け取り、SSEで途中の文章を渡します。
完了前に切れた応答は履歴に加えません。共有イベントの`user`に任意の`started_at`
（time.monotonicの秒）を追加しました。既存ホストで未指定なら画面の受信時刻を使います。
回答時間の計算は`chat_foundation/core/timing.py`、表示は共通会話画面が担当します。
`scripts/message_copy_smoke.py`と`scripts/acp_smoke.py`で、時間表示・本文コピーからの
除外・途中表示・要約との分離を確認します。
共通基盤のAPI直接接続のTCP接続は`chat_foundation/gemini/network.py`で候補を200ms間隔で試し、
最初に成功した接続だけを使います。IPv4・IPv6の候補を交互にし、保留中の候補は最大6個、
全体の接続待ち時間は呼び出し側のtimeoutで制限します。TLS・プロキシ・HTTP処理は
urllib/http.clientを維持し、ソケットの差し替えはそのHTTPS接続インスタンス内に限定します。
socket.create_connectionやOSのアドレス優先順位を変更しないでください。
接続の競争中はHTTP本文や認証情報を送らず、APIリクエストを重複送信しません。
`tests/test_gemini_network.py`はネットワークなしで遅い候補・候補全滅・資源解放を検証します。

Geminiの接続と状態管理は次で検証します。GUI検証は独立Wayland・D-Busセッションと
一時HOME・設定で行い、`GEMINI_API_KEY` と `GOOGLE_API_KEY` は引き継がないでください。

```bash
python3 -m unittest discover -s tests -p 'test_gemini.py' -v
env GSETTINGS_BACKEND=memory python3 scripts/acp_smoke.py
```

接続本体のテストで差し替える対象は `chat_foundation.codex` 側です。
`tests/test_shared_codex.py` は別プロセスでErrandとGTKを読み込まないことも検証します。

会話画面の本体は `chat_foundation/gtk/conversation.py` にあります。
製品側の画面から共通の `Conversation` を配置し、ホストを通してサービス、設定、
案内、定番依頼のメニュー生成を渡します。ホストとownerに必要な項目は同モジュールの
冒頭、会話操作の契約は `chat_foundation/core/controller.py` を参照してください。
Errandのウィンドウ・起動・OS連携は引き続き `errand/ui.py` が担当します。

製品構成の画面検証は、他のGUI検証と同様に独立したWayland・一時設定で実行します。
実モデルやMCPには接続しません。

```bash
env GSETTINGS_BACKEND=memory python3 scripts/product_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/foundation_view_smoke.py
```

## GNOME拡張のビルド

Python 3と `glib-compile-schemas` が必要です。
配布ZIPを生成します。インストールや有効化は自動で行いません。

```bash
python3 scripts/build.py
```

生成先は `dist/errand@jidaikobo.shibata.zip` です。
既存の開発用チェックアウトを上書きしないよう、開発中はそのディレクトリへ
ZIPを再インストールしないでください。

## macOSのアプリ生成と実機検証

Codex CLIがインストール・ログイン済みのMacで、Homebrewの実行環境を使います。
Homebrewが未導入の場合は、公式の手順で導入してください。
この節の開発用ソース版はGTK 4・libadwaita・PyGObjectと、その依存のPyCairoなどを同梱しません。
それぞれのライセンス条件が適用されます。

```bash
brew install gtk4 libadwaita pygobject3
```

リポジトリのルートで実行すると、Finderから開けるアプリを生成できます。
ビルドにはPython標準ライブラリとmacOSのCommand Line Tools（clang）を使います。

```bash
python3 scripts/build_macos.py
open dist/macos/Errand.app
```

生成物にはチャットアプリのソースと小さなmacOS用ランチャーを入れます。
ランチャーはErrandのアプリ識別を保ったままHomebrewのPythonを読み込みます。
認証情報・設定・作業記録は含めません。
生成後は元のリポジトリを移動しても起動できますが、Homebrewの実行環境は引き続き必要です。
Apple SiliconとIntelの標準Homebrew配置から、GTKを使えるPythonを探します。
CodexもHomebrewの標準配置やnvmから探します。
見つからない場合はTerminalから明示できます。

```bash
dist/macos/Errand.app/Contents/MacOS/Errand \
  --codex /absolute/path/to/codex
```

更新時は別の出力先へ生成してください。既存のアプリは上書きしません。

```bash
python3 scripts/build_macos.py --output dist/macos-next
```

macOSではCtrlに加えて⌘+Enterで送信、⌘+Tでタブ作成、⌘+Wでタブを閉じ、
⌘+Qまたはアプリメニューから終了できます。ウィンドウを閉じると隠れます。
Dockから再度呼び出して開けます。メニューバー常駐は未実装です。起動キーはmacOSのCarbon APIで登録し、Errandが起動中の間だけ使えます。

これは署名・公証済みの配布版ではありません。
Apple SiliconのMacで起動・実モデル応答・GTK操作を確認しています。
最初にFinderからの単一・複数ファイルのドロップ、日本語ファイル名、日本語入力、
コピー、Dockからの再表示、起動キーの登録・競合・再表示、終了と子プロセスの停止を確認してください。

## macOSの実行環境同梱ZIP

利用者がターミナルを使わず導入するための試用版です。macOS 15以降を対象にします。
ビルドにはmacOS、出力するCPU向けのGTK実行環境とPython、Cコンパイラが必要です。
Apple SiliconでもRosettaとIntel向け依存を揃えればIntel版を生成できます。
Apple Silicon用とIntel用のHomebrew・Python・GTKを混在させず、ビルドプロセスをIntelモードで起動します。
受け取る側にはこれらを追加インストールさせません。

ビルド用のPyInstaller・Meson・Ninjaは専用仮想環境に導入します。
GTKのPythonバインディングを使うため `--system-site-packages` を指定します。

```bash
brew install gtk4 libadwaita pygobject3 python@3.14
python3 -m venv --system-site-packages dist/build-env
dist/build-env/bin/python -m pip install \
  --only-binary=:all: -r scripts/build-requirements.txt
dist/build-env/bin/python scripts/build_macos_release.py
```

PythonがHomebrew以外を指す場合は、HomebrewのGIを使えるPython 3.14で仮想環境を生成してください。
Apple Siliconでは `dist/distribution/arm64/Errand-Trial-AppleSilicon.zip`、
Intelでは `dist/distribution/x86_64/Errand-Trial-Intel.zip` を生成します。
両CPUで生成すると、配布対象は次の4ファイルになります。それぞれにSHA-256ファイルも生成します。

| CPU | 試用アプリ | 対応ソース |
| --- | --- | --- |
| Apple Silicon | `Errand-Trial-AppleSilicon.zip` | `Errand-Sources-AppleSilicon.zip` |
| Intel | `Errand-Trial-Intel.zip` | `Errand-Sources-Intel.zip` |

既存の出力先は上書きせず拒否します。更新時には `--output` で別の出力先を指定してください。

試用ZIPはGTK・Python、導入用HTML、ライセンスを含みます。
Slack向けの案内文は配布者用にリポジトリへ残し、試用ZIPには含めません。
Codexは初回の「動作環境を準備する」で必要な場合だけ取得します。
公式パッケージのバージョン・CPU・URL・サイズ・SHA-256を `errand/runtime_manifest.json` に固定します。
既存のCodexはapp-server接続で確認して再利用し、取得したものはErrand専用フォルダーに保存します。
macOSの証明書でHTTPSを検証し、取得先の制限・サイズとSHA-256の照合・安全な展開・接続検証後に公開します。
共有のCodex設定、認証、PATH、Homebrewには変更を加えません。

対応ソースは `Errand-Sources-AppleSilicon.zip` または `Errand-Sources-Intel.zip` に分けます。
配布時には試用ZIPと同じ投稿・場所から追加料金なしで取得できるようにしてください。
Slackへは試用ZIPを添付し、対応ソースを別の保存先に置いて取得リンクを同じ投稿へ載せる方法も使えます。
受け取った人が対応ソースへアクセスできる権限と、配布したバージョンのソースを維持してください。
Homebrewの各ライブラリの実際のバージョンとレシピからソースURL・SHA-256を取り、
検証したアーカイブを同梱します。個人のCodex認証、設定、作業記録、他アプリのアイコンはコピーしません。
Cairoのスクリプト描画ライブラリはLZOを無効にしてビルドし、不要なGPL圧縮依存を外します。
Errand本体のMIT Licenseは維持し、依存のライセンス・通知・ソースは分けて収録します。

PyInstallerでCPUごとの実行環境を生成し、ローカルのad-hoc署名と構造検証を行います。
Developer IDでの署名とAppleによる公証は行いません。
Finderでコピー・起動し、ダウンロード後の初回警告を含めて別のMacでも検証してください。

Homebrewなしの起動は、ビルドしたバンドルを別の場所へ移して確認し、
`sandbox-exec` で `/opt/homebrew` と `/usr/local` の読み取りを拒否して検証できます。
通常の `--smoke-test` は画面を表示して終了し、モデルへの依頼は送りません。

`.github/workflows/macos-trial.yml` はGitHub ActionsのmacOS 15 Apple Silicon・Intel環境で
同じ4ファイルを作る手動実行専用のワークフローです。
pushでは実行せず、Release公開もしません。
成功したCPUのZIPとチェックサムを成果物からダウンロードできます。
IntelのGTK依存は環境によってソースビルドになり、ビルド時間が長くなる場合があります。

## テストと変更の反映

モデルに接続しない自動テスト:

```bash
python3 -m unittest discover -s tests -v
```

独立したGTK画面と疑似サーバーを使う操作検証:

```bash
env GSETTINGS_BACKEND=memory python3 scripts/ui_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/setup_ui_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/file_drop_smoke.py
```

ファイルの検証はGTKのURI変換と実際の添付表示を通します。macOSではFoundationを使い、
GTK 4.22がファイルURIのコロンを誤ってエスケープする不具合も再現します。
日本語・空白・記号を含む名前、重複、無効なURIの拒否を確認し、モデルには接続しません。

最終バンドルの公式取得・チェックサム・接続確認（新規の検証先を指定）:

```bash
dist/distribution/arm64/Errand-Trial-AppleSilicon/Errand.app/Contents/MacOS/Errand \
  --runtime-check-root dist/runtime-check
```

この検証はCodexを実際に取得します。モデルへの依頼は送らず、認証情報をコピーしません。
`--runtime-check-arch x86_64` でIntel用の取得と、RosettaがあるMacでの接続を検証できます。
Rosettaでの接続確認だけではIntel用GTKアプリの実機確認を代替できません。
初回準備の起動確認は、Rosettaで新しい実行ファイルの変換に時間がかかる場合も考慮し、
最大180秒待ちます。準備の中止では待機を打ち切り、子プロセスを終了させます。

ショートカット設定画面の検証:

```bash
env GSETTINGS_BACKEND=memory gjs -m scripts/shortcut_smoke.js
```

Pythonの変更はアプリ終了・再起動で反映します。
拡張本体や設定画面の変更は、Waylandではログアウト・ログインで反映してください。

プロトコルの詳細は [Codex App Serverドキュメント](https://learn.chatgpt.com/docs/app-server)、
GNOME拡張については [GJSガイド](https://gjs.guide/extensions/) を参照してください。

## 環境設定の検証

実際の設定を変更しないGTK操作検証:

```bash
glib-compile-schemas --strict schemas
env GSETTINGS_BACKEND=memory python3 scripts/preferences_smoke.py
```

定番のお願いの保存・呼び出し・編集・削除確認は、独立したWayland環境で実行してください。

```bash
env GSETTINGS_BACKEND=memory python3 scripts/bookmarks_smoke.py
```

MCPからのフォーム・URL確認と承認方法の記憶も、独立したWayland環境で検証できます。
実MCP・ブラウザ・モデルへの接続は行いません。

```bash
env GSETTINGS_BACKEND=memory python3 scripts/elicitation_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/approval_settings_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/message_copy_smoke.py
env GSETTINGS_BACKEND=memory python3 scripts/summary_copy_smoke.py
```

終了処理の例外がほかの後始末を妨げないことは、画面を表示せず検証できます。

```bash
python3 scripts/application_shutdown_smoke.py
```

ショートカットの変更・無効化・既定値への復帰と重複検出の検証:

```bash
env GSETTINGS_BACKEND=memory python3 scripts/shortcuts_smoke.py
```

GNOMEではGSettingsを共有し、macOSでは利用者のApplication Support内に設定を保存します。
設定ファイルとCodexのパスはローカル情報なので、リポジトリや配布物へ含めないでください。
新しいスキーマを追加した場合はビルド時にコンパイルし、拡張の設定画面を開き直してください。

### 終了・中断・初期化の回帰検証

`tests/test_acp.py`は別Pythonプロセスを終了させ、SIGTERMを無視する子プロセスまで
停止することをLinuxの/procで確認します。GUIのclose(wait=False)を通る経路でも、
クリーンアップは非daemonスレッドとし、終了前にプロセス群を停止します。
`tests/test_session.py`は遅延した中断を次のCodex依頼へ適用しないことを検証します。
`tests/test_secret_store.py`は初期化対象のシンボリックリンク・ハードリンクで領域外を
変更せず、作業ディレクトリ自体のリンクは拒否することを確認します。

入力履歴の実フォーカスを必要としない検証は`prompt_history_smoke.py --logic-only`で実行できます。
これは履歴の遷移・下書き保持の検証であり、実フォーカスやキー入力の成功を意味しません。
フラグなしの検証は引き続きウィンドウのアクティブ状態と入力フォーカスを確認します。
