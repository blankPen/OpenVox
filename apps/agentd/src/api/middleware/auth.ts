/**
 * Bearer token auth middleware.
 *
 * If no tokens are configured the middleware is a no-op (local dev).
 * Configure `auth.tokens` in ~/.agentd/config.json to enable.
 */
import type { FastifyReply, FastifyRequest } from 'fastify';
import type { AgentdConfig } from '../../config/schema.js';

export function makeAuthHook(cfg: AgentdConfig) {
  const tokens = cfg.auth.tokens;
  return async function authenticate(
    request: FastifyRequest,
    reply: FastifyReply,
  ): Promise<void> {
    if (tokens.length === 0) return; // open mode
    const header = request.headers.authorization;
    if (!header || !header.toLowerCase().startsWith('bearer ')) {
      reply.code(401).send({ error: { message: 'missing bearer token', type: 'auth' } });
      return reply;
    }
    const presented = header.slice('bearer '.length).trim();
    if (!tokens.includes(presented)) {
      reply.code(403).send({ error: { message: 'invalid token', type: 'auth' } });
      return reply;
    }
  };
}
