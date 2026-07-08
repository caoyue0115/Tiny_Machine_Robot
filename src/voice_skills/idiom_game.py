from __future__ import annotations

import json
import random
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


_TEXT_CLEAN_RE = re.compile(r"[\s,，。.!！?？:：;；、\"'“”‘’（）()\[\]【】]+")
_START_REPLY_RE = re.compile(r"小机仔先来：(?P<word>[^。]+)。轮到你啦，要接“(?P<py>[^”]+)”")
_CHAIN_REPLY_RE = re.compile(r"小机仔接：(?P<word>[^。]+)。轮到你啦，要接“(?P<py>[^”]+)”")
_NEED_REPLY_RE = re.compile(r"要接“(?P<py>[^”]+)”开头的成语哦")
_REPEATED_REPLY_RE = re.compile(r"“(?P<word>[^”]+)”刚刚用过啦")
_WIN_REPLY_RE = re.compile(r"你接上了“(?P<word>[^”]+)”，小机仔暂时接不上啦")
_ASSISTANT_PREFIXES = ("小机仔", "小明同学")
_LEADING_NOISE_PHRASES = tuple(
    sorted(
        (
            "那我来接一个",
            "那我接一个",
            "我来接一个",
            "我想接一个",
            "我要接一个",
            "我接一个",
            "接一个",
            "那我来接",
            "那我接",
            "我来接",
            "我想接",
            "我要接",
            "我接",
            "答案是",
            "成语是",
            "接",
        ),
        key=len,
        reverse=True,
    )
)
_TRAILING_NOISE_PARTICLES = ("吧", "呀", "啊", "哦", "啦", "了", "呢")
_DEFAULT_OPENING_WORDS = (
    "画龙点睛",
    "精卫填海",
    "海阔天空",
    "空前绝后",
    "后来居上",
    "上善若水",
    "水到渠成",
    "成竹在胸",
    "胸有成竹",
    "竹报平安",
    "安居乐业",
    "叶公好龙",
    "龙马精神",
    "神采飞扬",
    "扬眉吐气",
    "气象万千",
    "千言万语",
    "语重心长",
    "长驱直入",
    "入木三分",
)


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
    for prefix in _ASSISTANT_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    for prefix in _LEADING_NOISE_PHRASES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    while len(cleaned) > 4 and cleaned.endswith(_TRAILING_NOISE_PARTICLES):
        cleaned = cleaned[:-1]
    return cleaned


def build_idiom_audio_plan(answer_text: str) -> list[str] | None:
    text = str(answer_text or "")
    start_match = _START_REPLY_RE.search(text)
    if start_match:
        return [
            "idiom_game/start",
            "idiom_game/robot_first",
            f"idioms/{start_match.group('word')}",
            "idiom_game/turn_prompt",
            f"pinyin/{start_match.group('py')}",
        ]

    chain_match = _CHAIN_REPLY_RE.search(text)
    if chain_match:
        return [
            "idiom_game/robot_reply",
            f"idioms/{chain_match.group('word')}",
            "idiom_game/turn_prompt",
            f"pinyin/{chain_match.group('py')}",
        ]

    need_match = _NEED_REPLY_RE.search(text)
    if need_match:
        return [
            "idiom_game/need_prefix",
            f"pinyin/{need_match.group('py')}",
            "idiom_game/need_suffix",
        ]

    repeated_match = _REPEATED_REPLY_RE.search(text)
    if repeated_match:
        return [
            "idiom_game/repeated_prefix",
            f"idioms/{repeated_match.group('word')}",
            "idiom_game/repeated_suffix",
        ]

    win_match = _WIN_REPLY_RE.search(text)
    if win_match:
        return [
            "idiom_game/user_connected_prefix",
            f"idioms/{win_match.group('word')}",
            "idiom_game/robot_no_reply_user_win",
        ]

    if "这个我还没在成语词库里找到" in text:
        return ["idiom_game/not_found"]
    if "这局先到这里" in text:
        return ["idiom_game/exit"]
    return None


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

    def __init__(
        self,
        idioms: list[IdiomEntry],
        store: InMemoryIdiomGameStore | None = None,
        *,
        opening_words: Iterable[str] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        if not idioms:
            raise ValueError("idiom_game_requires_idioms")
        self._idioms = idioms
        self._by_word = {entry.word: entry for entry in idioms}
        self._by_first_py: dict[str, list[IdiomEntry]] = defaultdict(list)
        for entry in idioms:
            self._by_first_py[entry.first_py].append(entry)
        self._known_words = sorted(self._by_word, key=len, reverse=True)
        opening_pool_words = tuple(opening_words or _DEFAULT_OPENING_WORDS)
        self._opening_pool = [self._by_word[word] for word in opening_pool_words if word in self._by_word]
        if not self._opening_pool:
            self._opening_pool = self._idioms[: min(64, len(self._idioms))]
        self._rng = rng or random.SystemRandom()
        self._store = store or InMemoryIdiomGameStore()

    @property
    def store(self) -> InMemoryIdiomGameStore:
        return self._store

    def start(self, device_id: str) -> str:
        opening = self._rng.choice(self._opening_pool)
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
        user_entry = self._extract_user_entry(cleaned, expected_py=state.expected_py)
        if user_entry is None:
            return "这个我还没在成语词库里找到。你可以换一个四字成语再接。"
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

    def _extract_user_entry(self, cleaned_text: str, *, expected_py: str) -> IdiomEntry | None:
        exact = self._by_word.get(cleaned_text)
        if exact is not None:
            return exact

        expected_entry = self._find_entry_in_text(cleaned_text, self._by_first_py.get(expected_py, []))
        if expected_entry is not None:
            return expected_entry

        return self._find_entry_in_text(cleaned_text, (self._by_word[word] for word in self._known_words))

    @staticmethod
    def _find_entry_in_text(cleaned_text: str, entries: Iterable[IdiomEntry]) -> IdiomEntry | None:
        best: tuple[int, int, IdiomEntry] | None = None
        for entry in entries:
            index = cleaned_text.find(entry.word)
            if index < 0:
                continue
            candidate = (index, -len(entry.word), entry)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
        return best[2] if best is not None else None

    def _find_reply(self, first_py: str, used_words: set[str]) -> IdiomEntry | None:
        for entry in self._by_first_py.get(first_py, []):
            if entry.word not in used_words:
                return entry
        return None
