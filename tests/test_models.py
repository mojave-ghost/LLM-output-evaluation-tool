"""Unit tests for all domain model classes."""

import uuid
from datetime import datetime

import pytest

from src.models.dimension_score import DimensionScore
from src.models.eval_job import EvalJob
from src.models.eval_result import EvalResult
from src.models.job_status import JobStatus
from src.models.rubric import Rubric
from src.models.rubric_dimension import RubricDimension
from src.models.user import User


def _dim(weight=0.5):
    return RubricDimension(
        id=uuid.uuid4(),
        rubric_id=uuid.uuid4(),
        name="Test",
        description="desc",
        weight=weight,
    )


def _job(**kw):
    return EvalJob(
        owner_id=uuid.uuid4(),
        prompt="p",
        response_text="r",
        response_hash="h",
        rubric_id=uuid.uuid4(),
        **kw,
    )


# ── DimensionScore ────────────────────────────────────────────────────────

class TestDimensionScore:
    def test_valid_scores_accepted(self):
        for score in range(1, 6):
            ds = DimensionScore(
                result_id=uuid.uuid4(),
                dimension_id=uuid.uuid4(),
                score=score,
                rationale="ok",
            )
            assert ds.score == score

    def test_score_zero_raises(self):
        with pytest.raises(ValueError, match="score must be between 1 and 5"):
            DimensionScore(result_id=uuid.uuid4(), dimension_id=uuid.uuid4(), score=0, rationale="")

    def test_score_six_raises(self):
        with pytest.raises(ValueError):
            DimensionScore(result_id=uuid.uuid4(), dimension_id=uuid.uuid4(), score=6, rationale="")

    def test_score_negative_raises(self):
        with pytest.raises(ValueError):
            DimensionScore(result_id=uuid.uuid4(), dimension_id=uuid.uuid4(), score=-1, rationale="")


# ── EvalJob ───────────────────────────────────────────────────────────────

class TestEvalJob:
    def test_default_status_is_queued(self):
        assert _job().status == JobStatus.QUEUED

    def test_invalid_priority_raises(self):
        with pytest.raises(ValueError, match="priority must be 0, 1, or 2"):
            _job(priority=3)

    def test_priority_boundaries_accepted(self):
        for p in (0, 1, 2):
            assert _job(priority=p).priority == p

    def test_enqueue_sets_queued(self):
        job = _job()
        job.status = JobStatus.PROCESSING
        job.enqueue()
        assert job.status == JobStatus.QUEUED

    def test_retry_increments_count(self):
        job = _job()
        job.retry()
        assert job.retry_count == 1
        assert job.status == JobStatus.QUEUED

    def test_retry_second_attempt_still_queued(self):
        job = _job()
        job.retry()
        job.retry()
        assert job.retry_count == 2
        assert job.status == JobStatus.QUEUED

    def test_retry_third_attempt_raises_and_marks_failed(self):
        job = _job()
        job.retry()
        job.retry()
        with pytest.raises(RuntimeError, match="permanently failed"):
            job.retry()
        assert job.status == JobStatus.FAILED
        assert job.retry_count == 3


# ── EvalResult ────────────────────────────────────────────────────────────

class TestEvalResult:
    def _result(self):
        return EvalResult(job_id=uuid.uuid4(), rubric_id=uuid.uuid4())

    def _dim_score(self, result_id, dim_id, score):
        return DimensionScore(
            result_id=result_id, dimension_id=dim_id, score=score, rationale="r"
        )

    def test_compute_composite_weighted_average(self):
        result = self._result()
        d1 = _dim(weight=0.4)
        d2 = _dim(weight=0.6)
        ds1 = self._dim_score(result.id, d1.id, 5)
        ds2 = self._dim_score(result.id, d2.id, 2)
        score = result.compute_composite([ds1, ds2], [d1, d2])
        # 5*0.4 + 2*0.6 = 2.0 + 1.2 = 3.2
        assert abs(score - 3.2) < 1e-9

    def test_compute_composite_stores_on_self(self):
        result = self._result()
        d = _dim(weight=1.0)
        ds = self._dim_score(result.id, d.id, 4)
        result.compute_composite([ds], [d])
        assert result.composite_score == 4.0

    def test_compute_composite_empty_dims_returns_zero(self):
        result = self._result()
        score = result.compute_composite([], [])
        assert score == 0.0

    def test_compute_composite_default_three_dims(self):
        """Verify FR-012: Correctness×0.4 + Relevance×0.3 + Faithfulness×0.3."""
        result = self._result()
        dims = [_dim(0.4), _dim(0.3), _dim(0.3)]
        scores = [
            self._dim_score(result.id, dims[0].id, 5),
            self._dim_score(result.id, dims[1].id, 4),
            self._dim_score(result.id, dims[2].id, 3),
        ]
        composite = result.compute_composite(scores, dims)
        expected = 5 * 0.4 + 4 * 0.3 + 3 * 0.3  # 2.0 + 1.2 + 0.9 = 4.1
        assert abs(composite - expected) < 1e-9

    def test_to_csv_row_contains_ids_and_score(self):
        result = self._result()
        result.composite_score = 3.75
        row = result.to_csv_row()
        assert str(result.id) in row
        assert str(result.job_id) in row
        assert "3.7500" in row

    def test_to_csv_row_is_single_line(self):
        result = self._result()
        result.composite_score = 1.0
        assert "\n" not in result.to_csv_row()


# ── Rubric / User stubs ───────────────────────────────────────────────────

class TestRubricStub:
    def test_validate_weights_not_implemented(self):
        r = Rubric(owner_id=uuid.uuid4(), name="R")
        with pytest.raises(NotImplementedError):
            r.validate_weights()


class TestUserStub:
    def test_register_not_implemented(self):
        u = User(email="a@b.com", hashed_pw="x")
        with pytest.raises(NotImplementedError):
            u.register()

    def test_login_not_implemented(self):
        u = User(email="a@b.com", hashed_pw="x")
        with pytest.raises(NotImplementedError):
            u.login()
