import 'package:flutter/material.dart';

/// Vox design tokens — 1:1 mapping of the CSS custom properties.
/// Light and dark variants match start.html / assistant.html exactly.
class VoxColors {
  // ── Light mode ──
  static const lightBg = Color(0xFFF4F5F7);
  static const lightBg2 = Color(0xFFFAFAFB);
  static const lightSurface = Color(0xFFFFFFFF);
  static const lightSurface2 = Color(0xFFF8F9FB);
  static const lightSurface3 = Color(0xFFEFF1F5);
  static const lightSurface4 = Color(0xFFE7E9EE);

  static const lightFg = Color(0xFF15171F);
  static const lightFg2 = Color(0xFF4A5260);
  static const lightFg3 = Color(0xFF8A95A4);
  static const lightFgOnAccent = Color(0xFFFFFFFF);

  static const lightBorder = Color(0xFFE7E9EE);
  static const lightBorderSoft = Color(0xFFF0F2F6);
  static const lightBorderStrong = Color(0xFFD4D8E0);

  static const lightAccent = Color(0xFFFF6B4A);
  static const lightAccent2 = Color(0xFF4F46E5);
  static const lightAccent3 = Color(0xFFF59E0B);
  static const lightAccentSoft = Color(0xFFFFE5DC);
  static const lightAccentSoft2 = Color(0xFFF0EBFF);
  static const lightAccentGlow = Color(0x59FF6B4A); // 35% opacity

  static const lightSuccess = Color(0xFF22C55E);
  static const lightWarn = Color(0xFFF59E0B);
  static const lightDanger = Color(0xFFEF4444);

  // Dark mode
  static const darkBg = Color(0xFF0E1116);
  static const darkBg2 = Color(0xFF14171E);
  static const darkSurface = Color(0xFF1A1E26);
  static const darkSurface2 = Color(0xFF20242D);
  static const darkSurface3 = Color(0xFF2A2F3A);

  static const darkFg = Color(0xFFF2F4F7);
  static const darkFg2 = Color(0xFFB6BCC7);
  static const darkFg3 = Color(0xFF6A7280);
  static const darkFgOnAccent = Color(0xFFFFFFFF);

  static const darkBorder = Color(0xFF2A2F3A);
  static const darkBorderSoft = Color(0xFF20242D);
  static const darkBorderStrong = Color(0xFF3A3F4A);

  static const darkAccent = Color(0xFFFF6B4A);
  static const darkAccent2 = Color(0xFF4F46E5);
  static const darkAccent3 = Color(0xFFF59E0B);
  static const darkAccentSoft = Color(0x2EFF6B4A); // 18%
  static const darkAccentGlow = Color(0x80FF6B4A); // 50%

  // Orb sunset gradient colours (shared between light/dark)
  static const orbWhite = Color(0xFFFFFFFF);
  static const orbPeach = Color(0xFFFFD2BC);
  static const orbOrangeLight = Color(0xFFFF9B7A);
  static const orbOrange = Color(0xFFFF6B4A);
  static const orbMagenta = Color(0xFFE8489C);
  static const orbPurple = Color(0xFF7C5CFF);

  // CTA gradient
  static const ctaGradStart = Color(0xFFFF6B4A); // accent
  static const ctaGradEnd = Color(0xFFE8489C); // magenta

  // Hangup gradient
  static const hangupGradStart = Color(0xFFFF6B6B);
  static const hangupGradEnd = Color(0xFFEF4444);

  // Helper: get accent glow for current brightness
  static Color accentGlow(BuildContext context) =>
      Theme.of(context).brightness == Brightness.light ? lightAccentGlow : darkAccentGlow;

  // Helper: get accent soft for current brightness
  static Color accentSoft(BuildContext context) =>
      Theme.of(context).brightness == Brightness.light ? lightAccentSoft : darkAccentSoft;
}

/// Spacing constants from the design.
class VoxSpacing {
  static const double px1 = 4;
  static const double px2 = 8;
  static const double px3 = 12;
  static const double px4 = 16;
  static const double px5 = 20;
  static const double px6 = 24;
  static const double px8 = 32;
  static const double px10 = 40;
  static const double px12 = 48;
}

/// Border radius constants from the design.
class VoxRadius {
  static const double sm = 12;
  static const double md = 18;
  static const double lg = 28;
  static const double pill = 9999;
  static const double screen = 44;
}

/// Shadow constants from the design.
class VoxShadow {
  static List<BoxShadow> lightSm = const [
    BoxShadow(
      color: Color(0x0A14161E),
      offset: Offset(0, 1),
      blurRadius: 2,
      spreadRadius: 0,
    ),
    BoxShadow(
      color: Color(0x0D14161E),
      offset: Offset(0, 1),
      blurRadius: 3,
      spreadRadius: 0,
    ),
  ];

  static List<BoxShadow> darkSm = const [
    BoxShadow(
      color: Color(0x4014161E),
      offset: Offset(0, 1),
      blurRadius: 2,
      spreadRadius: 0,
    ),
    BoxShadow(
      color: Color(0x4D14161E),
      offset: Offset(0, 1),
      blurRadius: 3,
      spreadRadius: 0,
    ),
  ];

  static List<BoxShadow> lightMd = const [
    BoxShadow(
      color: Color(0x0D14161E),
      offset: Offset(0, 4),
      blurRadius: 12,
    ),
    BoxShadow(
      color: Color(0x0814161E),
      offset: Offset(0, 2),
      blurRadius: 4,
    ),
  ];

  static List<BoxShadow> darkMd = const [
    BoxShadow(
      color: Color(0x4D14161E),
      offset: Offset(0, 4),
      blurRadius: 12,
    ),
    BoxShadow(
      color: Color(0x3314161E),
      offset: Offset(0, 2),
      blurRadius: 4,
    ),
  ];

  static List<BoxShadow> lightLg = const [
    BoxShadow(
      color: Color(0x1414161E),
      offset: Offset(0, 16),
      blurRadius: 40,
    ),
    BoxShadow(
      color: Color(0x0A14161E),
      offset: Offset(0, 4),
      blurRadius: 12,
    ),
  ];

  static List<BoxShadow> darkLg = const [
    BoxShadow(
      color: Color(0x6614161E),
      offset: Offset(0, 16),
      blurRadius: 40,
    ),
    BoxShadow(
      color: Color(0x4014161E),
      offset: Offset(0, 4),
      blurRadius: 12,
    ),
  ];

  static List<BoxShadow> darkXl = const [
    BoxShadow(
      color: Color(0x8014161E),
      offset: Offset(0, 32),
      blurRadius: 80,
    ),
    BoxShadow(
      color: Color(0x4D14161E),
      offset: Offset(0, 8),
      blurRadius: 20,
    ),
  ];
}
