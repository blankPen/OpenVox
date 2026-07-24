/**
 * Daemon bootstrap — load config, discover providers, build Fastify server, listen.
 *
 * Pure factory: returns the assembled server + metadata so /src/index.ts can
 * install signal handlers without coupling.
 */
import { loadConfig, applyEnvOverrides } from './config/loader.js';
import { discoverProviders } from './providers/discovery.js';
import { ProviderRegistry } from './providers/registry.js';
import { SessionManager } from './sessions/manager.js';
import { TtlSweeper } from './sessions/ttl.js';
import { buildServer } from './api/server.js';
import { logger } from './util/logger.js';

export interface DaemonHandle {
  server: Awaited<ReturnType<typeof buildServer>>;
  cfg: Awaited<ReturnType<typeof loadConfig>>;
  registry: ProviderRegistry;
  sessions: SessionManager;
  sweeper: TtlSweeper;
}

export async function startDaemon(configPath?: string): Promise<DaemonHandle> {
  const cfg = applyEnvOverrides(await loadConfig(configPath));
  logger.level = cfg.logLevel ?? 'info';

  const sessions = new SessionManager();
  await sessions.load();

  const discovered = await discoverProviders();
  const registry = new ProviderRegistry();
  for (const p of cfg.providers ?? []) {
    registry.registerCustom(p);
  }
  const { added, skipped } = registry.load(discovered);
  logger.info({ added, skipped, totalProviders: registry.list().length }, 'providers loaded');

  const server = await buildServer({ cfg, registry, sessions });

  const sweeper = new TtlSweeper(sessions, { ttlSeconds: cfg.sessionTtlSeconds });
  sweeper.start();

  await server.listen({ port: cfg.port, host: cfg.host });
  logger.info({ host: cfg.host, port: cfg.port }, 'agentd listening');

  return { server, cfg, registry, sessions, sweeper };
}
