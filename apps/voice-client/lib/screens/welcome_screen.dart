import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:livekit_client/livekit_client.dart' as sdk;
import 'package:provider/provider.dart';

import '../app.dart';
import '../controllers/app_ctrl.dart';
import '../ui/vox_colors.dart';
import '../ui/vox_fonts.dart';
import '../widgets/vox_orb.dart';

/// Welcome / launch screen — matches start.html 1:1.
class WelcomeScreen extends StatelessWidget {
  const WelcomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.transparent,
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(40, 16, 40, 28),
          child: Column(
            children: [
              _TopBar(),
              Expanded(
                child: LayoutBuilder(
                  builder: (ctx, constraints) {
                    final scale = (constraints.maxHeight / 480).clamp(0.7, 1.0);
                    final orbSize = (180 * scale).clamp(120.0, 200.0);
                    final gap1 = 20.0 * scale;
                    final gap2 = 8.0 * scale;
                    return Column(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        VoxOrb(
                          key: const Key('vox_orb_welcome'),
                          size: orbSize,
                          state: VoxOrbState.idle,
                          showRings: false,
                        ),
                        SizedBox(height: gap1),
                        const _BrandText(),
                        SizedBox(height: gap2),
                        const _Description(),
                      ],
                    );
                  },
                ),
              ),
              const _CtaButton(),
            ],
          ),
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Top Bar
// ═══════════════════════════════════════════

class _TopBar extends StatelessWidget {
  const _TopBar();

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Row(
      key: const Key('vox_top_bar'),
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: isDark ? VoxColors.darkAccent : VoxColors.lightAccent,
                boxShadow: [
                  BoxShadow(
                    color: isDark ? VoxColors.darkAccentGlow : VoxColors.lightAccentGlow,
                    blurRadius: 10,
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Text(
              'Vox',
              style: VoxFonts.display(
                fontSize: 13,
                fontWeight: FontWeight.w600,
                letterSpacing: -0.01,
                color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
              ),
            ),
          ],
        ),
        const _ThemeToggleBtn(),
      ],
    );
  }
}

class _ThemeToggleBtn extends StatefulWidget {
  const _ThemeToggleBtn();

  @override
  State<_ThemeToggleBtn> createState() => _ThemeToggleBtnState();
}

