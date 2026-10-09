# 開発と検証

以下のコマンドはリポジトリのルートで実行します。
利用者向けの機能・操作・承認方法は [README](README.md) を参照してください。

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
ビルド自体はPython標準ライブラリだけで動きます。

```bash
python3 scripts/build_macos.py
open dist/macos/Errand.app
```

生成物にはチャットアプリのソースだけをコピーします。認証情報・設定・作業記録は含めません。
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
```

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

GNOMEではGSettingsを共有し、macOSでは利用者のApplication Support内に設定を保存します。
設定ファイルとCodexのパスはローカル情報なので、リポジトリや配布物へ含めないでください。
新しいスキーマを追加した場合はビルド時にコンパイルし、拡張の設定画面を開き直してください。
