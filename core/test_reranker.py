"""
Tests for the reranker module (reranker.py).

Cross-encoder model loading is mocked to avoid downloading models in CI.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))


class TestRerankerConfig:
    """Test configuration and availability checks."""

    def test_rerank_disabled_by_default(self):
        """MOONSHINE_RERANK defaults to false."""
        import importlib
        import os

        # Ensure env var is unset
        env = os.environ.copy()
        env.pop("MOONSHINE_RERANK", None)

        with patch.dict(os.environ, {}, clear=True):
            # Re-import to pick up env
            if "reranker" in sys.modules:
                del sys.modules["reranker"]
            import reranker
            importlib.reload(reranker)
            # When disabled, is_available should return False
            assert reranker.is_available() is False

    def test_get_status_structure(self):
        import reranker
        status = reranker.get_status()
        assert "enabled" in status
        assert "model" in status
        assert "loaded" in status
        assert "error" in status
        assert isinstance(status["enabled"], bool)

    def test_default_model_name(self):
        import reranker
        assert "ms-marco" in reranker.RERANK_MODEL or "MiniLM" in reranker.RERANK_MODEL


class TestRerankerGracefulFallback:
    """Test that reranker falls back gracefully when disabled or unavailable."""

    def test_rerank_returns_original_when_disabled(self):
        """When RERANK_ENABLED is False, rerank returns input unchanged."""
        import importlib
        import os

        with patch.dict(os.environ, {"MOONSHINE_RERANK": "false"}):
            if "reranker" in sys.modules:
                del sys.modules["reranker"]
            import reranker
            importlib.reload(reranker)

            results = [
                (0.9, {"title": "Relevant doc", "content": "Very relevant content"}),
                (0.5, {"title": "Less relevant", "content": "Some content"}),
            ]
            output = reranker.rerank("test query", results)
            assert output == results  # Unchanged

    def test_rerank_handles_empty_input(self):
        import reranker
        result = reranker.rerank("query", [])
        assert result == []


class TestRerankerWithMockedModel:
    """Test reranking with a mocked cross-encoder model."""

    def _get_reranker_with_mock(self):
        """Set up reranker module with mocked model."""
        import importlib
        import os

        with patch.dict(os.environ, {"MOONSHINE_RERANK": "true"}):
            if "reranker" in sys.modules:
                del sys.modules["reranker"]
            import reranker
            importlib.reload(reranker)

        # Create mock model
        mock_model = MagicMock()
        reranker._cross_encoder = mock_model
        reranker._load_attempted = True
        reranker.RERANK_ENABLED = True
        return reranker, mock_model

    def test_rerank_scores_and_sorts(self):
        """Reranker should score and sort by cross-encoder score."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [
            (0.9, {"title": "Doc A", "content": "Less relevant to query"}),
            (0.5, {"title": "Doc B", "content": "More relevant to query"}),
            (0.3, {"title": "Doc C", "content": "Most relevant to query"}),
        ]

        # Mock predicts Doc C as most relevant, Doc A as least
        mock_model.predict.return_value = [0.2, 0.7, 0.95]

        output = reranker.rerank("relevant query", results)

        assert len(output) == 3
        # Should be sorted by rerank_score descending
        scores = [row["rerank_score"] for _, row in output]
        assert scores == sorted(scores, reverse=True)
        # Doc C should be first (highest rerank score)
        assert output[0][1]["title"] == "Doc C"

    def test_rerank_respects_top_k(self):
        """top_k should limit output count."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [
            (0.9, {"title": f"Doc {i}", "content": f"Content {i}"})
            for i in range(5)
        ]

        mock_model.predict.return_value = [0.1, 0.5, 0.3, 0.8, 0.2]

        output = reranker.rerank("query", results, top_k=2)
        assert len(output) == 2

    def test_rerank_adds_score_key(self):
        """Each result should get a rerank_score key."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [
            (0.8, {"title": "Doc", "content": "Content"}),
        ]

        mock_model.predict.return_value = [0.75]

        output = reranker.rerank("query", results)
        assert len(output) == 1
        assert "rerank_score" in output[0][1]
        assert output[0][1]["rerank_score"] == 0.75

    def test_rerank_custom_score_key(self):
        """Custom score_key should be used."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [(0.5, {"title": "Doc", "content": "Content"})]
        mock_model.predict.return_value = [0.6]

        output = reranker.rerank("query", results, score_key="my_score")
        assert "my_score" in output[0][1]

    def test_rerank_relevant_higher_than_irrelevant(self):
        """A relevant document should score higher than an irrelevant one."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [
            (0.5, {"title": "Irrelevant", "content": "Talking about cooking recipes"}),
            (0.5, {"title": "Relevant", "content": "SQLite FTS5 full text search configuration"}),
        ]

        # Mock: relevant doc scores higher
        mock_model.predict.return_value = [0.1, 0.9]

        output = reranker.rerank("SQLite search", results)
        assert output[0][1]["title"] == "Relevant"
        assert output[1][1]["title"] == "Irrelevant"

    def test_rerank_handles_model_error_gracefully(self):
        """If model.predict raises, should return original order."""
        reranker, mock_model = self._get_reranker_with_mock()

        results = [
            (0.8, {"title": "Doc A", "content": "Content A"}),
            (0.5, {"title": "Doc B", "content": "Content B"}),
        ]

        mock_model.predict.side_effect = RuntimeError("Model exploded")

        output = reranker.rerank("query", results)
        # Should return original order
        assert len(output) == 2
        assert output[0][1]["title"] == "Doc A"
