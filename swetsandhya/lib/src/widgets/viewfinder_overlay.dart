import 'package:flutter/material.dart';

import '../theme/palette.dart';

/// The framing window drawn over the camera feed.
///
/// Two jobs: dim everything outside the window so the eye lands on the printed
/// name, and show the user where to put it. Deliberately static — a sweeping
/// "scan line" is the obvious flourish here, and it pulls attention away from
/// the thing the user is trying to aim at, which is the opposite of what a
/// scanner needs.
class ViewfinderOverlay extends StatelessWidget {
  const ViewfinderOverlay({
    super.key,
    this.isBusy = false,
    this.windowAspectRatio = 1.45,
    this.horizontalInset = 28,
  });

  /// Dims the window while a scan is in flight, so the frame reads as "holding"
  /// rather than "ready".
  final bool isBusy;

  /// Width : height of the framing window. Wider than tall, because medicine
  /// names are printed as single long words across a box face.
  final double windowAspectRatio;

  final double horizontalInset;

  /// The framing window in local coordinates, shared by the painter and by
  /// anything that needs to align to it.
  static Rect windowRect(
    Size size, {
    double aspectRatio = 1.45,
    double horizontalInset = 28,
  }) {
    final double width = size.width - horizontalInset * 2;
    final double height = width / aspectRatio;
    // Sits above centre: the lower third of the screen belongs to the controls.
    final double top = (size.height - height) / 2 - size.height * 0.06;
    return Rect.fromLTWH(horizontalInset, top, width, height);
  }

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: CustomPaint(
        size: Size.infinite,
        painter: _ViewfinderPainter(
          isBusy: isBusy,
          aspectRatio: windowAspectRatio,
          horizontalInset: horizontalInset,
        ),
      ),
    );
  }
}

class _ViewfinderPainter extends CustomPainter {
  const _ViewfinderPainter({
    required this.isBusy,
    required this.aspectRatio,
    required this.horizontalInset,
  });

  final bool isBusy;
  final double aspectRatio;
  final double horizontalInset;

  static const double _bracketLength = 26;
  static const double _bracketWidth = 3;

  @override
  void paint(Canvas canvas, Size size) {
    final Rect window = ViewfinderOverlay.windowRect(
      size,
      aspectRatio: aspectRatio,
      horizontalInset: horizontalInset,
    );
    final RRect rounded = RRect.fromRectAndRadius(
      window,
      const Radius.circular(Palette.radiusLarge),
    );

    _paintScrim(canvas, size, rounded);
    _paintBrackets(canvas, window);
  }

  /// Fills the screen with a scrim, then punches the window out of it.
  void _paintScrim(Canvas canvas, Size size, RRect window) {
    final Path scrim = Path.combine(
      PathOperation.difference,
      Path()..addRect(Offset.zero & size),
      Path()..addRRect(window),
    );
    canvas.drawPath(
      scrim,
      Paint()
        ..color = isBusy
            ? Palette.viewfinderScrim.withValues(alpha: 0.86)
            : Palette.viewfinderScrim,
    );
  }

  /// Four corner brackets rather than a full outline: they mark the frame
  /// without drawing a hard box around the label the user is reading.
  void _paintBrackets(Canvas canvas, Rect window) {
    final Paint paint = Paint()
      ..color = isBusy ? Palette.accentOnDark : Palette.white
      ..strokeWidth = _bracketWidth
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    // Inset so the stroke sits inside the rounded window edge.
    final double radius = Palette.radiusLarge;
    final Rect r = window.deflate(_bracketWidth / 2);

    void corner(Offset pivot, double dx, double dy) {
      canvas.drawLine(
        pivot.translate(dx * radius, 0),
        pivot.translate(dx * (radius + _bracketLength), 0),
        paint,
      );
      canvas.drawLine(
        pivot.translate(0, dy * radius),
        pivot.translate(0, dy * (radius + _bracketLength)),
        paint,
      );
    }

    corner(r.topLeft.translate(0, 0), 1, 1);
    corner(r.topRight.translate(0, 0), -1, 1);
    corner(r.bottomLeft.translate(0, 0), 1, -1);
    corner(r.bottomRight.translate(0, 0), -1, -1);
  }

  @override
  bool shouldRepaint(_ViewfinderPainter oldDelegate) =>
      oldDelegate.isBusy != isBusy ||
      oldDelegate.aspectRatio != aspectRatio ||
      oldDelegate.horizontalInset != horizontalInset;
}
