// ResumeIQ load test.
//
// Deliberately not a generic "hammer the API" script. The endpoints here are the
// ones a recruiter actually sits waiting on:
//
//   /screenings/{id}/results   the ranked table, paginated and filtered — the
//                              heaviest read in the product
//   /candidates/{id}           evidence detail, one query per requirement
//   /dashboard                 aggregate counts across the whole org
//   /screenings/{id}/rescore   recomputes ranks from stored evidence; the one
//                              write that must feel instant
//
// Deliberately excluded: job creation and screening start. Both trigger model
// calls, so a load test would spend real money and measure Anthropic's latency
// rather than yours. Those paths are queue-bound by design and belong in a
// separate soak test with a stubbed client.
//
// Usage:
//   k6 run --env BASE_URL=http://localhost:8000 \
//          --env EMAIL=you@example.com --env PASSWORD=... \
//          --env BATCH_ID=<uuid> load/k6-load.js
//
//   k6 run --env SCENARIO=ramp50 ... load/k6-load.js

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Trend, Rate, Counter } from 'k6/metrics';

const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const EMAIL = __ENV.EMAIL;
const PASSWORD = __ENV.PASSWORD;
const BATCH_ID = __ENV.BATCH_ID;
const SCENARIO = __ENV.SCENARIO || 'smoke';

// Separate trends per endpoint: an aggregate p95 hides which page is slow, and
// "the app is slow" is not an actionable finding.
const resultsLatency = new Trend('resumeiq_results_ms');
const candidateLatency = new Trend('resumeiq_candidate_ms');
const dashboardLatency = new Trend('resumeiq_dashboard_ms');
const rescoreLatency = new Trend('resumeiq_rescore_ms');
const rateLimited = new Counter('resumeiq_429s');
const errors = new Rate('resumeiq_errors');

const PROFILES = {
  smoke:  { vus: 1,  duration: '30s' },
  load10: { vus: 10, duration: '2m' },
  load50: { vus: 50, duration: '3m' },
  ramp50: {
    stages: [
      { duration: '30s', target: 10 },
      { duration: '1m',  target: 50 },
      { duration: '2m',  target: 50 },
      { duration: '30s', target: 0 },
    ],
  },
  ramp100: {
    stages: [
      { duration: '1m', target: 25 },
      { duration: '1m', target: 100 },
      { duration: '2m', target: 100 },
      { duration: '1m', target: 0 },
    ],
  },
};