class _ThemeToggleBtnState extends State<_ThemeToggleBtn>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 400),
    );
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return GestureDetector(
      key: const Key('vox_theme_toggle_welcome'),
      onTap: () {
        unawaited(_ctrl.forward(from: 0));
        AppTheme.toggle(context);
      },
      child: AnimatedBuilder(
        animation: _ctrl,
        builder: (context, _) {
          return Container(
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
            child: Transform(
              transform: Matrix4.diagonal3Values(
                    1 - _ctrl.value * 0.08,
                    1 - _ctrl.value * 0.08,
                    1 - _ctrl.value * 0.08,
                  )
                ..rotateZ(_ctrl.value * -0.26),
              alignment: Alignment.center,
              child: Icon(
                isDark ? Icons.light_mode : Icons.dark_mode,
                size: 18,
                color: isDark ? VoxColors.darkFg2 : VoxColors.lightFg2,
              ),
            ),
          );
        },
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Brand Text
// ═══════════════════════════════════════════

class _BrandText extends StatelessWidget {
  const _BrandText();

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Column(
      key: const Key('vox_brand_text'),
      children: [
        Text.rich(
          TextSpan(
            style: VoxFonts.display(
              fontSize: 56,
              fontWeight: FontWeight.w500,
              letterSpacing: -0.04,
              height: 1,
            ),
            children: [
              TextSpan(
                text: 'Vo',
                style: TextStyle(
                  color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
                ),
              ),
              const WidgetSpan(
                child: _GradientText(
                  text: 'x',
                  gradient: LinearGradient(
                    // Pink → purple only — the orange at the start of the
                    // gradient pulled the "x" toward red.
                    colors: [
                      VoxColors.orbMagenta,
                      VoxColors.orbPurple,
                    ],
                    stops: [0.0, 1.0],
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Text(
          'just speak.',
          style: VoxFonts.body(
            fontSize: 13,
            letterSpacing: 0.04,
            // LightFg3 (0x8A95A4) reads slightly blue; the design wants a
            // neutral pale gray so the subtitle recedes behind the wordmark.
            color: isDark
                ? VoxColors.darkFg3
                : const Color(0xFFB4BAC4),
          ),
        ),
      ],
    );
  }
}

/// Renders text with a gradient fill using ShaderMask.
class _GradientText extends StatelessWidget {
  final String text;
  final Gradient gradient;

  const _GradientText({required this.text, required this.gradient});

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      shaderCallback: (bounds) => gradient.createShader(bounds),
      child: Text(
        text,
        style: VoxFonts.display(
          fontSize: 56,
          fontWeight: FontWeight.w500,
          letterSpacing: -0.04,
          color: Colors.white, // ShaderMask replaces this
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  Description
// ═══════════════════════════════════════════

class _Description extends StatelessWidget {
  const _Description();

  @override
  Widget build(BuildContext context) {
    final isDark = Theme.of(context).brightness == Brightness.dark;
    return Padding(
      key: const Key('vox_description'),
      padding: const EdgeInsets.symmetric(horizontal: 40),
      child: Text.rich(
        TextSpan(
          style: VoxFonts.body(
            fontSize: 16,
            height: 1.6,
            color: isDark ? VoxColors.darkFg2 : VoxColors.lightFg2,
          ),
          children: [
            const TextSpan(text: '一句话,我就懂了。\n'),
            TextSpan(
              text: '实时语音 AI',
              style: TextStyle(
                color: isDark ? VoxColors.darkFg : VoxColors.lightFg,
                fontWeight: FontWeight.w600,
              ),
            ),
            // Explicit \n before "听你说。" so it lands on its own line,
            // matching the design's three-line layout instead of relying on
            // column-width soft-wrap heuristics.
            // Half-width punctuation throughout — design uses English ","
            // not full-width "，".
            const TextSpan(text: ',陪你聊、帮你做、\n听你说。'),
          ],
        ),
        textAlign: TextAlign.center,
      ),
    );
  }
}

// ═══════════════════════════════════════════
//  CTA Button
// ═══════════════════════════════════════════

class _CtaButton extends StatelessWidget {
  const _CtaButton();

  @override
  Widget build(BuildContext context) {
    return Consumer2<AppCtrl, sdk.Session>(
      builder: (context, ctrl, session, _) {
        final isConnecting =
            ctrl.isSessionStarting || session.connectionState != sdk.ConnectionState.disconnected;

        return SizedBox(
          key: const Key('vox_welcome_cta'),
          width: double.infinity,
          child: _GradientCta(
            isConnecting: isConnecting,
            onTap: isConnecting ? null : () => ctrl.connect(),
          ),
        );
      },
    );
  }
}

class _GradientCta extends StatefulWidget {
  final bool isConnecting;
  final VoidCallback? onTap;

  const _GradientCta({required this.isConnecting, this.onTap});

  @override
  State<_GradientCta> createState() => _GradientCtaState();
}

class _GradientCtaState extends State<_GradientCta>
    with SingleTickerProviderStateMixin {
  bool _isHovered = false;
  late AnimationController _shimmerCtrl;

  @override
  void initState() {
    super.initState();
    _shimmerCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 700),
    );
  }

  @override
  void dispose() {
    _shimmerCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      onEnter: (_) => setState(() => _isHovered = true),
      onExit: (_) => setState(() => _isHovered = false),
      child: GestureDetector(
        onTap: () {
          if (widget.isConnecting || widget.onTap == null) return;
          widget.onTap!();
        },
        child: AnimatedBuilder(
          animation: _shimmerCtrl,
          builder: (context, _) {
            return AnimatedContainer(
              duration: const Duration(milliseconds: 160),
              curve: Curves.easeInOut,
              transform: _isHovered
                  ? Matrix4.translationValues(0, -2, 0)
                  : Matrix4.identity(),
              padding: const EdgeInsets.symmetric(vertical: 18, horizontal: 28),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(9999),
                gradient: const LinearGradient(
                  colors: [VoxColors.ctaGradStart, VoxColors.ctaGradEnd],
                  begin: Alignment.centerLeft,
                  end: Alignment.centerRight,
                ),
                boxShadow: [
                  BoxShadow(
                    color: VoxColors.orbOrange.withValues(alpha: _isHovered ? 0.45 : 0.35),
                    blurRadius: _isHovered ? 32 : 24,
                    offset: Offset(0, _isHovered ? 12.0 : 8.0),
                  ),
                  BoxShadow(
                    color: VoxColors.orbMagenta.withValues(alpha: _isHovered ? 0.30 : 0.20),
                    blurRadius: _isHovered ? 10 : 6,
                    offset: const Offset(0, 2),
                  ),
                ],
              ),
              // Single child — pure gradient background, no shimmer sweep
              // (the previous wide white band broke the surface purity).
              child: Center(
                child: widget.isConnecting
                    ? _LoadingContent()
                    : _IdleContent(isHovered: _isHovered),
              ),
            );
          },
        ),
      ),
    );
  }
}

class _IdleContent extends StatelessWidget {
  final bool isHovered;
  const _IdleContent({required this.isHovered});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          '开始语音通话',
          style: VoxFonts.display(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.01,
            color: VoxColors.lightFgOnAccent,
          ),
        ),
        const SizedBox(width: 6),
        AnimatedContainer(
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeInOutBack,
          transform: isHovered
              ? Matrix4.translationValues(4, -1, 0)
              : Matrix4.identity(),
          child: const Text(
            '→',
            style: TextStyle(
              fontFamily: 'PlusJakartaSans',
              fontSize: 16,
              fontWeight: FontWeight.w600,
              color: VoxColors.lightFgOnAccent,
            ),
          ),
        ),
      ],
    );
  }
}

class _LoadingContent extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        const _Spinner(size: 16),
        const SizedBox(width: 10),
        Text(
          '正在连接…',
          style: VoxFonts.display(
            fontSize: 16,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.01,
            color: VoxColors.lightFgOnAccent,
          ),
        ),
      ],
    );
  }
}

class _Spinner extends StatefulWidget {
  final double size;
  const _Spinner({this.size = 16});

  @override
  State<_Spinner> createState() => _SpinnerState();
}

class _SpinnerState extends State<_Spinner>
    with SingleTickerProviderStateMixin {
  late AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 800),
    )..repeat(); // ignore: discarded_futures
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _ctrl,
      builder: (context, _) {
        return Transform.rotate(
          angle: _ctrl.value * 2 * math.pi,
          child: CustomPaint(
            size: Size(widget.size, widget.size),
            painter: const _SpinnerPainter(),
          ),
        );
      },
    );
  }
}

class _SpinnerPainter extends CustomPainter {
  const _SpinnerPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round;
    canvas.drawArc(
      Rect.fromLTWH(0, 0, size.width, size.height),
      0,
      2.5,
      false,
      paint
        ..shader = const LinearGradient(
          colors: [Color(0x59FFFFFF), Colors.white],
        ).createShader(Rect.fromLTWH(0, 0, size.width, size.height)),
    );
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => true;
}
