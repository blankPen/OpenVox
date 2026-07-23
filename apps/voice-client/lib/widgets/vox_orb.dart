import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../ui/vox_colors.dart';

/// Orb state — controls animation speed and visual intensity.
enum VoxOrbState {
  idle,
  listening,
  thinking,
  speaking,
}

/// Vox Orb — a sunset-gradient animated sphere with halo, rings, and sparks.
class VoxOrb extends StatefulWidget {
  final double size;
  final VoxOrbState state;
  final bool compact;
  final bool collapsed;
  final bool showRings;

  const VoxOrb({
    super.key,
    this.size = 180,
    this.state = VoxOrbState.idle,
    this.compact = false,
    this.collapsed = false,
    this.showRings = true,
  });

  @override
  State<VoxOrb> createState() => _VoxOrbState();
}

class _VoxOrbState extends State<VoxOrb> with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  double get _breatheDuration {
    switch (widget.state) {
      case VoxOrbState.listening:
        return 2.4;
      case VoxOrbState.thinking:
        return 2.6;
      case VoxOrbState.speaking:
        return 1.1;
      case VoxOrbState.idle:
        return 4.6;
    }
  }

  double get _ringDuration => widget.state == VoxOrbState.speaking ? 1.4 : 3.8;

  double get _ringMaxScale => widget.state == VoxOrbState.speaking ? 1.6 : 1.55;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: Duration(milliseconds: (_breatheDuration * 1000).round()),
    )..repeat(); // ignore: discarded_futures
  }

  @override
  void didUpdateWidget(VoxOrb oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.state != widget.state) {
      _ctrl.duration = Duration(milliseconds: (_breatheDuration * 1000).round());
    }
  }

  @override
  void dispose() {
    _ctrl.dispose();
    super.dispose();
  }

  // Spark positions — "right N%" → fractional left position
  static const _sparks = <_SparkDef>[
    _SparkDef(0.50, 0.06, 0.0),
    _SparkDef(0.96, 0.50, 1.6),
    _SparkDef(0.22, 0.78, 3.0),
    _SparkDef(0.06, 0.28, 4.4),
    _SparkDef(0.82, 0.16, 2.2),
    _SparkDef(0.70, 0.68, 5.4),
    _SparkDef(0.92, 0.38, 3.7),
    _SparkDef(0.46, 0.88, 6.1),
  ];

  @override
  Widget build(BuildContext context) {
    final displaySize = widget.collapsed ? 72.0 : widget.size;
    final coreRatio = widget.collapsed ? 50.0 / 72.0 : (widget.compact ? 0.667 : 0.7);
    final coreSize = displaySize * coreRatio;
    final showRingsAndSparks = !widget.collapsed;
    final isDark = Theme.of(context).brightness == Brightness.dark;

    return AnimatedBuilder(
      animation: _ctrl,
      builder: (context, _) {
        final breathe = _breatheValue;
        final ringValue = _ringValue;

        return SizedBox(
          width: displaySize,
          height: displaySize,
          child: Stack(
            children: [
              // Halo
              if (showRingsAndSparks)
              Positioned.fill(
                child: Center(
                  child: Container(
                    width: displaySize + 20,
                    height: displaySize + 20,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      gradient: RadialGradient(
                        colors: [
                          VoxColors.orbOrange.withValues(alpha: isDark ? 0.32 : 0.30),
                          VoxColors.orbMagenta.withValues(alpha: isDark ? 0.18 : 0.16),
                          Colors.transparent,
                        ],
                        stops: const [0.0, 0.35, 0.7],
                      ),
                    ),
                    transform: Matrix4.diagonal3Values(breathe, breathe, 1),
                    transformAlignment: Alignment.center,
                  ),
                ),
              ),

              // Rings — opt-in. The welcome screen design has no visible
              // ring lines, so callers can pass showRings: false.
              if (widget.showRings && showRingsAndSparks) ...[
              _OrbRing(
                size: displaySize,
                value: ringValue,
                delay: 0,
                maxScale: _ringMaxScale,
                breathe: breathe,
                color: VoxColors.orbOrange.withValues(alpha: widget.state == VoxOrbState.speaking ? 1.0 : 0.4),
                strokeWidth: widget.state == VoxOrbState.speaking ? 2.0 : 1.5,
              ),
              _OrbRing(
                size: displaySize,
                value: ringValue,
                delay: 1.3,
                maxScale: _ringMaxScale,
                breathe: breathe,
                color: VoxColors.orbMagenta.withValues(alpha: 0.36),
                strokeWidth: 1.5,
              ),
              _OrbRing(
                size: displaySize,
                value: ringValue,
                delay: 2.6,
                maxScale: _ringMaxScale,
                breathe: breathe,
                color: VoxColors.orbPurple.withValues(alpha: 0.36),
                strokeWidth: 1.5,
              ),
              ],

              // Core
              Center(
                child: Container(
                  width: coreSize,
                  height: coreSize,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    gradient: const RadialGradient(
                      center: Alignment(-0.36, -0.44),
                      radius: 1.0,
                      colors: [
                        VoxColors.orbWhite,
                        VoxColors.orbPeach,
                        VoxColors.orbOrangeLight,
                        VoxColors.orbOrange,
                        VoxColors.orbMagenta,
                        VoxColors.orbPurple,
                      ],
                      stops: [0.0, 0.16, 0.36, 0.58, 0.80, 1.0],
                    ),
                    boxShadow: [
                      const BoxShadow(
                        color: Color(0x8CFFFFFF),
                        blurRadius: 28,
                      ),
                      BoxShadow(
                        color: Colors.black.withValues(alpha: 0.18),
                        blurRadius: 28,
                        offset: const Offset(0, 16),
                      ),
                      BoxShadow(
                        color: VoxColors.orbOrange.withValues(alpha: 0.32),
                        blurRadius: 36,
                        offset: const Offset(0, 10),
                      ),
                      BoxShadow(
                        // Magenta glow — the design wants a warm halo, so
                        // keep this faint. Bigger alpha washes the orb out
                        // toward purple and reads as a separate ring layer.
                        color: VoxColors.orbMagenta.withValues(alpha: 0.14),
                        blurRadius: 60,
                      ),
                    ],
                  ),
                  transform: Matrix4.diagonal3Values(
                    widget.state == VoxOrbState.thinking
                        ? 1.0 + (_ringValue * 0.04)
                        : breathe,
                    widget.state == VoxOrbState.thinking
                        ? 1.0 + (_ringValue * 0.04)
                        : breathe,
                    1,
                  ),
                  transformAlignment: Alignment.center,
                ),
              ),

              // Sparks (hidden when collapsed)
              if (showRingsAndSparks)
                ..._sparks.map((s) => _OrbSpark(
                      position: Offset(s.left, s.top),
                      delay: s.delay,
                      totalSize: displaySize,
                    )),
            ],
          ),
        );
      },
    );
  }

  double get _breatheValue {
    final phase = _ctrl.value * 2 * math.pi;
    return 1.0 + 0.07 * (0.5 + 0.5 * math.sin(phase - math.pi / 2));
  }

  double get _ringValue {
    final ringSpeed = _breatheDuration / _ringDuration;
    final phase = (_ctrl.value * ringSpeed) % 1.0;
    return phase;
  }
}

