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
  /**
   * Provider instances that own long-lived children — drained at shutdown.
   * Currently only ClaudeProvider (claude CLI subprocess pool).
   */
  longLivedProviders: BaseProviderWithShutdown[];
}

interface BaseProviderWithShutdown {
  readonly id: string;
  shutdown(): Promise<void>;
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

  // Initialise long-lived providers with runtime knobs.
  const longLivedProviders: BaseProviderWithShutdown[] = [];
  for (const entry of registry.list()) {
    const providerAny = entry.provider as unknown as {
      shutdown?: () => Promise<void>;
      init?: (cfg: unknown) => Promise<void>;
      prewarm?: () => Promise<void>;
    };
    if (typeof providerAny.shutdown === 'function' && typeof providerAny.init === 'function') {
      try {
        await providerAny.init({
          maxPoolSize: cfg.maxConcurrentPerProvider,
          idleTtlMs: cfg.sessionTtlSeconds * 1000,
        });
        longLivedProviders.push({
          id: entry.provider.id,
          shutdown: providerAny.shutdown.bind(entry.provider),
        });
        // Best-effort prewarm: spawn one idle child up front so the first
        // real conversation in a fresh daemon doesn't pay cold-start tax.
        // Failures here are logged but never block server start.
        if (typeof providerAny.prewarm === 'function') {
          try {
            await providerAny.prewarm();
          } catch (err) {
            logger.warn({ err, id: entry.provider.id }, 'provider prewarm failed');
          }
        }
      } catch (err) {
        logger.warn({ err, id: entry.provider.id }, 'long-lived provider init failed');
      }
    }
  }

  const server = await buildServer({ cfg, registry, sessions });

  const sweeper = new TtlSweeper(sessions, { ttlSeconds: cfg.sessionTtlSeconds });
  sweeper.start();

  await server.listen({ port: cfg.port, host: cfg.host });
  logger.info({ host: cfg.host, port: cfg.port }, 'agentd listening');

  return { server, cfg, registry, sessions, sweeper, longLivedProviders };
}
