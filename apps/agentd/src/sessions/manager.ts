import { promises as fs } from 'node:fs';
import path from 'node:path';
import { logger } from '../util/logger.js';
import { SESSIONS_PATH } from '../config/loader.js';
import { IdMap, buildSessionId, type SessionRecord } from './id-map.js';

/**
 * SessionManager — lifecycle (create / touch / close / cleanup) for sessions.
 *
 * Each session maps (roomId?) → agentd_session_id → cli_session_id and is
 * persisted to ~/.agentd/sessions.json so restarts can replay.
 *
 * SessionManager is intentionally protocol-agnostic: it only holds metadata
 * and an `abort` handle so callers can cancel an in-flight stream.
 */
export class SessionManager {
  private map = new IdMap();
  private aborts = new Map<string, AbortController>();
  private saveQueue: Promise<void> = Promise.resolve();

  constructor(private readonly storePath: string = SESSIONS_PATH) {}

  async load(): Promise<void> {
    try {
      const text = await fs.readFile(this.storePath, 'utf8');
      const arr = JSON.parse(text) as SessionRecord[];
      if (Array.isArray(arr)) {
        for (const r of arr) this.map.upsert(r);
        logger.info({ count: arr.length }, 'sessions loaded from disk');
      }
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT') {
        logger.warn({ err }, 'failed to read sessions.json');
      }
    }
  }

  create(opts: {
    provider: string;
    roomId?: string;
    meta?: Record<string, unknown>;
  }): SessionRecord {
    const now = new Date().toISOString();
    const rec: SessionRecord = {
      id: buildSessionId(),
      provider: opts.provider,
      roomId: opts.roomId,
      createdAt: now,
      lastActiveAt: now,
      meta: opts.meta,
    };
    this.map.upsert(rec);
    this.aborts.set(rec.id, new AbortController());
    void this.persist();
    return rec;
  }

  touch(id: string): void {
    const rec = this.map.get(id);
    if (!rec) return;
    rec.lastActiveAt = new Date().toISOString();
    this.map.upsert(rec);
  }

  setCliSessionId(id: string, cliSessionId: string): void {
    const rec = this.map.get(id);
    if (!rec) return;
    rec.cliSessionId = cliSessionId;
    rec.lastActiveAt = new Date().toISOString();
    this.map.upsert(rec);
    void this.persist();
  }

  get(id: string): SessionRecord | undefined {
    return this.map.get(id);
  }

  byRoom(roomId: string): SessionRecord | undefined {
    return this.map.byRoom(roomId);
  }

  signal(id: string): AbortSignal | undefined {
    return this.aborts.get(id)?.signal;
  }

  list(): SessionRecord[] {
    return this.map.list();
  }

  async close(id: string): Promise<boolean> {
    const rec = this.map.get(id);
    if (!rec) return false;
    const ac = this.aborts.get(id);
    if (ac) {
      ac.abort();
      this.aborts.delete(id);
    }
    this.map.delete(id);
    void this.persist();
    return true;
  }

  /**
   * Await any pending persistence writes. Useful in tests that need to
   * tear down the tmp dir immediately after the last mutation; without
   * this, fire-and-forget writes can race `fs.rm` and surface as
   * ENOTEMPTY on Linux runners.
   */
  async flush(): Promise<void> {
    await this.saveQueue;
  }

  private persist(): Promise<void> {
    const snapshot = this.map.list();
    const target = this.storePath;
    this.saveQueue = this.saveQueue.then(async () => {
      try {
        await fs.mkdir(path.dirname(target), { recursive: true });
        await fs.writeFile(target, JSON.stringify(snapshot, null, 2), 'utf8');
      } catch (err) {
        logger.warn({ err }, 'failed to persist sessions.json');
      }
    });
    return this.saveQueue;
  }
}
