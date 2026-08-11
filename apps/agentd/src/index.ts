#!/usr/bin/env node
/**
 * agentd CLI entry. Spawns the daemon and installs SIGINT/SIGTERM handlers.
 *
 * Usage:
 *   agentd                      # start daemon (uses AGENTD_CONFIG or ~/.agentd/config.json)
 *   agentd --check              # print resolved config and provider list, then exit
 *   agentd --config <path>      # start daemon with an explicit config file
 */
import { startDaemon } from './daemon.js';
import { logger } from './util/logger.js';
import { parseAgentdArgs, AgentdArgError } from './cli-args.js';

function parseArgsOrExit(argv: readonly string[]) {
  try {
    return parseAgentdArgs(argv);
  } catch (err) {
    if (err instanceof AgentdArgError) {
      logger.error({ err: err.message }, 'invalid CLI arguments');
      process.exit(2);
    }
    throw err;
  }
}

async function main() {
  const args = parseArgsOrExit(process.argv.slice(2));

  if (args.check) {
    const handle = await startDaemon(args.configPath);
    // Print status, then exit.
    handle.sweeper.stop();
    await drainLongLived(handle);
    await handle.server.close();
    return;
  }

  const handle = await startDaemon(args.configPath);
  const shutdown = async (signal: NodeJS.Signals) => {
    logger.info({ signal }, 'shutting down');
    try {
      handle.sweeper.stop();
      await drainLongLived(handle);
      await handle.server.close();
      process.exit(0);
    } catch (err) {
      logger.error({ err }, 'shutdown failed');
      process.exit(1);
    }
  };
  process.once('SIGINT', shutdown);
  process.once('SIGTERM', shutdown);
}

async function drainLongLived(handle: {
  longLivedProviders: Array<{ id: string; shutdown: () => Promise<void> }>;
}): Promise<void> {
  await Promise.all(
    handle.longLivedProviders.map(async (p) => {
      try {
        await p.shutdown();
      } catch (err) {
        logger.warn({ err, id: p.id }, 'long-lived provider shutdown failed');
      }
    }),
  );
}

main().catch((err) => {
  logger.error({ err }, 'fatal');
  process.exit(1);
});
