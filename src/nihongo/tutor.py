"""AI 老师：对话、语法讲解、例句、句子拆解和发音评测的提示词。"""

from __future__ import annotations

from typing import Any

from .llm import LLMClient

RUBY_RULE = (
    "所有日语里的汉字都必须用「漢字[かな]」格式标注读音，例如「私[わたし]は学生[がくせい]です」；"
    "假名本身不要标注。"
)

LEVELS = {
    "zero": "完全零基础，刚学完或正在学假名，只认识最简单的问候语",
    "n5": "入门（约 N5），会基本的です／ます句型和几百个单词",
    "n4": "初级（约 N4），能进行简单日常对话",
}

SCENARIOS = {
    "free": "自由聊天，老师根据学生的话题自然回应",
    "intro": "自我介绍：老师引导学生说出名字、国籍、职业、爱好",
    "restaurant": "在餐厅点餐：老师扮演店员",
    "shopping": "在便利店购物：老师扮演店员，涉及价格和数量",
    "directions": "问路：老师扮演路人，涉及车站、左右、远近",
    "travel": "在日本旅行：酒店入住、买车票等",
}


def _system_tutor(level: str, scenario: str) -> str:
    return f"""你是一位耐心、鼓励式的日语老师，学生的母语是中文。
学生水平：{LEVELS.get(level, LEVELS["zero"])}。
练习场景：{SCENARIOS.get(scenario, SCENARIOS["free"])}。

规则：
- 用简单、自然、礼貌体（です／ます）的日语回应，句子短，每次最多 2–3 句，只用符合学生水平的词汇。
- 学生可能用中文、日语或罗马音写，也可能写错。如果学生写了日语且有错误，温和地指出并给出正确说法。
- 每次回应结尾抛出一个简单的问题，引导学生继续用日语回答。
- {RUBY_RULE}

只输出一个 JSON 对象，不要输出其它文字：
{{
  "reply": "老师的日语回复（带读音标注）",
  "romaji": "回复的罗马音",
  "translation": "回复的中文翻译",
  "correction": "如果学生的日语有错，用中文说明并给出正确句子（带读音标注）；没有错误或学生没写日语时为 null",
  "hint": "用中文提示学生下一句可以怎么回答，并给一个日语例句（带读音标注）",
  "words": [{{"word": "回复中值得学的单词", "reading": "读音", "meaning": "中文意思"}}]
}}
words 最多 4 个，挑对这个水平的学生最有用的。"""


def tutor_reply(
    llm: LLMClient, history: list[dict[str, str]], level: str, scenario: str
) -> dict[str, Any]:
    messages = [{"role": "system", "content": _system_tutor(level, scenario)}]
    for turn in history[-16:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": str(turn["content"])[:2000]})
    if len(messages) == 1:
        messages.append(
            {"role": "user", "content": "（学生刚进入对话，请先用日语打招呼并开始这个场景）"}
        )
    data = llm.chat_json(messages)
    data.setdefault("words", [])
    return data


def explain_grammar(llm: LLMClient, point: dict, question: str, level: str) -> dict[str, Any]:
    system = f"""你是日语语法老师，用中文给母语为中文的学生讲解。学生水平：{LEVELS.get(level, LEVELS["zero"])}。
讲解要短、具体，多和中文做对比，指出中国学生常犯的错误。{RUBY_RULE}
只输出 JSON：
{{
  "answer": "针对学生问题的中文讲解（可以包含带读音标注的日语）",
  "examples": [{{"ja": "例句（带读音标注）", "zh": "中文翻译"}}],
  "pitfalls": ["中国学生常见错误，一条一句"],
  "quiz": [{{"question": "一道小练习题（中文题干，可含日语）", "options": ["A", "B", "C"], "answer": 0, "why": "解析"}}]
}}
examples 给 3 个，quiz 给 2 道，answer 是正确选项的下标。"""
    user = (
        f"语法点：{point['title']}\n已有讲解：{point.get('explanation', '')}\n\n"
        f"学生的问题：{question or '请再详细讲一讲这个语法点，并多给几个生活中的例句。'}"
    )
    return llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=1800
    )


def make_example(llm: LLMClient, word: str, reading: str, meaning: str) -> dict[str, Any]:
    system = f"""给日语初学者造一个简单例句。{RUBY_RULE}
只输出 JSON：{{"ja": "例句（带读音标注）", "zh": "中文翻译", "tip": "一句中文记忆小窍门或用法说明"}}"""
    user = f"单词：{word}（{reading}），意思：{meaning}。例句要短，只用最基础的词汇和です／ます。"
    return llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        role="fast",
        max_tokens=400,
    )


