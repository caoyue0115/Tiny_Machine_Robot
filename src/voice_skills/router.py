from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from src.settings import settings
from src.voice_skills.companion_chat import stream_companion_chat
from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, clean_idiom_text, load_default_idioms


CompanionStreamer = Callable[[str, str | None], Iterable[str]]

_START_IDIOM_GAME_PHRASES = ("开始成语接龙", "玩成语接龙", "来成语接龙", "成语接龙")
_EXIT_IDIOM_GAME_PHRASES = ("退出", "结束", "不玩了", "退出游戏", "结束游戏")


@dataclass
class SkillResult:
    skill_name: str
    answer_text: str | None = None
    answer_stream: Iterable[str] | None = None
    end_skill_state: bool = False
    trace: dict = field(default_factory=dict)


def _parse_enabled_skills(raw: str | Iterable[str] | None) -> set[str]:
    if raw is None:
        return {"idiom_game", "companion_chat"}
    if isinstance(raw, str):
        return {part.strip() for part in raw.split(",") if part.strip()}
    return {str(part).strip() for part in raw if str(part).strip()}


class SkillRouter:
    def __init__(
        self,
        *,
        idiom_skill: IdiomGameSkill,
        companion_streamer: CompanionStreamer = stream_companion_chat,
        enabled_skills: str | Iterable[str] | None = None,
    ) -> None:
        self._idiom_skill = idiom_skill
        self._companion_streamer = companion_streamer
        self._enabled_skills = _parse_enabled_skills(enabled_skills)

    def route(
        self,
        *,
        device_id: str,
        text: str,
        answer_mode: str | None = None,
        trace: dict | None = None,
    ) -> SkillResult:
        raw_text = str(text or "").strip()
        cleaned_text = clean_idiom_text(raw_text)
        base_trace = dict(trace or {})

        if "idiom_game" in self._enabled_skills and self._idiom_skill.store.is_active(device_id):
            if self._matches_any(cleaned_text, _EXIT_IDIOM_GAME_PHRASES):
                return self._text_result(
                    "idiom_game",
                    self._idiom_skill.exit(device_id),
                    base_trace,
                    end_skill_state=True,
                )
            return self._text_result("idiom_game", self._idiom_skill.handle(device_id, raw_text), base_trace)

        if "idiom_game" in self._enabled_skills and self._matches_any(cleaned_text, _START_IDIOM_GAME_PHRASES):
            return self._text_result("idiom_game", self._idiom_skill.start(device_id), base_trace)

        if "天气" in cleaned_text:
            return self._text_result("weather_placeholder", "天气技能还没接好，小机仔先不乱报天气。", base_trace)

        if "翻译" in cleaned_text:
            return self._text_result("translation_placeholder", "翻译技能还没接好，小机仔先不装专业翻译。", base_trace)

        if "companion_chat" not in self._enabled_skills:
            return self._text_result("companion_chat_disabled", "陪伴聊天技能暂时没有启用。", base_trace)

        result_trace = self._trace(base_trace, "companion_chat")
        return SkillResult(
            skill_name="companion_chat",
            answer_stream=self._companion_streamer(raw_text, answer_mode),
            trace=result_trace,
        )

    @staticmethod
    def _matches_any(cleaned_text: str, phrases: tuple[str, ...]) -> bool:
        return any(phrase in cleaned_text for phrase in phrases)

    def _text_result(
        self,
        skill_name: str,
        answer_text: str,
        trace: dict,
        *,
        end_skill_state: bool = False,
    ) -> SkillResult:
        return SkillResult(
            skill_name=skill_name,
            answer_text=answer_text,
            end_skill_state=end_skill_state,
            trace=self._trace(trace, skill_name),
        )

    @staticmethod
    def _trace(trace: dict, skill_name: str) -> dict:
        result = dict(trace)
        result["skill_name"] = skill_name
        return result


_default_router: SkillRouter | None = None


def get_default_skill_router() -> SkillRouter:
    global _default_router
    if _default_router is None:
        _default_router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                load_default_idioms(),
                store=InMemoryIdiomGameStore(ttl_seconds=settings.idiom_game_ttl_seconds),
            ),
            enabled_skills=settings.enabled_skills,
        )
    return _default_router


def route_voice_skill(
    *,
    device_id: str,
    text: str,
    answer_mode: str | None = None,
    trace: dict | None = None,
) -> SkillResult:
    return get_default_skill_router().route(
        device_id=device_id,
        text=text,
        answer_mode=answer_mode,
        trace=trace,
    )
