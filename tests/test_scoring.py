import unittest

from src.recruiting_tool import CandidateCard, cards_to_csv_text, score_candidate, score_location, to_status, update_card_status


class ScoreCandidateTests(unittest.TestCase):
    def test_score_candidate_weights_must_haves_higher(self):
        must_haves = ["python", "distributed systems", "llm"]
        nice = ["rust", "kubernetes"]
        text = "Senior Python engineer focused on LLM systems and distributed systems."

        must_hits, nice_hits, score = score_candidate(text, must_haves, nice)

        self.assertEqual(len(must_hits), 3)
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.75)

    def test_score_candidate_ignores_negated_signal(self):
        must_haves = ["React", "TypeScript"]
        nice = ["Rust"]
        text = "Strong TypeScript engineer with limited recent React frontend delivery."

        must_hits, nice_hits, score = score_candidate(text, must_haves, nice)

        self.assertEqual(must_hits, ["TypeScript"])
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.375)

    def test_score_location_requires_matching_target(self):
        hits = score_location("Berlin-based engineer shipping React systems.", ["Berlin", "London"])
        self.assertEqual(hits, ["Berlin"])

    def test_to_status_rejects_without_location_eligibility(self):
        self.assertEqual(to_status(0.9, location_eligible=False), "reject")

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
                location_hits=["London"],
                location_eligible=True,
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
                location_hits=["London"],
                location_eligible=True,
                outreach_draft="Hi Ada",
            )
        ]

        csv_text = cards_to_csv_text(cards)

        self.assertIn("TypeScript; React", csv_text)
        self.assertIn("Rust", csv_text)
        self.assertIn("shortlist", csv_text)


if __name__ == "__main__":
    unittest.main()
