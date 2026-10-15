import 'package:flutter/material.dart';

import '../theme/palette.dart';

/// The bottom control row: choose a photo, capture, toggle the torch.
///
/// Capture is the only emphasised control. The other two are quiet icon
/// buttons, because they are occasional needs and should not compete with the
/// shutter for attention.
class CaptureBar extends StatelessWidget {
  const CaptureBar({
    super.key,
    required this.onCapture,
    required this.onPickFromGallery,
    required this.onToggleTorch,
    this.isTorchOn = false,
    this.isBusy = false,
    this.isTorchAvailable = true,
  });

  final VoidCallback onCapture;
  final VoidCallback onPickFromGallery;
  final VoidCallback onToggleTorch;
  final bool isTorchOn;
  final bool isBusy;
  final bool isTorchAvailable;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 28, left: 24, right: 24),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: <Widget>[
          _SecondaryButton(
            icon: Icons.photo_library_outlined,
            tooltip: 'Choose a photo',
            onPressed: isBusy ? null : onPickFromGallery,
          ),
          _ShutterButton(onPressed: isBusy ? null : onCapture, isBusy: isBusy),
          Opacity(
            opacity: isTorchAvailable ? 1 : 0,
            child: _SecondaryButton(
              icon: isTorchOn ? Icons.flashlight_on : Icons.flashlight_off_outlined,
              tooltip: isTorchOn ? 'Turn off the light' : 'Turn on the light',
              isActive: isTorchOn,
              onPressed: (isBusy || !isTorchAvailable) ? null : onToggleTorch,
            ),
          ),
        ],
      ),
    );
  }
}

class _ShutterButton extends StatelessWidget {
  const _ShutterButton({required this.onPressed, required this.isBusy});

  final VoidCallback? onPressed;
  final bool isBusy;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      button: true,
      label: isBusy ? 'Reading the package' : 'Scan this package',
      child: GestureDetector(
        onTap: onPressed,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          height: 74,
          width: 74,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            // The one place navy appears on the dark scanner: the control the
            // user is meant to reach for.
            color: isBusy ? Colors.transparent : Palette.white,
            border: Border.all(
              color: isBusy ? Palette.accentOnDark : Palette.white,
              width: 3,
            ),
          ),
          child: Center(
            child: isBusy
                ? const SizedBox(
                    height: 26,
                    width: 26,
                    child: CircularProgressIndicator(
                      strokeWidth: 2.5,
                      valueColor:
                          AlwaysStoppedAnimation<Color>(Palette.accentOnDark),
                    ),
                  )
                : Container(
                    height: 56,
                    width: 56,
                    decoration: const BoxDecoration(
                      shape: BoxShape.circle,
                      color: Palette.accent,
                    ),
                  ),
          ),
        ),
      ),
    );
  }
}

class _SecondaryButton extends StatelessWidget {
  const _SecondaryButton({
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.isActive = false,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback? onPressed;
  final bool isActive;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: IconButton(
        onPressed: onPressed,
        iconSize: 24,
        icon: Icon(icon),
        style: IconButton.styleFrom(
          foregroundColor: isActive ? Palette.accentOnDark : Palette.white,
          backgroundColor: Colors.white.withValues(alpha: 0.12),
          padding: const EdgeInsets.all(14),
          disabledForegroundColor: Palette.inkFaint,
        ),
      ),
    );
  }
}
