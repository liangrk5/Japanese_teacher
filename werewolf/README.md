# AI 狼人杀观战

12 个 AI 玩家（豆包 Seed 2.1 Pro 和 GPT-6 Astra 各 6 个）在圆桌上打一局预女猎守，
代码实现的法官严格执行规则、点名每一步、在玩家之间传话，你在浏览器里看动画观战。
每局结束会自动导出一个可离线打开、可拖动时间轴的 HTML 复盘。

## 运行

只用 Python 标准库（3.9+），不需要安装依赖：

```bash
cd werewolf
python3 -m werewolf            # 自动打开浏览器 http://127.0.0.1:8765/
python3 -m werewolf --mock     # 不连网关，用假模型演示动画
python3 -m werewolf --window   # 用 pywebview 独立窗口（需 pip install pywebview）
```

复盘默认保存到 `~/Documents/werewolf-replays/`（`--replays` 可改），观战页右上角也能下载当前对局的复盘。

## 模型配置

走 Prism 网关（OpenAI 兼容 `/v1/chat/completions`），请求带 `Authorization: Bearer <Virtual Key>` 和 `X-Caller-PSM`。
参考 `credentials.example.env` 填写 `~/.config/werewolf/credentials.env`；已经配好日语助手的话会自动沿用
`~/.config/nihongo/credentials.env`。没配置时只能用模拟模式。

- 法官旁白默认用 `doubao-seed-2-1-pro-260915`
- 玩家按阵营平均分配两种模型（狼人 2+2，好人 4+4），头像外圈颜色区分模型
- `gpt-6-astra` 不发送 temperature；网关拒绝其他可选参数时会自动去掉重试

## 设计要点

- `engine.py`：法官状态机。夜晚 守卫 → 狼队密谈和投票 → 女巫 → 预言家；白天 公布死讯 → 遗言/猎人 → 依次发言 → 投票（平票 PK 一次）。屠边规则判胜负。
- 信息隔离：每条事件带可见范围（`public` / `wolves` / `p<N>` / `judge`），每个玩家的上下文只由它能看到的事件拼成；内心独白只有观众能看到。
- 玩家输出 JSON（`thought` + `speech` / `target`），引擎校验合法性，最多重试 3 次，仍失败就由法官代选合法动作，保证游戏不卡住。
- `static/`：圆桌观战页，昼夜切换、发言打字机、狼爪/守护/药水/查验/开枪特效、投票箭头、出局盖章；上帝视角/村民视角、0.5×–5× 倍速、点头像只看此人。
- `replay.py`：把事件日志和同一套前端打包成单文件 HTML。

## 测试

```bash
cd werewolf && python3 -m unittest discover -s tests -t .
```
