# 日语学习助手

一个帮助零基础学日语的 macOS 应用：从五十音开始，按遗忘曲线记单词，学入门语法，再和 AI 老师用日语对话、练发音。

| 模块 | 内容 |
| --- | --- |
| 今日 | 连续学习天数、待复习单词、学习路线和最近 30 天的练习记录 |
| 假名 | 平假名 / 片假名五十音图（清音、浊音、拗音），点击发音；选行测验，错得多的假名出现得更频繁 |
| 单词 | JLPT N5–N1 约 8000 词，按简化 SM-2 安排复习，可选级别和每天新词数；英文释义由 AI 翻成中文并缓存；AI 例句；对话和课程里的词一键加入；支持导入 CSV / Anki 词表 |
| 语法 | N5–N3 共 91 课的大纲，打开一课时 AI 编写讲解、例句、易错点、练习和朗读句，保存在本机可离线复习；可以继续向 AI 老师追问 |
| AI 老师 | 自由聊天或情景对话（自我介绍、点餐、购物、问路、旅行），回复带注音、罗马音、翻译、纠错和下一句提示；句子拆解逐词讲解 |
| 发音 | 句子来自入门句、已学课程和单词例句；听示范、录下自己的朗读，音频模型打分并指出具体的音 |

朗读用系统自带的日语语音（macOS 的 Kyoko 等），不消耗模型调用。学习进度、翻译过的释义和生成的课程都保存在本机 SQLite。

## 学习内容从哪里来

除了假名表，学习内容都是数据而不是代码：

- **单词**：`src/nihongo/data/jlpt_vocab.tsv`，来自开源的 [open-anki-jlpt-decks](https://github.com/jamsinclair/open-anki-jlpt-decks)（MIT，见 [docs/THIRD_PARTY.md](docs/THIRD_PARTY.md)）。另有 120 个手工整理了中文释义的入门词排在最前面。词表释义是英文，复习时由模型批量翻成中文后存进数据库，同一个词只翻一次；没有配置模型时显示英文。
- **语法**：`src/nihongo/data/grammar_syllabus.json` 只有课程标题和顺序。课程正文第一次打开时由模型生成并缓存，「重新生成」可以换一个版本。其中 12 课有手写的离线内容（`grammar_lessons.json`）。
- **自己的词**：在「单词本」里手动添加或批量导入，导入的词最先学习。

更新词表或大纲只需替换这些数据文件，已有的学习进度会保留。

## 安装

### 直接下载 .app

在仓库的 Actions（或 Releases）里下载 `日语学习助手-macOS.zip`，解压后把 `日语学习助手.app` 拖进「应用程序」。应用没有经过 Apple 公证，第一次打开时请右键点击 →「打开」。目前构建的是 Apple 芯片（arm64）版本。

### 从源码运行

依赖：Python 3.12、[uv](https://docs.astral.sh/uv/)。

```sh
uv sync
uv run nihongo app        # 原生 macOS 窗口
uv run nihongo serve --open   # 或者在浏览器里打开 http://127.0.0.1:4567/
```

自己打包 .app（需要 macOS 和 Xcode 命令行工具）：

```sh
packaging/macos/build.sh  # 输出 dist/日语学习助手.app
```

## 配置模型

AI 功能通过 OpenAI 兼容的 Prism 网关调用。打开应用的「设置 › 模型连接」直接填写，或写入配置文件：

| 路径 | 内容 |
| --- | --- |
| `~/.config/nihongo/credentials.env` | 网关地址、Virtual Key、调用方，参考 `credentials.example.env` |
| `~/.config/nihongo/config.toml` | 各用途的模型名，参考 `config.example.toml` |
| `~/.local/share/nihongo/nihongo.db` | 学习进度 |

环境变量优先于 `credentials.env`；`NIHONGO_CONFIG_HOME`、`NIHONGO_DATA_HOME` 可更改目录。凭据只从运行环境或私有配置文件读取，不写进代码。

| 用途 | 默认模型 | 请求 |
| --- | --- | --- |
| 对话、语法讲解、句子拆解 | `gpt-6-astra` | `POST /v1/chat/completions` |
| 发音评测 | `doubao-seed-2-1-lite-260915` | `POST /v1/chat/completions`，录音转为 16 kHz WAV，以 `input_audio` 发送 |
| 单词例句、释义翻译 | `doubao-seed-2-1-lite-260915` | `POST /v1/chat/completions` |
| 语法课程生成 | `gpt-6-astra` | `POST /v1/chat/completions` |

每个请求携带 `Authorization: Bearer <Virtual Key>` 和 `X-Caller-PSM`。用 `uv run nihongo doctor` 检查配置并逐个测试模型。

模型被要求输出 JSON，日语中的汉字以「漢字[かな]」标注读音，界面渲染成注音（可在设置里关闭）。

## 开发

```sh
uv run ruff check src tests
uv run pytest -q
```

测试使用模拟网关，不需要凭据。界面是 `src/nihongo/static/` 下的原生 HTML / CSS / JS，无需构建。服务只监听 `127.0.0.1`，并拒绝跨站的写请求。

`.github/workflows/macos-app.yml` 在每次推送时运行测试，并在 macOS 上打包 .app；推送 `v*` 标签时附加到 Release。
