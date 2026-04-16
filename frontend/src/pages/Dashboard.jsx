import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { results as resultsApi, health as healthApi } from '../api/index.js';
import ScoreBar from '../components/ScoreBar.jsx';

function StatCard({ label, value, sub, accent = false }) {
  return (
    <div className="card">
      <div className="card-body" style={{ padding: '14px 16px' }}>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: 'var(--text-subtle)',
          marginBottom: 6,
        }}>
          {label}
        </div>
        <div style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 28,
          fontWeight: 500,
          color: accent ? 'var(--accent)' : 'var(--text)',
          lineHeight: 1,
          marginBottom: 4,
        }}>
          {value}
        </div>
        {sub && (
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            color: 'var(--text-placeholder)',
          }}>
            {sub}
          </div>
        )}
      </div>
    </div>
  );
}

/** Simple ASCII-style score distribution bar chart. */
function ScoreHistogram({ buckets }) {
  const max = Math.max(...buckets.map(b => b.count), 1);
  const labels = ['1–2', '2–3', '3–4', '4–5'];
  const colors = ['var(--score-1)', 'var(--score-3)', 'var(--score-4)', 'var(--score-5)'];

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', height: 72 }}>
      {buckets.map((b, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            color: 'var(--text-subtle)',
          }}>
            {b.count}
          </div>
          <div style={{
            width: '100%',
            height: Math.max(4, Math.round((b.count / max) * 52)),
            background: colors[i],
            borderRadius: '2px 2px 0 0',
            opacity: 0.8,
          }} />
          <div style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 8,
            color: 'var(--text-placeholder)',
            whiteSpace: 'nowrap',
          }}>
            {labels[i]}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();

  const [summary,  setSummary]  = useState(null);
  const [recent,   setRecent]   = useState([]);
  const [sysHealth, setSysHealth] = useState(null);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const [data, healthData] = await Promise.all([
          resultsApi.list({ pageSize: 100 }),
          healthApi().catch(() => null),
        ]);

        setSysHealth(healthData);

        const all = data.results;

        // Stats
        const total = data.total_count;
        const avg = all.length
          ? all.reduce((s, r) => s + r.composite_score, 0) / all.length
          : 0;
        const highest = all.length
          ? Math.max(...all.map(r => r.composite_score))
          : 0;
        const lowest = all.length
          ? Math.min(...all.map(r => r.composite_score))
          : 0;

        // Score histogram — 4 buckets: [1,2), [2,3), [3,4), [4,5]
        const buckets = [
          { count: all.filter(r => r.composite_score < 2).length },
          { count: all.filter(r => r.composite_score >= 2 && r.composite_score < 3).length },
          { count: all.filter(r => r.composite_score >= 3 && r.composite_score < 4).length },
          { count: all.filter(r => r.composite_score >= 4).length },
        ];

        setSummary({ total, avg, highest, lowest, buckets });
        setRecent(all.slice(0, 5));  // most recent 5 (API returns newest first)
      } catch {
        // Non-fatal — dashboard degrades gracefully
      } finally {
        setLoading(false);
      }
    };
    load();
  }, []);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between" style={{ marginBottom: 24 }}>
        <div>
          <h1 className="page-heading">Dashboard</h1>
          <p className="page-subheading">Overview of your evaluation activity</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/submit')}>
          + New evaluation
        </button>
      </div>

      {loading ? (
        <div className="empty-state">
          <span className="spinner spinner-lg" />
        </div>
      ) : (
        <>
          {/* System health strip */}
          {sysHealth && (
            <div style={{
              background: sysHealth.db_status === 'ok' ? 'var(--success-bg)' : 'var(--error-bg)',
              border: `1px solid ${sysHealth.db_status === 'ok' ? '#C8E6C9' : '#FFCDD2'}`,
              borderRadius: 'var(--radius)',
              padding: '8px 14px',
              display: 'flex',
              alignItems: 'center',
              gap: 16,
              marginBottom: 20,
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
            }}>
              <span style={{ color: sysHealth.db_status === 'ok' ? 'var(--success)' : 'var(--error)' }}>
                ● {sysHealth.db_status === 'ok' ? 'All systems operational' : 'DB connection error'}
              </span>
              <span style={{ color: 'var(--text-subtle)' }}>
                Queue depth: {sysHealth.queue_depth}
              </span>
            </div>
          )}

          {/* Stat cards */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 12,
            marginBottom: 20,
          }}>
            <StatCard
              label="Total evaluations"
              value={summary?.total ?? 0}
              sub="all time"
              accent
            />
            <StatCard
              label="Avg score"
              value={summary?.avg ? summary.avg.toFixed(2) : '—'}
              sub="composite / 5.00"
            />
            <StatCard
              label="Highest"
              value={summary?.highest ? summary.highest.toFixed(2) : '—'}
              sub="best composite score"
            />
            <StatCard
              label="Lowest"
              value={summary?.lowest ? summary.lowest.toFixed(2) : '—'}
              sub="worst composite score"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: 16 }}>
            {/* Score distribution */}
            <div className="card">
              <div className="card-header">
                <span className="card-title">Score distribution</span>
              </div>
              <div className="card-body">
                {summary?.total > 0 ? (
                  <ScoreHistogram buckets={summary.buckets} />
                ) : (
                  <div className="empty-state" style={{ padding: '24px 0' }}>
                    <p className="empty-state-subtitle">No data yet</p>
                  </div>
                )}
              </div>
            </div>

            {/* Recent evaluations */}
            <div className="card">
              <div className="card-header">
                <span className="card-title">Recent evaluations</span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => navigate('/results')}
                >
                  View all →
                </button>
              </div>
              {recent.length === 0 ? (
                <div className="empty-state" style={{ padding: '24px 0' }}>
                  <p className="empty-state-title">No evaluations yet</p>
                  <p className="empty-state-subtitle">Submit your first evaluation to get started</p>
                  <button
                    className="btn btn-primary btn-sm"
                    style={{ marginTop: 12 }}
                    onClick={() => navigate('/submit')}
                  >
                    Submit evaluation
                  </button>
                </div>
              ) : (
                <div>
                  {recent.map((r, i) => (
                    <div
                      key={r.result_id}
                      onClick={() => navigate(`/results/${r.result_id}`)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        padding: '10px 16px',
                        borderBottom: i < recent.length - 1 ? '1px solid var(--border-light)' : 'none',
                        cursor: 'pointer',
                      }}
                    >
                      {/* Score pill */}
                      <div style={{
                        fontFamily: 'var(--font-mono)',
                        fontSize: 12,
                        fontWeight: 500,
                        color: 'var(--accent)',
                        minWidth: 36,
                        flexShrink: 0,
                      }}>
                        {r.composite_score.toFixed(1)}
                      </div>

                      {/* Prompt snippet */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          fontSize: 12,
                          color: 'var(--text)',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {r.prompt}
                        </div>
                        <div style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 9,
                          color: 'var(--text-placeholder)',
                          marginTop: 1,
                        }}>
                          {r.rubric_name ?? 'default rubric'}
                        </div>
                      </div>

                      <div style={{ width: 80, flexShrink: 0 }}>
                        <ScoreBar score={r.composite_score} showValue={false} />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
