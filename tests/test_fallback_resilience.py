import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import requests
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gemini_service import GeminiService, CANDIDATE_MODELS
from google.genai import types

class TestFallbackResilience(unittest.TestCase):
    def test_simulated_503_automatic_fallback(self):
        """Simulate primary model returning 503 and verify automatic switch to fallback model."""
        service = GeminiService()
        mock_client = MagicMock()

        # Simulate: gemini-3.5-flash-lite fails with 503 twice, gemini-3.5-flash succeeds
        mock_success_response = MagicMock()
        mock_success_response.text = '{"intent_type": "problem_solving", "category": "test", "category_display_name": "Test", "primary_headline": "Test", "summary": "Success", "urgency_level": "normal", "observed_facts": ["fact 1"], "insights_and_interpretation": "insight", "safety_precautions": [], "actionable_checklist": [], "contextual_assistance_options": [], "visible_text_extracted": "", "needs_web_search": false, "follow_up_suggestions": []}'

        def fake_generate_content(model, contents, config):
            if model == "gemini-3.5-flash-lite":
                raise Exception("503 UNAVAILABLE: This model is currently experiencing high demand. Spikes in demand are usually temporary. Please try again later.")
            elif model == "gemini-3.5-flash":
                return mock_success_response
            raise ValueError(f"Unexpected model: {model}")

        mock_client.models.generate_content.side_effect = fake_generate_content

        config = types.GenerateContentConfig(response_mime_type="application/json")
        response, model_used = service._generate_with_fallback(
            client=mock_client,
            contents=["test prompt"],
            config=config,
            operation_name="Simulated 503 Test",
            deadline_seconds=10.0,
            max_attempts_per_model=2
        )

        self.assertEqual(model_used, "gemini-3.5-flash")
        self.assertEqual(response, mock_success_response)
        print("\n[SIMULATED TEST PASSED] Successfully caught 503 on gemini-3.5-flash-lite and automatically recovered via gemini-3.5-flash!")

    def test_simulated_grounding_429_graceful_recovery(self):
        """Simulate Google Search grounding hitting 429 and verify clean fallback without citations."""
        service = GeminiService()
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("429 RESOURCE_EXHAUSTED: Search grounding quota exceeded.")

        citations, summary = service._run_grounding_search(
            client=mock_client,
            model_name="gemini-3.5-flash-lite",
            query="test query",
            language="en"
        )
        self.assertEqual(citations, [])
        self.assertIsNone(summary)
        print("[SIMULATED TEST PASSED] Grounding 429 was handled gracefully without throwing an exception!")

    def test_live_api_endpoint(self):
        """Live multimodal analysis call to running Flask server verifying end-to-end functionality."""
        img_dir = os.path.join(os.path.dirname(__file__), "..", "test_image")
        images = [f for f in os.listdir(img_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
        if not images:
            self.skipTest(f"No test images found in {img_dir}")

        test_file = os.path.join(img_dir, images[0])
        url = "http://127.0.0.1:5000/api/analyze"
        with open(test_file, "rb") as f:
            files = {"image": (images[0], f, "image/jpeg")}
            data = {"mode": "quick", "language": "en"}
            res = requests.post(url, files=files, data=data, timeout=30)

        self.assertEqual(res.status_code, 200, f"Live API returned {res.status_code}: {res.text}")
        payload = res.json()
        self.assertTrue(payload.get("success"), "Response success flag was false")
        self.assertIn("analysis", payload)
        self.assertIn(payload.get("model_used"), CANDIDATE_MODELS)
        print(f"\n[LIVE API TEST PASSED] Image: {images[0]} | Model used: {payload.get('model_used')} | Headline: {payload['analysis'].get('primary_headline')}")

if __name__ == "__main__":
    unittest.main()
