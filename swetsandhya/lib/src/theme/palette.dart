import 'package:flutter/material.dart';

/// Colour and shape tokens lifted directly from the web client's stylesheets
/// (`static/style.css` and `static/medicine.css`), so the phone app and the
/// browser page read as one product.
///
/// The source palette is almost entirely greyscale with a single accent:
///
/// | CSS                     | Token            |
/// |-------------------------|------------------|
/// | `navy`                  | [accent]         |
/// | `#fafafa`               | [surface]        |
/// | `#f8f8f8`               | [surfaceSunken]  |
/// | `grey`                  | [outline]        |
/// | `#000` / `#555` / `#999`| [ink] / [inkMuted] / [inkFaint] |
/// | `rgba(0, 0, 0, 0.2)`    | [shadow]         |
///
/// One accent against neutrals is what keeps the scanner calm, so resist
/// introducing a second hue: navy should only ever mark the thing the user is
/// meant to act on or read first.
abstract final class Palette {
  /// `navy` — the web page's heading accent, and the only chromatic colour.
  static const Color accent = Color(0xFF000080);

  /// A lifted navy, for text sitting on very dark backgrounds where pure navy
  /// fails contrast against the camera feed.
  static const Color accentOnDark = Color(0xFF9FB2FF);

  /// `#fafafa` — the hover state of the web drop zone.
  static const Color surface = Color(0xFFFAFAFA);

  /// `#f8f8f8` — the web result card background.
  static const Color surfaceSunken = Color(0xFFF8F8F8);

  /// `grey` — the dashed border of the drop zone.
  static const Color outline = Color(0xFF808080);

  /// `#000`, `#555`, `#999` — the three greys of the loading pulse animation,
  /// reused here as a text hierarchy.
  static const Color ink = Color(0xFF000000);
  static const Color inkMuted = Color(0xFF555555);
  static const Color inkFaint = Color(0xFF999999);

  static const Color white = Color(0xFFFFFFFF);

  /// `rgba(0, 0, 0, 0.2)` — the card shadow.
  static const Color shadow = Color(0x33000000);

  /// The scrim over the camera feed outside the framing window.
  static const Color viewfinderScrim = Color(0xB3000000);

  /// `border-radius: 0.8rem` at a 16px root.
  static const double radiusCard = 12.8;

  /// `border-radius: 1rem`.
  static const double radiusLarge = 16.0;

  /// The web card shadow, `0 4px 8px 0 rgba(0, 0, 0, 0.2)`.
  static const List<BoxShadow> cardShadow = <BoxShadow>[
    BoxShadow(color: shadow, offset: Offset(0, 4), blurRadius: 8),
  ];

  /// The web hover shadow, `0 8px 16px 0 rgba(0, 0, 0, 0.2)`.
  static const List<BoxShadow> raisedShadow = <BoxShadow>[
    BoxShadow(color: shadow, offset: Offset(0, 8), blurRadius: 16),
  ];
}
