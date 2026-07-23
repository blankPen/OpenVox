import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:livekit_components/livekit_components.dart' as components;
import 'package:provider/provider.dart';

import 'controllers/app_ctrl.dart';
import 'screens/agent_screen.dart';
import 'screens/welcome_screen.dart';
import 'ui/vox_colors.dart';
import 'widgets/app_layout_switcher.dart';
import 'widgets/session_error_banner.dart';
import 'widgets/vox_background.dart';

/// Design spec viewport — matches the HTML `.screen` (390 × 844).
const double kDesignWidth = 390;
const double kDesignHeight = 844;

final appCtrl = AppCtrl();

/// Global theme controller.
class AppTheme extends ChangeNotifier {
  static final AppTheme _instance = AppTheme._();
  AppTheme._();

  bool isDark = false;

  static void toggle(BuildContext context) {
    _instance.isDark = !_instance.isDark;
    _instance.notifyListeners();
  }

  static AppTheme of(BuildContext context) => _instance;

  /// Read the current dark-mode state. Exposed for e2e tests so they can
  /// verify theme toggles without sniffing widget colors.
  static bool get isDarkMode => _instance.isDark;
}

class VoiceAssistantApp extends StatefulWidget {
  const VoiceAssistantApp({super.key});

  @override
  State<VoiceAssistantApp> createState() => _AppState();
}

class _AppState extends State<VoiceAssistantApp> {
  @override
  void initState() {
    super.initState();
    AppTheme._instance.addListener(_onThemeChanged);
  }

  @override
  void dispose() {
    AppTheme._instance.removeListener(_onThemeChanged);
    super.dispose();
  }

  void _onThemeChanged() {
    setState(() {});
  }

  ThemeData buildTheme({required bool isLight}) {
    return ThemeData(
      useMaterial3: true,
      brightness: isLight ? Brightness.light : Brightness.dark,
      scaffoldBackgroundColor: Colors.transparent,
      cardColor: isLight ? VoxColors.lightSurface : VoxColors.darkSurface,
      canvasColor: isLight ? VoxColors.lightSurface : VoxColors.darkSurface,
      inputDecorationTheme: InputDecorationTheme(
        fillColor: isLight ? VoxColors.lightSurface2 : VoxColors.darkSurface2,
        hintStyle: TextStyle(
          color: isLight ? VoxColors.lightFg3 : VoxColors.darkFg3,
          fontSize: 14.5,
        ),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(22),
          borderSide: BorderSide(
            color: isLight ? VoxColors.lightBorder : VoxColors.darkBorder,
          ),
        ),
      ),
      colorScheme: ColorScheme(
        brightness: isLight ? Brightness.light : Brightness.dark,
        primary: isLight ? VoxColors.lightAccent : VoxColors.darkAccent,
        onPrimary: VoxColors.lightFgOnAccent,
        secondary: isLight ? VoxColors.lightAccent2 : VoxColors.darkAccent2,
        onSecondary: VoxColors.lightFgOnAccent,
        surface: isLight ? VoxColors.lightSurface : VoxColors.darkSurface,
        onSurface: isLight ? VoxColors.lightFg : VoxColors.darkFg,
        error: VoxColors.lightDanger,
        onError: VoxColors.lightFgOnAccent,
      ),
      textTheme: GoogleFonts.interTextTheme(
        const TextTheme(
          bodyMedium: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w400,
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext ctx) {
    final isDark = AppTheme._instance.isDark;

    return MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: appCtrl),
        ChangeNotifierProvider.value(value: appCtrl.session),
        ChangeNotifierProvider.value(value: appCtrl.roomContext),
      ],
      child: components.SessionContext(
        session: appCtrl.session,
        child: MaterialApp(
          title: 'Vox',
          debugShowCheckedModeBanner: false,
          theme: buildTheme(isLight: true),
          darkTheme: buildTheme(isLight: false),
          themeMode: isDark ? ThemeMode.dark : ThemeMode.light,
          home: Builder(
            builder: (ctx) => VoxBackground(
              child: Stack(
                children: [
                  Selector<AppCtrl, AppScreenState>(
                    selector: (ctx, appCtx) => appCtx.appScreenState,
                    builder: (ctx, screen, _) => AppLayoutSwitcher(
                      frontBuilder: (ctx) => const WelcomeScreen(),
                      backBuilder: (ctx) => const AgentScreen(),
                      isFront: screen == AppScreenState.welcome,
                    ),
                  ),
                  const SessionErrorBanner(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
