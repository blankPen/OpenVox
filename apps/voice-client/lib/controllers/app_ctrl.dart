import 'dart:async';
import 'dart:math';

import 'package:dart_jsonwebtoken/dart_jsonwebtoken.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:livekit_client/livekit_client.dart' as sdk;
import 'package:livekit_components/livekit_components.dart' as components;
import 'package:logging/logging.dart';
import 'package:uuid/uuid.dart';

import '../livekit_config.dart';
import '../util/client_log.dart';

enum AppScreenState { welcome, agent }

enum AgentScreenState { visualizer, transcription }

class AppCtrl extends ChangeNotifier {
  static const uuid = Uuid();
  static final _logger = Logger('AppCtrl');
  static final _random = Random.secure();

  // States
  AppScreenState appScreenState = AppScreenState.welcome;
  AgentScreenState agentScreenState = AgentScreenState.visualizer;

  //Test
  bool isUserCameEnabled = false;
  bool isScreenshareEnabled = false;

  final messageCtrl = TextEditingController();
  final messageFocusNode = FocusNode();

  late final sdk.Room room = sdk.Room(roomOptions: const sdk.RoomOptions(enableVisualizer: true));
  late final roomContext = components.RoomContext(room: room);
  late final sdk.Session session = _createSession(room: room);

  /// Generates a unique participant identity with timestamp + random suffix.
  static String _generateIdentity() {
    final ts = DateTime.now().millisecondsSinceEpoch.toRadixString(36);
    final rand = _random.nextInt(0x1000000).toRadixString(36).padLeft(5, '0');
    return 'participant-$ts-$rand';
  }

  /// Generates a unique room suffix so each connection gets a fresh agent.
  /// In e2e tests we can force an empty suffix (via VOX_E2E_ROOM_SUFFIX=)
  /// so the room name is the bare base — matching the dispatch pattern
  /// used by e2e_test.py — and the LiveKit server's auto-dispatch reliably
  /// routes the same agent to repeated test runs.
  static String _generateRoomSuffix() {
    final fixed = const String.fromEnvironment(
      'VOX_E2E_ROOM_SUFFIX',
      defaultValue: '__default__',
    );
    if (fixed == '__default__') {
      return '${DateTime.now().millisecondsSinceEpoch.toRadixString(36)}-'
          '${_random.nextInt(0x100000).toRadixString(36).padLeft(4, '0')}';
    }
    return fixed; // '' or any explicit value
  }

  static sdk.Session _createSession({required sdk.Room room}) {
    // Custom token source: signs an HS256 JWT locally on every fetch.
    // Each call generates a unique identity AND a unique room so successive
    // sessions don't reuse either:
    //   - unique identity  → the user isn't mistaken for a stale session
    //   - unique room      → server always dispatches a fresh agent
    final tokenSource = sdk.CustomTokenSource((options) async {
      final identity = _generateIdentity();
      final suffix = _generateRoomSuffix();
      final sessionRoom = suffix.isEmpty ? roomName : '$roomName-$suffix';

      final jwt = JWT(
        {
          'identity': identity,
          'name': identity,
          'video': {
            'room': sessionRoom,
            'roomJoin': true,
            'roomCreate': true,
            'roomList': true,
            'roomAdmin': true,
            'canUpdateOwnMetadata': true,
          },
          'roomConfig': {
            'agents': [
              {'agentName': agentName},
            ],
          },
        },
        issuer: liveKitApiKey,
        subject: identity,
      );

      final token = jwt.sign(
        SecretKey(liveKitApiSecret),
        algorithm: JWTAlgorithm.HS256,
        expiresIn: Duration(seconds: tokenTtlSeconds),
      );

      return sdk.TokenSourceResponse(
        serverUrl: liveKitUrl,
        participantToken: token,
      );
    }).cached();

    return sdk.Session.fromConfigurableTokenSource(
      tokenSource,
      tokenOptions: sdk.TokenRequestOptions(agentName: agentName),
      // PreConnectAudio starts recording while the agent dispatches, so
      // the user can speak the moment the agent becomes ready. In e2e
      // tests this races the 20s hard-coded `Agent did not become ready
      // within timeout` before the cold-started agent joins. Disable it
      // in the test build via dart-define so production keeps the
      // latency win while the test isn't gated by STT warm-up.
      options: sdk.SessionOptions(
        room: room,
        preConnectAudio: const bool.fromEnvironment(
          'VOX_PRECONNECT_AUDIO',
          defaultValue: true,
        ),
      ),
    );
  }

  bool isSendButtonEnabled = false;
  bool isSessionStarting = false;
  bool _hasCleanedUp = false;

  /// 订阅中的 remote audio track 数；每秒由 timer 上报给 ClientLog。
  int _subscribedAudioTracks = 0;
  Timer? _audioTickTimer;

