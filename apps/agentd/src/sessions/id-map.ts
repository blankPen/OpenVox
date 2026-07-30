/**
 * Three-tier session id map.
 *
 *   room_id            (caller-supplied, e.g. OpenVox room)
 *     ↕ 1:N
 *   agentd_session_id  (UUID, internal)
 *     ↕ 1:1
 *   cli_session_id     (provider-native, e.g. Claude Code session)
 *
 * Persisted to ~/.agentd/sessions.json so restarts can resurrect sessions.
 */
import type { CustomProvider } from '../config/schema.js';

export interface SessionRecord {
  id: string;
  provider: string;
  roomId?: string;
  cliSessionId?: string;
  createdAt: string;
  lastActiveAt: string;
  /** Free-form metadata (model used, etc.) */
  meta?: Record<string, unknown>;
}

export class IdMap {
  private byId = new Map<string, SessionRecord>();
  private roomToAgentd = new Map<string, string>(); // last agentd per room
  private byCli = new Map<string, string>(); // cli_session_id → agentd_session_id

  upsert(rec: SessionRecord): void {
    this.byId.set(rec.id, rec);
    if (rec.roomId) this.roomToAgentd.set(rec.roomId, rec.id);
    if (rec.cliSessionId) this.byCli.set(rec.cliSessionId, rec.id);
  }

  get(id: string): SessionRecord | undefined {
    return this.byId.get(id);
  }

  /** Find the most recent agentd session for a given room, if any. */
  byRoom(roomId: string): SessionRecord | undefined {
    const aid = this.roomToAgentd.get(roomId);
    return aid ? this.byId.get(aid) : undefined;
  }

  byCliSessionId(cliSessionId: string): SessionRecord | undefined {
    const aid = this.byCli.get(cliSessionId);
    return aid ? this.byId.get(aid) : undefined;
  }

  list(): SessionRecord[] {
    return Array.from(this.byId.values());
  }

  delete(id: string): boolean {
    const rec = this.byId.get(id);
    if (!rec) return false;
    this.byId.delete(id);
    if (rec.roomId && this.roomToAgentd.get(rec.roomId) === id) {
      this.roomToAgentd.delete(rec.roomId);
    }
    if (rec.cliSessionId) this.byCli.delete(rec.cliSessionId);
    return true;
  }
}

export function buildSessionId(): string {
  // RFC4122 v4 via Web Crypto (available in Node 20+).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  return (globalThis.crypto ?? require('node:crypto').webcrypto).randomUUID();
}

export function _unused(_p: CustomProvider): void {
  /* keep type alive for tests */
}
