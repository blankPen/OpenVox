/// Centralised widget Key names so tests don't pass stringly-typed keys.
///
/// Adding a new Key? Append it here and import this file in the test file.
/// Tests find widgets by Key('vox_xxx'); Flutter renders them as runtime
/// `ValueKey('vox_xxx')` strings, so the test side stays a simple
/// `find.byKey(voxXxx)`.
library;

import 'package:flutter/widgets.dart';

const String kVoxTopBar = 'vox_top_bar';
const String kVoxThemeToggle = 'vox_theme_toggle';
const String kVoxOrbWelcome = 'vox_orb_welcome';
const String kVoxOrbAgent = 'vox_orb_agent';
const String kVoxBrandText = 'vox_brand_text';
const String kVoxDescription = 'vox_description';
const String kVoxWelcomeCta = 'vox_welcome_cta';

const String kVoxAgentTopBar = 'vox_agent_topbar';
const String kVoxAgentTopBarBack = 'vox_agent_topbar_back';
const String kVoxAgentStatusText = 'vox_agent_status_text';
const String kVoxAgentHintText = 'vox_agent_hint_text';

const String kVoxChatPanel = 'vox_chat_panel';
const String kVoxChatInput = 'vox_chat_input';
const String kVoxSendButton = 'vox_send_button';

const String kVoxControlMic = 'vox_control_mic';
const String kVoxControlSpeaker = 'vox_control_speaker';
const String kVoxControlChat = 'vox_control_chat';
const String kVoxControlHangup = 'vox_control_hangup';

const String kVoxErrorBanner = 'vox_error_banner';

/// Helper to turn a static key constant into a [Key] instance. Use sparingly:
/// prefer the raw [find] APIs (find.byKey(const Key('vox_xxx'))) which the
/// compiler can statically resolve.
Key voxKey(String name) => Key(name);