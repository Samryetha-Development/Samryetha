"""语义审核 provider：OpenAI 兼容的 chat completions 接口。

为什么要抽象成 provider 而不是直接写死一家：
  - 校园场景可能要求数据不出内网（本地 vLLM/Ollama），也可能用云端；
  - 审核模型换代很快，调用方（`service.py`）不该跟着改。

**失败语义（重要）**：这个模块抛出的任何异常都由 `service.py` 捕获并降级到规则层，
绝不让"模型超时"变成"全站发不了帖"。所以这里的取舍是：宁可抛错也不返回猜测值——
猜一个 allow 会静默放过内容，比明确失败更危险。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .automod_rules import DECISION_ALLOW, DECISION_BLOCK, DECISION_REVIEW, Signal, Verdict

logger = logging.getLogger("samryetha.automod")

# 审核模型必须回一个 JSON 对象。用 tool-calling 比"请只输出 JSON"稳得多的地方在于
# 结构化约束由服务端保证；但并非所有兼容端点都支持 tools，所以这里用
# response_format=json_object + 严格解析 + 失败即抛。
_SYSTEM_PROMPT = """你是校园论坛的内容审核助手，服务于一所中学的公开论坛。

本论坛**只有六类封禁项**。除此之外的任何内容都不应被判定为违规——这一点最重要，
因为错误的封禁会让一个学生再也不敢发言。

【六类封禁项】
1. 对任意政治主体的无理由辱骂：针对政府/政党/国家/政治人物的谩骂（如"脑残政府""狗官"）。
   注意：批评政策、分析时事、评价历史人物、表达政治观点 → 正常，不是辱骂。
2. 露骨性描写：情色创作、性行为细节描写。（仅公开版块；本判断不含位置信息，
   若内容明显是露骨性描写则标记。）
3. 三类严重违法：人口贩卖（拐卖/器官买卖/介绍卖淫）；血腥视频图片（割喉/砍头/虐杀/
   分尸/尸体照片/虐待动物）；未成年人色情（儿童色情、未成年露骨描写）。
4. 枪支与毒品贩卖：枪支弹药的贩卖、毒品的买卖与推广。
   注意：禁毒教育、公共安全讨论、新闻报道、科普 → 正常，不是贩卖。
5. 针对性人身攻击：侮辱贬损特定个人、人身威胁、涉及家人的攻击、组织校园霸凌。
   注意：观点争论、说明理由的批评、吐槽讲课方式 → 正常，不是人身攻击。
6. 无意义的重复内容：整条内容都是重复字符或反复粘贴同一句话，属刷屏噪音。
   注意：表达情绪的"哈哈哈哈哈"（合理长度内）→ 正常。

【明确正常、绝不要标记的内容】
- 批评学校、老师、管理规定
- 负面情绪表达（"考砸了""压力好大""很烦"）
- 有争议的话题、观点冲突（不含人身攻击）
- 政治观点、时事评论、对政策的批评
- 游戏、影视、动漫、恋爱、考试焦虑等青少年正常话题
- 使用英文、代码、数学公式
- 赌博、诈骗、学术不端、站外引流、隐私讨论等——**这些不在封禁清单内，不要标记**

【输出格式】必须严格回 JSON，不要输出任何其他文字：
{"risk": 0-100 的整数, "category": "none|political_abuse|explicit|trafficking|gore|csam|drug_guns|harassment|spam|other", "reason": "一句中文理由"}

risk 含义：0 = 完全正常；0-69 = 正常或仅需留意；70 以上 = 疑似属于上述六类之一。
宁可给低分：不确定时给低分，让人工去看，不要自己判重。
不要因为内容是英文或含大量代码就提高 risk。"""

# 复审专用提示词。用途：内容过了人工确认窗口仍无人定案时，让模型**独立地**再判一次。
# 关键在"独立"：如果提示里暗示"它已被标记"，模型会倾向于确认前一次判定（锚定效应），
# 复审就退化成了复读。所以这里明确要求忽略标记事实、只按标准重判。
_RECHECK_PROMPT = """这是一次**复审**。上面这条内容此前被机器标记为可疑，但标记本身不是证据。

请**独立地重新判断一次**，完全忽略"它曾被标记"这件事：
- 只依据内容本身与上面列出的六类封禁标准给出 risk；
- 如果重新看下来它并不属于这六类，请给出低分——宁可漏放，不可误封；
- 如果你的结论与上一次不同，不必顾虑，照实给出。

