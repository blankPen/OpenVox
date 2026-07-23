import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Vox typography helpers — maps the design's font stack to Google Fonts.
///
/// CSS:
///   --font-display: "Plus Jakarta Sans", Inter, system-ui, sans-serif;
///   --font-body: Inter, system-ui, sans-serif;
///   --font-mono: "JetBrains Mono", ui-monospace, Menlo, monospace;
class VoxFonts {
  VoxFonts._();

  /// Plus Jakarta Sans — brand, headings, orb status.
  static TextStyle display({
    double fontSize = 14,
    FontWeight fontWeight = FontWeight.w600,
    Color? color,
    double? height,
    double? letterSpacing,
  }) =>
      GoogleFonts.plusJakartaSans(
        fontSize: fontSize,
        fontWeight: fontWeight,
        color: color,
        height: height,
        letterSpacing: letterSpacing,
      );

  /// Inter — body text, descriptions, messages.
  static TextStyle body({
    double fontSize = 15,
    FontWeight fontWeight = FontWeight.w400,
    Color? color,
    double? height,
    double? letterSpacing,
  }) =>
      GoogleFonts.inter(
        fontSize: fontSize,
        fontWeight: fontWeight,
        color: color,
        height: height,
        letterSpacing: letterSpacing,
      );

  /// JetBrains Mono — timer, meta text, labels.
  static TextStyle mono({
    double fontSize = 12,
    FontWeight fontWeight = FontWeight.w500,
    Color? color,
    double? letterSpacing,
    List<FontFeature>? fontFeatures,
  }) =>
      GoogleFonts.jetBrainsMono(
        fontSize: fontSize,
        fontWeight: fontWeight,
        color: color,
        letterSpacing: letterSpacing,
        fontFeatures: fontFeatures,
      );
}
