import 'package:flutter/material.dart';

import 'palette.dart';

/// The single [ThemeData] for SwetSandhya.
///
/// The scanner itself is deliberately dark — the camera feed is the content, so
/// everything around it recedes. The results surface is light, matching the web
/// client's `#f8f8f8` card.
abstract final class AppTheme {
  static ThemeData get light {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: Palette.accent,
      brightness: Brightness.light,
    ).copyWith(
      primary: Palette.accent,
      onPrimary: Palette.white,
      surface: Palette.surface,
      onSurface: Palette.ink,
      outline: Palette.outline,
      shadow: Palette.shadow,
    );

    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: Palette.surface,
      splashFactory: InkSparkle.splashFactory,
      textTheme: _textTheme,
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: Palette.ink,
        contentTextStyle: TextStyle(color: Palette.white, fontSize: 14),
      ),
    );
  }

  /// A restrained scale. The web page leans on size and weight alone rather
  /// than colour to build hierarchy, and that carries over here.
  static const TextTheme _textTheme = TextTheme(
    // The medicine name on a result card.
    headlineMedium: TextStyle(
      fontSize: 28,
      height: 1.15,
      fontWeight: FontWeight.w700,
      color: Palette.accent,
      letterSpacing: -0.4,
    ),
    // Section labels: "Used as", "Warning". Mirrors the web `<dt>`.
    labelLarge: TextStyle(
      fontSize: 12,
      fontWeight: FontWeight.w700,
      color: Palette.inkMuted,
      letterSpacing: 0.8,
    ),
    // Body copy inside result cards.
    bodyMedium: TextStyle(
      fontSize: 15,
      height: 1.45,
      color: Palette.ink,
    ),
    // Supporting text and hints.
    bodySmall: TextStyle(
      fontSize: 13,
      height: 1.4,
      color: Palette.inkMuted,
    ),
  );
}
