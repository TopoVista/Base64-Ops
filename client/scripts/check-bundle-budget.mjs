import { readdir, stat } from "node:fs/promises";
import path from "node:path";

const assetsDirectory = path.resolve("dist/assets");
const maximumChunkBytes = Number(process.env.MAX_JS_CHUNK_BYTES ?? 750_000);
const maximumTotalBytes = Number(process.env.MAX_TOTAL_JS_BYTES ?? 5_500_000);

if (!Number.isSafeInteger(maximumChunkBytes) || maximumChunkBytes < 1) {
  throw new Error("MAX_JS_CHUNK_BYTES must be a positive integer.");
}
if (!Number.isSafeInteger(maximumTotalBytes) || maximumTotalBytes < 1) {
  throw new Error("MAX_TOTAL_JS_BYTES must be a positive integer.");
}

const entries = await readdir(assetsDirectory);
const chunks = await Promise.all(
  entries
    .filter((entry) => entry.endsWith(".js"))
    .map(async (entry) => ({ name: entry, bytes: (await stat(path.join(assetsDirectory, entry))).size })),
);

const totalBytes = chunks.reduce((total, chunk) => total + chunk.bytes, 0);
const largestChunk = chunks.reduce(
  (largest, chunk) => (chunk.bytes > largest.bytes ? chunk : largest),
  { name: "none", bytes: 0 },
);

console.log(
  `Bundle budget: ${chunks.length} JS chunks, ${(totalBytes / 1_000_000).toFixed(2)} MB total, ` +
    `${largestChunk.name} ${(largestChunk.bytes / 1_000).toFixed(1)} KB largest.`,
);

if (largestChunk.bytes > maximumChunkBytes || totalBytes > maximumTotalBytes) {
  throw new Error(
    `Bundle budget exceeded (max chunk ${maximumChunkBytes} bytes; max total ${maximumTotalBytes} bytes).`,
  );
}
