from __future__ import annotations

import unittest
from unittest import mock

from tests._stubs import install_dependency_stubs

install_dependency_stubs()


class VoiceSkillRouterTests(unittest.TestCase):
    def test_start_idiom_game_sets_device_state_and_returns_opening(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        store = InMemoryIdiomGameStore(ttl_seconds=900)
        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store),
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
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store),
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
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=store),
            companion_streamer=lambda text, answer_mode=None: iter([f"chat:{text}"]),
        )
        router.route(device_id="esp-1", text="开始成语接龙")

        wrong = router.route(device_id="esp-1", text="一心一意")

        self.assertIn("要接", wrong.answer_text)
        self.assertTrue(store.is_active("esp-1"))
        exited = router.route(device_id="esp-1", text="退出游戏")
        self.assertIn("这局先到这里", exited.answer_text)
        self.assertFalse(store.is_active("esp-1"))

    def test_weather_and_translation_are_explicit_placeholders(self) -> None:
        from src.voice_skills.idiom_game import IdiomGameSkill, InMemoryIdiomGameStore, load_default_idioms
        from src.voice_skills.router import SkillRouter

        router = SkillRouter(
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=InMemoryIdiomGameStore(ttl_seconds=900)),
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
            idiom_skill=IdiomGameSkill(load_default_idioms(), store=InMemoryIdiomGameStore(ttl_seconds=900)),
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
        self.assertIn("画龙点睛", updated["answer_text"])


if __name__ == "__main__":
    unittest.main()
