/** Horizontal score bar — score is 0–5, width scales accordingly. */

function scoreColor(score) {
  if (score >= 4.5) return 'var(--score-5)';
  if (score >= 3.5) return 'var(--score-4)';
  if (score >= 2.5) return 'var(--score-3)';
  if (score >= 1.5) return 'var(--score-2)';
  return 'var(--score-1)';
}

export default function ScoreBar({ score, showValue = true }) {
  const pct = Math.min(100, Math.max(0, (score / 5) * 100));
  const color = scoreColor(score);

  return (
    <div className="score-bar-wrap">
      <div className="score-bar-track">
        <div
          className="score-bar-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      {showValue && (
        <span className="score-value" style={{ color }}>
          {score.toFixed(2)}
        </span>
      )}
    </div>
  );
}
