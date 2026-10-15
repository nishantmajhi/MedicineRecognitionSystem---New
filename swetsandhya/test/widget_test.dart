import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:swetsandhya/src/models/scan_result.dart';
import 'package:swetsandhya/src/theme/app_theme.dart';
import 'package:swetsandhya/src/theme/palette.dart';
import 'package:swetsandhya/src/widgets/result_sheet.dart';
import 'package:swetsandhya/src/widgets/viewfinder_overlay.dart';

Widget wrap(Widget child) => MaterialApp(
      theme: AppTheme.light,
      home: Scaffold(body: Stack(children: <Widget>[child])),
    );

const ScanResult kOneMedicine = ScanResult(
  medicines: <Medicine>[
    Medicine(
      genericName: 'Ibuprofen',
      purpose: 'Pain reliever',
      warning: 'If allergic to NSAIDs',
    ),
  ],
);

void main() {
  group('ResultSheet', () {
    testWidgets('shows the medicine name and both sections', (tester) async {
      await tester.pumpWidget(
        wrap(ResultSheet(result: kOneMedicine, onScanAgain: () {})),
      );

      expect(find.text('Ibuprofen'), findsOneWidget);
      expect(find.text('USED AS'), findsOneWidget);
      expect(find.text('Pain reliever'), findsOneWidget);
      expect(find.text('WARNING'), findsOneWidget);
      expect(find.text('If allergic to NSAIDs'), findsOneWidget);
    });

    testWidgets('renders one card per medicine', (tester) async {
      await tester.pumpWidget(
        wrap(
          ResultSheet(
            result: const ScanResult(
              medicines: <Medicine>[
                Medicine(genericName: 'Ibuprofen', purpose: 'a', warning: 'b'),
                Medicine(genericName: 'Aspirin', purpose: 'c', warning: 'd'),
              ],
            ),
            onScanAgain: () {},
          ),
        ),
      );

      expect(find.text('Ibuprofen'), findsOneWidget);
      expect(find.text('Aspirin'), findsOneWidget);
    });

    testWidgets('omits empty sections instead of showing blank labels',
        (tester) async {
      await tester.pumpWidget(
        wrap(
          ResultSheet(
            result: const ScanResult(
              medicines: <Medicine>[
                Medicine(
                  genericName: 'Ibuprofen',
                  purpose: 'Pain reliever',
                  warning: '',
                ),
              ],
            ),
            onScanAgain: () {},
          ),
        ),
      );

      expect(find.text('USED AS'), findsOneWidget);
      expect(find.text('WARNING'), findsNothing);
    });

    testWidgets('explains when a medicine has no description', (tester) async {
      await tester.pumpWidget(
        wrap(
          ResultSheet(
            result: const ScanResult(
              medicines: <Medicine>[
                Medicine(genericName: 'Ibuprofen', purpose: '', warning: ''),
              ],
            ),
            onScanAgain: () {},
          ),
        ),
      );

      expect(
        find.textContaining('no description attached'),
        findsOneWidget,
      );
    });

    testWidgets('always shows the source and safety note', (tester) async {
      await tester.pumpWidget(
        wrap(ResultSheet(result: kOneMedicine, onScanAgain: () {})),
      );

      expect(find.textContaining('ask a pharmacist or doctor'), findsOneWidget);
    });

    testWidgets('scan again button fires its callback', (tester) async {
      int taps = 0;
      await tester.pumpWidget(
        wrap(ResultSheet(result: kOneMedicine, onScanAgain: () => taps++)),
      );

      await tester.tap(find.text('Scan another package'));
      await tester.pump();

      expect(taps, 1);
    });
  });

  group('ViewfinderOverlay', () {
    testWidgets('does not intercept touches meant for the controls',
        (tester) async {
      await tester.pumpWidget(wrap(const ViewfinderOverlay()));
      expect(find.byType(IgnorePointer), findsOneWidget);
    });

    test('framing window sits inside the screen bounds', () {
      const Size screen = Size(390, 844);
      final Rect window = ViewfinderOverlay.windowRect(screen);

      expect(window.left, greaterThanOrEqualTo(0));
      expect(window.right, lessThanOrEqualTo(screen.width));
      expect(window.top, greaterThan(0));
      expect(window.bottom, lessThan(screen.height));
    });

    test('framing window is wider than tall, matching printed names', () {
      final Rect window = ViewfinderOverlay.windowRect(const Size(390, 844));
      expect(window.width, greaterThan(window.height));
    });
  });

  group('Palette', () {
    test('accent is the navy from the web stylesheet', () {
      // CSS `navy` is #000080.
      expect(Palette.accent, const Color(0xFF000080));
    });

    test('surfaces match the web stylesheet greys', () {
      expect(Palette.surface, const Color(0xFFFAFAFA));
      expect(Palette.surfaceSunken, const Color(0xFFF8F8F8));
    });

    test('theme uses the palette accent as its primary', () {
      expect(AppTheme.light.colorScheme.primary, Palette.accent);
    });
  });
}
