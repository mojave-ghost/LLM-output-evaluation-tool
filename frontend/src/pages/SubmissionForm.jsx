/**
 * SubmissionForm — the primary user-facing flow.
 *
 * States
 * ------
 * idle       → form ready to submit
 * submitting → POST /api/v1/jobs in flight
 * polling    → job queued/processing; setInterval every 2.5 s
 * done       → status COMPLETED or CACHED → redirect to ResultDetail
 * failed     → status FAILED or network error
 */

import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { jobs, rubrics as rubricsApi } from '../api/index.js';
import StatusBadge from '../components/StatusBadge.jsx';

const POLL_INTERVAL_MS = 2500;
const TERMINAL_STATUSES = new Set(['COMPLETED', 'CACHED', 'FAILED']);

export default function SubmissionForm() {
  const navigate = useNavigate();

  // Form state
  const [prompt, setPrompt]             = useState('');
  const [responseText, setResponseText] = useState('');
  const [rubricId, setRubricId]         = useState('');
  const [priority, setPriority]         = useState(1);
  const [rubricList, setRubricList]     = useState([]);

  // Submission / polling state
  const [phase, setPhase]         = useState('idle');   // idle | submitting | polling | done | failed
  const [jobStatus, setJobStatus] = useState(null);     // latest status string
  const [jobResult, setJobResult] = useState(null);     // result payload (when COMPLETED/CACHED)
  const [resultId, setResultId]   = useState(null);     // for redirect
  const [error, setError]         = useState('');

  const intervalRef = useRef(null);
  const jobIdRef    = useRef(null);

  // ── Load rubrics on mount ─────────────────────────────────────────────────
  useEffect(() => {
    rubricsApi.list()
      .then(setRubricList)
      .catch(() => {/* non-fatal: user can still submit without rubric selector */});
  }, []);

  // ── Polling cleanup ───────────────────────────────────────────────────────
  useEffect(() => {
    return () => clearInterval(intervalRef.current);
  }, []);

  // ── Poll a job until terminal status ─────────────────────────────────────
  function startPolling(jobId) {
    jobIdRef.current = jobId;
    setPhase('polling');

    intervalRef.current = setInterval(async () => {
      try {
        const data = await jobs.get(jobId);
        setJobStatus(data.status);

        if (TERMINAL_STATUSES.has(data.status)) {
          clearInterval(intervalRef.current);

          if (data.status === 'FAILED') {
            setPhase('failed');
            setError('Evaluation failed. The job was permanently rejected after 3 attempts.');
            return;
          }

          // COMPLETED or CACHED — redirect to result detail
          if (data.result?.result_id) {
            setJobResult(data.result);
            setResultId(data.result.result_id);
            setPhase('done');
            // Short delay so user sees the "Completed" flash before redirect
            setTimeout(() => navigate(`/results/${data.result.result_id}`), 800);
          } else {
            // Completed but result not populated yet (race condition) — keep polling briefly
          }
        }
      } catch (err) {
        clearInterval(intervalRef.current);
        setPhase('failed');
        setError(err.message);
      }
    }, POLL_INTERVAL_MS);
  }

  // ── Submit handler ────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setPhase('submitting');

    try {
      const data = await jobs.submit(prompt, responseText, {
        rubricId: rubricId || undefined,
        priority: Number(priority),
      });

      setJobStatus(data.status);

      if (TERMINAL_STATUSES.has(data.status)) {
        // Cache hit comes back CACHED immediately with a result
        if (data.result?.result_id) {
          setJobResult(data.result);
          setResultId(data.result.result_id);
          setPhase('done');
          setTimeout(() => navigate(`/results/${data.result.result_id}`), 800);
        } else {
          setPhase('done');
        }
      } else {
        startPolling(data.job_id);
      }
    } catch (err) {
      setPhase('failed');
      setError(err.message);
    }
  }

  // ── Reset to idle ─────────────────────────────────────────────────────────
  function reset() {
    clearInterval(intervalRef.current);
    setPhase('idle');
    setJobStatus(null);
    setJobResult(null);
    setResultId(null);
    setError('');
    setPrompt('');
    setResponseText('');
    setRubricId('');
    setPriority(1);
  }

  // ── Render ────────────────────────────────────────────────────────────────

  const isWorking = phase === 'submitting' || phase === 'polling' || phase === 'done';

  return (
    <div style={{ maxWidth: 680 }}>
      {/* Page heading */}
      <div style={{ marginBottom: 24 }}>
        <h1 className="page-heading">Submit evaluation</h1>
        <p className="page-subheading">
          Paste a prompt and the LLM response you want scored
        </p>
      </div>

      {/* Status tracker (visible once submitted) */}
      {phase !== 'idle' && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div className="card-header">
            <span className="card-title">Evaluation status</span>
            {jobStatus && <StatusBadge status={jobStatus} />}
          </div>
          <div className="card-body">
            {(phase === 'submitting') && (
              <div className="flex items-center gap-8" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                <span className="spinner" />
                Submitting job…
              </div>
            )}

            {(phase === 'polling') && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div className="flex items-center gap-8" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                  <span className="spinner" />
                  {jobStatus === 'PROCESSING'
                    ? 'Claude is evaluating your response…'
                    : 'Waiting in queue…'}
                </div>
                <div style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: 9,
                  color: 'var(--text-placeholder)',
                }}>
                  Polling every {POLL_INTERVAL_MS / 1000}s · job {jobIdRef.current?.slice(0, 8)}…
                </div>
              </div>
            )}

            {(phase === 'done') && (
              <div className="flex items-center gap-8" style={{ color: 'var(--success)', fontSize: 12 }}>
                <span>✓</span>
                Evaluation complete — redirecting to results…
              </div>
            )}

            {(phase === 'failed') && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                <p className="error-msg">{error}</p>
                <button className="btn btn-ghost btn-sm" onClick={reset}>
                  Try again
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Form — hidden while actively working */}
      {!isWorking && (
        <form onSubmit={handleSubmit}>
          <div className="card">
            <div className="card-header">
              <span className="card-title">Input</span>
            </div>
            <div className="card-body flex-col gap-16">
              {/* Prompt */}
              <div className="field">
                <label className="field-label" htmlFor="prompt">Prompt</label>
                <p className="field-hint">The original instruction or question given to the LLM</p>
                <textarea
                  id="prompt"
                  className="textarea"
                  placeholder="e.g. Explain the difference between precision and recall."
                  value={prompt}
                  onChange={e => setPrompt(e.target.value)}
                  rows={4}
                  required
                />
              </div>

              {/* Response */}
              <div className="field">
                <label className="field-label" htmlFor="response">LLM response</label>
                <p className="field-hint">Paste the exact text output you want evaluated</p>
                <textarea
                  id="response"
                  className="textarea"
                  placeholder="Paste the model's response here…"
                  value={responseText}
                  onChange={e => setResponseText(e.target.value)}
                  rows={6}
                  required
                />
              </div>
            </div>
          </div>

          {/* Options */}
          <div className="card" style={{ marginTop: 12 }}>
            <div className="card-header">
              <span className="card-title">Options</span>
            </div>
            <div className="card-body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {/* Rubric */}
              <div className="field">
                <label className="field-label" htmlFor="rubric">Rubric</label>
                <select
                  id="rubric"
                  className="select"
                  value={rubricId}
                  onChange={e => setRubricId(e.target.value)}
                >
                  <option value="">Default rubric</option>
                  {rubricList.map(r => (
                    <option key={r.id} value={r.id}>
                      {r.name}{r.is_default ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
              </div>

              {/* Priority */}
              <div className="field">
                <label className="field-label" htmlFor="priority">Priority</label>
                <select
                  id="priority"
                  className="select"
                  value={priority}
                  onChange={e => setPriority(Number(e.target.value))}
                >
                  <option value={0}>0 — Urgent</option>
                  <option value={1}>1 — Standard</option>
                  <option value={2}>2 — Background</option>
                </select>
              </div>
            </div>
          </div>

          {error && <p className="error-msg" style={{ marginTop: 12 }}>{error}</p>}

          <div style={{ marginTop: 16, display: 'flex', justifyContent: 'flex-end' }}>
            <button type="submit" className="btn btn-primary btn-lg">
              Submit for evaluation →
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
