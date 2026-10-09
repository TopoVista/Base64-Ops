import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = (__ENV.BASE_URL || "").replace(/\/$/, "");
const bearerToken = __ENV.LOAD_BEARER_TOKEN || "";

if (!baseUrl.startsWith("https://")) {
  throw new Error("BASE_URL must be an HTTPS staging URL, for example https://api.staging.example.com");
}
if (!bearerToken) {
  throw new Error("LOAD_BEARER_TOKEN must be a short-lived staging-only Clerk token");
}

export const options = {
  scenarios: {
    authenticated_reads: {
      executor: "ramping-arrival-rate",
      startRate: Number(__ENV.START_RPS || 5),
      timeUnit: "1s",
      preAllocatedVUs: Number(__ENV.PREALLOCATED_VUS || 25),
      maxVUs: Number(__ENV.MAX_VUS || 250),
      stages: [
        { target: Number(__ENV.RAMP_RPS || 25), duration: __ENV.RAMP_DURATION || "2m" },
        { target: Number(__ENV.SUSTAIN_RPS || 50), duration: __ENV.SUSTAIN_DURATION || "5m" },
        { target: 0, duration: "30s" },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<750"],
  },
};

export default function () {
  // A staging-only, read-only request verifies Clerk authentication, tenant
  // scoping, Mongo connection-pool behavior, and distributed rate limits. It
  // must use a disposable test tenant; do not load test customer sessions.
  const response = http.get(`${baseUrl}/api/session/all?pageSize=20&pageNumber=1`, {
    headers: { Authorization: `Bearer ${bearerToken}` },
    tags: { surface: "authenticated-session-read" },
  });
  check(response, {
    "session read returns 200": (res) => res.status === 200,
    "session response is JSON": (res) => (res.headers["Content-Type"] || "").includes("application/json"),
  });
  sleep(0.1);
}
