import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import fs from "fs";

const ndjsonMiddleware = () => {
  const rootOutPath = path.resolve(__dirname, "..", "out.ndjson");
  const localOutPath = path.resolve(__dirname, "out.ndjson");
  const readNdjson = () => {
    const candidate = fs.existsSync(rootOutPath) ? rootOutPath : localOutPath;
    if (!fs.existsSync(candidate)) return null;
    return fs.readFileSync(candidate, "utf8");
  };
  const attach = (server) => {
    server.middlewares.use("/out.ndjson", (req, res, next) => {
      const data = readNdjson();
      if (data == null) return next();
      res.setHeader("Content-Type", "application/x-ndjson; charset=utf-8");
      res.end(data);
    });
    server.middlewares.use("/api/listings", (req, res, next) => {
      const data = readNdjson();
      if (data == null) return next();
      try {
        const lines = data
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter(Boolean)
          .map((l) => JSON.parse(l));
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        res.end(JSON.stringify(lines));
        return;
      } catch (err) {
        console.error("Failed to parse out.ndjson", err);
        res.statusCode = 500;
        res.end("Failed to parse out.ndjson");
        return;
      }
    });
  };
  return {
    name: "ndjson-endpoints",
    configureServer(server) {
      attach(server);
    },
    configurePreviewServer(server) {
      attach(server);
    },
  };
};

export default defineConfig({
  plugins: [react(), ndjsonMiddleware()],
  server: {
    port: 4173,
    open: false,
    fs: {
      // allow reading out.ndjson from project root
      allow: [path.resolve(__dirname, ".."), path.resolve(__dirname)],
    },
    middlewareMode: false,
  },
});
