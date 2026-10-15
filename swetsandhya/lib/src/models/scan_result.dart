/// One medicine recognised on a package.
class Medicine {
  const Medicine({
    required this.genericName,
    required this.purpose,
    required this.warning,
  });

  /// Generic name, already title-cased by the backend (e.g. "Ibuprofen").
  final String genericName;

  /// What the medicine is used for. Empty when the label had no purpose field.
  final String purpose;

  /// Who should avoid it. Empty when the label had no warning field.
  final String warning;

  bool get hasPurpose => purpose.trim().isNotEmpty;

  bool get hasWarning => warning.trim().isNotEmpty;

  @override
  String toString() => 'Medicine($genericName)';

  @override
  bool operator ==(Object other) =>
      other is Medicine &&
      other.genericName == genericName &&
      other.purpose == purpose &&
      other.warning == warning;

  @override
  int get hashCode => Object.hash(genericName, purpose, warning);
}

/// Everything recognised in a single photo.
///
/// The backend answers with three parallel fields rather than a list of
/// objects:
///
/// ```json
/// {
///   "genericName": "Ibuprofen, Aspirin",
///   "purpose":     ["Pain reliever", "Fever reducer"],
///   "do_not_use":  ["If allergic to NSAIDs", "Stomach ulcers"]
/// }
/// ```
///
/// [ScanResult.fromJson] zips them back into [Medicine] objects so the UI can
/// render one card per medicine. The shapes are recombined defensively: a short
/// or missing list yields empty strings rather than a range error, because a
/// crash mid-scan is a far worse outcome than a card with one blank section.
class ScanResult {
  const ScanResult({required this.medicines});

  final List<Medicine> medicines;

  bool get isEmpty => medicines.isEmpty;

  factory ScanResult.fromJson(Map<String, dynamic> json) {
    final List<String> names = _splitNames(json['genericName']);
    final List<String> purposes = _stringList(json['purpose']);
    final List<String> warnings = _stringList(json['do_not_use']);

    // Trust whichever field describes the most medicines, so a name list that
    // failed to split still surfaces every purpose the server sent.
    final int count = <int>[
      names.length,
      purposes.length,
      warnings.length,
    ].reduce((int a, int b) => a > b ? a : b);

    final List<Medicine> medicines = <Medicine>[];
    for (int index = 0; index < count; index++) {
      final String name = index < names.length ? names[index] : '';
      final String purpose = index < purposes.length ? purposes[index] : '';
      final String warning = index < warnings.length ? warnings[index] : '';

      // A card with no name and nothing to say is not worth showing.
      if (name.isEmpty && purpose.isEmpty && warning.isEmpty) continue;

      medicines.add(
        Medicine(
          genericName: name.isEmpty ? 'Unnamed medicine' : name,
          purpose: purpose,
          warning: warning,
        ),
      );
    }

    return ScanResult(medicines: medicines);
  }

  /// Splits the comma-joined `genericName` field back into individual names.
  static List<String> _splitNames(Object? value) {
    if (value is! String) return const <String>[];
    return value
        .split(',')
        .map((String part) => part.trim())
        .where((String part) => part.isNotEmpty)
        .toList(growable: false);
  }

  static List<String> _stringList(Object? value) {
    if (value is! List) return const <String>[];
    return value
        .map((Object? item) => item?.toString().trim() ?? '')
        .toList(growable: false);
  }
}
