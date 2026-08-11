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
  /**
   * Per-room serialization for session-create. Prevents the race that
   * crashed retries from a flaky mobile client: when two requests for the
   * same `room_id` arrive simultaneously without `session_id`, both
   * `byRoom(roomId)` reads returned undefined, both `create()`'d two
   * distinct agentd sessions, and the second write won the roomToAgentd
   * mapping while the loser's in-flight stream had no future caller able
   * to address it.  Now create() chains through this map: the second
   * caller awaits the first's promise and reuses the same record.
   */
  private roomCreateInFlight = new Map<string, Promise<SessionRecord>>();

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

  /**
   * Serialized per-room creator. Returns the existing session for a room
   * when one is already mapped; otherwise creates a new record, ensuring
   * only one create runs at a time per room.
   */
  async byRoomOrCreate(
    roomId: string,
    factory: () => SessionRecord,
  ): Promise<SessionRecord> {
    const existing = this.byRoom(roomId);
    if (existing) return existing;
    const inFlight = this.roomCreateInFlight.get(roomId);
    if (inFlight) return inFlight;
    const promise = (async () => {
      try {
        const rec = factory();
        this.upsert(rec);
        return rec;
      } finally {
        this.roomCreateInFlight.delete(roomId);
      }
    })();
    this.roomCreateInFlight.set(roomId, promise);
    return promise;
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
