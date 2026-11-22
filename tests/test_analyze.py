"""Tests for the analyzer: classification, sentiment, urgency, PII."""

import os
import sys
import unittest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
)

from analyze import (  # noqa: E402
    analyze_transcript,
    assess_urgency,
    classify_category,
    redact_pii,
    score_sentiment,
)


class CategoryTests(unittest.TestCase):
    def test_billing_keywords_classify_as_billing(self):
        text = "I was charged twice and the invoice is wrong, I want a refund."
        self.assertEqual(classify_category(text.lower()), "billing")

    def test_outage_keywords_classify_as_outage(self):
        text = "There is an outage, we have no service and everything is offline."
        self.assertEqual(classify_category(text.lower()), "outage")

    def test_access_keywords_classify_as_access(self):
        text = "I am locked out and my password reset never arrived."
        self.assertEqual(classify_category(text.lower()), "access")

    def test_technical_keywords_classify_as_technical(self):
        text = "The router light is blinking and the device is not working."
        self.assertEqual(classify_category(text.lower()), "technical")

    def test_no_keywords_classify_as_other(self):
        text = "I am calling to change my appointment to next week."
        self.assertEqual(classify_category(text.lower()), "other")


class SentimentTests(unittest.TestCase):
    def test_positive_words_score_positive(self):
        label, score = score_sentiment("thank you, that was great and helpful")
        self.assertEqual(label, "positive")
        self.assertGreater(score, 0)

    def test_negative_words_score_negative(self):
        label, score = score_sentiment("this is terrible and I am furious")
        self.assertEqual(label, "negative")
        self.assertLess(score, 0)

    def test_plain_text_scores_neutral(self):
        label, score = score_sentiment("I am calling about my appointment")
        self.assertEqual(label, "neutral")
        self.assertEqual(score, 0)


class UrgencyTests(unittest.TestCase):
    def test_urgency_words_mean_high(self):
        self.assertEqual(
            assess_urgency("this is urgent, I cannot work", "neutral"), "high"
        )

    def test_negative_sentiment_means_medium(self):
        self.assertEqual(assess_urgency("the charge is wrong", "negative"), "medium")

    def test_calm_call_means_low(self):
        self.assertEqual(assess_urgency("just a question", "neutral"), "low")


class PiiTests(unittest.TestCase):
    def test_valid_card_number_is_flagged_and_redacted(self):
        redacted, kinds = redact_pii("my card is 4111 1111 1111 1111 thanks")
        self.assertIn("card", kinds)
        self.assertIn("[REDACTED-CARD]", redacted)
        self.assertNotIn("4111", redacted)

    def test_random_digits_do_not_flag_as_card(self):
        # 1234 5678 9012 3456 fails the Luhn check, so it stays as is.
        redacted, kinds = redact_pii("reference 1234 5678 9012 3456 on the case")
        self.assertNotIn("card", kinds)
        self.assertIn("1234 5678 9012 3456", redacted)

    def test_ssn_is_flagged_and_redacted(self):
        redacted, kinds = redact_pii("my social is 123-45-6789 for the check")
        self.assertIn("ssn", kinds)
        self.assertIn("[REDACTED-SSN]", redacted)
        self.assertNotIn("123-45-6789", redacted)

    def test_phone_is_flagged_and_redacted(self):
        redacted, kinds = redact_pii("call me back on (555) 014-2288 please")
        self.assertIn("phone", kinds)
        self.assertIn("[REDACTED-PHONE]", redacted)
        self.assertNotIn("014-2288", redacted)

    def test_email_is_flagged_and_redacted(self):
        redacted, kinds = redact_pii("send it to jane.doe@example.com today")
        self.assertIn("email", kinds)
        self.assertIn("[REDACTED-EMAIL]", redacted)
        self.assertNotIn("jane.doe@example.com", redacted)

    def test_clean_text_passes_through_unchanged(self):
        original = "I am calling about my appointment next week."
        redacted, kinds = redact_pii(original)
        self.assertEqual(redacted, original)
        self.assertEqual(kinds, [])


class AnalyzeTranscriptTests(unittest.TestCase):
    def test_full_analysis_shape(self):
        result = analyze_transcript(
            "I was charged twice on my bill and it is frustrating. "
            "My card is 4111 1111 1111 1111."
        )
        self.assertEqual(result["category"], "billing")
        self.assertEqual(result["sentiment"], "negative")
        self.assertTrue(result["pii_flagged"])
        self.assertIn("card", result["pii_types"])
        self.assertNotIn("4111", result["redacted_transcript"])


if __name__ == "__main__":
    unittest.main()
