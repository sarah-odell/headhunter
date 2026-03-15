import unittest

from src.recruiting_tool import CandidateCard, cards_to_csv_text, score_candidate, score_candidate_with_evidence, score_location, to_status, update_card_status
from src.recruiting_tool import _choose_preferred_email, _extract_github_search_payload, _extract_public_email, _normalize_email, generate_outreach, is_github_profile_url


class ScoreCandidateTests(unittest.TestCase):
    def test_score_candidate_weights_must_haves_higher(self):
        must_haves = ["python", "distributed systems", "llm"]
        nice = ["rust", "kubernetes"]
        text = "Senior Python engineer focused on LLM systems and distributed systems."

        must_hits, nice_hits, score, must_score, nice_score = score_candidate(text, must_haves, nice)

        self.assertEqual(len(must_hits), 3)
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.75)
        self.assertEqual(must_score, 1.0)
        self.assertEqual(nice_score, 0.0)

    def test_score_candidate_ignores_negated_signal(self):
        must_haves = ["React", "TypeScript"]
        nice = ["Rust"]
        text = "Strong TypeScript engineer with limited recent React frontend delivery."

        must_hits, nice_hits, score, must_score, nice_score = score_candidate(text, must_haves, nice)

        self.assertEqual(must_hits, ["TypeScript"])
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.375)
        self.assertEqual(must_score, 0.5)
        self.assertEqual(nice_score, 0.0)

    def test_score_candidate_uses_aliases_for_frontend_backend(self):
        must_haves = ["frontend", "backend"]
        nice = ["performance"]
        text = "Engineer building React and Node.js products with API and Postgres experience."

        must_hits, nice_hits, score, must_score, nice_score = score_candidate(text, must_haves, nice)

        self.assertEqual(must_hits, ["frontend", "backend"])
        self.assertEqual(nice_hits, [])
        self.assertEqual(score, 0.75)
        self.assertEqual(must_score, 1.0)
        self.assertEqual(nice_score, 0.0)

    def test_score_location_requires_matching_target(self):
        hits = score_location("Berlin-based engineer shipping React systems.", ["Berlin", "London"])
        self.assertEqual(hits, ["Berlin"])

    def test_score_candidate_with_evidence_prefers_repo_over_profile(self):
        brief = {
            "must_haves": ["TypeScript", "React"],
            "nice_to_haves": ["Rust"],
            "must_have_weights": {"TypeScript": 0.7, "React": 0.3},
            "nice_to_have_weights": {"Rust": 1.0},
            "must_have_category_weight": 0.85,
            "nice_to_have_category_weight": 0.15,
        }
        source_texts = {
            "search": "",
            "profile": "Frontend engineer with React experience.",
            "repo": "typescript react node postgres project",
            "website": "",
        }

        must_hits, nice_hits, fit_score, must_score, nice_score, confidence, requirement_scores, requirement_sources, requirement_evidence = score_candidate_with_evidence(brief, source_texts)

        self.assertEqual(must_hits, ["TypeScript", "React"])
        self.assertEqual(nice_hits, [])
        self.assertEqual(requirement_scores["TypeScript"], 1.0)
        self.assertEqual(requirement_sources["TypeScript"], "repo")
        self.assertEqual(requirement_evidence["TypeScript"], "")
        self.assertGreater(confidence, 0.6)
        self.assertGreater(fit_score, 0.8)

    def test_to_status_rejects_without_location_eligibility(self):
        self.assertEqual(to_status(0.9, location_eligible=False), "reject")
        self.assertEqual(to_status(0.56, location_eligible=True), "shortlist")
        self.assertEqual(to_status(0.3, location_eligible=True), "hold")

    def test_is_github_profile_url_accepts_profile_and_rejects_repo(self):
        self.assertTrue(is_github_profile_url("https://github.com/yyx990803"))
        self.assertFalse(is_github_profile_url("https://github.com/vuejs/vue"))
        self.assertFalse(is_github_profile_url("https://github.com/topics/typescript"))
        self.assertFalse(is_github_profile_url("https://github.com/orgs/supabase/discussions/40821"))

    def test_extract_github_search_payload_reads_embedded_json(self):
        html = (
            '<script type="application/json" data-target="react-app.embeddedData">'
            '{"payload":{"results":[{"login":"yyx990803","name":"Evan You"}]}}'
            "</script>"
        )
        payload = _extract_github_search_payload(html)
        self.assertEqual(payload["results"][0]["login"], "yyx990803")

    def test_update_card_status_changes_matching_candidate(self):
        cards = [
            CandidateCard(
                id="abc123",
                name="Ada Lovelace",
                headline="Ada Lovelace - Engineer",
                source_url="https://example.com/ada",
                email="ada@example.com",
                found_via=["GitHub user search"],
                evidence_links=["https://example.com/ada"],
                evidence_records=[],
                evidence_count=0,
                evidence_density="low",
                must_have_hits=["TypeScript"],
                nice_to_have_hits=["React"],
                fit_score=0.7,
                must_have_score=0.5,
                nice_to_have_score=0.2,
                confidence_score=0.7,
                rationale="Matched core requirements.",
                status="hold",
                location_hits=["London"],
                location_eligible=True,
                eligibility_reason="Eligible",
                requirement_scores={"TypeScript": 1.0},
                requirement_sources={"TypeScript": "repo"},
                requirement_evidence={"TypeScript": "Repo evidence"},
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
                email="ada@example.com",
                found_via=["GitHub user search"],
                evidence_links=["https://example.com/ada"],
                evidence_records=[],
                evidence_count=0,
                evidence_density="low",
                must_have_hits=["TypeScript", "React"],
                nice_to_have_hits=["Rust"],
                fit_score=0.7,
                must_have_score=0.8,
                nice_to_have_score=0.2,
                confidence_score=0.8,
                rationale="Matched core requirements.",
                status="shortlist",
                location_hits=["London"],
                location_eligible=True,
                eligibility_reason="Eligible",
                requirement_scores={"TypeScript": 1.0, "React": 1.0},
                requirement_sources={"TypeScript": "repo", "React": "profile"},
                requirement_evidence={"TypeScript": "Repo evidence", "React": "Profile evidence"},
                outreach_draft="Hi Ada",
            )
        ]

        csv_text = cards_to_csv_text(cards)

        self.assertIn("TypeScript; React", csv_text)
        self.assertIn("ada@example.com", csv_text)
        self.assertIn("Rust", csv_text)
        self.assertIn("shortlist", csv_text)
        self.assertIn("0.8", csv_text)

    def test_extract_public_email_prefers_mailto(self):
        html = '<a href="mailto:ada%40example.com">ada@example.com</a>'
        self.assertEqual(_extract_public_email(html), "ada@example.com")

    def test_extract_public_email_falls_back_to_visible_text(self):
        html = "<div>Contact: ada@example.com</div>"
        self.assertEqual(_extract_public_email(html), "ada@example.com")

    def test_choose_preferred_email_avoids_noreply_when_possible(self):
        chosen = _choose_preferred_email([
            "12345+ada@users.noreply.github.com",
            "ada@example.com",
        ])
        self.assertEqual(chosen, "ada@example.com")

    def test_choose_preferred_email_keeps_noreply_as_last_resort(self):
        chosen = _choose_preferred_email([
            "12345+ada@users.noreply.github.com",
            "",
        ])
        self.assertEqual(chosen, "12345+ada@users.noreply.github.com")

    def test_normalize_email_handles_none(self):
        self.assertEqual(_normalize_email(None), "")

    def test_generate_outreach_uses_hash_signature(self):
        role_brief = {"company": "HASH", "role_name": "Full-Stack Engineer"}
        draft = generate_outreach(
            "Ada",
            role_brief,
            ["TypeScript"],
            ["React"],
            "https://github.com/ada",
            {"TypeScript": "TypeScript-heavy open source repo"},
            ["Berlin"],
        )
        self.assertTrue(draft.endswith("Best,\nHASH Recruiting Team"))


if __name__ == "__main__":
    unittest.main()
