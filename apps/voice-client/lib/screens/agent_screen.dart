import 'dart:async';
import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:livekit_client/livekit_client.dart' as sdk;
import 'package:livekit_components/livekit_components.dart' as components;
import 'package:provider/provider.dart';

import '../app.dart';
import '../controllers/app_ctrl.dart';
import '../ui/vox_colors.dart';
import '../ui/vox_fonts.dart';
import '../util/client_log.dart';
import '../widgets/vox_orb.dart';

/// Agent screen — matches assistant.html 1:1.
///
/// Layout (Stack):
///   Top bar (height ~50)
///   Stage (orb centered, status+hint below)
///   Chat overlay (absolute, frosted glass, default hidden, shows on chat toggle)
///   Control bar (bottom)
class AgentScreen extends StatefulWidget {
  const AgentScreen({super.key});

  @override
  State<AgentScreen> createState() => _AgentScreenState();
}

class _AgentScreenState extends State<AgentScreen> {
  bool _chatOn = false;  // Default: chat overlay hidden, only orb visible

  int _stateIdx = 0;

  /// Tracks whether the worker has actually joined, published audio and
  /// produced *any* response. Until that flips false, we show a splash
  /// ("Agent 正在加载…") so users no longer see a static "正在聆听"
  /// screen for 5+ seconds while the LLM warms up. This is purely UX —
  /// no behavioural change.
  bool _agentReady = false;

  static const _states = [
    _OrbStateInfo('正在聆听', '说话即开始,沉默即结束', VoxOrbState.listening),
    _OrbStateInfo('正在思考', 'Vox 正在处理你的问题', VoxOrbState.thinking),
    _OrbStateInfo('正在回答', '回应会带时间戳存入档案', VoxOrbState.speaking),
  ];

  VoxOrbState get _currentOrbState => _states[_stateIdx].orbState;
  String get _currentStatus => _states[_stateIdx].status;
  String get _currentHint => _states[_stateIdx].hint;

  @override
  void initState() {
    super.initState();
    _scheduleAdvance();
    // Hook the LiveKit room: any remote audio track (i.e., the agent's
    // mic) being subscribed == the worker is alive. Flip the splash off
    // at that moment.
    final ctrl = context.read<AppCtrl>();
    ctrl.room.addListener(_onRoomChanged);
  }

  @override
  void dispose() {
    try {
      context.read<AppCtrl>().room.removeListener(_onRoomChanged);
    } catch (_) {
      // Provider may be torn down before dispose runs.
    }
    super.dispose();
  }

  void _onRoomChanged() {
    final room = context.read<AppCtrl>().room;
    // `remoteParticipants` is a map that flips when remote tracks join.
    // We use it as the "agent is alive" signal — cheap and race-free.
    final hasRemoteAudio = room.remoteParticipants.values.any(
      (p) => p.trackPublications.values.any(
        (pub) => pub.kind == sdk.TrackType.AUDIO,
      ),
    );
    if (hasRemoteAudio && !_agentReady) {
      if (!mounted) return;
      setState(() => _agentReady = true);
    }
  }

