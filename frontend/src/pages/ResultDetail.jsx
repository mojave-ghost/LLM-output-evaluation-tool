import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { results as resultsApi } from '../api/index.js';
import ScoreBar from '../components/ScoreBar.jsx';

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

/** Color for a 1–5 integer dimension score. */
function dimScoreColor(score) {
  if (score >= 5) return 'var(--score-5)';
  if (score >= 4) return 'var(--score-4)';
  if (score >= 3) return 'var(--score-3)';
  if (score >= 2) return 'var(--score-2)';
  return 'var(--score-1)';
}

export default function ResultDetail() {
  const { resultId } = useParams();
  const navigate = useNavigate();

  const [result,  setResult]  = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState('');

  useEffect(() => {
    setLoading(true);
    resultsApi.get(resultId)
      .then(setResult)
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [resultId]);

  if (loading) {
    return (
      <div className="empty-state">
        <span className="spinner spinner-lg" />
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ maxWidth: 640 }}>
        <p className="error-msg">{error}</p>
        <button className="btn btn-ghost" style={{ marginTop: 12 }} onClick={() => navigate('/results')}>
          ← Back to results
        </button>
      </div>
    );
  }

  if (!result) return null;

  return (
    <div style={{ maxWidth: 720 }}>
      {/* Back link */}
      <button
        className="btn btn-ghost btn-sm"
        style={{ marginBottom: 20 }}
        onClick={() => navigate('/results')}
      >
        ← Results
      </button>

      {/* Header card */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <span className="card-title">Evaluation result</span>
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            color: 'var(--text-placeholder)',
          }}>
            {formatDate(result.created_at)}
          </span>
        </div>
        <div className="card-body">
          {/* Composite score */}
          <div style={{ marginBottom: 20 }}>
            <div style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              color: 'var(--text-muted)',
              textTransform: 'uppercase',
              letterSpacing: '0.06em',
              marginBottom: 8,
            }}>
              Composite score
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 32,
                fontWeight: 500,
                color: 'var(--text)',
                lineHeight: 1,
              }}>
                {result.composite_score.toFixed(2)}
              </span>
              <span style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 12,
                color: 'var(--text-placeholder)',
              }}>
                / 5.00
              </span>
              <div style={{ flex: 1 }}>
                <ScoreBar score={result.composite_score} showValue={false} />
              </div>
            </div>
            {result.rubric_name && (
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 9,
                color: 'var(--text-subtle)',
                marginTop: 6,
              }}>
                Rubric: {result.rubric_name}
              </div>
            )}
          </div>

          {/* Meta row */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 12,
            paddingTop: 16,
            borderTop: '1px solid var(--border-light)',
          }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Job ID</div>
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                color: 'var(--text-muted)',
              }}>
                {result.job_id}
              </div>
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Result ID</div>
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                color: 'var(--text-muted)',
              }}>
                {result.result_id}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Prompt / response */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <span className="card-title">Input</span>
        </div>
        <div className="card-body flex-col gap-16">
          <div>
            <div className="label" style={{ marginBottom: 6 }}>Prompt</div>
            <div style={{
              background: 'var(--surface)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius)',
              padding: '10px 12px',
              fontSize: 13,
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}>
              {result.prompt}
            </div>
          </div>
          <div>
            <div className="label" style={{ marginBottom: 6 }}>LLM response</div>
            <div style={{
              background: 'var(--surface)',
              border: '1px solid var(--border-light)',
              borderRadius: 'var(--radius)',
              padding: '10px 12px',
              fontSize: 13,
              lineHeight: 1.6,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: 320,
              overflowY: 'auto',
            }}>
              {result.response_text}
            </div>
          </div>
        </div>
      </div>

      {/* Per-dimension scores */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">
            Dimension scores
            <span style={{
              marginLeft: 8,
              background: 'var(--bg)',
              color: 'var(--text-subtle)',
              fontFamily: 'var(--font-mono)',
              fontSize: 9,
              padding: '1px 6px',
              borderRadius: 8,
            }}>
              {result.dimension_scores.length}
            </span>
          </span>
        </div>

        <div className="card-body flex-col" style={{ gap: 0 }}>
          {result.dimension_scores.map((ds, idx) => (
            <div
              key={ds.dimension_id}
              style={{
                paddingTop: idx === 0 ? 0 : 16,
                paddingBottom: 16,
                borderBottom: idx < result.dimension_scores.length - 1
                  ? '1px solid var(--border-light)'
                  : 'none',
              }}
            >
              {/* Dim header */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: 8,
              }}>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  fontWeight: 500,
                  color: 'var(--text)',
                }}>
                  {ds.dimension_name ?? 'Dimension'}
                </span>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 13,
                  fontWeight: 500,
                  color: dimScoreColor(ds.score),
                }}>
                  {ds.score} / 5
                </span>
              </div>

              {/* Score bar */}
              <div style={{ marginBottom: 10 }}>
                <ScoreBar score={ds.score} showValue={false} />
              </div>

              {/* Rationale */}
              <div style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 10,
                color: 'var(--text-muted)',
                lineHeight: 1.6,
                background: 'var(--surface)',
                borderRadius: 'var(--radius-sm)',
                padding: '8px 10px',
                borderLeft: `3px solid ${dimScoreColor(ds.score)}`,
              }}>
                {ds.rationale}
              </div>
            </div>
          ))}

          {result.dimension_scores.length === 0 && (
            <div className="empty-state">
              <p className="empty-state-subtitle">No dimension scores available</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
