import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { results as resultsApi, rubrics as rubricsApi } from '../api/index.js';
import ScoreBar from '../components/ScoreBar.jsx';
import StatusBadge from '../components/StatusBadge.jsx';

const PAGE_SIZE = 10;

function formatDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function truncate(str, max = 80) {
  if (!str) return '';
  return str.length > max ? str.slice(0, max) + '…' : str;
}

export default function ResultsTable() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // ── Pagination from URL ───────────────────────────────────────────────────
  const page = Number(searchParams.get('page') ?? 1);

  // ── Filter state ──────────────────────────────────────────────────────────
  const [rubricId,  setRubricId]  = useState(searchParams.get('rubric') ?? '');
  const [minScore,  setMinScore]  = useState(searchParams.get('min') ?? '');
  const [maxScore,  setMaxScore]  = useState(searchParams.get('max') ?? '');
  const [dateFrom,  setDateFrom]  = useState(searchParams.get('from') ?? '');
  const [dateTo,    setDateTo]    = useState(searchParams.get('to') ?? '');

  // ── Data ──────────────────────────────────────────────────────────────────
  const [rows,       setRows]       = useState([]);
  const [totalCount, setTotalCount] = useState(0);
  const [loading,    setLoading]    = useState(true);
  const [error,      setError]      = useState('');
  const [rubricList, setRubricList] = useState([]);

  // ── Load rubrics for filter dropdown ─────────────────────────────────────
  useEffect(() => {
    rubricsApi.list().then(setRubricList).catch(() => {});
  }, []);

  // ── Fetch results ─────────────────────────────────────────────────────────
  const fetchResults = useCallback(async (pg = page) => {
    setLoading(true);
    setError('');
    try {
      const data = await resultsApi.list({
        page: pg,
        pageSize: PAGE_SIZE,
        rubricId:  rubricId  || undefined,
        minScore:  minScore  !== '' ? Number(minScore)  : undefined,
        maxScore:  maxScore  !== '' ? Number(maxScore)  : undefined,
        dateFrom:  dateFrom  || undefined,
        dateTo:    dateTo    || undefined,
      });
      setRows(data.results);
      setTotalCount(data.total_count);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [page, rubricId, minScore, maxScore, dateFrom, dateTo]);

  useEffect(() => { fetchResults(); }, [fetchResults]);

  // ── Apply filters (reset to page 1) ──────────────────────────────────────
  function applyFilters(e) {
    e.preventDefault();
    const next = {};
    if (rubricId) next.rubric = rubricId;
    if (minScore) next.min    = minScore;
    if (maxScore) next.max    = maxScore;
    if (dateFrom) next.from   = dateFrom;
    if (dateTo)   next.to     = dateTo;
    setSearchParams({ ...next, page: 1 });
  }

  function clearFilters() {
    setRubricId(''); setMinScore(''); setMaxScore('');
    setDateFrom(''); setDateTo('');
    setSearchParams({});
  }

  // ── Pagination helpers ────────────────────────────────────────────────────
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  function goPage(pg) {
    const params = Object.fromEntries(searchParams.entries());
    setSearchParams({ ...params, page: pg });
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between" style={{ marginBottom: 20 }}>
        <div>
          <h1 className="page-heading">Results</h1>
          <p className="page-subheading">
            {loading ? 'Loading…' : `${totalCount} evaluation${totalCount !== 1 ? 's' : ''}`}
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/submit')}>
          + New evaluation
        </button>
      </div>

      {/* Filters */}
      <form onSubmit={applyFilters}>
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-header">
            <span className="card-title">Filters</span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={clearFilters}>
              Clear
            </button>
          </div>
          <div className="card-body" style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
            gap: 12,
          }}>
            <div className="field">
              <label className="field-label">Rubric</label>
              <select className="select" value={rubricId} onChange={e => setRubricId(e.target.value)}>
                <option value="">All rubrics</option>
                {rubricList.map(r => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="field-label">Min score</label>
              <input
                className="input"
                type="number"
                min="0" max="5" step="0.1"
                placeholder="0.0"
                value={minScore}
                onChange={e => setMinScore(e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field-label">Max score</label>
              <input
                className="input"
                type="number"
                min="0" max="5" step="0.1"
                placeholder="5.0"
                value={maxScore}
                onChange={e => setMaxScore(e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field-label">From date</label>
              <input
                className="input"
                type="datetime-local"
                value={dateFrom}
                onChange={e => setDateFrom(e.target.value)}
              />
            </div>

            <div className="field">
              <label className="field-label">To date</label>
              <input
                className="input"
                type="datetime-local"
                value={dateTo}
                onChange={e => setDateTo(e.target.value)}
              />
            </div>

            <div className="field" style={{ justifyContent: 'flex-end', paddingTop: 18 }}>
              <button type="submit" className="btn btn-primary">
                Apply
              </button>
            </div>
          </div>
        </div>
      </form>

      {/* Table */}
      <div className="card">
        {error && <p className="error-msg" style={{ margin: 16 }}>{error}</p>}

        {loading ? (
          <div className="empty-state">
            <span className="spinner spinner-lg" />
          </div>
        ) : rows.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state-title">No results found</p>
            <p className="empty-state-subtitle">
              Submit an evaluation to see results here
            </p>
            <button
              className="btn btn-primary btn-sm"
              style={{ marginTop: 12 }}
              onClick={() => navigate('/submit')}
            >
              Submit evaluation
            </button>
          </div>
        ) : (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Prompt</th>
                    <th>Score</th>
                    <th>Rubric</th>
                    <th>Evaluated</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(row => (
                    <tr
                      key={row.result_id}
                      style={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/results/${row.result_id}`)}
                    >
                      <td style={{ maxWidth: 320 }}>
                        <div style={{ fontSize: 12, color: 'var(--text)' }}>
                          {truncate(row.prompt, 90)}
                        </div>
                        <div style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 9,
                          color: 'var(--text-placeholder)',
                          marginTop: 2,
                        }}>
                          {truncate(row.response_text, 60)}
                        </div>
                      </td>
                      <td style={{ minWidth: 140 }}>
                        <ScoreBar score={row.composite_score} />
                      </td>
                      <td>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10,
                          color: 'var(--text-muted)',
                        }}>
                          {row.rubric_name ?? '—'}
                        </span>
                        {row.rubric_name && (
                          <span className="badge badge-default" style={{ marginLeft: 6 }}>
                            rubric
                          </span>
                        )}
                      </td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10,
                          color: 'var(--text-subtle)',
                        }}>
                          {formatDate(row.created_at)}
                        </span>
                      </td>
                      <td>
                        <span style={{
                          fontFamily: 'var(--font-mono)',
                          fontSize: 10,
                          color: 'var(--accent)',
                        }}>
                          View →
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="pagination">
              <span className="pagination-info">
                {((page - 1) * PAGE_SIZE) + 1}–{Math.min(page * PAGE_SIZE, totalCount)} of {totalCount}
              </span>
              <div className="pagination-btns">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={page <= 1}
                  onClick={() => goPage(page - 1)}
                >
                  ← Prev
                </button>
                <span style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 10,
                  color: 'var(--text-subtle)',
                  padding: '0 10px',
                  display: 'flex',
                  alignItems: 'center',
                }}>
                  {page} / {totalPages}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={page >= totalPages}
                  onClick={() => goPage(page + 1)}
                >
                  Next →
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
