// 모집 잔액 경합 부하 테스트 (project-plan §5)
// 사용: k6 run ops/k6/invest_race.js -e API=http://localhost:8000 -e PRODUCT_ID=1 -e TOKEN=...
import http from "k6/http";
import { check } from "k6";

export const options = {
  vus: 20,
  iterations: 100,
};

const API = __ENV.API || "http://127.0.0.1:8000";
const PRODUCT_ID = __ENV.PRODUCT_ID;
const TOKEN = __ENV.TOKEN;

export default function () {
  const key = `k6-${__VU}-${__ITER}-${Date.now()}`;
  const res = http.post(
    `${API}/api/investments`,
    JSON.stringify({ product_id: Number(PRODUCT_ID), amount: 1000000 }),
    {
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": key,
        Authorization: `Bearer ${TOKEN}`,
      },
    }
  );
  check(res, {
    "no over-recruitment (201 or 409)": (r) =>
      r.status === 201 || r.status === 409,
  });
}

// 멱등성 시나리오: 같은 키 재전송
export function idempotentRetry() {
  const key = `k6-idem-${Date.now()}`;
  const body = JSON.stringify({
    product_id: Number(PRODUCT_ID),
    amount: 1000000,
  });
  const headers = {
    "Content-Type": "application/json",
    "Idempotency-Key": key,
    Authorization: `Bearer ${TOKEN}`,
  };
  const r1 = http.post(`${API}/api/investments`, body, { headers });
  const r2 = http.post(`${API}/api/investments`, body, { headers });
  check(r2, {
    "replayed with 200": () => r2.status === 200,
    "same investment id": () =>
      r1.status === 201 &&
      r2.json("investment_id") === r1.json("investment_id"),
  });
}