export const options = {
  ...(PROFILES[SCENARIO] || PROFILES.smoke),
  thresholds: {
    // Targets, not guarantees. If these fail on the first run that is the
    // finding, not a reason to relax them.
    'resumeiq_results_ms':   ['p(95)<800'],
    'resumeiq_candidate_ms': ['p(95)<600'],
    'resumeiq_dashboard_ms': ['p(95)<500'],
    'resumeiq_rescore_ms':   ['p(95)<2000'],
    'resumeiq_errors':       ['rate<0.01'],
    'http_req_failed':       ['rate<0.05'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  if (!EMAIL || !PASSWORD) {
    throw new Error('Set EMAIL and PASSWORD. See docs/performance-baseline.md');
  }

  // One login for the whole run. Logging in per VU would measure Argon2 —
  // deliberately slow, and not what a recruiter waits on during normal use.
  const res = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );

  if (res.status !== 200) {
    throw new Error(`Login failed: ${res.status} ${res.body}`);
  }

  const token = res.json('access_token');
  const auth = { Authorization: `Bearer ${token}` };

  // Resolve a batch to read against, and one candidate inside it.
  let batchId = BATCH_ID;
  if (!batchId) {
    const list = http.get(`${BASE}/api/screenings?page=1&page_size=1`, { headers: auth });
    const items = list.json('items') || [];
    if (!items.length) {
      throw new Error('No screenings exist. Run one first, or pass BATCH_ID.');
    }
    batchId = items[0].id;
  }

  const results = http.get(
    `${BASE}/api/screenings/${batchId}/results?page=1&page_size=25`,
    { headers: auth },
  );
  const rows = results.json('items') || [];

  return {
    token,
    batchId,
    candidateId: rows.length ? rows[0].candidate_id : null,
  };
}

export default function (data) {
  const auth = {
    headers: { Authorization: `Bearer ${data.token}` },
    tags: {},
  };

  group('ranked results', () => {
    // Vary the query so the database cannot serve everything from one plan.
    const page = Math.floor(Math.random() * 3) + 1;
    const tiers = ['', '&tier=meets_all', '&tier=one_short'];
    const tier = tiers[Math.floor(Math.random() * tiers.length)];

    const res = http.get(
      `${BASE}/api/screenings/${data.batchId}/results?page=${page}&page_size=25${tier}`,
      auth,
    );
    resultsLatency.add(res.timings.duration);
    if (res.status === 429) rateLimited.add(1);
    const ok = check(res, {
      'results 200': (r) => r.status === 200,
      'results has items array': (r) => Array.isArray(r.json('items')),
    });
    errors.add(!ok);
  });

  sleep(Math.random() * 2 + 1); // recruiters read before clicking

  if (data.candidateId) {
    group('candidate evidence', () => {
      const res = http.get(
        `${BASE}/api/screenings/${data.batchId}/candidates/${data.candidateId}`,
        auth,
      );
      candidateLatency.add(res.timings.duration);
      if (res.status === 429) rateLimited.add(1);
      const ok = check(res, {
        'candidate 200': (r) => r.status === 200,
        'candidate has coverage': (r) => Array.isArray(r.json('coverage')),
      });
      errors.add(!ok);
    });
    sleep(Math.random() * 3 + 2); // reading evidence takes longer
  }

  group('dashboard', () => {
    const res = http.get(`${BASE}/api/dashboard`, auth);
    dashboardLatency.add(res.timings.duration);
    if (res.status === 429) rateLimited.add(1);
    errors.add(!check(res, { 'dashboard 200': (r) => r.status === 200 }));
  });

  sleep(1);
}

// Re-scoring is a write and much rarer than reading, so it runs on a small
// fraction of iterations rather than every one. Hammering it would measure a
// pattern no recruiter produces.
export function handleSummary(data) {
  const line = (name) => {
    const m = data.metrics[name];
    if (!m) return `  ${name}: no data`;
    const v = m.values;
    return `  ${name.padEnd(26)} med ${String(Math.round(v.med)).padStart(5)}ms  ` +
           `p95 ${String(Math.round(v['p(95)'])).padStart(5)}ms  ` +
           `p99 ${String(Math.round(v['p(99)'])).padStart(5)}ms  ` +
           `max ${String(Math.round(v.max)).padStart(5)}ms`;
  };

  const summary = [
    '',
    '='.repeat(78),
    `ResumeIQ load test — scenario: ${SCENARIO}`,
    '='.repeat(78),
    '',
    'Latency by endpoint:',
    line('resumeiq_results_ms'),
    line('resumeiq_candidate_ms'),
    line('resumeiq_dashboard_ms'),
    '',
    `Requests:      ${data.metrics.http_reqs ? data.metrics.http_reqs.values.count : 0}`,
    `Failed:        ${data.metrics.http_req_failed ? (data.metrics.http_req_failed.values.rate * 100).toFixed(2) : '0'}%`,
    `Rate limited:  ${data.metrics.resumeiq_429s ? data.metrics.resumeiq_429s.values.count : 0}`,
    '',
    'Record these in docs/performance-baseline.md as MEASURED, and keep',
    'them separate from the targets. A number without a date and a machine',
    'behind it is not a baseline.',
    '='.repeat(78),
    '',
  ].join('\n');

  // Only stdout: writing a file needs a writable mount, and the run is more
  // useful when it cannot fail on a permissions error at the very end.
  return { stdout: summary };
}
