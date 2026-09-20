import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { NextRequest } from "next/server";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "::1"]);

function backendBase(): string {
  const raw = process.env.PROJECT_ASSISTANT_BACKEND_URL ?? "http://127.0.0.1:8000";
  const url = new URL(raw);
  if (!LOOPBACK.has(url.hostname)) {
    throw new Error("PROJECT_ASSISTANT_BACKEND_URL must point to loopback");
  }
  return raw.replace(/\/$/, "");
}

function localToken(): string {
  if (process.env.PROJECT_ASSISTANT_API_TOKEN?.trim()) return process.env.PROJECT_ASSISTANT_API_TOKEN.trim();
  const assistantHome = process.env.PROJECT_ASSISTANT_HOME ?? join(homedir(), ".project-assistant");
  const file = process.env.PROJECT_ASSISTANT_TOKEN_FILE ?? join(assistantHome, "api-token");
  return readFileSync(file, "utf8").trim();
}

function validateBrowserOrigin(request: NextRequest) {
  const method = request.method.toUpperCase();
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return;

  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin") {
    throw new Error("Cross-site requests are not allowed");
  }

  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (origin && host) {
    const parsed = new URL(origin);
    if (parsed.host !== host || !LOOPBACK.has(parsed.hostname)) {
      throw new Error("Request origin is not the local Project Assistant UI");
    }
  }
}

type Context = { params: Promise<{ path: string[] }> };

async function proxy(request: NextRequest, context: Context) {
  try {
    validateBrowserOrigin(request);
  } catch (error) {
    return Response.json({ detail: error instanceof Error ? error.message : String(error) }, { status: 403 });
  }

  let token: string;
  try {
    token = localToken();
  } catch {
    return Response.json(
      { detail: "Local API token is unavailable. Start the FastAPI backend, then reload the page." },
      { status: 503 },
    );
  }

  let target: URL;
  try {
    const { path } = await context.params;
    target = new URL(`${backendBase()}/api/${path.join("/")}`);
    request.nextUrl.searchParams.forEach((value: string, key: string) => target.searchParams.append(key, value));
  } catch (error) {
    return Response.json({ detail: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }

  const headers = new Headers();
  headers.set("x-project-assistant-token", token);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);

  let body: ArrayBuffer | undefined;
  if (!["GET", "HEAD"].includes(request.method.toUpperCase())) {
    body = await request.arrayBuffer();
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: body && body.byteLength ? body : undefined,
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    responseHeaders.set("content-type", upstream.headers.get("content-type") ?? "application/json");
    responseHeaders.set("cache-control", "no-store");
    responseHeaders.set("x-content-type-options", "nosniff");

    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return Response.json(
      { detail: `FastAPI backend is unavailable at ${backendBase()}. Start project-assistant-api and reload.` },
      { status: 502 },
    );
  }
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
