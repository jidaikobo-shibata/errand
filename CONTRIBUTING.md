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
追加の依存はGTK 4・libadwaita・PyGObjectと、その依存のPyCairoなどです。
それぞれのライセンス条件が適用されます。このアプリには同梱しません。

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

これは署名・公証済みの配布版ではありません。仲間のMacでソースから生成する検証を想定しています。
GTKのmacOS対応を利用しますが、ErrandとしてのmacOS実機動作はまだ確認していません。
最初にFinderからの単一・複数ファイルのドロップ、日本語ファイル名、日本語入力、
コピー、Dockからの再表示、起動キーの登録・競合・再表示、終了と子プロセスの停止を確認してください。

## テストと変更の反映

モデルに接続しない自動テスト:

```bash
python3 -m unittest discover -s tests -v
```

独立したGTK画面と疑似サーバーを使う操作検証:

```bash
env GSETTINGS_BACKEND=memory python3 scripts/ui_smoke.py
```

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
env GSETTINGS_BACKEND=memory python3 scripts/preferences_smoke.py
```

GNOMEではGSettingsを共有し、macOSでは利用者のApplication Support内に設定を保存します。
設定ファイルとCodexのパスはローカル情報なので、リポジトリや配布物へ含めないでください。
新しいスキーマを追加した場合はビルド時にコンパイルし、拡張の設定画面を開き直してください。
