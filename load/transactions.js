// Hot-path load test: POST /v1/transactions at a constant arrival rate.
// Run via scripts (make load / ./scripts/dev.ps1 load); k6 runs from the pinned Docker
// image grafana/k6:1.7.0 (ADR-010), nothing is installed on the host.
//
// Env: BASE_URL, API_KEY (a client seeded by scripts/seed_history.py), CUSTOMERS,
//      RATE (req/s, default 200), DURATION (default 60s).
import http from 'k6/http';
import { check } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://host.docker.internal:8000';
const API_KEY = __ENV.API_KEY;
const CUSTOMERS = parseInt(__ENV.CUSTOMERS || '2000', 10);
const RATE = parseInt(__ENV.RATE || '200', 10);
const DURATION = __ENV.DURATION || '60s';
const MCCS = ['5411', '5812', '5541', '5999', '4111', '5691', '5912', '5814'];

export const options = {
  scenarios: {
    hot_path: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 100,
      maxVUs: 400,
    },
  },
  // Plan acceptance (M2): 200 RPS locally with p99 < 100 ms.
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(99)<100'],
  },
  // p93 and p97 are reported on every run (standing rule, BENCHMARK.md).
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(93)', 'p(95)', 'p(97)', 'p(99)', 'max'],
};

function pick(n) {
  return Math.floor(Math.random() * n);
}

export function setup() {
  if (!API_KEY) throw new Error('API_KEY is required (see load/README.md)');
  const ready = http.get(`${BASE_URL}/readyz`);
  if (ready.status !== 200) throw new Error(`API not ready: ${ready.status} ${ready.body}`);
}

export default function () {
  const customer = `cus_${String(pick(CUSTOMERS)).padStart(5, '0')}`;
  const body = JSON.stringify({
    merchant_id: `m_${String(pick(400)).padStart(5, '0')}`,
    merchant_category: MCCS[pick(MCCS.length)],
    customer_token: customer,
    amount_minor: 500 + pick(9500),
    currency: 'USD',
  });
  const res = http.post(`${BASE_URL}/v1/transactions`, body, {
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': API_KEY,
      'Idempotency-Key': `k6-${__VU}-${__ITER}-${Date.now()}`,
    },
    tags: { endpoint: 'create_transaction' },
  });
  check(res, {
    'status is 201': (r) => r.status === 201,
    'has decision': (r) => r.status === 201 && r.json('decision') !== null,
  });
}
