"""自动审核：确定性规则 + 语义模型，产出 allow / review / block 判定。

分三层，职责不重叠：

1. **规则层（本模块）**：关键词、正则、外链、重复内容、新号频率。确定性、零成本、
   可单测；也是模型不可用时的兜底（见 `service.py` 的降级策略）。
2. **语义层（`providers.py`）**：OpenAI 兼容接口，负责规则抓不到的隐晦表达——反讽、
   黑话变体、软性引流。慢且可能失败，所以永远不单独决定放行。
3. **人工层（`service.py` 的队列）**：任何 `review` 都进队列等人。自动审核的目标是
   **把 99% 的正常内容直接放行、把可疑的挑出来给人看**，不是取代人。

判定取向：**宁可多送人工，不可自动误杀**。规则层的 `block` 只留给极少数确定性的
东西（明确的违法/色情关键词），其余一律 `review`——自动拒绝一条正常发言的代价
（作者流失、申诉）远高于让版主多点一下。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

DECISION_ALLOW = "allow"
DECISION_REVIEW = "review"
DECISION_BLOCK = "block"

# 转人工的分数门槛。规则命中累积到它就送审；模型分数另有自己的门槛。
REVIEW_THRESHOLD = 40
# 自动驳回门槛：只有确定性规则能到达这个高度（见 `_RULES` 的权重说明）。
BLOCK_THRESHOLD = 90

# 只在公开版块生效的规则：隐藏版（members/private）里发露骨描写是允许的。
PUBLIC_ONLY_RULES = {"explicit_public"}


@dataclass
class Signal:
    """一条判定依据。audit/申诉/调参都靠它，所以要能说清"为什么"。"""

    rule: str
    weight: int
    detail: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"rule": self.rule, "weight": self.weight}
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class Verdict:
    decision: str
    score: int
    signals: list[Signal] = field(default_factory=list)
    source: str = "rules"  # rules | llm | rules+llm | fallback

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "score": self.score,
            "source": self.source,
            "signals": [signal.to_dict() for signal in self.signals],
        }


# ---------------------------------------------------------------- 规则配置

# 分档权重：
#   100：封禁项。本清单**只有封禁项**（见下），所以全部是 100——没有"轻度信号"了。
# 取消封禁项之外的类别后，"累积到阈值才处理"这套机制也不再需要：命中即封禁，
# 未命中即放行。阈值仍然保留，是为了让未来加入"仅转人工"的观察类规则时不必改结构。
@dataclass(frozen=True)
class KeywordRule:
    name: str
    patterns: tuple[str, ...]
    weight: int
    detail: str


# 说明：这里是**服务端**规则，不涉及用户隐私之外的东西；命中只作为送审理由，
# 列表页会把它展示给版主，所以 detail 必须是能读的中文。
_BLOCKLIST: tuple[KeywordRule, ...] = (
    # 本清单**只包含封禁项**。封禁是不可逆的用户伤害，所以这份名单刻意很短：
    # 只有性质明确、公开传播即造成伤害的内容才进来。其余一律不进规则层。
    #
    # 100 分档 = 达到 BLOCK_THRESHOLD，直接拦截（不公开 + 进人工队列留痕）。
    KeywordRule(
        "violent_gore",
        (
            "血腥视频", "血腥图片", "血腥画面", "割喉", "砍头", "虐杀",
            "分尸", "尸体照片", "自杀直播", "虐猫", "虐狗", "活体解剖",
        ),
        100,
        "疑似血腥暴力内容",
    ),
    KeywordRule(
        "trafficking",
        (
            "人口贩卖", "拐卖", "贩卖人口", "买卖儿童", "拐骗儿童",
            "器官买卖", "卖器官", "介绍卖淫",
        ),
        100,
        "疑似人口贩卖",
    ),
    KeywordRule(
        "drug_guns",
        (
            "出售毒品", "贩卖毒品", "冰毒", "海洛因", "摇头丸", "大麻交易",
            "买枪", "卖枪", "枪支贩卖", "枪支弹药", "仿真枪", "军火",
        ),
        100,
        "疑似枪支或毒品贩卖",
    ),
    KeywordRule(
        "csam",
        (
            "未成年色情", "儿童色情", "幼女", "萝莉资源", "未成年裸照",
            "未成年性行为描写",
        ),
        100,
        "疑似未成年人色情内容",
    ),
    # 80 分档 = 同样达到封禁线，但保留"同一类别只计一次"的合并规则。
    KeywordRule(
        "political_abuse",
        (
            # 无理由辱骂政治主体：只列明确的辱骂性词组，不涉及任何观点表达。
            "脑残政府", "畜生政府", "傻逼政府", "狗官", "贪官都去死",
            "亡国之君", "独裁狗", "走狗政府", "汉奸政府", "卖国贼政府",
        ),
        100,
        "疑似对政治主体的无理由辱骂",
    ),
    KeywordRule(
        "personal_attack",
        (
            "你就是个废物", "你算什么东西", "去死吧你", "傻逼", "滚出这个学校",
            "你不配活着", "死全家", "全家暴毙",
        ),
        100,
        "疑似针对性人身攻击",
    ),
    # 60 分档：单次不封禁，靠累积或与其它规则叠加到 90 才封。
    KeywordRule(
        "repeat_spam",
        (
            "dddd", "顶顶顶顶", "aaaa", "水水水水", "刷屏",
        ),
        100,
        "疑似无意义重复内容",
    ),
    # 露骨描写：**位置相关**——只在公开版块触发。隐藏版内允许。
    # 位置判断在 automod.evaluate_for_context 里做（规则引擎本身拿不到版块可见性）。
    KeywordRule(
        "explicit_public",
        (
            "性行为描写", "露骨描写", "做爱细节", "床上细节", "器官描写",
            "情色小说", "黄文",
        ),
        100,
        "疑似在公开版块发布露骨描写",
    ),
)
# 拉长/变体绕过：把常见分隔符去掉再匹配一次关键词
_SEPARATOR_RE = re.compile(r"[\s\-_*·．.、|/\\]+")


def _normalize(text: str) -> str:
    """压掉分隔符与全角，降低"加*微*信"这类简单绕过。"""
    lowered = text.lower().replace("\u3000", " ")
    return _SEPARATOR_RE.sub("", lowered)


def _keyword_signals(text: str) -> list[Signal]:
    signals: list[Signal] = []
    raw = text.lower()
    squeezed = _normalize(text)
    for rule in _BLOCKLIST:
        for pattern in rule.patterns:
            needle = pattern.lower()
            if needle in raw or _normalize(needle) in squeezed:
                signals.append(Signal(rule=rule.name, weight=rule.weight, detail=rule.detail))
                break  # 同一规则只计一次，避免堆叠分数直接冲到 block
    return signals


def _repetition_signals(text: str, *, recent_bodies: Iterable[str]) -> list[Signal]:
    """同一作者近期发过几乎一样的内容 = 刷屏/广告的典型形态。"""
    normalized = _normalize(text)
    if len(normalized) < 12:
        return []
    for previous in recent_bodies:
        if _normalize(previous) == normalized:
            return [Signal("duplicate", 55, "与该作者近期发布的内容完全相同")]
    return []


def _meaningless_repeat_signals(text: str) -> list[Signal]:
    """无意义的重复内容：整段以同样的字/词堆叠为主，或有意义字符占比过低。

    关键词表抓不住"哈哈哈哈哈哈"、"asdfasdfasdf"、同一句话刷十遍这类内容，所以补一条
    结构化判定。判据刻意保守——正常长句、引用、代码都不该命中。
    """
    from collections import Counter

    stripped = text.strip()
    if len(stripped) < 8:
        return []
    # 单一字符占比过高（"aaaaaaaa" / "哈哈哈哈哈" / "。。。。。"）
    char, hits = Counter(stripped).most_common(1)[0]
    if hits >= 8 and hits / len(stripped) >= 0.7:
        return [Signal("repeat_spam", 100, f"内容以「{char}」重复堆叠为主")]
    # 去重后字符占比过低（同一句反复粘贴、"asdfasdf"）
    if len(stripped) >= 12 and len(set(stripped)) / len(stripped) <= 0.15:
        return [Signal("repeat_spam", 100, "内容字符重复率过高，疑似无意义刷屏")]
    return []


def evaluate_rules(
    text: str,
    *,
    recent_bodies: Iterable[str] = (),
    is_new_account: bool = False,
    is_public_board: bool = True,
) -> Verdict:
    """规则层判定。返回的 score 是各命中项权重之和（上限 100）。

    ``is_public_board=False``（隐藏版块：members/private）时，``PUBLIC_ONLY_RULES``
    不参与判定——"在非隐藏版发布露骨描写"这条规则的成立与否取决于发布位置，而位置
    不在文本里，所以由调用方告知。
    """
    signals: list[Signal] = []
    for signal in _keyword_signals(text):
        if not is_public_board and signal.rule in PUBLIC_ONLY_RULES:
            continue
        signals.append(signal)
    signals += _meaningless_repeat_signals(text)
    # 与该作者近期内容比对：重复粘贴同一段是刷屏/广告的典型形态。
    # 这条一直没被调用（recent_bodies 从 discussions 一路透传到这里却没人用），
    # 等于"跨帖查重"从未生效，只能靠模型偶尔看出来。
    signals += _repetition_signals(text, recent_bodies=recent_bodies)

    # 新账号本身不是罪，只是同样内容更值得看一眼。
    if is_new_account and signals:
        signals.append(Signal("new_account", 15, "新注册账号的首次投稿"))
        for signal in signals:
            if signal.rule != "new_account":
                signal.weight = min(100, int(signal.weight * 1.15))
                break

    score = min(100, sum(signal.weight for signal in signals))
    if score >= BLOCK_THRESHOLD:
        decision = DECISION_BLOCK
    elif score >= REVIEW_THRESHOLD:
        decision = DECISION_REVIEW
    else:
        decision = DECISION_ALLOW
    return Verdict(decision=decision, score=score, signals=signals, source="rules")


def merge_verdicts(rules: Verdict, model: Verdict | None) -> Verdict:
    """规则 + 模型合成一个判定。**取更严的那个**。

    规则说 block 就是 block——确定性规则是硬约束，模型不能把它降级。
    模型单独判 block 时**也直接封禁**（用户确认：AI 判 block 直接生效），
    但 `enqueue` 会把这条记进队列并标 `blocked_by_machine`，
    管理员可在后台随时复审、推翻——这就是误判的纠正通道。
    """
    if model is None:
        return rules
    signals = list(rules.signals) + list(model.signals)
    score = max(rules.score, model.score)
    if DECISION_BLOCK in (rules.decision, model.decision):
        decision = DECISION_BLOCK
    elif DECISION_REVIEW in (rules.decision, model.decision):
        decision = DECISION_REVIEW
    else:
        decision = DECISION_ALLOW
    return Verdict(decision=decision, score=score, signals=signals, source="rules+llm")


def excerpt_of(*parts: str | None, limit: int = 160) -> str:
    """列表页要看的摘要：拼起来、压平空白、截断。"""
    joined = " ".join(part.strip() for part in parts if part and part.strip())
    flattened = re.sub(r"\s+", " ", joined)
    return flattened[:limit]


def signals_json(signals: list[Signal]) -> str:
    return json.dumps([signal.to_dict() for signal in signals], ensure_ascii=False)


def now_seconds() -> int:
    return int(time.time())
