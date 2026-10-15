import 'dart:io';
import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';

import '../models/scan_result.dart';
import '../services/medicine_api.dart';
import '../theme/palette.dart';
import '../widgets/capture_bar.dart';
import '../widgets/result_sheet.dart';
import '../widgets/viewfinder_overlay.dart';

/// What the screen is doing right now.
enum _Stage { starting, ready, scanning, showingResult, cameraUnavailable }

/// The single screen of the app: aim, capture, read.
///
/// There is no navigation stack and no home screen. Opening the app puts you in
/// the viewfinder, which is the whole point of a scanner.
class ScannerScreen extends StatefulWidget {
  const ScannerScreen({super.key, required this.api});

  final MedicineApi api;

  @override
  State<ScannerScreen> createState() => _ScannerScreenState();
}

class _ScannerScreenState extends State<ScannerScreen>
    with WidgetsBindingObserver {
  CameraController? _controller;
  _Stage _stage = _Stage.starting;
  ScanResult? _result;
  String? _cameraMessage;
  bool _isTorchOn = false;
  bool _isTorchAvailable = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _startCamera();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _controller?.dispose();
    super.dispose();
  }

  /// Releases the camera when the app is backgrounded and reclaims it on
  /// return. Without this the preview comes back frozen on Android.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;

    if (state == AppLifecycleState.inactive) {
      controller.dispose();
      _controller = null;
    } else if (state == AppLifecycleState.resumed) {
      _startCamera();
    }
  }

  Future<void> _startCamera() async {
    try {
      final List<CameraDescription> cameras = await availableCameras();
      if (cameras.isEmpty) {
        _failCamera('No camera was found on this device. You can still choose '
            'a photo from your gallery.');
        return;
      }

      final CameraDescription back = cameras.firstWhere(
        (CameraDescription c) => c.lensDirection == CameraLensDirection.back,
        orElse: () => cameras.first,
      );

      final CameraController controller = CameraController(
        back,
        // High is plenty for OCR on printed text, and starts faster and uses
        // less memory than max.
        ResolutionPreset.high,
        enableAudio: false,
        imageFormatGroup: ImageFormatGroup.jpeg,
      );

      await controller.initialize();
      if (!mounted) {
        await controller.dispose();
        return;
      }

      setState(() {
        _controller = controller;
        _isTorchAvailable = back.lensDirection == CameraLensDirection.back;
        _stage = _Stage.ready;
      });
    } on CameraException catch (error) {
      _failCamera(
        error.code == 'CameraAccessDenied'
            ? 'SwetSandhya needs camera access to scan a package. Turn it on in '
                'Settings, or choose a photo from your gallery instead.'
            : 'The camera could not be started. You can still choose a photo '
                'from your gallery.',
      );
    }
  }

  void _failCamera(String message) {
    if (!mounted) return;
    setState(() {
      _stage = _Stage.cameraUnavailable;
      _cameraMessage = message;
    });
  }

  Future<void> _capture() async {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;

    setState(() => _stage = _Stage.scanning);
    try {
      final XFile photo = await controller.takePicture();
      await _upload(await photo.readAsBytes(), photo.name);
    } on CameraException {
      _showMessage('The photo could not be taken. Try again.');
      if (mounted) setState(() => _stage = _Stage.ready);
    }
  }

  Future<void> _pickFromGallery() async {
    final XFile? photo =
        await ImagePicker().pickImage(source: ImageSource.gallery);
    if (photo == null) return;

    setState(() => _stage = _Stage.scanning);
    await _upload(await photo.readAsBytes(), photo.name);
  }

  Future<void> _upload(Uint8List bytes, String fileName) async {
    try {
      final ScanResult result = await widget.api.identify(
        imageBytes: bytes,
        fileName: fileName,
      );
      if (!mounted) return;

      // A short haptic marks the result without a sound or a banner.
      HapticFeedback.lightImpact();
      setState(() {
        _result = result;
        _stage = _Stage.showingResult;
      });
    } on ScanException catch (error) {
      if (!mounted) return;
      setState(() => _stage = _Stage.ready);
      _showMessage(error.message);
    }
  }

  void _scanAgain() {
    setState(() {
      _result = null;
      _stage = _Stage.ready;
    });
  }

  void _showMessage(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text(message),
          duration: const Duration(seconds: 5),
        ),
      );
  }

  Future<void> _toggleTorch() async {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) return;

    final bool next = !_isTorchOn;
    try {
      await controller.setFlashMode(next ? FlashMode.torch : FlashMode.off);
      setState(() => _isTorchOn = next);
    } on CameraException {
      _showMessage('The light is not available on this camera.');
    }
  }

  bool get _isBusy => _stage == _Stage.scanning;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Palette.ink,
      body: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          _buildPreview(),
          if (_stage != _Stage.cameraUnavailable)
            ViewfinderOverlay(isBusy: _isBusy),
          SafeArea(
            child: Column(
              children: <Widget>[
                const _TitleBar(),
                Expanded(child: _buildGuidance()),
                if (_stage != _Stage.showingResult)
                  CaptureBar(
                    onCapture: _capture,
                    onPickFromGallery: _pickFromGallery,
                    onToggleTorch: _toggleTorch,
                    isTorchOn: _isTorchOn,
                    isBusy: _isBusy,
                    isTorchAvailable: _isTorchAvailable,
                  ),
              ],
            ),
          ),
          if (_stage == _Stage.showingResult && _result != null)
            ResultSheet(result: _result!, onScanAgain: _scanAgain),
        ],
      ),
    );
  }

  Widget _buildPreview() {
    final CameraController? controller = _controller;
    if (controller == null || !controller.value.isInitialized) {
      return const ColoredBox(color: Palette.ink);
    }

    // Cover the screen without distorting the feed.
    return FittedBox(
      fit: BoxFit.cover,
      child: SizedBox(
        width: controller.value.previewSize?.height ?? 1,
        height: controller.value.previewSize?.width ?? 1,
        child: CameraPreview(controller),
      ),
    );
  }

  Widget _buildGuidance() {
    if (_stage == _Stage.showingResult) return const SizedBox.shrink();

    if (_stage == _Stage.cameraUnavailable) {
      return _CentredNotice(
        message: _cameraMessage ?? 'The camera is unavailable.',
        actionLabel: 'Choose a photo',
        onAction: _pickFromGallery,
      );
    }

    final String hint = _isBusy
        ? 'Reading the package…'
        : 'Point at the medicine name';

    return Align(
      alignment: Alignment.bottomCenter,
      child: Padding(
        padding: const EdgeInsets.only(bottom: 20),
        child: AnimatedSwitcher(
          duration: const Duration(milliseconds: 200),
          child: Text(
            hint,
            key: ValueKey<String>(hint),
            style: const TextStyle(
              color: Palette.white,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ),
    );
  }
}

class _TitleBar extends StatelessWidget {
  const _TitleBar();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.fromLTRB(24, 14, 24, 0),
      child: Row(
        children: <Widget>[
          Text(
            'SwetSandhya',
            style: TextStyle(
              color: Palette.white,
              fontSize: 16,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.2,
            ),
          ),
        ],
      ),
    );
  }
}

class _CentredNotice extends StatelessWidget {
  const _CentredNotice({
    required this.message,
    required this.actionLabel,
    required this.onAction,
  });

  final String message;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 36),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Palette.white,
                fontSize: 15,
                height: 1.5,
              ),
            ),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: onAction,
              style: FilledButton.styleFrom(
                backgroundColor: Palette.white,
                foregroundColor: Palette.accent,
                padding:
                    const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
              ),
              child: Text(actionLabel),
            ),
          ],
        ),
      ),
    );
  }
}
