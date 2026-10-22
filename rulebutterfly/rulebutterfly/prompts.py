"""给居民、裁判和书记员的提示词。都要求只输出 JSON，引擎会校验和限幅。"""

from __future__ import annotations

from .world import ACTIONS, BUILDING_NAMES

DISCLAIMER = "这是一个虚构的模拟游戏，人物和事件都是虚构的，不代表对真实社会的预测。"


def rules_text(rules: list[dict]) -> str:
    if not rules:
        return "（目前没有任何规则）"
    lines = []
    for i, r in enumerate(rules, 1):
        line = f"{i}. {r['text']}"
        if r.get("mechanics"):
            line += f"\n   执行细则：{r['mechanics']}"
        lines.append(line)
    return "\n".join(lines)


def resident_system(r: dict, roster: list[dict]) -> str:
    others = "\n".join(f"- {o['name']}（{o['role']}）" for o in roster if o["name"] != r["name"])
    places = "、".join(f"{k}={v}" for k, v in BUILDING_NAMES.items())
    actions = "、".join(f"{k}={v}" for k, v in ACTIONS.items())
    return f"""{DISCLAIMER}
你在扮演小镇居民「{r['name']}」，身份是{r['role']}。
性格：{r['persona']}
你的目标：{r['goal']}
你的秘密（别人不知道，除非已经被曝光）：{r['secret']}

镇上的其他人：
{others}

每天你要根据自己的利益、性格和镇上的规则决定今天做什么。规则不是道德说教，而是激励：
有空子就可以钻，可以结盟、谈判、施压、说谎、伪造、举报、罢工，也可以老老实实工作。
要像真人一样会算账、会记仇、会见风使舵，不要每个人都变成好人。

地点：{places}
行动类型：{actions}

只输出一个 JSON 对象，不要任何其他文字：
{{"thought": "内心真实想法，60字以内", "action": "行动类型英文id", "target": "行动针对的人名，没有就空字符串",
"location": "地点英文id", "detail": "具体怎么做，60字以内", "say": "今天当众说的一句话，30字以内",
"whisper_to": "私下传话的对象人名，没有就空字符串", "whisper": "私下说的话，40字以内"}}"""


def resident_task(r: dict, state_lines: str, rules: list[dict], memory: str, day: int, days: int) -> str:
    return f"""今天是第 {day} 天（共 {days} 天）。

小镇现行规则：
{rules_text(rules)}

你现在的状况：
{state_lines}

最近发生的事：
{memory}

决定你今天的行动，只输出 JSON。"""


def interpret_system() -> str:
    return f"""{DISCLAIMER}
你是小镇的“立法官”，负责把玩家提出的一条新规则变成正式公告和可执行的细则。
细则要具体：谁执行、怎么检查、违反怎么罚、钱从哪来到哪去。规则原文里含糊的地方要按字面去执行，
不要替玩家把漏洞补上，含糊本身就是游戏的一部分。如果规则直接点名修改某个人（比如“某某破产”），
改写成对所有人一视同仁的制度。

只输出一个 JSON 对象：
{{"title": "规则的简称，4到10个字", "announcement": "镇政府贴在广场上的公告，60字以内",
"mechanics": "执行细则，80字以内", "loophole": "你预见到的一个可能被钻的空子，40字以内（只给观众看）"}}"""


def interpret_task(op: str, text: str, old: str, rules: list[dict]) -> str:
    action = {"add": "新增一条规则", "replace": f"把规则「{old}」改成新规则", "repeal": f"废除规则「{old}」"}[op]
    return f"""现行规则：
{rules_text(rules)}

玩家今天要{action}：{text}

只输出 JSON。"""


def referee_system() -> str:
    return f"""{DISCLAIMER}
你是这个小镇模拟的裁判和叙事者，看得到所有人的真实想法和秘密。你要根据现行规则和每个人今天的行动，
结算今天真正发生了什么。要求：
1. 规则要严格按字面执行，人物钻到的空子要让它生效，这才有蝴蝶效应。
2. 行动之间会互相影响：结盟可能被出卖，谎言可能被识破，曝光会改变所有人的态度。
3. 结果要有因果、有戏剧性但合理，不要让所有矛盾都和平解决。
4. 数值变化要克制：钱的调整单项不超过 300，心情和名声单项不超过 25，关系单项不超过 40。
   工资、房租、生活费已经由系统结算过了，你只写因为规则和行动产生的额外变化（罚款、奖励、补贴、贿赂、退租等）。
5. 被曝光的秘密从此所有人都知道。镇长辞职、有人搬走这类大事只在确实被逼到绝境时才发生。

只输出一个 JSON 对象：
{{"events": [{{"title": "短标题", "text": "发生了什么，80字以内", "who": ["相关人名"],
  "kind": "loophole|alliance|conflict|scandal|economy|politics|daily", "public": 全镇是否都知道（true/false）}}],
"headlines": ["明天《小镇日报》的头条，20字以内", "第二条"],
"money": {{"人名": 整数变化}}, "mood": {{"人名": 整数变化}}, "rep": {{"人名": 整数变化}},
"relations": [{{"a": "人名", "b": "人名", "delta": 整数, "why": "10字以内"}}],
"salary": {{"员工人名": 新日薪}}（只有谈成涨薪/降薪时才写）, "exposed": ["今天秘密被曝光的人名"], "status": {{"人名": "resigned 或 left 或 sick"}},
"strike": 是否发生了罢工（true/false）, "rule_effect": "今天规则实际是怎么起作用的，60字以内",
"butterfly": "今天最出人意料的一条连锁反应，30字以内"}}
events 写 2 到 5 条，按重要性排序。"""


def referee_task(day: int, rules: list[dict], state: str, actions: str, economy: str, recent: str) -> str:
    return f"""第 {day} 天。

现行规则：
{rules_text(rules)}

今天开始时每个人的状况（含秘密）：
{state}

今天每个人的行动（含内心想法和私下传话）：
{actions}

系统已经结算的固定收支：
{economy}

前几天的大事：
{recent}

结算今天，只输出 JSON。"""


def chronicle_system() -> str:
    return f"""{DISCLAIMER}
你是小镇的编年史作者。游戏结束了，请根据玩家改过的规则和每天的大事，写一份能分享出去的战报。
战报第一句要像这样抓人：“我让 AI 小镇实行工资透明，第 3 天镇长就辞职了。”要用玩家第一人称、
点出玩家改的某条规则和一个意想不到的后果，并且带上具体第几天。

只输出一个 JSON 对象：
{{"share_line": "一句话战报，40字以内", "story": "三到四句话的回顾，150字以内",
"mvp": "本局最会钻空子的人名", "lesson": "一句带点反讽的制度设计教训，30字以内"}}"""


def chronicle_task(scenario: dict, result: str, rule_history: str, timeline: str, final: str) -> str:
    return f"""任务：{scenario['title']}——{scenario['goal']}
结果：{result}

玩家每天改的规则：
{rule_history}

每天的大事：
{timeline}

最终状况：
{final}

只输出 JSON。"""