def breakdown(llm: LLMClient, text: str, level: str) -> dict[str, Any]:
    """把中文翻成日语，或把日语拆成单词逐个讲解。"""
    system = f"""你是日语老师。学生水平：{LEVELS.get(level, LEVELS["zero"])}。
如果学生给的是中文，把它翻成自然、简单的日语礼貌体；如果是日语，直接分析这句日语。
然后把日语句子拆成词，逐个说明。{RUBY_RULE}
只输出 JSON：
{{
  "japanese": "完整日语句子（带读音标注）",
  "romaji": "罗马音",
  "translation": "中文意思",
  "tokens": [{{"text": "词（带读音标注）", "reading": "假名读音", "pos": "词性（中文）", "meaning": "中文意思或语法作用"}}],
  "notes": "一两句中文讲解这句话的语法要点"
}}"""
    return llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": text[:500]}],
        max_tokens=1500,
    )


def assess_pronunciation(llm: LLMClient, target: str, reading: str, audio: bytes) -> dict[str, Any]:
    system = "你是日语发音老师，学生母语是中文。你会听到学生朗读的录音。只输出 JSON。"
    prompt = f"""学生要朗读的目标句：{target}
读音（假名）：{reading or "（未提供）"}

请仔细听录音，判断学生实际读出的内容和发音质量，用中文反馈。只输出 JSON：
{{
  "heard": "你听到的内容（用假名写）",
  "score": 0-100 的整数，综合准确度和自然度,
  "summary": "一句中文总评，先肯定做得好的地方",
  "issues": [{{"part": "有问题的音（假名）", "problem": "问题是什么", "tip": "怎么改进，尽量对照中文发音说明"}}],
  "focus": "下次练习最该注意的一点"
}}
特别留意中国学生常见问题：长音和促音（っ）的长度、清浊音（か／が、た／だ）、「つ」「す」「ら行」的发音、语调是否平。
如果录音没有人声或完全听不清，score 为 0，并在 summary 里说明。"""
    return llm.audio_json(prompt, audio, system=system)


def translate_meanings(llm: LLMClient, items: list[dict]) -> dict[int, str]:
    """把词表的英文释义批量翻成简洁的中文释义。"""
    if not items:
        return {}
    lines = "\n".join(
        f"{it['id']}\t{it['word']}（{it['reading']}）\t{it['meaning_en']}" for it in items
    )
    system = """你是日中词典编辑。根据日语单词、读音和英文释义，给出简洁准确的中文释义，
多个义项用「；」分隔，不超过 20 个字，不要解释。只输出 JSON：{"<id>": "中文释义", ...}"""
    data = llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": lines}],
        role="fast",
        temperature=0.2,
        max_tokens=60 * len(items) + 100,
    )
    out = {}
    for key, value in data.items():
        try:
            out[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    return out


def generate_lesson(llm: LLMClient, point: dict, level: str, known_titles: list[str]) -> dict:
    """为语法大纲里的一课生成完整内容。"""
    system = f"""你是日语老师，为母语是中文的学生编写一节语法课。学生水平：{LEVELS.get(level, LEVELS["zero"])}。
讲解用中文，简明具体，多和中文对比，例句只用 {point["level"]} 及以下的词汇，用礼貌体。{RUBY_RULE}
只输出 JSON：
{{
  "title": "语法点名称（日语句型）",
  "summary": "一句话说明用途",
  "structure": "接续方式，例如「名词 ＋ は ＋ 名词 ＋ です」",
  "explanation": "中文讲解，3–6 句",
  "examples": [{{"ja": "例句（带读音标注）", "zh": "中文翻译"}}],
  "pitfalls": ["中国学生容易犯的错，一条一句"],
  "quiz": [{{"question": "练习题（中文题干，可含带读音标注的日语）", "options": ["选项"], "answer": 0, "why": "解析"}}],
  "speaking": [{{"ja": "适合朗读练习的短句（带读音标注）", "zh": "中文翻译"}}],
  "words": [{{"word": "课里出现的重点单词", "reading": "读音", "meaning": "中文意思"}}]
}}
examples 5 个，由易到难；pitfalls 2–3 条；quiz 4 道，每道 3–4 个选项；speaking 3 句；words 最多 6 个。"""
    context = "、".join(known_titles[-8:]) or "（这是第一课）"
    user = (
        f"本课：{point['title']}（{point['level']}）\n要点：{point['summary']}\n"
        f"学生之前学过：{context}"
    )
    return llm.chat_json(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=2600,
    )


def ping(llm: LLMClient, role: str = "fast") -> str:
    return llm.chat(
        [{"role": "user", "content": "用一句日语打招呼，只回复这一句。"}], role=role, max_tokens=50
    )
