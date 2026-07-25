import type { BaseProvider } from './base.js';
import type { CustomProvider } from '../config/schema.js';
import type { DiscoveredProvider } from './discovery.js';
import { logger } from '../util/logger.js';

/**
 * Factory table — see ~/workspace/paseo/packages/server/src/server/agent/provider-registry.ts:109.
 *
 * Each entry name matches a discovered binary id (claude / codex / openclaw)
 * OR the generic-acp escape hatch. Custom user providers defined in
 * ~/.agentd/config.json are appended at runtime.
 */
export type ProviderFactory = {
  id: string;
  label: string;
  /** Match a DiscoveredProvider.id to decide if this factory applies. */
  binaryIds: readonly string[];
  build: (discovered: { command: string; version: string } | null, cfg: CustomProvider | null) => BaseProvider;
};

import { buildClaudeProvider } from './claude.js';
import { buildCodexProvider } from './codex.js';
import { buildOpenClawProvider } from './openclaw.js';
import { buildGenericAcpProvider } from './generic-acp.js';

export const FACTORIES: Record<string, ProviderFactory> = {
  claude: {
    id: 'claude',
    label: 'Claude Code',
    binaryIds: ['claude'],
    build: (discovered, cfg) => buildClaudeProvider(discovered, cfg),
  },
  codex: {
    id: 'codex',
    label: 'Codex',
    binaryIds: ['codex'],
    build: (discovered, cfg) => buildCodexProvider(discovered, cfg),
  },
  openclaw: {
    id: 'openclaw',
    label: 'OpenClaw',
    binaryIds: ['openclaw'],
    build: (discovered, cfg) => buildOpenClawProvider(discovered, cfg),
  },
  'generic-acp': {
    id: 'generic-acp',
    label: 'Generic ACP',
    binaryIds: [],
    build: (_discovered, cfg) => {
      if (!cfg) throw new Error('generic-acp factory requires a config entry');
      return buildGenericAcpProvider(cfg);
    },
  },
};

/** Allow tests to introspect factory list. */
export function listFactoryIds(): string[] {
  return Object.keys(FACTORIES);
}

export interface ProviderEntry {
  provider: BaseProvider;
  status: 'available' | 'degraded' | 'unavailable';
  binaryPath?: string;
  version?: string;
  source: 'discovered' | 'config';
}

/**
 * Registry — merges discovered binaries with config-file providers.
 *
 * Status semantics:
 *  - `available`: binary present + factory built an instance
 *  - `degraded` : binary present but auth/credential missing
 *  - `unavailable`: factory had no binary (e.g. custom config without discovery)
 */
export class ProviderRegistry {
  private entries = new Map<string, ProviderEntry>();
  private customCfgs = new Map<string, CustomProvider>();

  registerCustom(cfg: CustomProvider): void {
    this.customCfgs.set(cfg.id, cfg);
  }

  load(
    discovered: DiscoveredProvider[],
  ): { added: string[]; skipped: string[] } {
    const added: string[] = [];
    const skipped: string[] = [];

    // Pass 1 — discovered binaries that map to a known factory.
    for (const d of discovered) {
      const factory = Object.values(FACTORIES).find((f) =>
        f.binaryIds.includes(d.id),
      );
      if (!factory) {
        skipped.push(d.id);
        continue;
      }
      try {
        const provider = factory.build(
          { command: d.path, version: d.version },
          null,
        );
        const status = probeStatus(d.id);
        this.entries.set(factory.id, {
          provider,
          status,
          binaryPath: d.path,
          version: d.version,
          source: 'discovered',
        });
        added.push(factory.id);
      } catch (err) {
        logger.warn({ err, id: d.id }, 'failed to build provider from discovered binary');
      }
    }

    // Pass 2 — user-defined providers from ~/.agentd/config.json.
    for (const [id, cfg] of this.customCfgs) {
      // Pick the right factory by protocol — `acp` always falls to generic-acp,
      // otherwise prefer a factory whose id matches the user's id, falling
      // back to the first factory that handles the protocol.
      let factory = FACTORIES[id];
      if (!factory) {
        if (cfg.protocol === 'acp') factory = FACTORIES['generic-acp'];
        else if (cfg.protocol === 'stream-json') factory = FACTORIES['claude'];
        else if (cfg.protocol === 'openai-http') factory = FACTORIES['openclaw'];
        else factory = FACTORIES['generic-acp'];
      }
      if (!factory) {
        skipped.push(id);
        continue;
      }
      try {
        const provider = factory.build(null, cfg);
        // Override the id/label so /v1/models reports the user's chosen name.
        // We mutate the instance since BaseProvider exposes both as readonly
        // fields and our providers don't define setters — using defineProperty
        // to keep types consistent for downstream readers.
        Object.defineProperty(provider, 'id', { value: cfg.id, writable: true });
        Object.defineProperty(provider, 'label', { value: cfg.label, writable: true });
        const entry: ProviderEntry = {
          provider,
          status: 'available',
          source: 'config',
        };
        this.entries.set(id, entry);
        added.push(id);
      } catch (err) {
        logger.warn({ err, id }, 'failed to build user-config provider');
      }
    }

    return { added, skipped };
  }

  get(id: string): ProviderEntry | undefined {
    return this.entries.get(id);
  }

  list(): ProviderEntry[] {
    return Array.from(this.entries.values());
  }

  resolveByModel(model: string): ProviderEntry | undefined {
    // `agentd/<provider>` → strip prefix
    const m = model.replace(/^agentd\//, '');
    return this.entries.get(m);
  }
}

/**
 * Probe well-known credential files and return `degraded` when they're missing.
 *
 * Only Claude is implemented for now; other providers keep `available`.
 */
function probeStatus(providerId: string): 'available' | 'degraded' {
  if (providerId !== 'claude') return 'available';
  try {
    const fs = require('node:fs');
    const path = require('node:path');
    const os = require('node:os');
    const cred = path.join(os.homedir(), '.claude', '.credentials.json');
    try {
      fs.accessSync(cred);
      return 'available';
    } catch {
      logger.warn({ provider: providerId, path: cred }, 'Claude credentials missing — provider is in degraded mode');
      return 'degraded';
    }
  } catch {
    return 'available';
  }
}
