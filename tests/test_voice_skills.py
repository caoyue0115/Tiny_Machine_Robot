from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import wave
from unittest import mock

from tests._stubs import install_dependency_stubs

install_dependency_stubs()


def _write_test_wav(frames: bytes) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    with wave.open(tmp.name, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(frames)
    return tmp.name


class VoiceSkillRouterTests(unittest.TestCase):
    def test_start_idiom_game_sets_device_state_and_returns_opening(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}:{answer_mode}"]),
        )

        result = router.route(device_id="esp-1", text="小机仔，开始成语接龙", answer_mode="short")

        self.assertEqual(result.skill_name, "idiom_game")
        self.assertIn("画龙点睛", result.answer_text)
        self.assertTrue(store.is_active("esp-1"))
        self.assertEqual(result.trace["skill_name"], "idiom_game")

    def test_idiom_game_continues_across_wakeups_for_same_device_only(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        continued = router.route(device_id="esp-1", text="精卫填海")
        other_device = router.route(device_id="esp-2", text="精卫填海")

        self.assertEqual(continued.skill_name, "idiom_game")
        self.assertIn("小机仔接：海阔天空", continued.answer_text)
        self.assertEqual(other_device.skill_name, "companion_chat")

    def test_idiom_game_rejects_wrong_chain_and_exit_clears_state(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        wrong = router.route(device_id="esp-1", text="一心一意")

        self.assertIn("要接", wrong.answer_text)
        self.assertTrue(store.is_active("esp-1"))
        exited = router.route(device_id="esp-1", text="退出游戏")
        self.assertIn("这局先到这里", exited.answer_text)
        self.assertFalse(store.is_active("esp-1"))

    def test_idiom_game_extracts_idiom_from_noisy_answer_and_uses_expanded_lexicon(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, clean_idiom_text, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        continued = router.route(device_id="esp-1", text="小机仔，那我接精忠报国吧")

        self.assertEqual(clean_idiom_text("小机仔，那我接精忠报国吧"), "精忠报国")
        self.assertEqual(continued.skill_name, "idiom_game")
        self.assertIn("小机仔接：国泰民安", continued.answer_text)

    def test_idiom_game_accepts_llm_judged_idiom_when_local_lexicon_misses(self) -> None:
        from src.voice_skills.idiom_game import (
            IdiomEntry,
            IdiomGameSkill,
            IdiomJudgeDecision,
            InMemoryIdiomGameStore,
        )
        from src.voice_skills.router import SkillRouter

        calls: list[tuple[str, str]] = []

        def _judge_unknown(text: str, expected_py: str) -> IdiomJudgeDecision:
            calls.append((text, expected_py))
            return IdiomJudgeDecision("精忠报国", "jing", "guo", confidence=0.95)

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                [
                    IdiomEntry("画龙点睛", "hua", "jing"),
                    IdiomEntry("国泰民安", "guo", "an"),
                ],
                store=store,
                opening_words=("画龙点睛",),
                judge_unknown_idiom=_judge_unknown,
                robot_difficulty="full",
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        continued = router.route(device_id="esp-1", text="我接精中报国")

        self.assertEqual(calls, [("精中报国", "jing")])
        self.assertEqual(continued.skill_name, "idiom_game")
        self.assertIn("小机仔接：国泰民安", continued.answer_text)

    def test_idiom_game_trace_marks_llm_judge_fallback_for_cost_tracking(self) -> None:
        from src.voice_skills.idiom_game import (
            IdiomEntry,
            IdiomGameSkill,
            IdiomJudgeDecision,
            InMemoryIdiomGameStore,
        )
        from src.voice_skills.router import SkillRouter

        def _judge_unknown(text: str, expected_py: str) -> IdiomJudgeDecision:
            self.assertEqual(text, "精中报国")
            self.assertEqual(expected_py, "jing")
            return IdiomJudgeDecision("精忠报国", "jing", "guo", confidence=0.95)

        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                [
                    IdiomEntry("画龙点睛", "hua", "jing"),
                    IdiomEntry("国泰民安", "guo", "an"),
                ],
                store=InMemoryIdiomGameStore(ttl_seconds=900),
                opening_words=("画龙点睛",),
                judge_unknown_idiom=_judge_unknown,
                robot_difficulty="full",
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        result = router.route(device_id="esp-1", text="我接精中报国")

        self.assertEqual(result.trace["idiom_user_match_source"], "llm_judge")
        self.assertEqual(result.trace["idiom_user_word"], "精忠报国")
        self.assertEqual(result.trace["idiom_expected_py"], "jing")
        self.assertTrue(result.trace["idiom_llm_judge_used"])
        self.assertEqual(result.trace["idiom_llm_judge_confidence"], 0.95)

    def test_idiom_game_trace_marks_local_reply_without_llm_cost(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        def _judge_unknown(text: str, expected_py: str):
            raise AssertionError(f"LLM judge should not run for local match: {text}/{expected_py}")

        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                load_default_idioms(),
                store=InMemoryIdiomGameStore(ttl_seconds=900),
                opening_words=("画龙点睛",),
                judge_unknown_idiom=_judge_unknown,
                robot_difficulty="full",
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        result = router.route(device_id="esp-1", text="精卫填海")

        self.assertEqual(result.trace["idiom_user_match_source"], "exact")
        self.assertEqual(result.trace["idiom_user_word"], "精卫填海")
        self.assertFalse(result.trace["idiom_llm_judge_used"])
        self.assertEqual(result.trace["idiom_result"], "robot_replied")
        self.assertEqual(result.trace["idiom_robot_reply_word"], "海阔天空")
        self.assertEqual(result.trace["idiom_next_expected_py"], "kong")

    def test_idiom_game_start_can_set_difficulty_from_voice_request(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )

        result = router.route(device_id="esp-1", text="我想玩成语接龙困难模式")

        state = store.get("esp-1")
        self.assertIsNotNone(state)
        self.assertEqual(state.robot_difficulty, "hard")
        self.assertIn("困难模式", result.answer_text)
        self.assertIsNone(result.audio_plan)

    def test_idiom_game_can_switch_difficulty_during_active_round(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store, opening_words=("画龙点睛",)),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        result = router.route(device_id="esp-1", text="切换到简单模式")

        state = store.get("esp-1")
        self.assertIsNotNone(state)
        self.assertEqual(state.robot_difficulty, "easy")
        self.assertEqual(state.expected_py, "jing")
        self.assertIn("已切换到简单模式", result.answer_text)
        self.assertNotIn("词库", result.answer_text)

    def test_idiom_game_difficulty_switch_changes_reply_pool(self) -> None:
        from src.voice_skills.idiom_game import IdiomEntry, IdiomGameSkill, InMemoryIdiomGameStore
        from src.voice_skills.router import SkillRouter

        filler = [IdiomEntry(f"占位{i:03d}", "zhan", "zhan") for i in range(498)]
        idioms = [
            IdiomEntry("画龙点睛", "hua", "jing"),
            IdiomEntry("精忠报国", "jing", "guo"),
            *filler,
            IdiomEntry("国泰民安", "guo", "an"),
        ]
        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                idioms,
                store=store,
                opening_words=("画龙点睛",),
                robot_difficulty="easy",
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")
        router.route(device_id="esp-1", text="切换到普通模式")

        result = router.route(device_id="esp-1", text="精忠报国")

        self.assertIn("小机仔接：国泰民安", result.answer_text)

    def test_idiom_game_limited_robot_pool_lets_user_win(self) -> None:
        from src.voice_skills.idiom_game import IdiomEntry, IdiomGameSkill, InMemoryIdiomGameStore
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                [
                    IdiomEntry("画龙点睛", "hua", "jing"),
                    IdiomEntry("精忠报国", "jing", "guo"),
                    IdiomEntry("国泰民安", "guo", "an"),
                ],
                store=store,
                opening_words=("画龙点睛",),
                robot_reply_limit=1,
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        result = router.route(device_id="esp-1", text="精忠报国")

        self.assertIn("这局你赢", result.answer_text)
        self.assertFalse(store.is_active("esp-1"))

    def test_idiom_game_target_user_turns_can_end_as_user_win(self) -> None:
        from src.voice_skills.idiom_game import IdiomEntry, IdiomGameSkill, InMemoryIdiomGameStore
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                [
                    IdiomEntry("画龙点睛", "hua", "jing"),
                    IdiomEntry("精忠报国", "jing", "guo"),
                    IdiomEntry("国泰民安", "guo", "an"),
                ],
                store=store,
                opening_words=("画龙点睛",),
                target_user_turns=1,
                robot_difficulty="full",
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        result = router.route(device_id="esp-1", text="精忠报国")

        self.assertIn("连续接上1轮", result.answer_text)
        self.assertIn("这局你赢", result.answer_text)
        self.assertFalse(store.is_active("esp-1"))

    def test_weather_and_translation_are_explicit_placeholders(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                load_default_idioms(),
                store=InMemoryIdiomGameStore(ttl_seconds=900),
                opening_words=("画龙点睛",),
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )

        weather = router.route(device_id="esp-1", text="今天上海天气怎么样")
        translation = router.route(device_id="esp-1", text="帮我专业翻译这句话")

        self.assertEqual(weather.skill_name, "weather_placeholder")
        self.assertIn("天气技能还没接好", weather.answer_text)
        self.assertEqual(translation.skill_name, "translation_placeholder")
        self.assertIn("翻译技能还没接好", translation.answer_text)

    def test_default_route_uses_companion_streamer(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        router = SkillRouter(
            idiom_skill=IdiomGameSkill(
                load_default_idioms(),
                store=InMemoryIdiomGameStore(ttl_seconds=900),
                opening_words=("画龙点睛",),
            ),
            companion_streamer=lambda text, answer_mode=None: iter([f"陪伴:{text}:{answer_mode}"]),
        )

        result = router.route(device_id="esp-1", text="你今天开心吗", answer_mode="short")

        self.assertEqual(result.skill_name, "companion_chat")
        self.assertEqual("".join(result.answer_stream or []), "陪伴:你今天开心吗:short")


class RealtimeSkillIntegrationTests(unittest.TestCase):
    def test_realtime_session_routes_text_to_skills_without_default_coffee_rag(self) -> None:
        from src.services import realtime_session as realtime_session_service
        from src.storage.realtime_store import InMemoryRealtimeSessionStore

        store = InMemoryRealtimeSessionStore(base_url="http://testserver")
        session = store.create_session(device_id="esp-skill-1")
        store.update_session(session["session_id"], question_text="开始成语接龙")

        def _stream_realtime_tts_chunks(text_chunks, **kwargs):
            del kwargs
            list(text_chunks)
            return iter([b"\x01\x00"])

        with mock.patch.object(
            realtime_session_service,
            "retrieve_references",
            side_effect=AssertionError("coffee RAG should not run by default"),
        ), mock.patch.object(
            realtime_session_service,
            "realtime_tts_health",
            return_value=True,
        ), mock.patch.object(
            realtime_session_service,
            "stream_realtime_tts_chunks",
            side_effect=_stream_realtime_tts_chunks,
        ):
            realtime_session_service.run_stub_realtime_session(store, session["session_id"])

        updated = store.get_session(session["session_id"])
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["trace"]["skill_name"], "idiom_game")
        self.assertIn("好呀，我们玩成语接龙", updated["answer_text"])

    def test_realtime_session_uses_static_audio_plan_before_tts(self) -> None:
        from src.services import realtime_session as realtime_session_service
        from src.storage.realtime_store import InMemoryRealtimeSessionStore
        from src.voice_skills.router import SkillResult

        audio_plan = [
            "idiom_game/robot_reply",
            "idioms/国泰民安",
            "idiom_game/turn_prompt",
            "pinyin/an",
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for index, segment_id in enumerate(audio_plan, start=1):
                path = root / f"{segment_id}.pcm"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bytes([index, 0]))

            store = InMemoryRealtimeSessionStore(base_url="http://testserver")
            session = store.create_session(device_id="esp-static-audio")
            store.update_session(session["session_id"], question_text="我接精忠报国")

            original_static_audio_dir = realtime_session_service.settings.static_audio_dir
            original_static_audio_enabled = realtime_session_service.settings.static_audio_enabled
            try:
                realtime_session_service.settings.static_audio_dir = str(root)
                realtime_session_service.settings.static_audio_enabled = True
                with mock.patch.object(
                    realtime_session_service,
                    "route_voice_skill",
                    return_value=SkillResult(
                        skill_name="idiom_game",
                        answer_text="小机仔接：国泰民安。轮到你啦，要接“an”。",
                        audio_plan=audio_plan,
                        trace={"skill_name": "idiom_game"},
                    ),
                ), mock.patch.object(
                    realtime_session_service,
                    "realtime_tts_health",
                    return_value=True,
                ), mock.patch.object(
                    realtime_session_service,
                    "stream_realtime_tts_chunks",
                    side_effect=AssertionError("realtime TTS should not run for complete static audio plan"),
                ), mock.patch.object(
                    realtime_session_service,
                    "synthesize_audio",
                    side_effect=AssertionError("fallback TTS should not run for complete static audio plan"),
                ):
                    realtime_session_service.run_stub_realtime_session(store, session["session_id"])
            finally:
                realtime_session_service.settings.static_audio_dir = original_static_audio_dir
                realtime_session_service.settings.static_audio_enabled = original_static_audio_enabled

        updated = store.get_session(session["session_id"])
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["trace"]["static_audio_used"], True)
        self.assertEqual(updated["trace"]["static_audio_segment_count"], len(audio_plan))
        self.assertEqual(updated["trace"]["audio_bytes"], len(audio_plan) * 2)

    def test_realtime_session_falls_back_to_tts_when_static_audio_plan_is_incomplete(self) -> None:
        from src.services import realtime_session as realtime_session_service
        from src.storage.realtime_store import InMemoryRealtimeSessionStore
        from src.voice_skills.router import SkillResult

        wav_path = _write_test_wav(b"\x01\x00\x02\x00")
        store = InMemoryRealtimeSessionStore(base_url="http://testserver")
        session = store.create_session(device_id="esp-static-audio-fallback")
        store.update_session(session["session_id"], question_text="我接精忠报国")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                original_static_audio_dir = realtime_session_service.settings.static_audio_dir
                original_static_audio_enabled = realtime_session_service.settings.static_audio_enabled
                try:
                    realtime_session_service.settings.static_audio_dir = tmpdir
                    realtime_session_service.settings.static_audio_enabled = True
                    with mock.patch.object(
                        realtime_session_service,
                        "route_voice_skill",
                        return_value=SkillResult(
                            skill_name="idiom_game",
                            answer_text="小机仔接：国泰民安。轮到你啦，要接“an”。",
                            audio_plan=["idiom_game/robot_reply", "idioms/国泰民安", "idiom_game/turn_prompt", "pinyin/an"],
                            trace={"skill_name": "idiom_game"},
                        ),
                    ), mock.patch.object(
                        realtime_session_service,
                        "realtime_tts_health",
                        return_value=False,
                    ), mock.patch.object(
                        realtime_session_service,
                        "synthesize_audio",
                        return_value=(wav_path, None),
                    ) as synthesize_audio:
                        realtime_session_service.run_stub_realtime_session(store, session["session_id"])
                finally:
                    realtime_session_service.settings.static_audio_dir = original_static_audio_dir
                    realtime_session_service.settings.static_audio_enabled = original_static_audio_enabled
        finally:
            Path(wav_path).unlink(missing_ok=True)

        updated = store.get_session(session["session_id"])
        self.assertEqual(updated["status"], "done")
        self.assertEqual(updated["trace"]["static_audio_used"], False)
        self.assertEqual(updated["trace"]["static_audio_missing"], True)
        synthesize_audio.assert_called()


if __name__ == "__main__":
    unittest.main()
