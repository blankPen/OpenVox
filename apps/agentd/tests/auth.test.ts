/**
 * Tests Bearer-token auth middleware.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import Fastify from 'fastify';
import { makeAuthHook } from '../src/api/middleware/auth.js';
import type { AgentdConfig } from '../src/config/schema.js';

const baseCfg: AgentdConfig = {
  port: 8787,
  host: '127.0.0.1',
  logLevel: 'info',
  sessionTtlSeconds: 1800,
  maxConcurrentPerProvider: 4,
  rateLimit: { max: 60, windowMs: 60_000 },
  auth: { tokens: [] },
  providers: [],
  cliOAuth: { probeClaudeCredentials: true },
  acp: { serverSocket: null },
};

describe('auth middleware', () => {
  let app: ReturnType<typeof Fastify>;

  beforeEach(async () => {
    app = Fastify();
    const cfg = { ...baseCfg, auth: { tokens: ['secret-1', 'secret-2'] } };
    const hook = makeAuthHook(cfg);
    app.addHook('onRequest', async (req, reply) => {
      const url = req.routeOptions?.url ?? req.url;
      if (url === '/open') return;
      await hook(req, reply);
    });
    app.get('/secure', async () => ({ ok: true }));
    app.get('/open', async () => ({ ok: true, public: true }));
  });

  afterEach(async () => {
    await app.close();
  });

  it('returns 401 when Authorization header is missing', async () => {
    const res = await app.inject({ method: 'GET', url: '/secure' });
    expect(res.statusCode).toBe(401);
  });

  it('returns 401 when scheme is not Bearer', async () => {
    const res = await app.inject({
      method: 'GET',
      url: '/secure',
      headers: { authorization: 'Basic abcdef' },
    });
    expect(res.statusCode).toBe(401);
  });

  it('returns 403 when token is wrong', async () => {
    const res = await app.inject({
      method: 'GET',
      url: '/secure',
      headers: { authorization: 'Bearer wrong-token' },
    });
    expect(res.statusCode).toBe(403);
  });

  it('passes through with valid token', async () => {
    const res = await app.inject({
      method: 'GET',
      url: '/secure',
      headers: { authorization: 'Bearer secret-1' },
    });
    expect(res.statusCode).toBe(200);
    expect(res.json()).toEqual({ ok: true });
  });

  it('is a no-op when no tokens configured', async () => {
    await app.close();
    const openApp = Fastify();
    const hook = makeAuthHook({ ...baseCfg, auth: { tokens: [] } });
    openApp.addHook('onRequest', hook);
    openApp.get('/anything', async () => ({ ok: true }));
    const res = await openApp.inject({ method: 'GET', url: '/anything' });
    expect(res.statusCode).toBe(200);
    await openApp.close();
  });
});
