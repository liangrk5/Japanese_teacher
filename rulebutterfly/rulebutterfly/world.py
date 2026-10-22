"""小镇的初始设定：建筑、居民、关系、默认规则、任务和推荐规则。都是虚构的。"""

from __future__ import annotations

# 建筑：id、名字、在地图上的格子坐标（x, z）、颜色、高度
BUILDINGS = [
    {"id": "hall", "name": "市政厅", "x": 0, "z": -9, "color": "#e8dcc4", "h": 4.2, "kind": "hall"},
    {"id": "plaza", "name": "中心广场", "x": 0, "z": 0, "color": "#d9cbb0", "h": 0, "kind": "plaza"},
    {"id": "factory", "name": "钱氏工厂", "x": 11, "z": -6, "color": "#a9a29a", "h": 3.4, "kind": "factory"},
    {"id": "apartment", "name": "丽华公寓", "x": -11, "z": -5, "color": "#e3a87c", "h": 5.6, "kind": "apartment"},
    {"id": "clinic", "name": "仁心诊所", "x": -10, "z": 7, "color": "#f2f2ee", "h": 2.6, "kind": "clinic"},
    {"id": "news", "name": "小镇日报", "x": 10, "z": 7, "color": "#8fb3c9", "h": 3.0, "kind": "news"},
    {"id": "bakery", "name": "甜甜面包店", "x": -3, "z": 11, "color": "#f4c6a5", "h": 2.2, "kind": "bakery"},
    {"id": "tavern", "name": "老槐树酒馆", "x": 4, "z": 11, "color": "#9c6b4e", "h": 2.6, "kind": "tavern"},
]
BUILDING_IDS = {b["id"] for b in BUILDINGS}
BUILDING_NAMES = {b["id"]: b["name"] for b in BUILDINGS}

CAPITAL = "capital"  # 资方：老板、房东、镇长
LABOR = "labor"  # 劳方：员工、医生、记者、面包师

# 每天固定的经济参数（确定性结算，裁判模型只能在此之上做有限的调整）
LIVING_COST = 30
RENT = {"李小强": 40, "王芳": 40, "陈直言": 40, "吴甜甜": 60}  # 吴甜甜租的是店面
FACTORY_REVENUE_PER_WORKER = 260
MAYOR_SALARY = 120
DAILY_TAX = 50  # 老板和房东每天各交给镇财政
FLOUR_COST = 25  # 面包店每天的原料钱

RESIDENTS = [
    {
        "name": "周明远", "role": "镇长", "faction": CAPITAL, "home": "hall", "work": "hall",
        "money": 800, "mood": 60, "rep": 62, "color": "#3d5a80", "salary": 0,
        "persona": "圆滑的老政客，讲话永远四平八稳，最怕丢面子。",
        "goal": "保住支持率，明年连任。",
        "secret": "去年挪用了 3000 元镇财政修自家院子，账是他自己做平的。",
    },
    {
        "name": "钱有福", "role": "工厂老板", "faction": CAPITAL, "home": "factory", "work": "factory",
        "money": 3000, "mood": 65, "rep": 50, "color": "#6d597a", "salary": 0,
        "persona": "精明的生意人，算账极快，嘴上客气，心里只认利润。",
        "goal": "让工厂利润最大化，最好把人工成本压下去。",
        "secret": "工厂长期偷偷排污超标，河下游的鱼死过一批。",
    },
    {
        "name": "孙丽华", "role": "房东", "faction": CAPITAL, "home": "apartment", "work": "apartment",
        "money": 2000, "mood": 60, "rep": 48, "color": "#b56576", "salary": 0,
        "persona": "强势、记账一丝不苟，对欠租零容忍，但很在意邻居怎么看她。",
        "goal": "按时收齐房租，找机会涨租。",
        "secret": "公寓三楼的承重墙有裂缝，一直没修也没告诉租客。",
    },
    {
        "name": "李小强", "role": "工厂员工", "faction": LABOR, "home": "apartment", "work": "factory",
        "money": 300, "mood": 50, "rep": 55, "color": "#e56b6f", "salary": 100,
        "persona": "二十五岁，冲动、讲义气，觉得自己被低估了。",
        "goal": "涨工资，攒钱明年结婚。",
        "secret": "晚上偷偷在酒馆打黑工，老板明令禁止员工兼职。",
    },
    {
        "name": "王芳", "role": "工厂员工", "faction": LABOR, "home": "apartment", "work": "factory",
        "money": 500, "mood": 58, "rep": 60, "color": "#eaac8b", "salary": 130,
        "persona": "细心、会来事，想往上爬，不愿意站队太早。",
        "goal": "升职当车间组长。",
        "secret": "是老板钱有福的远房表妹，所以工资比李小强高 30。",
    },
    {
        "name": "赵仁心", "role": "医生", "faction": LABOR, "home": "clinic", "work": "clinic",
        "money": 1500, "mood": 62, "rep": 75, "color": "#52b788", "salary": 0,
        "persona": "温和、受人尊敬，镇上的老好人，但也在为自己的退路打算。",
        "goal": "攒够钱开一家更大的私立诊所。",
        "secret": "收过药商的回扣，给病人开过不必要的贵药。",
    },
    {
        "name": "陈直言", "role": "记者", "faction": LABOR, "home": "apartment", "work": "news",
        "money": 400, "mood": 55, "rep": 52, "color": "#4ea8de", "salary": 0,
        "persona": "嗅觉灵敏、爱较真，也爱出风头，坚信“公众有权知道”。",
        "goal": "写出一篇轰动全镇的大新闻。",
        "secret": "为了阅读量，曾经编造过一篇“工厂闹鬼”的新闻。",
    },
    {
        "name": "吴甜甜", "role": "面包店老板", "faction": LABOR, "home": "bakery", "work": "bakery",
        "money": 250, "mood": 52, "rep": 70, "color": "#f4a261", "salary": 0,
        "persona": "乐观、嘴甜，消息灵通，谁的八卦都知道一点。",
        "goal": "让面包店撑下去，还清欠款。",
        "secret": "已经欠了房东孙丽华三个月的店面租金，一直在拖。",
    },
]
NAMES = [r["name"] for r in RESIDENTS]

