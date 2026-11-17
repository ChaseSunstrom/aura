import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    // `tests/` only: the pages' pure logic (`ui/logic.ts`). The Rust half is
    // `cargo test` in src-tauri. A wider glob once collected a Playwright spec,
    // failed on its import and still exited 0 -- a suite that fails without
    // failing is worse than one that does not exist.
    include: ["tests/**/*.test.ts"],
    environment: "node",
  },
});