仍然只回同样的 JSON 格式。"""


@dataclass
class LLMVerdict:
    risk: int
    category: str
    reason: str


class AutomodUnavailable(RuntimeError):
    """模型不可用（未配置/超时/报错/返回不可解析）。调用方必须降级到规则层。"""


class OpenAICompatibleProvider:
    """最小的 OpenAI 兼容客户端。只依赖 `POST {base_url}/chat/completions`。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 12.0,
        # 判定预算是 300 时实测有 ~13% 的调用被截断：Kimi 这类推理型模型会先"想"
        # 一大段，JSON 还没写完就用光了 token（finish_reason=length，content 为空）。
        # 那会让解析失败并静默降级到规则层，等于最该拦的内容走了最弱的通道。
        # 1000 足够容纳 reasoning + 完整 JSON。
        max_tokens: int = 1000,
        temperature: float | None = 0,
        user_agent: str = "samryetha-automod/1.0",
        session_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._endpoint = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        # None = 不发送 temperature，交给服务端默认值。
        # 某些模型（Kimi 的 coding 系列）只接受 temperature=1，其它值直接 400；
        # 所以这个值是配置项，且服务端抱怨 temperature 时会自动降级重试。
        self._temperature = temperature
        self._user_agent = user_agent
        self._session_id = session_id
        self._extra_headers = extra_headers or {}

    def _headers(self) -> dict[str, str]:
        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
            # 网关（如 OpenCode Go）要求客户端自报身份、并为一段会话带稳定的会话 ID，
            # 否则会被当成滥用流量限流。默认值即"这是审核服务"，不伪装成编程 Agent。
            "user-agent": self._user_agent,
        }
        if self._session_id:
            headers["x-opencode-session"] = self._session_id
        headers.update(self._extra_headers)
        return headers

    def classify(self, text: str, *, context: str = "post", recheck: bool = False) -> LLMVerdict:
        """判定一次。**截断/解析失败时会自动重试一次**。

        重试是必要的：模型偶发把 token 花在 reasoning 上导致 JSON 没写完
        （finish_reason=length）。这类失败是随机的，重试一次基本都能拿到结果；
        不重试就会降级到规则层，把语义类违规（变体写法）直接放行。
        """
        verdict, truncated = self._classify_once(text, context=context, recheck=recheck)
        if verdict is not None:
            return verdict
        if not truncated:
            # 不是截断（HTTP 错、负载异常）：重试一次没意义，直接交给上层降级。
            raise AutomodUnavailable("automod provider returned an unusable response")
        logger.warning("automod response was truncated; retrying once")
        verdict, _ = self._classify_once(text, context=context, recheck=recheck)
        if verdict is None:
            raise AutomodUnavailable("automod provider returned no parsable verdict after retry")
        return verdict

    def _classify_once(
        self, text: str, *, context: str, recheck: bool
    ) -> tuple[LLMVerdict | None, bool]:
        """跑一次请求。返回 (判定, 是否因截断而失败)；HTTP/负载错误直接抛。"""
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"[内容类型: {context}]\n\n{text}"},
        ]
        if recheck:
            messages.append({"role": "user", "content": _RECHECK_PROMPT})
        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            # 结构化输出：多数兼容端点支持；不支持的服务端会直接报错而不是返回烂格式，
            # 所以失败一次后降级重试（见下），不会因此丢掉判定。
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        if self._temperature is not None:
            payload["temperature"] = self._temperature

        response = self._post(payload)
        if response.status_code >= 400 and "temperature" in response.text:
            # 服务端对 temperature 有硬性要求（Kimi coding 只接受 1）：按它说的改。
            # 先试 1（Kimi 的约束值），再退到"完全不发这个字段"。
            for retry_value in (1, None):
                if retry_value is None:
                    payload.pop("temperature", None)
                else:
                    payload["temperature"] = retry_value
                response = self._post(payload)
                if response.status_code < 400 or "temperature" not in response.text:
                    break
        if response.status_code >= 400 and "response_format" in response.text:
            # 该端点不支持 response_format：去掉它重试一次。提示词里已经要求"严格回 JSON"，
            # 解析器也能从自由文本里捞出 JSON 对象。
            payload.pop("response_format", None)
            response = self._post(payload)
        if response.status_code >= 400:
            raise AutomodUnavailable(f"automod provider returned HTTP {response.status_code}")
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AutomodUnavailable("automod provider returned an unexpected payload") from exc
        try:
            return _parse_verdict(content), False
        except AutomodUnavailable:
            # 只有"确实是被截断"才值得重试；其它解析失败重试也是白费 token。
            truncated = choice.get("finish_reason") == "length"
            if not truncated:
                raise
            return None, True

    def _post(self, payload: dict):
        try:
            return httpx.post(
                self._endpoint,
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise AutomodUnavailable(f"automod provider unreachable: {exc}") from exc


def _parse_verdict(content: Any) -> LLMVerdict:
    """把模型回的内容解析成 LLMVerdict。解析不出来就抛——不猜。"""
    if not isinstance(content, str):
        raise AutomodUnavailable("automod provider returned non-text content")
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match is None:
        raise AutomodUnavailable("automod provider did not return JSON")
    try:
        parsed = json.loads(match.group(0))
    except ValueError as exc:
        raise AutomodUnavailable("automod provider returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise AutomodUnavailable("automod provider returned a non-object verdict")
    try:
        risk = int(parsed.get("risk", 0))
    except (TypeError, ValueError) as exc:
        raise AutomodUnavailable("automod provider returned a non-numeric risk") from exc
    risk = max(0, min(100, risk))
    category = str(parsed.get("category") or "other")[:32]
    reason = str(parsed.get("reason") or "")[:200]
    return LLMVerdict(risk=risk, category=category, reason=reason)


def verdict_from_llm(result: LLMVerdict, *, review_at: int, block_at: int | None = None) -> Verdict:
    """把模型结果翻译成统一的 Verdict。

    ``block_at``：模型判到该分数以上即**直接封禁**（用户确认：AI 判 block 直接生效）。
    误判的纠正通道是队列——`enqueue` 会把这类封禁标成 `blocked_by_machine`，
    管理员在后台随时可以推翻，所以不是不可逆的伤害。
    传 ``None`` 则退回旧行为（模型只转人工、不封禁）。
    """
    if block_at is not None and result.risk >= block_at:
        decision = DECISION_BLOCK
    elif result.risk >= review_at:
        decision = DECISION_REVIEW
    else:
        decision = DECISION_ALLOW
    detail = result.reason or result.category
    signals = [Signal(rule=f"llm:{result.category}", weight=result.risk, detail=detail)] if result.risk > 0 else []
    return Verdict(decision=decision, score=result.risk, signals=signals, source="llm")