  AppCtrl() {
    final format = DateFormat('HH:mm:ss');
    // configure logs for debugging
    Logger.root.level = Level.FINE;
    Logger.root.onRecord.listen((record) {
      debugPrint('${format.format(record.time)}: ${record.message}');
    });

    messageCtrl.addListener(() {
      final newValue = messageCtrl.text.isNotEmpty;
      if (newValue != isSendButtonEnabled) {
        isSendButtonEnabled = newValue;
        notifyListeners();
      }
    });

    session.addListener(_handleSessionChange);

    // 监听 remote audio track 订阅；每秒汇总一次报 ClientLog.audioTick。
    // agent 的 TTS 音频以 remote track publish，SDK 触发 TrackSubscribedEvent。
    // （LocalTrackSubscribedEvent 只携带 trackSid 没有 publication，无法判断 kind。）
    room.events.on<sdk.TrackSubscribedEvent>((event) {
      if (event.track.kind == sdk.TrackType.AUDIO) {
        _subscribedAudioTracks++;
      }
    });
    room.events.on<sdk.TrackUnsubscribedEvent>((event) {
      if (event.track.kind == sdk.TrackType.AUDIO) {
        _subscribedAudioTracks = (_subscribedAudioTracks - 1).clamp(0, 99);
      }
    });
    _audioTickTimer?.cancel();
    _audioTickTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      ClientLog.audioTick(_subscribedAudioTracks, 'agent');
    });
  }

  Future<void> cleanUp() async {
    if (_hasCleanedUp) return;
    _hasCleanedUp = true;

    session.removeListener(_handleSessionChange);
    await session.dispose();
    await room.dispose();
    roomContext.dispose();
    messageCtrl.dispose();
    messageFocusNode.dispose();
  }

  @override
  void dispose() {
    unawaited(cleanUp());
    super.dispose();
  }

  void sendMessage() async {
    isSendButtonEnabled = false;

    final text = messageCtrl.text;
    messageCtrl.clear();
    notifyListeners();

    if (text.isEmpty) return;
    final preview = text.length > 80 ? '${text.substring(0, 80)}...' : text;
    ClientLog.event('text', 'send: $preview');
    await session.sendText(text);
  }

  void toggleUserCamera(components.MediaDeviceContext? deviceCtx) {
    isUserCameEnabled = !isUserCameEnabled;
    isUserCameEnabled ? deviceCtx?.enableCamera() : deviceCtx?.disableCamera();
    notifyListeners();
  }

  void toggleScreenShare() {
    isScreenshareEnabled = !isScreenshareEnabled;
    notifyListeners();
  }

  void toggleAgentScreenMode() {
    agentScreenState =
        agentScreenState == AgentScreenState.visualizer ? AgentScreenState.transcription : AgentScreenState.visualizer;
    notifyListeners();
  }

  void connect() async {
    if (isSessionStarting) {
      _logger.fine('Connection attempt ignored: session already starting.');
      return;
    }

    _logger.info('Starting session connection…');
    ClientLog.event('connect', 'start');
    isSessionStarting = true;
    notifyListeners();

    try {
      await session.start();
      if (session.connectionState == sdk.ConnectionState.connected) {
        appScreenState = AppScreenState.agent;
        ClientLog.event('connect', 'success room=${room.name}');
        notifyListeners();
      }
    } catch (error, stackTrace) {
      _logger.severe('Connection error: $error', error, stackTrace);
      ClientLog.event('connect', 'error: $error');
      appScreenState = AppScreenState.welcome;
      notifyListeners();
    } finally {
      if (isSessionStarting) {
        isSessionStarting = false;
        notifyListeners();
      }
    }
  }

  Future<void> disconnect() async {
    ClientLog.event('hangup', 'user initiated');
    await session.end();
    session.restoreMessageHistory(const []);
    appScreenState = AppScreenState.welcome;
    agentScreenState = AgentScreenState.visualizer;
    _audioTickTimer?.cancel();
    _audioTickTimer = null;
    _subscribedAudioTracks = 0;
    ClientLog.event('disconnect', 'reason=user');
    notifyListeners();
  }

  void _handleSessionChange() {
    final sdk.ConnectionState state = session.connectionState;
    AppScreenState? nextScreen;
    switch (state) {
      case sdk.ConnectionState.connected:
      case sdk.ConnectionState.reconnecting:
        nextScreen = AppScreenState.agent;
        break;
      case sdk.ConnectionState.disconnected:
        nextScreen = AppScreenState.welcome;
        break;
      case sdk.ConnectionState.connecting:
        nextScreen = null;
        break;
    }

    if (nextScreen != null && nextScreen != appScreenState) {
      appScreenState = nextScreen;
      if (state == sdk.ConnectionState.connected) {
        ClientLog.event('connect', 'state=connected room=${room.name}');
      } else if (state == sdk.ConnectionState.disconnected) {
        ClientLog.event('disconnect', 'state=disconnected');
      }
      notifyListeners();
    }
  }
}
