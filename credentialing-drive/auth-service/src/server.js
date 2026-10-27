import { serve } from "@hono/node-server";
import { Hono } from "hono";
import { cors } from "hono/cors";

import { auth, pool } from "./auth.js";

const app = new Hono();
const allowedOrigins = new Set((process.env.BETTER_AUTH_TRUSTED_ORIGINS || "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean));
allowedOrigins.add(process.env.BETTER_AUTH_URL);
allowedOrigins.add(process.env.APP_URL);

app.use("/api/auth/*", cors({
  origin: (origin) => (origin && allowedOrigins.has(origin) ? origin : undefined),
  credentials: true,
  allowHeaders: ["Content-Type", "Authorization"],
  allowMethods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
}));

app.get("/healthz", async (context) => {
  try {
    await pool.query("SELECT 1");
    return context.json({ status: "ok" });
  } catch (error) {
    console.error("Auth database health check failed", error);
    return context.json({ status: "unavailable" }, 503);
  }
});

app.all("/api/auth/*", (context) => auth.handler(context.req.raw));

const port = Number(process.env.PORT || 8080);
serve({ fetch: app.fetch, port }, () => {
  console.info(`Cozema Health auth service listening on ${port}`);
});
