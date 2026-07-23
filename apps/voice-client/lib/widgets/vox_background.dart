import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../ui/vox_colors.dart';

/// Vox background with radial gradients and dot grid.
///
/// Matches the body background of start.html / assistant.html.
class VoxBackground extends StatelessWidget {
  final Widget child;

  const VoxBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    final bg = isDark ? VoxColors.darkBg : VoxColors.lightBg;

    final grad1 = isDark
        ? VoxColors.darkAccent.withValues(alpha: 0.06)
        : VoxColors.lightAccent.withValues(alpha: 0.12);
    final grad2 = isDark
        ? VoxColors.darkAccent2.withValues(alpha: 0.05)
        : VoxColors.lightAccent2.withValues(alpha: 0.10);
    final grad3 = isDark
        ? VoxColors.darkAccent3.withValues(alpha: 0.04)
        : VoxColors.lightAccent3.withValues(alpha: 0.08);

    return SizedBox.expand(
      child: DecoratedBox(
        decoration: BoxDecoration(color: bg),
        child: Stack(
        children: [
          // Radial gradient 1: top-left
          Positioned.fill(
            child: CustomPaint(
              painter: _RadialGradientPainter(
                center: Alignment.topLeft,
                color: grad1,
              ),
            ),
          ),

          // Radial gradient 2: top-right
          Positioned.fill(
            child: CustomPaint(
              painter: _RadialGradientPainter(
                center: Alignment.topRight,
                color: grad2,
              ),
            ),
          ),

          // Radial gradient 3: bottom-center
          Positioned.fill(
            child: CustomPaint(
              painter: _RadialGradientPainter(
                center: const Alignment(0.0, 1.0),
                color: grad3,
              ),
            ),
          ),

          // Dot grid overlay with vignette mask:
          //   mask-image: radial-gradient(ellipse 80% 70% at 50% 50%, black 20%, transparent 80%)
          Positioned.fill(
            child: ShaderMask(
              shaderCallback: (bounds) {
                return RadialGradient(
                  colors: [
                    Colors.black,
                    Colors.black.withValues(alpha: 0.6),
                    Colors.transparent,
                  ],
                  stops: const [0.2, 0.5, 0.8],
                ).createShader(
                  Rect.fromCircle(
                    center: bounds.center,
                    radius: math.max(bounds.width, bounds.height) * 0.7,
                  ),
                );
              },
              blendMode: BlendMode.dstIn,
              child: CustomPaint(
                painter: _DotGridPainter(isDark: isDark),
              ),
            ),
          ),

          child,
        ],
        ),
      ),
    );
  }
}

/// Paints a large elliptical radial gradient.
class _RadialGradientPainter extends CustomPainter {
  final Alignment center;
  final Color color;

  _RadialGradientPainter({required this.center, required this.color});

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..shader = RadialGradient(
        colors: [color, color.withValues(alpha: 0), Colors.transparent],
        stops: const [0.0, 0.55, 1.0],
      ).createShader(Rect.fromLTWH(
        -size.width * 0.2,
        -size.height * 0.2,
        size.width * 1.4,
        size.height * 1.4,
      ));
    canvas.drawRect(Rect.fromLTWH(0, 0, size.width, size.height), paint);
  }

  @override
  bool shouldRepaint(_RadialGradientPainter oldDelegate) =>
      oldDelegate.color != color;
}

/// Paints a 28px dot grid with a soft vignette mask.
class _DotGridPainter extends CustomPainter {
  final bool isDark;

  _DotGridPainter({required this.isDark});

  @override
  void paint(Canvas canvas, Size size) {
    final dotColor = isDark
        ? Colors.white.withValues(alpha: 0.04)
        : const Color(0xFF14161E).withValues(alpha: 0.04);
    final dotPaint = Paint()..color = dotColor;
    const spacing = 28.0;
    const dotRadius = 0.5;

    for (double x = 0; x < size.width; x += spacing) {
      for (double y = 0; y < size.height; y += spacing) {
        canvas.drawCircle(Offset(x - 1, y - 1), dotRadius, dotPaint);
      }
    }
  }

  @override
  bool shouldRepaint(_DotGridPainter oldDelegate) =>
      oldDelegate.isDark != isDark;
}
