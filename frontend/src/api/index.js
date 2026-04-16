/**
 * Centralized API client for the LLM Eval Tool backend.
 *
 * Design principles
 * -----------------
 * - Single module owns all fetch() calls — auth header injected in one place.
 * - Token lives in module-level memory (never localStorage/sessionStorage) to
 *   avoid XSS token theft.  setToken() is called by AuthContext after login;
 *   clearToken() is called on logout or 401.
 * - 401 responses dispatch a custom DOM event so AuthContext can react without
 *   creating a circular dependency.
 * - Login uses application/x-www-form-urlencoded (OAuth2PasswordRequestForm).
 *   Every other request is JSON.
 * - All error paths throw an Error whose .message is a human-readable string
 *   suitable for displaying in the UI.
 */

// ---------------------------------------------------------------------------
// Base URL
// ---------------------------------------------------------------------------

// In dev, Vite proxies /api, /auth, /health to localhost:8000 so this is "".
// In production, point VITE_API_URL at the deployed backend.
const BASE = import.meta.env.VITE_API_URL ?? '';

// ---------------------------------------------------------------------------
// In-memory token store
// ---------------------------------------------------------------------------

let _accessToken = null;

/** Called by AuthContext immediately after a successful login or token refresh. */
export function setToken(token) {
  _accessToken = token;
}

/** Called by AuthContext on logout or when a 401 response is received. */
export function clearToken() {
  _accessToken = null;
}

// ---------------------------------------------------------------------------
// Core request helper
// ---------------------------------------------------------------------------

/**
 * @param {string} method  HTTP verb
 * @param {string} path    Path relative to BASE (e.g. "/api/v1/jobs")
 * @param {object} [opts]
 * @param {object} [opts.body]    JSON-serialisable request body
 * @param {object} [opts.params]  Query-string parameters (undefined/null values skipped)
 * @returns {Promise<any>}  Parsed JSON response, or null for 204 No Content
 */
async function request(method, path, { body, params } = {}) {
  const url = new URL(BASE + path, window.location.origin);

  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`;

  let res;
  try {
    res = await fetch(url.toString(), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (networkErr) {
    throw new Error('Cannot reach the server. Check that the backend is running.');
  }

  // Session expired — notify AuthContext without a direct import dependency
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new CustomEvent('auth:expired'));
    throw new Error('Session expired. Please log in again.');
  }

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      if (typeof data.detail === 'string') detail = data.detail;
      else if (Array.isArray(data.detail)) {
        // Pydantic validation error array: pull the first message
        detail = data.detail.map(e => e.msg ?? JSON.stringify(e)).join('; ');
      }
    } catch {
      // Body wasn't JSON — keep the generic message
    }
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

// ---------------------------------------------------------------------------
// Auth endpoints
// ---------------------------------------------------------------------------

export const auth = {
  /**
   * Register a new account.
   * @returns {{ id, email, created_at }}
   */
  register(email, password) {
    return request('POST', '/auth/register', { body: { email, password } });
  },

  /**
   * Login.  FastAPI expects OAuth2PasswordRequestForm (form-encoded, not JSON).
   * @returns {{ access_token, refresh_token, token_type }}
   */
  async login(email, password) {
    const body = new URLSearchParams({ username: email, password });

    let res;
    try {
      res = await fetch(`${BASE}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
    } catch {
      throw new Error('Cannot reach the server. Check that the backend is running.');
    }

    if (!res.ok) {
      let detail = `HTTP ${res.status}`;
      try { detail = (await res.json()).detail ?? detail; } catch { /* empty */ }
      throw new Error(detail);
    }
    return res.json();
  },

  /**
   * Exchange a refresh token for a new access + refresh token pair.
   * @returns {{ access_token, refresh_token, token_type }}
   */
  refresh(refreshToken) {
    return request('POST', '/auth/refresh', { body: { refresh_token: refreshToken } });
  },
};

// ---------------------------------------------------------------------------
// Jobs endpoints
// ---------------------------------------------------------------------------

export const jobs = {
  /**
   * Submit a prompt + response pair for evaluation.
   *
   * @param {string}  prompt
   * @param {string}  responseText
   * @param {object}  [opts]
   * @param {string}  [opts.rubricId]   UUID of a specific rubric (uses default if omitted)
   * @param {number}  [opts.priority]   0 = urgent, 1 = standard (default), 2 = background
   * @returns {{ job_id, status, priority, created_at, updated_at, result? }}
   */
  submit(prompt, responseText, { rubricId, priority = 1 } = {}) {
    return request('POST', '/api/v1/jobs', {
      body: {
        prompt,
        response_text: responseText,
        ...(rubricId ? { rubric_id: rubricId } : {}),
        priority,
      },
    });
  },

  /**
   * Poll the status of a submitted job.
   * Status values: QUEUED | PROCESSING | COMPLETED | FAILED | CACHED
   *
   * @param {string} jobId
   * @returns {{ job_id, status, priority, created_at, updated_at, result? }}
   */
  get(jobId) {
    return request('GET', `/api/v1/jobs/${jobId}`);
  },
};

// ---------------------------------------------------------------------------
// Rubrics endpoints
// ---------------------------------------------------------------------------

export const rubrics = {
  /**
   * List all rubrics visible to the current user (own + system defaults).
   * @returns {Array<{ id, owner_id, name, is_default, created_at, dimensions }>}
   */
  list() {
    return request('GET', '/api/v1/rubrics');
  },

  /**
   * Create a custom rubric.  Weights must sum to 1.0.
   *
   * @param {string} name
   * @param {Array<{ name, description, weight }>} dimensions
   * @returns {{ id, owner_id, name, is_default, created_at, dimensions }}
   */
  create(name, dimensions) {
    return request('POST', '/api/v1/rubrics', { body: { name, dimensions } });
  },
};

// ---------------------------------------------------------------------------
// Results endpoints
// ---------------------------------------------------------------------------

export const results = {
  /**
   * Fetch a paginated, filtered list of evaluation results.
   *
   * @param {object} [opts]
   * @param {number}  [opts.page=1]
   * @param {number}  [opts.pageSize=10]
   * @param {string}  [opts.rubricId]   Filter by rubric UUID
   * @param {number}  [opts.minScore]   Minimum composite score (0–5)
   * @param {number}  [opts.maxScore]   Maximum composite score (0–5)
   * @param {string}  [opts.dateFrom]   ISO 8601 timestamp
   * @param {string}  [opts.dateTo]     ISO 8601 timestamp
   * @returns {{ results, total_count, page, page_size }}
   */
  list({ page = 1, pageSize = 10, rubricId, minScore, maxScore, dateFrom, dateTo } = {}) {
    return request('GET', '/api/v1/results', {
      params: {
        page,
        page_size: pageSize,
        rubric_id: rubricId,
        min_score: minScore,
        max_score: maxScore,
        date_from: dateFrom,
        date_to: dateTo,
      },
    });
  },

  /**
   * Fetch full detail for a single result including per-dimension rationale.
   *
   * @param {string} resultId
   * @returns {{ result_id, job_id, prompt, response_text, composite_score,
   *             rubric_id, rubric_name, created_at, dimension_scores }}
   */
  get(resultId) {
    return request('GET', `/api/v1/results/${resultId}`);
  },
};

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

/** @returns {{ status, queue_depth, db_status }} */
export function health() {
  return request('GET', '/health');
}
