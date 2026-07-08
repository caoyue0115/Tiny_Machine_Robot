from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from src.settings import settings


ANSWER_MODE_DEFAULT = "default"
ANSWER_MODE_SHORT = "short"
SHORT_ANSWER_MAX_TOKENS = 96
TINY_COFFEE_SYSTEM_PROMPT = (
    "你是小机仔，一名咖啡问答助手。"
    "只回答咖啡豆、饮品、器具、研磨、萃取、烘焙、风味和冲煮相关问题。"
    "请用中文口语化回答，适合语音播报，控制在1到3句话。"
    "只能依据给定咖啡资料证据作答；资料不足就说明不确定，并引导用户换一个咖啡问题。"
    "不要编造品牌、价格、门店或活动信息，也不要编造库存或优惠信息。"
)
COMPANION_SYSTEM_PROMPT = (
    "你是{assistant_name}，一个可爱的语音桌宠。"
    "请用中文口语化短句陪用户聊天，语气亲切、轻快，但不要装作能看到现实环境。"
    "遇到天气、实时新闻、价格、库存、门店、活动等实时事实，不要编造，说明这个技能还没接好。"
    "默认回答适合语音播报，控制在1到3句话。"
)


def llm_health() -> bool:
    return bool(settings.dashscope_api_key)


def _build_client() -> OpenAI:
    return OpenAI(
        api_key=settings.dashscope_api_key,
        base_url=f"{settings.dashscope_base_url.rstrip('/')}/compatible-mode/v1",
        timeout=float(settings.request_timeout_seconds),
    )


def _normalize_answer_mode(answer_mode: str | None) -> str:
    return ANSWER_MODE_SHORT if str(answer_mode or "").strip().lower() == ANSWER_MODE_SHORT else ANSWER_MODE_DEFAULT


def _build_messages(
    question_text: str,
    references: list[dict],
    *,
    answer_mode: str | None = None,
) -> list[dict[str, str]]:
    evidence = "\n".join(f"- {item['source_title']}: {item['snippet']}" for item in references)
    if _normalize_answer_mode(answer_mode) == ANSWER_MODE_SHORT:
        return [
            {
                "role": "system",
                "content": TINY_COFFEE_SYSTEM_PROMPT + "短答时尽量1到2句，总字数不超过70字。",
            },
            {
                "role": "user",
                "content": (
                    f"问题：{question_text}\n"
                    f"证据：\n{evidence}\n"
                    "回答要求：请用中文回答，适合语音播报，1到3句内，总字数不超过70字。"
                    "先直接回答咖啡问题，再给一句可操作的理解或建议。"
                    "不要把资料来源直接说出口。"
                ),
            },
        ]
    return [
        {
            "role": "system",
            "content": TINY_COFFEE_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"问题：{question_text}\n"
                f"证据：\n{evidence}\n"
                "回答要求：先直接回答咖啡问题，再给一句简短原因或建议。"
                "不要把资料来源直接说出口。"
            ),
        },
    ]


def _build_companion_messages(question_text: str, *, answer_mode: str | None = None) -> list[dict[str, str]]:
    system_prompt = COMPANION_SYSTEM_PROMPT.format(assistant_name=settings.assistant_name)
    if _normalize_answer_mode(answer_mode) == ANSWER_MODE_SHORT:
        system_prompt += "短答时尽量1到2句，总字数不超过70字。"
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"用户说：{question_text}\n"
                "回答要求：直接回应用户，适合语音播报。"
                "如果问题需要未接入的外部能力，就友好说明暂时还没接好。"
            ),
        },
    ]


def _max_tokens_for_answer_mode(answer_mode: str | None) -> int:
    if _normalize_answer_mode(answer_mode) == ANSWER_MODE_SHORT:
        return min(settings.llm_max_tokens, SHORT_ANSWER_MAX_TOKENS)
    return settings.llm_max_tokens


def _iter_chunk_text(response_stream: object) -> Iterator[str]:
    for chunk in response_stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            if content:
                yield content
            continue
        if isinstance(content, list):
            for item in content:
                text = getattr(item, "text", None) or getattr(item, "content", None)
                if text:
                    yield str(text)


def stream_answer_text(
    question_text: str,
    references: list[dict],
    *,
    answer_mode: str | None = None,
) -> Iterator[str]:
    client = _build_client()
    response_stream = client.chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=_max_tokens_for_answer_mode(answer_mode),
        messages=_build_messages(question_text, references, answer_mode=answer_mode),
        stream=True,
        extra_body={"enable_thinking": False},
    )
    yield from _iter_chunk_text(response_stream)


def stream_companion_answer_text(question_text: str, *, answer_mode: str | None = None) -> Iterator[str]:
    if not llm_health():
        yield f"{settings.assistant_name}在呢，先陪你聊一会儿。"
        return
    client = _build_client()
    response_stream = client.chat.completions.create(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_tokens=_max_tokens_for_answer_mode(answer_mode),
        messages=_build_companion_messages(question_text, answer_mode=answer_mode),
        stream=True,
        extra_body={"enable_thinking": False},
    )
    yield from _iter_chunk_text(response_stream)


def generate_answer(question_text: str, references: list[dict]) -> str:
    return "".join(stream_answer_text(question_text, references)).strip()
