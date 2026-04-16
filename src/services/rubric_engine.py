import uuid
from typing import List

from ..models.dimension_score import DimensionScore
from ..models.eval_job import EvalJob
from ..models.eval_result import EvalResult
from ..models.rubric_dimension import RubricDimension


class RubricEngine:
    """Calls the judge LLM (Claude) to score an EvalJob against a rubric.

    Matches the RubricEngine participant in the UML sequence diagram.
    Retries are handled externally (see EvalJob.retry() and the worker loop).
    """

    def score(
        self,
        job: EvalJob,
        rubric_dimensions: List[RubricDimension],
    ) -> tuple[EvalResult, List[DimensionScore]]:
        """Score a job against its rubric dimensions.

        Returns an EvalResult (with composite_score computed) and the list
        of individual DimensionScore objects (one per dimension).

        Raises RuntimeError on judge API failure — caller handles retry.
        """
        result_id = uuid.uuid4()
        dimension_scores: List[DimensionScore] = []

        for dim in rubric_dimensions:
            raw_score, rationale = self._call_judge(job.prompt, job.response_text, dim)
            dimension_scores.append(
                DimensionScore(
                    result_id=result_id,
                    dimension_id=dim.id,
                    score=raw_score,
                    rationale=rationale,
                )
            )

        result = EvalResult(
            id=result_id,
            job_id=job.id,
            rubric_id=job.rubric_id,
        )
        result.compute_composite(dimension_scores, rubric_dimensions)
        return result, dimension_scores

    def _call_judge(
        self,
        prompt: str,
        response_text: str,
        dimension: RubricDimension,
    ) -> tuple[int, str]:
        """Score one dimension via Claude (CON-004: claude-sonnet-4-6).

        Uses tool_use with tool_choice="tool" to guarantee structured output,
        eliminating regex parsing of free-form text (FR-011).

        Returns (score: int 1–5, rationale: str).
        Raises RuntimeError on API failure or malformed response so the
        worker can apply its retry / backoff logic (FR-009).
        """
        import os
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. "
                "Export the variable before starting the server (CON-008)."
            )

        client = anthropic.Anthropic(api_key=api_key)

        user_message = (
            f'You are evaluating an LLM response on the "{dimension.name}" dimension.\n\n'
            f"Dimension description: {dimension.description}\n\n"
            f"---\nORIGINAL PROMPT:\n{prompt}\n\n"
            f"LLM RESPONSE TO EVALUATE:\n{response_text}\n---\n\n"
            f'Score the response on "{dimension.name}" from 1 (very poor) to 5 (excellent). '
            f"Think step by step, then call record_score with your reasoning and final score."
        )

        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                tools=[
                    {
                        "name": "record_score",
                        "description": (
                            "Record the dimension evaluation: chain-of-thought "
                            "rationale and integer score 1–5."
                        ),
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "rationale": {
                                    "type": "string",
                                    "description": (
                                        "2–4 sentence chain-of-thought explaining "
                                        "the score (FR-011)."
                                    ),
                                },
                                "score": {
                                    "type": "integer",
                                    "description": "Score from 1 (very poor) to 5 (excellent).",
                                },
                            },
                            "required": ["rationale", "score"],
                        },
                    }
                ],
                tool_choice={"type": "tool", "name": "record_score"},
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(f"Anthropic API unreachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise RuntimeError(
                f"Anthropic API error {exc.status_code}: {exc.message}"
            ) from exc

        for block in response.content:
            if block.type == "tool_use" and block.name == "record_score":
                score = int(block.input["score"])
                rationale = str(block.input["rationale"])
                if not (1 <= score <= 5):
                    raise RuntimeError(
                        f"Judge returned out-of-range score {score} for dimension "
                        f'"{dimension.name}".'
                    )
                return score, rationale

        raise RuntimeError(
            "Judge model did not return a record_score tool_use block."
        )
