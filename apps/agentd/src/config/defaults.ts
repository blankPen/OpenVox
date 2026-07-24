/**
 * Default configuration values for agentd.
 *
 * These are merged with the user-provided config from ~/.agentd/config.json
 * via shallow + dot-path merge in loader.ts.
 */
export const DEFAULT_CONFIG = {
  port: 8787,
  host: '127.0.0.1',
  logLevel: 'info',
  /** Provider-specific session TTL in seconds (idle eviction). */
  sessionTtlSeconds: 60 * 30,
  /** Max concurrent sessions per provider. */
  maxConcurrentPerProvider: 4,
  rateLimit: {
    max: 60,
    windowMs: 60_000,
  },
  auth: {
    tokens: [] as string[],
  },
  providers: [] as Array<Record<string, unknown>>,
  cliOAuth: {
    /** Probe ~/.claude/.credentials.json for OAuth status (informational). */
    probeClaudeCredentials: true,
  },
  acp: {
    /** Reserved for Phase 2 when agentd acts as ACP server. */
    serverSocket: null as string | null,
  },
} as const;

export type DefaultConfig = typeof DEFAULT_CONFIG;