# 初始关系（信任度 -100~100），没写的默认 0，双向分别设置
RELATIONS = {
    ("王芳", "钱有福"): 50, ("钱有福", "王芳"): 40,
    ("李小强", "钱有福"): -25, ("钱有福", "李小强"): -10,
    ("李小强", "王芳"): 20, ("王芳", "李小强"): 10,
    ("陈直言", "周明远"): -30, ("周明远", "陈直言"): -40,
    ("周明远", "钱有福"): 45, ("钱有福", "周明远"): 35,
    ("孙丽华", "吴甜甜"): -30, ("吴甜甜", "孙丽华"): -15,
    ("吴甜甜", "李小强"): 35, ("李小强", "吴甜甜"): 35,
    ("赵仁心", "周明远"): 20, ("周明远", "赵仁心"): 25,
    ("陈直言", "吴甜甜"): 30, ("吴甜甜", "陈直言"): 40,
    ("孙丽华", "周明远"): 30, ("周明远", "孙丽华"): 20,
    ("李小强", "孙丽华"): -20, ("陈直言", "钱有福"): -20,
}

DEFAULT_RULES = [
    "工资由老板自己决定，不对外公开。",
    "房租由房东定价，每天收取。",
    "镇长任期内不可罢免，有事可以去市政厅请愿。",
]
MAX_RULES = 6

ACTIONS = {
    "work": "正常工作",
    "strike": "罢工/罢市",
    "protest": "公开请愿抗议",
    "negotiate": "找人谈判提条件",
    "ally": "私下结盟串通",
    "expose": "曝光/举报某人",
    "exploit": "钻规则的空子",
    "deceive": "说谎/伪造",
    "bribe": "收买某人",
    "rest": "休息/看病",
    "resign": "辞职/撂挑子",
}
DEFAULT_PLACE = {
    "strike": "plaza", "protest": "hall", "ally": "tavern", "expose": "news", "rest": "clinic", "resign": "hall",
}

SCENARIOS = {
    "calm": {
        "title": "平稳十天", "days": 10,
        "goal": "让小镇撑过 10 天，期间不能发生罢工。",
        "hint": "罢工的判定：同一天有 2 人及以上罢工/罢市，或裁判认定发生了罢工。",
    },
    "nobody": {
        "title": "一个都不能少", "days": 7,
        "goal": "7 天内没有人破产（钱变成负数），也没有人离开小镇。",
        "hint": "房租、生活费每天都在扣，穷人撑不了几天。",
    },
    "mayor": {
        "title": "镇长保卫战", "days": 7,
        "goal": "7 天后镇长仍在任，且支持率不低于 50%。",
        "hint": "支持率由其他居民对镇长的信任度换算。",
    },
    "sandbox": {
        "title": "自由实验", "days": 10,
        "goal": "没有目标，随便改规则，看小镇会变成什么样。",
        "hint": "",
    },
}

SUGGESTED_RULES = [
    "从明天开始，所有人的工资公开。",
    "说谎会扣钱，但没人知道扣的钱去了哪里。",
    "小镇每天投票选出一个人免交房租。",
    "举报别人违规，可以拿到被罚款的一半。",
    "任何人的收入超过镇平均的两倍，多出的部分归镇财政。",
    "每天收入最低的人可以去市政厅领 80 元补贴。",
    "员工可以匿名给老板打分，分数每天贴在广场上。",
    "所有私下交易必须在酒馆公开登记。",
]
