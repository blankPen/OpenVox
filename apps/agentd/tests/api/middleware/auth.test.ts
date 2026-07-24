import { describe, expect, it } from 'vitest';
import { makeAuthHook } from '../../../src/api/middleware/auth.js';
import type { FastifyReply, FastifyRequest } from 'fastify';

function mockReq(auth?: string): FastifyRequest {
  return {
    headers: auth !== undefined ? { authorization: auth } : {},
    ip: '127.0.0.1',
  } as unknown as FastifyRequest;
}

function mockReply(): FastifyReply & { _code?: number; _body?: unknown } {
  const r: FastifyReply & { _code?: number; _body?: unknown } = {
    code(c: number) { this._code = c; return this; },
    send(b: unknown) { this._body = b; return this; },
  } as unknown as FastifyReply & { _code?: number; _body?: unknown };
  return r;
}

function cfgWithTokens(tokens: string[]) {
  return {
    port: 8787, host: '127.0.0.1', logLevel: 'info',
    sessionTtlSeconds: 1800, maxConcurrentPerProvider: 4,
    rateLimit: { max: 60, windowMs: 60_000 },
    auth: { tokens },
    providers: [], cliOAuth: { probeClaudeCredentials: true },
    acp: { serverSocket: null },
  };
}

describe('api/middleware/auth', () => {
  it('is open when no tokens are configured', async () => {
    const hook = makeAuthHook(cfgWithTokens([]));
    const reply = mockReply();
    await hook(mockReq(), reply);
    expect(reply._code).toBeUndefined();
  });

  it('rejects requests without a bearer header', async () => {
    const hook = makeAuthHook(cfgWithTokens(['secret-1']));
    const reply = mockReply();
    await hook(mockReq(), reply);
    expect(reply._code).toBe(401);
  });

  it('accepts requests with a matching bearer token', async () => {
    const hook = makeAuthHook(cfgWithTokens(['secret-1']));
    const reply = mockReply();
    await hook(mockReq('Bearer secret-1'), reply);
    expect(reply._code).toBeUndefined();
  });

  it('rejects requests with a non-matching bearer token', async () => {
    const hook = makeAuthHook(cfgWithTokens(['secret-1']));
    const reply = mockReply();
    await hook(mockReq('Bearer wrong-token'), reply);
    expect(reply._code).toBe(403);
  });

  it('is case-insensitive on the scheme name', async () => {
    const hook = makeAuthHook(cfgWithTokens(['secret-1']));
    const reply = mockReply();
    await hook(mockReq('bearer secret-1'), reply);
    expect(reply._code).toBeUndefined();
  });
});