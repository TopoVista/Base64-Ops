import { createServer } from "vite";

const server = await createServer({
  appType: "custom",
  server: { middlewareMode: true },
});

try {
  const { getPostAuthRedirect } = await server.ssrLoadModule(
    "/src/lib/auth-redirect.ts",
  );
  const cases = [
    {
      name: "preserves a replay route from router state",
      input: {
        state: {
          from: {
            pathname: "/dev/runs/run-123",
            search: "?tab=spans",
            hash: "#trace",
          },
        },
        search: "",
      },
      expected: "/dev/runs/run-123?tab=spans#trace",
    },
    {
      name: "preserves a session route from the sign-up handoff query",
      input: { state: null, search: "?redirect=%2Fsession%2Frepo%2Fhistory" },
      expected: "/session/repo/history",
    },
    {
      name: "rejects an external redirect",
      input: { state: null, search: "?redirect=https%3A%2F%2Fevil.example" },
      expected: "/new",
    },
    {
      name: "rejects a protocol-relative redirect",
      input: { state: null, search: "?redirect=%2F%2Fevil.example" },
      expected: "/new",
    },
    {
      name: "rejects a backslash redirect from mutable history state",
      input: { state: { from: { pathname: "\\\\evil.example" } }, search: "" },
      expected: "/new",
    },
  ];

  for (const test of cases) {
    const actual = getPostAuthRedirect(test.input);
    if (actual !== test.expected) {
      throw new Error(`${test.name}: expected ${test.expected}, received ${actual}`);
    }
  }

  console.log(`Auth redirect safety: ${cases.length} cases passed.`);
} finally {
  await server.close();
}