class _SparkDef {
  final double left;
  final double top;
  final double delay;
  const _SparkDef(this.left, this.top, this.delay);
}

// ═══════════════════════════════════════
//  Orb Ring
// ═══════════════════════════════════════

class _OrbRing extends StatelessWidget {
  final double size;
  final double value;
  final double delay;
  final double maxScale;
  final double breathe;
  final Color color;
  final double strokeWidth;

  const _OrbRing({
    required this.size,
    required this.value,
    required this.delay,
    required this.maxScale,
    required this.breathe,
    required this.color,
    required this.strokeWidth,
  });

  @override
  Widget build(BuildContext context) {
    final delayFraction = delay / 3.8;
    final phase = (value - delayFraction) % 1.0;
    final scale = 0.55 + (maxScale - 0.55) * phase;
    final opacity = 0.75 * (1.0 - phase);

    return Positioned.fill(
      child: Transform.scale(
        scale: scale,
        child: Opacity(
          opacity: opacity.clamp(0.0, 1.0),
          child: Container(
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(
                color: color.withValues(alpha: opacity.clamp(0.0, 1.0)),
                width: strokeWidth,
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ═══════════════════════════════════════
//  Orb Spark
// ═══════════════════════════════════════

class _OrbSpark extends StatefulWidget {
  final Offset position;
  final double delay;
  final double totalSize;

  const _OrbSpark({
    required this.position,
    required this.delay,
    required this.totalSize,
  });

  @override
  State<_OrbSpark> createState() => _OrbSparkState();
}

class _OrbSparkState extends State<_OrbSpark>
    with SingleTickerProviderStateMixin {
  late final AnimationController _ctrl;

  @override
  void initState() {
    super.initState();
    _ctrl = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 7),
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
        final rawPhase = (_ctrl.value - widget.delay / 7.0) % 1.0;
        final positionalPhase = rawPhase.clamp(0.0, 0.08) / 0.08;
        final sparkScale = 0.4 + 0.6 * positionalPhase;
        double sparkOpacity;
        if (rawPhase < 0.08) {
          sparkOpacity = rawPhase / 0.08;
        } else if (rawPhase < 0.6) {
          sparkOpacity = 1.0;
        } else {
          sparkOpacity = 1.0 - (rawPhase - 0.6) / 0.4;
        }
        final driftY = rawPhase > 0.6 ? -10.0 * ((rawPhase - 0.6) / 0.4) : 0.0;

        if (sparkOpacity <= 0) return const SizedBox.shrink();

        return Positioned(
          left: widget.position.dx * widget.totalSize - 2.5,
          top: widget.position.dy * widget.totalSize - 2.5 + driftY,
          child: Opacity(
            opacity: sparkOpacity.clamp(0.0, 1.0),
            child: Transform.scale(
              scale: sparkScale.clamp(0.0, 1.0),
              child: Container(
                width: 5,
                height: 5,
                decoration: const BoxDecoration(
                  shape: BoxShape.circle,
                  color: VoxColors.orbPeach,
                  boxShadow: [
                    BoxShadow(
                      color: Color(0x80FF9B7A),
                      blurRadius: 8,
                    ),
                    BoxShadow(
                      color: Color(0x4DFF6B4A),
                      blurRadius: 16,
                    ),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
