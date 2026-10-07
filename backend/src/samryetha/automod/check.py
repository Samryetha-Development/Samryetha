"""自动审核自检：`uv run python -m samryetha.automod.check`

配置完模型后先跑这个，确认密钥、端点、模型名三件事都对，再谈上线。
它不碰数据库、不写任何东西，只发几次真实请求并打印判定结果。

检查项：
  1. 配置齐不齐（总开关、base_url、model、api_key）；
  2. 端点可达 + 密钥有效 + 结构化输出能否解析；
  3. 拿几条**故意设计**的样例看模型判得对不对——特别是"正常内容不能误杀"，
     那比"违规能不能抓到"更影响公测体验。
"""

from __future__ import annotations

import sys

from .rules import evaluate_rules
from ..config import load_settings

# 样例：前四条应当 risk 低，后三条应当 risk 高。判错的要回去调提示词。
SAMPLES: list[tuple[str, str, int]] = [
    ("normal", "有人知道下周的物理实验课调到几点了吗？", 0),
    ("normal", "今天食堂的红烧肉还行，就是有点咸。", 0),
    ("normal", "这次月考我数学炸了，求安慰。", 0),
    ("normal", "推荐一本适合高中生看的历史书，谢谢！", 0),
    ("spam", "有兴趣的同学加微信 abcdef123456 详聊，有内部资料", 30),
    ("gambling", "推荐一个博彩网站，赔率很高，下注返水", 60),
    ("illegal", "提供色情服务，私聊我", 60),
]

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


def main() -> int:
    settings = load_settings()
    print("== 配置 ==")
    print(f"  AUTOMOD_ENABLED        {settings.automod_enabled}")
    print(f"  AUTOMOD_BASE_URL       {settings.automod_base_url or '(未设置)'}")
    print(f"  AUTOMOD_MODEL          {settings.automod_model or '(未设置)'}")
    print(f"  AUTOMOD_API_KEY        {'已设置' if settings.automod_api_key else '(未设置)'}")
    print(f"  AUTOMOD_HOLD_PENDING   {settings.automod_hold_pending}")
    headers = settings.automod_header_map
    print(f"  额外请求头              {headers or '(无)'}")

    problems: list[str] = []
    if not settings.automod_enabled:
        problems.append("AUTOMOD_ENABLED 还是 false —— 审核链路整个是关闭的")
    if not settings.automod_base_url:
        problems.append("AUTOMOD_BASE_URL 未设置 —— 只会跑规则层，不会调用模型")
    if not settings.automod_model:
        problems.append("AUTOMOD_MODEL 未设置 —— 同上")
    if settings.automod_base_url and not settings.automod_api_key:
        problems.append("AUTOMOD_API_KEY 未设置 —— 需要鉴权的端点会返回 401")

    base_url = settings.automod_base_url
    model = settings.automod_model
    if not settings.automod_llm_enabled or base_url is None or model is None:
        print(f"\n{YELLOW}未配置模型，跳过连通性检查。{RESET}")
        for problem in problems:
            print(f"  {YELLOW}!{RESET} {problem}")
        return 1

    print("\n== 连通性 + 判定 ==")
    from .providers import AutomodUnavailable, OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(
        base_url=base_url,
        api_key=settings.automod_api_key or "not-needed",
        model=model,
        timeout_seconds=float(settings.automod_timeout_seconds),
        extra_headers=headers,
    )

    bad = 0
    for kind, text, floor in SAMPLES:
        rules = evaluate_rules(text)
        try:
            result = provider.classify(text, context="post")
        except AutomodUnavailable as exc:
            print(f"  {RED}调用失败：{exc}{RESET}")
            print(f"  {DIM}提示：先确认 base_url 以 /v1 结尾；OpenCode Go 用 "
                  f"https://opencode.ai/zen/go/v1{RESET}")
            return 2
        risk = result.risk
        expected_low = floor == 0
        ok = (risk < 45) if expected_low else (risk >= floor)
        if not ok:
            bad += 1
        mark = f"{GREEN}OK{RESET}" if ok else f"{RED}??{RESET}"
        rules_note = f" 规则={rules.decision}/{rules.score}" if rules.signals else ""
        print(f"  [{mark}] risk={risk:3} {kind:9} {text[:34]}{DIM}{rules_note}{RESET}")
        print(f"       {DIM}模型理由：{result.reason or result.category}{RESET}")

    print()
    if bad:
        print(f"{YELLOW}{bad} 条判定与预期不符。{RESET} 误杀正常内容比漏放更伤公测体验；"
              f"先调 automod.providers._SYSTEM_PROMPT 里的判定标准再上线。")
        return 3
    print(f"{GREEN}全部符合预期。{RESET}模型可用、结构化输出可解析、样例判定正确。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
