# 找出人类（反向图灵）

一个聊天室里坐着 5 个 AI（豆包 Seed 2.1 Pro 和 GPT-6 Astra 混坐）和 1 个真人，谁是谁不公开。
AI 的目标是投票揪出人类，你的目标是伪装成 AI 活到最后。

## 规则

1. 每轮主持人出一道题，所有人**同时**作答，答案按随机顺序亮出。
2. 每人轮流发言一次：质疑别人、为自己辩解都行。
3. 全员投票“谁是人类”，票最多的人出局并公开身份（AI / 人类）。平票时由其他人在平票者之间重投一次，仍平票则无人出局。
4. 人类被投出，AI 获胜；撑过全部轮次（默认 3 轮），人类获胜。被误投出局的 AI 也算输，所以 AI 自己也会努力“像 AI”。

结束后揭晓每个座位的真实身份和模型、每个 AI 每轮对你的怀疑度曲线、本局哪个模型抓人更准，以及全部 AI 的内心想法。
战绩会累计成“抓人排行榜”。

**观战模式**：由一个 AI 带着具体人设扮演混进来的“真人”，你只看不玩，可以比较两个模型谁更会藏、谁更会抓（累计“藏身排行榜”）。

**日语模式**：聊天全部用日语进行，界面仍是中文。用日语假装 AI，顺便练日语。

## 运行

只用 Python 标准库（3.9+），不需要安装依赖：

```bash
cd turing
python3 -m turing            # 自动打开浏览器 http://127.0.0.1:8766/
python3 -m turing --mock     # 不连网关，用假模型演示
python3 -m turing --window   # 用 pywebview 独立窗口（需 pip install pywebview）
```

复盘（单文件 HTML，可直接发给朋友）和战绩 `results.jsonl` 默认保存在 `~/Documents/turing-replays/`（`--replays` 可改）。

## 模型配置

走 Prism 网关（OpenAI 兼容 `/v1/chat/completions`），请求带 `Authorization: Bearer <Virtual Key>` 和 `X-Caller-PSM`。
参考 `credentials.example.env` 填写 `~/.config/turing/credentials.env`；已经配好日语助手或 AI 狼人杀的话，会自动沿用
`~/.config/nihongo/credentials.env` / `~/.config/werewolf/credentials.env`。

- `gpt-6-astra` 不发送 temperature；网关拒绝其他可选参数时会自动去掉重试
- 豆包 Seed 关闭深度思考（`thinking: disabled`），否则一次调用要等很久
- 每次模型调用有墙钟上限（默认 45 秒，`TURING_TIMEOUT` 可改），超时或连续解析失败就用兜底发言/随机投票，游戏不会卡住

## 设计要点

- `engine.py`：主持人状态机。作答和投票阶段 AI 并发调用、真人在网页输入；发言阶段按随机顺序依次进行。
- 信息隔离：事件带可见范围（`public` / `human` / `judge`）。AI 的上下文只由公开事件和它自己的想法拼成；
  真人对局进行中，服务端不会把 AI 的内心、怀疑度和真实身份推给浏览器，结束后才揭晓。
- 真人输入通过 `HumanInbox` 交给引擎，每个阶段有倒计时（作答 120 秒、发言 90 秒、投票 60 秒），超时视为沉默/弃票。
- `static/`：聊天室界面，座位栏、逐条揭晓答案、投票卡片、出局盖章、结算页（身份揭晓、怀疑度曲线、模型排行）。
- `replay.py`：把事件日志和同一套前端打包成单文件 HTML。

## 测试

```bash
cd turing && python3 -m unittest discover -s tests -t .
```
