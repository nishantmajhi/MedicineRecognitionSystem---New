import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'src/screens/scanner_screen.dart';
import 'src/services/medicine_api.dart';
import 'src/theme/app_theme.dart';

/// Backend address, overridable without editing code:
///
/// ```
/// flutter run --dart-define=MRS_BASE_URL=http://192.168.1.5:8000
/// ```
///
/// The default targets the Android emulator, where `10.0.2.2` is the host
/// machine. See README.md for iOS simulator and physical-device addresses.
const String kBaseUrl = String.fromEnvironment(
  'MRS_BASE_URL',
  defaultValue: 'http://10.0.2.2:8000',
);

void main() {
  WidgetsFlutterBinding.ensureInitialized();

  // The scanner is a dark, full-bleed screen; light status-bar icons sit on it.
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
      statusBarBrightness: Brightness.dark,
    ),
  );

  runApp(const SwetSandhyaApp());
}

class SwetSandhyaApp extends StatefulWidget {
  const SwetSandhyaApp({super.key});

  @override
  State<SwetSandhyaApp> createState() => _SwetSandhyaAppState();
}

class _SwetSandhyaAppState extends State<SwetSandhyaApp> {
  late final MedicineApi _api = MedicineApi(baseUrl: kBaseUrl);

  @override
  void dispose() {
    _api.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'SwetSandhya',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light,
      home: ScannerScreen(api: _api),
    );
  }
}
