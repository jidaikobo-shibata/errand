import unittest

from chat_foundation.core.timing import ResponseTiming


class TimingTests(unittest.TestCase):
    def test_first_text_and_completion_have_distinct_durations(self):
        timing = ResponseTiming(100)
        self.assertEqual(timing.display(), ('', ''))
        timing.text_received(103.2)
        timing.text_received(109)
        timing.finish(112.3)
        text, details = timing.display()
        self.assertEqual(text, '12.3秒 · 初字 3.2秒')
        self.assertIn('終了まで: 9.1秒', details)
        timing.finish(150, 'failed')
        self.assertEqual(timing.display(), (text, details))

    def test_interrupted_failed_and_incomplete_are_not_reported_as_completed(self):
        for status, label in [('interrupted', '中断'), ('failed', '失敗'), ('incomplete', '途中まで')]:
            timing = ResponseTiming(100)
            timing.finish(104, status)
            self.assertEqual(timing.display()[0], label + ' · 4.0秒')
            timing.text_received(105)
            self.assertIsNone(timing.first_text_at)

    def test_clock_values_cannot_produce_negative_durations(self):
        timing = ResponseTiming(100)
        timing.text_received(99)
        timing.finish(98)
        self.assertEqual(timing.display()[0], '0.0秒 · 初字 0.0秒')
