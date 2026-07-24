import Fastify, { type FastifyInstance } from 'fastify';
import type { FastifyBaseLogger } from 'fastify';
import rateLimit from '@fastify/rate-limit';
import type { AgentdConfig } from '../config/schema.js';
import type { ProviderRegistry } from '../providers/registry.js';
import type { SessionManager } from '../sessions/manager.js';
import { logger } from '../util/logger.js';
import { makeAuthHook } from './middleware/auth.js';
import { chatRoute } from './routes/chat.js';
import { modelsRoute } from './routes/models.js';
import { sessionsRoute } from './routes/sessions.js';

export interface ServerDeps {
  cfg: AgentdConfig;
  registry: ProviderRegistry;
  sessions: SessionManager;
}

export async function buildServer(deps: ServerDeps): Promise<FastifyInstance> {
  const { cfg, registry, sessions } = deps;
  const app = Fastify({
    // pino satisfies FastifyBaseLogger structurally at runtime; cast keeps strict TS happy.
    loggerInstance: logger as unknown as FastifyBaseLogger,
    disableRequestLogging: false,
    trustProxy: true,
  });

  await app.register(rateLimit, {
    max: cfg.rateLimit.max,
    timeWindow: cfg.rateLimit.windowMs,
    keyGenerator: (req) => {
      const h = req.headers.authorization;
      if (typeof h === 'string' && h.length > 0) return h.slice(0, 64);
      return req.ip;
    },
  });

  const auth = makeAuthHook(cfg);
  app.addHook('onRequest', async (req, reply) => {
    const url = req.routeOptions?.url ?? req.url;
    if (url === '/health' || url === '/healthz') return;
    await auth(req, reply);
  });

  app.get('/health', async () => ({ status: 'ok', providers: registry.list().length }));

  await chatRoute(app, { cfg, registry, sessions });
  await modelsRoute(app, registry);
  await sessionsRoute(app, sessions);

  return app;
}
