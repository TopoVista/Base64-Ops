import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/$/, "");

if (!baseUrl.startsWith("https://")) {
  throw new Error("BASE_URL must be an HTTPS staging URL, for example https://api.staging.example.com");
}

export const options = {
  scenarios: {
    public_ingress: {
      executor: "ramping-arrival-rate",
      startRate: Number(__ENV.START_RPS || 10),
      timeUnit: "1s",
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 50),
      maxVUs: Number(__ENV.MAX_VUS || 500),
      stages: [
        { target: Number(__ENV.RAMP_RPS || 50), duration: __ENV.RAMP_DURATION || "2m" },
        { target: Number(__ENV.SUSTAIN_RPS || 100), duration: __ENV.SUSTAIN_DURATION || "5m" },
        { target: 0, duration: "30s" },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};

export default function () {
  // This is intentionally the inexpensive liveness endpoint. It verifies the
  // CDN -> Render ingress path without spending model tokens, touching a
  // tenant, or masking a readiness failure as a normal user request.
  const response = http.get(`${baseUrl}/health`, { tags: { surface: "public-health" } });
  check(response, {
    "health returns 200": (res) => res.status === 200,
    "health response is bounded": (res) => res.body.length < 2_000,
  });
  sleep(0.05);
}
