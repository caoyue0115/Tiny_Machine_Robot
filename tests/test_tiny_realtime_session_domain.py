from __future__ import annotations

import unittest

from tests._stubs import install_dependency_stubs

install_dependency_stubs()

from src.domain.coffee import COFFEE_RETRY_TEXT as DOMAIN_COFFEE_RETRY_TEXT
from src.services import realtime_session


class TinyRealtimeSessionDomainTests(unittest.TestCase):
    def test_coffee_asr_normalization_rules_are_named(self) -> None:
        normalized, rules = realtime_session.normalize_coffee_asr_text("美式和拿帖有什么区别")

        self.assertEqual(normalized, "美式和拿铁有什么区别")
        self.assertEqual(rules, ["拿帖->拿铁"])

    def test_coffee_asr_normalization_covers_direct_coffee_terms(self) -> None:
        cases = [
            ("拿贴有什么区别", "拿铁有什么区别", ["拿贴->拿铁"]),
            ("卡布其诺怎么做", "卡布奇诺怎么做", ["卡布其诺->卡布奇诺"]),
            ("卡布奇洛怎么做", "卡布奇诺怎么做", ["卡布奇洛->卡布奇诺"]),
            ("意式浓锁怎么萃取", "意式浓缩怎么萃取", ["意式浓锁->意式浓缩"]),
            ("手充咖啡怎么冲", "手冲咖啡怎么冲", ["手充->手冲"]),
            ("手冲加啡怎么冲", "手冲咖啡怎么冲", ["手冲加啡->手冲咖啡"]),
            ("咖非怎么选豆", "咖啡怎么选豆", ["咖非->咖啡"]),
            ("格夏豆适合手冲吗", "瑰夏豆适合手冲吗", ["格夏->瑰夏"]),
            ("手充咖啡用格夏豆", "手冲咖啡用瑰夏豆", ["手充->手冲", "格夏->瑰夏"]),
        ]

        for raw_text, expected_text, expected_rules in cases:
            with self.subTest(raw_text=raw_text):
                normalized, rules = realtime_session.normalize_coffee_asr_text(raw_text)
                self.assertEqual(normalized, expected_text)
                self.assertEqual(rules, expected_rules)

    def test_coffee_asr_normalization_chains_order_dependent_rules(self) -> None:
        normalized, rules = realtime_session.normalize_coffee_asr_text("手充加啡怎么冲")

        self.assertEqual(normalized, "手冲咖啡怎么冲")
        self.assertEqual(rules, ["手充->手冲", "手冲加啡->手冲咖啡"])

    def test_reject_prompt_is_coffee_specific_asset(self) -> None:
        self.assertEqual(realtime_session.COFFEE_RETRY_TEXT, "我还没听清，可以再问我一个咖啡问题吗？")
        self.assertEqual(DOMAIN_COFFEE_RETRY_TEXT, realtime_session.COFFEE_RETRY_TEXT)

    def test_coffee_asr_normalization_recovers_missing_coffee_for_acid_cup(self) -> None:
        normalized, rules = realtime_session.normalize_coffee_asr_text("\u676f\u662f\u9178\u7684\u3002")

        self.assertEqual(normalized, "\u5496\u5561\u662f\u9178\u7684\u3002")
        self.assertEqual(rules, ["\u676f\u662f\u9178\u7684->\u5496\u5561\u662f\u9178\u7684"])


if __name__ == "__main__":
    unittest.main()
