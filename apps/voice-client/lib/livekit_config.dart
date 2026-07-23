// LiveKit configuration constants.
//
// SECURITY WARNING
// ---------------
// The API key/secret below are DEV credentials (openz / 35b58a6...).
// They are bundled into the app and ship in the client binary.
//
// Anyone who unzips the IPA/APK can extract them and forge tokens
// for your LiveKit server. That is acceptable ONLY for local
// development against a private LiveKit server.
//
// For ANY deployment beyond your laptop, replace these with a fetch
// from a token server that you control.

/// WebSocket URL of the LiveKit server.
const liveKitUrl = 'wss://livekit.openz.top:7443';

/// Room the client connects to. Must match the agent's target room.
/// In e2e builds (set VOX_E2E_ROOM_NAME) we use a fixed name so the
/// LiveKit server's auto-dispatch table reliably routes the agent to
/// each test run; production uses the dynamic 'openz-room'.
const roomName = String.fromEnvironment(
  'VOX_E2E_ROOM_NAME',
  defaultValue: 'openz-room',
);

/// Agent name registered by the worker.
const agentName = 'openz';

/// DEV-ONLY: HS256 signing key for the self-hosted LiveKit server.
const liveKitApiKey = 'openz';

/// DEV-ONLY: HS256 signing secret.
const liveKitApiSecret =
    '35b58a62c4a6f5a188c8537999e0524dbb0b697085fc3660bf9564d5dc083ce6';

/// Default token TTL in seconds (24h).
const tokenTtlSeconds = 60 * 60 * 24;
