import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync, existsSync } from "fs";
import { resolve } from "path";

function forgeTsSrc(): string | null {
  function tryPath(suiteRoot: string): string | null {
    const p = resolve(suiteRoot, "forge-framework", "packages", "forge-ts", "src", "index.ts");
    return existsSync(p) ? p : null;
  }
  const homeDir = process.env.HOME || process.env.USERPROFILE;
  if (homeDir) {
    const envFile = resolve(homeDir, ".forge", "env");
    if (existsSync(envFile)) {
      const line = readFileSync(envFile, "utf8")
        .split("\n")
        .find((l) => l.startsWith("FORGE_SUITE_ROOT="));
      if (line) {
        const result = tryPath(line.split("=")[1].trim());
        if (result) return result;
      }
    }
  }
  const suiteRootFile = resolve(__dirname, ".forge-suite", "suite_root");
  if (existsSync(suiteRootFile)) {
    const result = tryPath(readFileSync(suiteRootFile, "utf8").trim());
    if (result) return result;
  }
  return null;
}

const forgeTsSrcPath = forgeTsSrc();
const installedTs = resolve(__dirname, "node_modules", "@forge-suite", "ts");

const tsAliases = forgeTsSrcPath
  ? {
      "@forge-suite/ts/forge.css": forgeTsSrcPath.replace("index.ts", "forge.css"),
      "@forge-suite/ts/runtime": forgeTsSrcPath.replace("index.ts", "runtime/index.ts"),
      "@forge-suite/ts": forgeTsSrcPath,
    }
  : {
      "@forge-suite/ts/forge.css": resolve(installedTs, "dist", "forge.css"),
      "@forge-suite/ts/runtime": resolve(installedTs, "dist", "runtime.mjs"),
      "@forge-suite/ts": resolve(installedTs, "dist", "index.mjs"),
    };

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: tsAliases,
  },
  server: {
    fs: { allow: ["../.."] },
    proxy: {
      "/api": `http://localhost:${process.env.VITE_API_PORT || "8000"}`,
      "/endpoints": `http://localhost:${process.env.VITE_API_PORT || "8000"}`,
    },
  },
});
