import unittest

from src.recruiting_tool import CandidateCard, cards_to_csv_text, score_candidate, update_card_status


class ScoreCandidateTests(unittest.TestCase):
    def test_score_candidate_weights_must_haves_higher(self):
        must_haves = ["python", "distributed systems", "llm"]
        nice = ["rust", "kubernetes"]
        text = "Senior Python engineer focused on LLM systems and distributed systems."

        must_hits, nice_hits, score = score_candidate(text, must_haves, nice)

        self.assertEqual(len(must_hits), 3)
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.75)

    def test_update_card_status_changes_matching_candidate(self):
        cards = [
            CandidateCard(
                id="abc123",
                name="Ada Lovelace",
                headline="Ada Lovelace - Engineer",
                source_url="https://example.com/ada",
                evidence_links=["https://example.com/ada"],
                must_have_hits=["TypeScript"],
                nice_to_have_hits=["React"],
                fit_score=0.7,
                rationale="Matched core requirements.",
                status="hold",
                outreach_draft="Hi Ada",
            )
        ]

        update_card_status(cards, "abc123", "shortlist")

        self.assertEqual(cards[0].status, "shortlist")

    def test_cards_to_csv_text_serializes_hits(self):
        cards = [
            CandidateCard(
                id="abc123",
                name="Ada Lovelace",
                headline="Ada Lovelace - Engineer",
                source_url="https://example.com/ada",
                evidence_links=["https://example.com/ada"],
                must_have_hits=["TypeScript", "React"],
                nice_to_have_hits=["Rust"],
                fit_score=0.7,
                rationale="Matched core requirements.",
                status="shortlist",
                outreach_draft="Hi Ada",
            )
        ]

        csv_text = cards_to_csv_text(cards)

        self.assertIn("TypeScript; React", csv_text)
        self.assertIn("Rust", csv_text)
        self.assertIn("shortlist", csv_text)


if __name__ == "__main__":
    unittest.main()
