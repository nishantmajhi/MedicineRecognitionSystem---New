import 'package:flutter/material.dart';

import '../models/scan_result.dart';
import '../theme/palette.dart';

/// The sheet that slides up over the viewfinder with the scan result.
///
/// Section labels mirror the web client's definition list ("Used as",
/// "Warning") so the two front-ends read the same way.
class ResultSheet extends StatelessWidget {
  const ResultSheet({
    super.key,
    required this.result,
    required this.onScanAgain,
  });

  final ScanResult result;
  final VoidCallback onScanAgain;

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.55,
      minChildSize: 0.35,
      maxChildSize: 0.92,
      builder: (BuildContext context, ScrollController controller) {
        return DecoratedBox(
          decoration: const BoxDecoration(
            color: Palette.surface,
            borderRadius: BorderRadius.vertical(
              top: Radius.circular(Palette.radiusLarge),
            ),
            boxShadow: Palette.raisedShadow,
          ),
          child: Column(
            children: <Widget>[
              const _DragHandle(),
              Expanded(
                child: ListView.separated(
                  controller: controller,
                  padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
                  itemCount: result.medicines.length + 1,
                  separatorBuilder: (_, __) => const SizedBox(height: 14),
                  itemBuilder: (BuildContext context, int index) {
                    if (index == result.medicines.length) {
                      return const _SourceNote();
                    }
                    return _MedicineCard(medicine: result.medicines[index]);
                  },
                ),
              ),
              _ScanAgainBar(onScanAgain: onScanAgain),
            ],
          ),
        );
      },
    );
  }
}

class _DragHandle extends StatelessWidget {
  const _DragHandle();

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 10, bottom: 8),
      height: 4,
      width: 44,
      decoration: BoxDecoration(
        color: Palette.inkFaint,
        borderRadius: BorderRadius.circular(2),
      ),
    );
  }
}

class _MedicineCard extends StatelessWidget {
  const _MedicineCard({required this.medicine});

  final Medicine medicine;

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(18, 18, 18, 20),
      decoration: BoxDecoration(
        // `#f8f8f8`, matching the web result card.
        color: Palette.surfaceSunken,
        borderRadius: BorderRadius.circular(Palette.radiusCard),
        boxShadow: Palette.cardShadow,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(medicine.genericName, style: text.headlineMedium),
          if (medicine.hasPurpose) ...<Widget>[
            const SizedBox(height: 18),
            _Section(label: 'USED AS', body: medicine.purpose),
          ],
          if (medicine.hasWarning) ...<Widget>[
            const SizedBox(height: 16),
            _Section(label: 'WARNING', body: medicine.warning),
          ],
          if (!medicine.hasPurpose && !medicine.hasWarning) ...<Widget>[
            const SizedBox(height: 12),
            Text(
              'The label for this medicine had no description attached.',
              style: text.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.label, required this.body});

  final String label;
  final String body;

  @override
  Widget build(BuildContext context) {
    final TextTheme text = Theme.of(context).textTheme;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(label, style: text.labelLarge),
        const SizedBox(height: 6),
        Text(body, style: text.bodyMedium),
      ],
    );
  }
}

/// Where the information came from, and its limits.
///
/// This is a medicines app: being explicit that the data is from drug labels
/// and is not personal advice belongs on the screen, not buried in a menu.
class _SourceNote extends StatelessWidget {
  const _SourceNote();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 16, 4, 0),
      child: Text(
        'Information comes from published drug labels. It describes the '
        'medicine in general and is not advice about your own use of it. '
        'Check the printed leaflet, and ask a pharmacist or doctor before '
        'taking anything.',
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: Palette.inkFaint,
              height: 1.5,
            ),
      ),
    );
  }
}

class _ScanAgainBar extends StatelessWidget {
  const _ScanAgainBar({required this.onScanAgain});

  final VoidCallback onScanAgain;

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
        child: SizedBox(
          width: double.infinity,
          child: FilledButton(
            onPressed: onScanAgain,
            style: FilledButton.styleFrom(
              backgroundColor: Palette.accent,
              foregroundColor: Palette.white,
              padding: const EdgeInsets.symmetric(vertical: 16),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(Palette.radiusCard),
              ),
            ),
            child: const Text(
              'Scan another package',
              style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
            ),
          ),
        ),
      ),
    );
  }
}
