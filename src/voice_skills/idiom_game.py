from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path


_TEXT_CLEAN_RE = re.compile(r"[\s,，。.!！?？:：;；、\"'“”‘’（）()\[\]【】]+")


@dataclass(frozen=True)
class IdiomEntry:
    word: str
    first_py: str
    last_py: str


@dataclass
class IdiomGameState:
    expected_py: str
    used_words: set[str] = field(default_factory=set)
    updated_at: float = field(default_factory=time.time)


def clean_idiom_text(text: str) -> str:
    cleaned = _TEXT_CLEAN_RE.sub("", str(text or ""))
    for prefix in ("小机仔", "小明同学"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned


def load_default_idioms(path: Path | None = None) -> list[IdiomEntry]:
    idiom_path = path or Path(__file__).with_name("idioms.json")
    payload = json.loads(idiom_path.read_text(encoding="utf-8"))
    return [
        IdiomEntry(
            word=str(item["word"]),
            first_py=str(item["first_py"]).strip().lower(),
            last_py=str(item["last_py"]).strip().lower(),
        )
        for item in payload
    ]


class InMemoryIdiomGameStore:
    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._states: dict[str, IdiomGameState] = {}

    def get(self, device_id: str) -> IdiomGameState | None:
        state = self._states.get(device_id)
        if state is None:
            return None
        if time.time() - state.updated_at > self._ttl_seconds:
            self._states.pop(device_id, None)
            return None
        return state

    def save(self, device_id: str, state: IdiomGameState) -> None:
        state.updated_at = time.time()
        self._states[device_id] = state

    def clear(self, device_id: str) -> None:
        self._states.pop(device_id, None)

    def is_active(self, device_id: str) -> bool:
        return self.get(device_id) is not None


class IdiomGameSkill:
    skill_name = "idiom_game"

    def __init__(self, idioms: list[IdiomEntry], store: InMemoryIdiomGameStore | None = None) -> None:
        if not idioms:
            raise ValueError("idiom_game_requires_idioms")
        self._idioms = idioms
        self._by_word = {entry.word: entry for entry in idioms}
        self._store = store or InMemoryIdiomGameStore()

    @property
    def store(self) -> InMemoryIdiomGameStore:
        return self._store

    def start(self, device_id: str) -> str:
        opening = self._idioms[0]
        self._store.save(
            device_id,
            IdiomGameState(
                expected_py=opening.last_py,
                used_words={opening.word},
            ),
        )
        return f"好呀，我们玩成语接龙。小机仔先来：{opening.word}。轮到你啦，要接“{opening.last_py}”。"

    def exit(self, device_id: str) -> str:
        self._store.clear(device_id)
        return "这局先到这里，小机仔把小本本合上啦。"

    def handle(self, device_id: str, text: str) -> str:
        state = self._store.get(device_id)
        if state is None:
            return self.start(device_id)

        cleaned = clean_idiom_text(text)
        user_entry = self._by_word.get(cleaned)
        if user_entry is None:
            return "这个我还没在成语小词库里找到。你可以换一个四字成语再接。"
        if user_entry.word in state.used_words:
            return f"“{user_entry.word}”刚刚用过啦，成语接龙不能重复哦。"
        if user_entry.first_py != state.expected_py:
            return f"要接“{state.expected_py}”开头的成语哦。你可以再来一次。"

        used_words = set(state.used_words)
        used_words.add(user_entry.word)
        reply_entry = self._find_reply(user_entry.last_py, used_words)
        if reply_entry is None:
            self._store.clear(device_id)
            return f"你接上了“{user_entry.word}”，小机仔暂时接不上啦，这局你赢。"

        used_words.add(reply_entry.word)
        self._store.save(
            device_id,
            IdiomGameState(
                expected_py=reply_entry.last_py,
                used_words=used_words,
            ),
        )
        return f"小机仔接：{reply_entry.word}。轮到你啦，要接“{reply_entry.last_py}”。"

    def _find_reply(self, first_py: str, used_words: set[str]) -> IdiomEntry | None:
        for entry in self._idioms:
            if entry.first_py == first_py and entry.word not in used_words:
                return entry
        return None