  void _scheduleAdvance() {
    Timer(const Duration(seconds: 4), () {
      if (!mounted) return;
      setState(() {
        _stateIdx = (_stateIdx + 1) % _states.length;
      });
      _scheduleAdvance();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Column(
          children: [
            _AgentTopBar(onThemeToggle: () => AppTheme.toggle(context)),
            // Stage slot. _ChatPanel is a Stack sibling of _Stage, so it
            // covers exactly the Stage area with no manual offsets —
            // and inside _ChatPanel the input bar lives at the bottom
            // alongside the messages list, sharing the same panel.
            Expanded(
              child: Stack(
                children: [
                  _Stage(
                    orbState: _currentOrbState,
                    status: _currentStatus,
                    hint: _currentHint,
                  ),
                  AnimatedOpacity(
                    duration: const Duration(milliseconds: 320),
                    opacity: _chatOn ? 1.0 : 0.0,
                    child: IgnorePointer(
                      ignoring: !_chatOn,
                      child: _ChatPanel(
                        onSend: (text) => context.read<AppCtrl>().sendMessage(),
                      ),
                    ),
                  ),
                  // UX splash: visible until agent actually publishes audio.
                  // Fade-out is generous (480ms) so it doesn't blink when
                  // audio arrives ~1s after entering the room.
                  AnimatedOpacity(
                    duration: const Duration(milliseconds: 480),
                    opacity: _agentReady ? 0.0 : 1.0,
                    child: IgnorePointer(
                      ignoring: _agentReady,
                      child: const _ConnectingSplash(),
                    ),
                  ),
                ],
              ),
            ),
            components.MediaDeviceContextBuilder(
              builder: (ctx, roomCtx, mediaDeviceCtx) => _ControlBar(
                micOn: mediaDeviceCtx.microphoneOpened,
                speakerOn: mediaDeviceCtx.isSpeakerOn ?? true,
                chatOn: _chatOn,
                onMicToggle: () {
                  final enable = !mediaDeviceCtx.microphoneOpened;
                  ClientLog.event('mic', enable ? 'enabled' : 'disabled');
                  enable
                      ? mediaDeviceCtx.enableMicrophone()
                      : mediaDeviceCtx.disableMicrophone();
                },
                onSpeakerToggle: () {
                  final enable = !(mediaDeviceCtx.isSpeakerOn ?? true);
                  ClientLog.event('speaker', enable ? 'on' : 'off');
                  mediaDeviceCtx.setSpeakerphoneOn(enable);
                },
                onChatToggle: () {
                  final open = !_chatOn;
                  ClientLog.event('chat', open ? 'open' : 'close');
                  setState(() => _chatOn = open);
                },
                onHangup: () {
                  ClientLog.event('hangup', 'control bar button');
                  unawaited(context.read<AppCtrl>().disconnect());
                },
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _OrbStateInfo {
  final String status;
  final String hint;
  final VoxOrbState orbState;
  const _OrbStateInfo(this.status, this.hint, this.orbState);
}

// ═══════════════════════════════════════════
//  Top Bar
// ═══════════════════════════════════════════

class _AgentTopBar extends StatefulWidget {
  final VoidCallback onThemeToggle;
  const _AgentTopBar({required this.onThemeToggle});

  @override
  State<_AgentTopBar> createState() => _AgentTopBarState();
}

class _AgentTopBarState extends State<_AgentTopBar> {
  int _seconds = 0;

  @override
  void initState() {
    super.initState();
    _startTimer();
  }

  void _startTimer() {
    Timer(const Duration(seconds: 1), () {
      if (!mounted) return;
      setState(() => _seconds++);
      _startTimer();
    });
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final minutes = (_seconds ~/ 60).toString().padLeft(2, '0');
    final secs = (_seconds % 60).toString().padLeft(2, '0');

    return Padding(
      key: const Key('vox_agent_topbar'),
      padding: const EdgeInsets.fromLTRB(20, 20, 20, 14),
      child: Row(
        children: [
          _IconBtn(
            key: const Key('vox_agent_topbar_back'),
            icon: Icons.chevron_left,
            isDark: isDark,
            onTap: () => context.read<AppCtrl>().disconnect(),
          ),
          const Spacer(),
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              _LiveDot(isDark: isDark),
              const SizedBox(width: 8),
              Text(
                '通话中',
                style: VoxFonts.display(
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                  color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
                ),
              ),
              const SizedBox(width: 4),
              Text(
                '$minutes:$secs',
                style: VoxFonts.mono(
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                  color: isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
                ),
              ),
            ],
          ),
          const Spacer(),
          _ThemeToggleBtn(
            key: const Key('vox_theme_toggle_agent'),
            isDark: isDark,
            onTap: widget.onThemeToggle,
          ),
        ],
      ),
    );
  }
}

class _LiveDot extends StatelessWidget {
  final bool isDark;
  const _LiveDot({required this.isDark});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 7,
      height: 7,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: isDark ? VoxColors.darkAccent : VoxColors.lightAccent,
        boxShadow: [
          BoxShadow(
            color: isDark ? VoxColors.darkAccentGlow : VoxColors.lightAccentGlow,
            blurRadius: 8,
          ),
        ],
      ),
    );
  }
}

class _IconBtn extends StatelessWidget {
  final IconData icon;
  final bool isDark;
  final VoidCallback? onTap;
  const _IconBtn({super.key, required this.icon, required this.isDark, this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 36,
        height: 36,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: isDark ? VoxColors.darkSurface : VoxColors.lightSurface,
          border: Border.all(
            color: isDark ? VoxColors.darkBorder : VoxColors.lightBorder,
          ),
          boxShadow: isDark ? VoxShadow.darkSm : VoxShadow.lightSm,
        ),
        child: Icon(
          icon,
          size: 18,
          color: isDark ? VoxColors.darkFg2 : VoxColors.lightFg2,
        ),
      ),
    );
  }
}

class _ThemeToggleBtn extends StatelessWidget {
  final bool isDark;
  final VoidCallback onTap;
  const _ThemeToggleBtn({super.key, required this.isDark, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        width: 36,
        height: 36,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: isDark ? VoxColors.darkSurface : VoxColors.lightSurface,
          border: Border.all(
            color: isDark ? VoxColors.darkBorder : VoxColors.lightBorder,
          ),
          boxShadow: isDark ? VoxShadow.darkSm : VoxShadow.lightSm,
        ),
        child: Icon(
          isDark ? Icons.light_mode : Icons.dark_mode,
          size: 18,
          color: isDark ? VoxColors.darkFg2 : VoxColors.lightFg2,
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Stage — orb centered as background (180px, same as welcome screen)
// ═══════════════════════════════════════════

class _Stage extends StatelessWidget {
  final VoxOrbState orbState;
  final String status;
  final String hint;
  const _Stage({
    required this.orbState,
    required this.status,
    required this.hint,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    // Center widget: takes the full Stage slot from the parent Stack
    // and centers this Column inside it. Without Center, the Column
    // would shrink to VoxOrb's 180-px width and Stack would lay it out
    // at topStart — leaving the orb pinned to the left side of the
    // screen. Center fixes both axes in one shot.
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          VoxOrb(
            key: const Key('vox_orb_agent'),
            size: 180,
            state: orbState,
          ),
          const SizedBox(height: 12),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Row(
                  key: const Key('vox_agent_status_text'),
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    _LiveDot(isDark: isDark),
                    const SizedBox(width: 8),
                    Text(
                      status,
                      style: VoxFonts.display(
                        fontSize: 14,
                        fontWeight: FontWeight.w600,
                        color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  key: const Key('vox_agent_hint_text'),
                  hint,
                  textAlign: TextAlign.center,
                  style: VoxFonts.body(
                    fontSize: 12,
                    color: isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Chat Panel — covers the Stage area; holds the message list AND
//  the input bar inside one Column so they're guaranteed to share the
//  same container size and never mis-align.
// ═══════════════════════════════════════════

class _ChatPanel extends StatelessWidget {
  final ValueChanged<String> onSend;
  const _ChatPanel({required this.onSend});

  /// Set of message ids that have been rendered at least once.
  /// Used by e2e to verify multi-round chat accumulation via sim log
  /// [Client] [bubble] rendered markers. Static so it survives widget
  /// rebuilds within the same screen session.
  static final Set<String> _renderedBubbleIds = <String>{};

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    // Frosted-glass background spans the whole panel so the orb shows
    // through above the message list. The input bar at the bottom uses
    // its own opaque surface so the text field stays readable.
    final panelBg = isDark
        ? VoxColors.darkSurface.withValues(alpha: 0.78)
        : VoxColors.lightSurface.withValues(alpha: 0.72);

    return SizedBox.expand(
      child: ClipRect(
        key: const Key('vox_chat_panel'),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: 28, sigmaY: 28),
          child: Container(
            decoration: BoxDecoration(
              color: panelBg,
              border: Border(
                top: BorderSide(
                  color: isDark
                      ? VoxColors.darkBorderSoft
                      : VoxColors.lightBorderSoft,
                ),
              ),
            ),
            child: Column(
              children: [
                Expanded(
                  child: Consumer<sdk.Session>(
                    builder: (context, session, _) {
                      return components.ChatScrollView(
                        session: session,
                        padding: const EdgeInsets.symmetric(
                          horizontal: 20,
                          vertical: 16,
                        ),
                        physics: const BouncingScrollPhysics(),
                        messageBuilder: (context, message) {
                          final isNew = _renderedBubbleIds.add(message.id);
                          if (isNew) {
                            final preview = message.content.text.length > 25
                                ? '${message.content.text.substring(0, 25)}...'
                                : message.content.text;
                            ClientLog.event(
                              'bubble',
                              'rendered id=${message.id} '
                              'kind=${message.content.runtimeType} '
                              'text="${preview}"',
                            );
                          }
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 16),
                            child: _MessageBubble(message: message),
                          );
                        },
                      );
                    },
                  ),
                ),
                _InputBar(onSend: onSend),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  final sdk.ReceivedMessage message;
  const _MessageBubble({required this.message});

  bool get _isUserMessage =>
      message.content is sdk.UserInput || message.content is sdk.UserTranscript;

  @override
  Widget build(BuildContext context) {
    final text = message.content.text.trim();
    if (text.isEmpty) return const SizedBox.shrink();

    final isUser = _isUserMessage;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Align(
      key: ValueKey(
        isUser ? 'vox_message_bubble_user_${message.id}' : 'vox_message_bubble_agent_${message.id}',
      ),
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Column(
        crossAxisAlignment: isUser ? CrossAxisAlignment.end : CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(4),
                  color: isUser
                      ? (isDark ? VoxColors.darkAccentSoft : VoxColors.lightAccentSoft)
                      : (isDark
                          ? VoxColors.darkAccent2.withValues(alpha: 0.2)
                          : VoxColors.lightAccentSoft2),
                ),
                child: Text(
                  isUser ? '你' : 'Vox',
                  style: VoxFonts.body(
                    fontSize: 9,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.08,
                    color: isUser
                        ? (isDark ? VoxColors.darkAccent : VoxColors.lightAccent)
                        : (isDark ? VoxColors.darkAccent2 : VoxColors.lightAccent2),
                  ),
                ),
              ),
              const SizedBox(width: 6),
              Text(
                _formatTime(message.timestamp),
                style: VoxFonts.mono(
                  fontSize: 10,
                  letterSpacing: 0.05,
                  color: isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          ConstrainedBox(
            constraints: BoxConstraints(
              maxWidth: MediaQuery.of(context).size.width * 0.75,
            ),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                gradient: isUser
                    ? const LinearGradient(
                        colors: [VoxColors.ctaGradStart, VoxColors.ctaGradEnd],
                        begin: Alignment.topLeft,
                        end: Alignment.bottomRight,
                      )
                    : null,
                color: isUser ? null : (isDark ? VoxColors.darkSurface : VoxColors.lightSurface),
                border: isUser
                    ? null
                    : Border.all(
                        color: isDark ? VoxColors.darkBorderSoft : VoxColors.lightBorderSoft,
                      ),
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(20),
                  topRight: const Radius.circular(20),
                  bottomLeft: Radius.circular(isUser ? 20 : 4),
                  bottomRight: Radius.circular(isUser ? 4 : 20),
                ),
                boxShadow: isUser
                    ? [
                        BoxShadow(
                          color: VoxColors.orbOrange.withValues(alpha: 0.20),
                          blurRadius: 12,
                          offset: const Offset(0, 4),
                        ),
                      ]
                    : [
                        const BoxShadow(
                          color: Color(0x0A14161E),
                          blurRadius: 2,
                          offset: Offset(0, 1),
                        ),
                      ],
              ),
              child: Text(
                text,
                style: VoxFonts.body(
                  fontSize: 14.5,
                  height: 1.5,
                  color: isUser
                      ? VoxColors.lightFgOnAccent
                      : (isDark ? VoxColors.darkFg : VoxColors.lightFg),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  String _formatTime(DateTime dt) {
    return '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}:${dt.second.toString().padLeft(2, '0')}';
  }
}

// ═══════════════════════════════════════════
//  Input Bar
// ═══════════════════════════════════════════

class _InputBar extends StatefulWidget {
  final ValueChanged<String> onSend;
  const _InputBar({required this.onSend});

  @override
  State<_InputBar> createState() => _InputBarState();
}

class _InputBarState extends State<_InputBar> {
  final _controller = TextEditingController();
  bool _hasText = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onTextChanged);
  }

  void _onTextChanged() {
    if (!mounted) return;
    // Mirror text into AppCtrl.messageCtrl so sendMessage() — which reads
    // from messageCtrl.text — picks up the actual user input. Without this
    // mirror, the TextField's local controller holds the text but the
    // shared messageCtrl stays empty, and the user's message is never
    // sent to the agent.
    context.read<AppCtrl>().messageCtrl.text = _controller.text;
    setState(() => _hasText = _controller.text.isNotEmpty);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return Container(
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(
            color: isDark ? VoxColors.darkBorderSoft : VoxColors.lightBorderSoft,
          ),
        ),
        color: isDark ? VoxColors.darkSurface : VoxColors.lightSurface,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(22),
                border: Border.all(
                  color: isDark ? VoxColors.darkBorder : VoxColors.lightBorder,
                ),
                color: isDark ? VoxColors.darkSurface2 : VoxColors.lightSurface2,
              ),
              padding: const EdgeInsets.symmetric(horizontal: 14),
              child: TextField(
                key: const Key('vox_chat_input'),
                controller: _controller,
                minLines: 1,
                maxLines: 3,
                style: VoxFonts.body(
                  fontSize: 14.5,
                  height: 1.4,
                  color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
                ),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: '输入文字消息…',
                  hintStyle: VoxFonts.body(
                    fontSize: 14.5,
                    color: isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
                  ),
                  contentPadding: const EdgeInsets.symmetric(vertical: 10),
                ),
                onSubmitted: _hasText ? (v) {
                  ClientLog.event('text', 'input submit: ${v.length > 80 ? "${v.substring(0, 80)}..." : v}');
                  widget.onSend(v);
                } : null,
              ),
            ),
          ),
          const SizedBox(width: 10),
          GestureDetector(
            key: const Key('vox_send_button'),
            onTap: _hasText
                ? () {
                    ClientLog.event('text', 'input send: ${_controller.text.length > 80 ? "${_controller.text.substring(0, 80)}..." : _controller.text}');
                    widget.onSend(_controller.text);
                    _controller.clear();
                  }
                : null,
            child: Container(
              width: 42,
              height: 42,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: const LinearGradient(
                  colors: [VoxColors.ctaGradStart, VoxColors.ctaGradEnd],
                ),
                boxShadow: [
                  BoxShadow(
                    color: VoxColors.orbOrange.withValues(alpha: 0.30),
                    blurRadius: 12,
                    offset: const Offset(0, 4),
                  ),
                ],
              ),
              child: Icon(
                Icons.arrow_forward_ios,
                size: 18,
                color: VoxColors.lightFgOnAccent.withValues(alpha: _hasText ? 1.0 : 0.5),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Control Bar (4 buttons: mic, speaker, chat, hangup)
// ═══════════════════════════════════════════

class _ControlBar extends StatelessWidget {
  final bool micOn;
  final bool speakerOn;
  final bool chatOn;
  final VoidCallback onMicToggle;
  final VoidCallback onSpeakerToggle;
  final VoidCallback onChatToggle;
  final VoidCallback onHangup;

  const _ControlBar({
    required this.micOn,
    required this.speakerOn,
    required this.chatOn,
    required this.onMicToggle,
    required this.onSpeakerToggle,
    required this.onChatToggle,
    required this.onHangup,
  });

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 18, 24, 26),
      decoration: BoxDecoration(
        border: Border(
          top: BorderSide(
            color: isDark ? VoxColors.darkBorderSoft : VoxColors.lightBorderSoft,
          ),
        ),
        color: isDark ? VoxColors.darkSurface : VoxColors.lightSurface,
      ),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          _ControlBtn(
            key: const Key('vox_control_mic'),
            icon: micOn ? Icons.mic : Icons.mic_off,
            label: '麦克风',
            isActive: micOn,
            isMuted: !micOn,
            isDark: isDark,
            onTap: onMicToggle,
          ),
          _ControlBtn(
            key: const Key('vox_control_speaker'),
            icon: speakerOn ? Icons.volume_up : Icons.volume_off,
            label: '声音',
            isActive: speakerOn,
            isMuted: !speakerOn,
            isDark: isDark,
            onTap: onSpeakerToggle,
          ),
          _ControlBtn(
            key: const Key('vox_control_chat'),
            icon: chatOn ? Icons.chat_bubble : Icons.chat_bubble_outline,
            label: '聊天',
            isActive: chatOn,
            isDark: isDark,
            onTap: onChatToggle,
          ),
          _HangupBtn(
            key: const Key('vox_control_hangup'),
            isDark: isDark,
            onTap: onHangup,
          ),
        ],
      ),
    );
  }
}

class _ControlBtn extends StatefulWidget {
  final IconData icon;
  final String label;
  final bool isActive;
  final bool isMuted;
  final bool isDark;
  final VoidCallback onTap;

  const _ControlBtn({
    super.key,
    required this.icon,
    required this.label,
    required this.isActive,
    this.isMuted = false,
    required this.isDark,
    required this.onTap,
  });

  @override
  State<_ControlBtn> createState() => _ControlBtnState();
}

class _ControlBtnState extends State<_ControlBtn> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 160),
              curve: Curves.easeInOut,
              width: 50,
              height: 50,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: _bgColor(),
                border: Border.all(color: _borderColor(), width: 1),
                boxShadow: _isHovered && !widget.isMuted
                    ? [
                        BoxShadow(
                          color: VoxColors.orbOrange.withValues(alpha: 0.20),
                          blurRadius: 18,
                          offset: const Offset(0, 8),
                        ),
                      ]
                    : null,
              ),
              transform: _isHovered
                  ? Matrix4.translationValues(0, -2, 0)
                  : Matrix4.identity(),
              child: Icon(
                widget.icon,
                size: 22,
                color: _iconColor(),
              ),
            ),
            AnimatedOpacity(
              duration: const Duration(milliseconds: 160),
              opacity: _isHovered ? 0.85 : 0.0,
              child: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  widget.label,
                  style: VoxFonts.body(
                    fontSize: 9,
                    letterSpacing: 0.04,
                    color: widget.isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Color _bgColor() {
    if (widget.isActive && !widget.isMuted) {
      return widget.isDark ? VoxColors.darkAccentSoft : VoxColors.lightAccentSoft;
    }
    if (widget.isMuted) {
      return widget.isDark ? VoxColors.darkSurface2 : VoxColors.lightSurface2;
    }
    return widget.isDark ? VoxColors.darkSurface : VoxColors.lightSurface;
  }

  Color _borderColor() {
    if (widget.isActive && !widget.isMuted) {
      return widget.isDark ? VoxColors.darkAccent : VoxColors.lightAccent;
    }
    if (widget.isMuted) {
      return widget.isDark ? VoxColors.darkBorder : VoxColors.lightBorder;
    }
    return widget.isDark ? VoxColors.darkBorder : VoxColors.lightBorder;
  }

  Color _iconColor() {
    if (widget.isMuted) {
      return widget.isDark ? VoxColors.darkFg3 : VoxColors.lightFg3;
    }
    if (widget.isActive) {
      return widget.isDark ? VoxColors.darkAccent : VoxColors.lightAccent;
    }
    return widget.isDark ? VoxColors.darkFg2 : VoxColors.lightFg2;
  }
}

class _HangupBtn extends StatefulWidget {
  final bool isDark;
  final VoidCallback onTap;
  const _HangupBtn({super.key, required this.isDark, required this.onTap});

  @override
  State<_HangupBtn> createState() => _HangupBtnState();
}

class _HangupBtnState extends State<_HangupBtn> {
  bool _isHovered = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 160),
              curve: Curves.easeInOut,
              width: 50,
              height: 50,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                gradient: LinearGradient(
                  colors: _isHovered
                      ? [const Color(0xFFFF8585), const Color(0xFFF25555)]
                      : const [VoxColors.hangupGradStart, VoxColors.hangupGradEnd],
                  begin: Alignment.topLeft,
                  end: Alignment.bottomRight,
                ),
                boxShadow: [
                  BoxShadow(
                    color: VoxColors.lightDanger.withValues(alpha: _isHovered ? 0.50 : 0.40),
                    blurRadius: _isHovered ? 24 : 18,
                    offset: Offset(0, _isHovered ? 10.0 : 6.0),
                  ),
                ],
              ),
              transform: _isHovered
                  ? Matrix4.translationValues(0, -2, 0)
                  : Matrix4.identity(),
              child: const Icon(
                Icons.call_end,
                size: 22,
                color: VoxColors.lightFgOnAccent,
              ),
            ),
            const SizedBox(height: 4),
            Text(
              '挂断',
              style: VoxFonts.body(
                fontSize: 9,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.04,
                color: widget.isDark ? VoxColors.darkFg3 : VoxColors.lightFg3,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Splash overlay shown on the Agent screen until the worker has actually
/// joined the room and published its audio track.
///
/// Why this exists: between pressing "connect" on the welcome screen and
/// the agent's first reply, the user previously saw a static "正在聆听"
/// orb for 5+ seconds.  This overlay makes it clear we're still
/// connecting — the same affordance mobile-app users expect from any
/// voice/chat boot.  Auto-fades when ``_agentReady`` flips true.
class _ConnectingSplash extends StatefulWidget {
  const _ConnectingSplash();

  @override
  State<_ConnectingSplash> createState() => _ConnectingSplashState();
}

class _ConnectingSplashState extends State<_ConnectingSplash>
    with SingleTickerProviderStateMixin {
  late final AnimationController _spinCtrl;

  @override
  void initState() {
    super.initState();
    _spinCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat();
  }

  @override
  void dispose() {
    _spinCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final dim = isDark
        ? Colors.black.withValues(alpha: 0.32)
        : Colors.white.withValues(alpha: 0.42);
    final ring = isDark
        ? VoxColors.darkAccent
        : VoxColors.lightAccent;
    final titleColor = isDark ? VoxColors.darkFg : VoxColors.lightFg;
    final subColor = isDark ? VoxColors.darkFg3 : VoxColors.lightFg3;

    return SizedBox.expand(
      child: DecoratedBox(
        decoration: BoxDecoration(color: dim),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                width: 56,
                height: 56,
                child: RotationTransition(
                  turns: _spinCtrl,
                  child: CircularProgressIndicator(
                    strokeWidth: 3.5,
                    valueColor: AlwaysStoppedAnimation<Color>(ring),
                  ),
                ),
              ),
              const SizedBox(height: 18),
              Text(
                'Vox 正在唤醒…',
                style: VoxFonts.display(
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                  color: titleColor,
                ),
              ),
              const SizedBox(height: 6),
              Text(
                '首次连接通常需要数秒',
                style: VoxFonts.body(fontSize: 12, color: subColor),
              ),
            ],
          ),
        ),
      ),
    );
  }
}