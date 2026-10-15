import 'package:flutter_test/flutter_test.dart';
import 'package:swetsandhya/src/models/scan_result.dart';

void main() {
  group('ScanResult.fromJson', () {
    test('parses a single medicine', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': 'Ibuprofen',
        'purpose': <String>['Pain reliever'],
        'do_not_use': <String>['If allergic to NSAIDs'],
      });

      expect(result.medicines, hasLength(1));
      expect(result.medicines.first.genericName, 'Ibuprofen');
      expect(result.medicines.first.purpose, 'Pain reliever');
      expect(result.medicines.first.warning, 'If allergic to NSAIDs');
    });

    test('splits the comma-joined name field into separate medicines', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': 'Ibuprofen, Aspirin',
        'purpose': <String>['Pain reliever', 'Fever reducer'],
        'do_not_use': <String>['NSAID allergy', 'Stomach ulcers'],
      });

      expect(result.medicines, hasLength(2));
      expect(result.medicines[0].genericName, 'Ibuprofen');
      expect(result.medicines[1].genericName, 'Aspirin');
      expect(result.medicines[1].purpose, 'Fever reducer');
    });

    test('trims whitespace around names', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': '  Ibuprofen ,  Aspirin  ',
        'purpose': <String>['a', 'b'],
        'do_not_use': <String>['c', 'd'],
      });

      expect(result.medicines[0].genericName, 'Ibuprofen');
      expect(result.medicines[1].genericName, 'Aspirin');
    });

    test('empty payload yields no medicines', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': '',
        'purpose': <String>[],
        'do_not_use': <String>[],
      });

      expect(result.isEmpty, isTrue);
    });

    test('missing keys do not throw', () {
      expect(ScanResult.fromJson(<String, dynamic>{}).isEmpty, isTrue);
    });

    test('wrong types are ignored rather than crashing', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': 42,
        'purpose': 'not a list',
        'do_not_use': null,
      });

      expect(result.isEmpty, isTrue);
    });

    group('mismatched list lengths', () {
      test('fewer purposes than names leaves the section blank', () {
        final ScanResult result = ScanResult.fromJson(<String, dynamic>{
          'genericName': 'Ibuprofen, Aspirin',
          'purpose': <String>['Pain reliever'],
          'do_not_use': <String>[],
        });

        expect(result.medicines, hasLength(2));
        expect(result.medicines[1].purpose, isEmpty);
        expect(result.medicines[1].hasPurpose, isFalse);
      });

      test('more purposes than names still surfaces every purpose', () {
        final ScanResult result = ScanResult.fromJson(<String, dynamic>{
          'genericName': 'Ibuprofen',
          'purpose': <String>['Pain reliever', 'Fever reducer'],
          'do_not_use': <String>[],
        });

        expect(result.medicines, hasLength(2));
        expect(result.medicines[1].genericName, 'Unnamed medicine');
        expect(result.medicines[1].purpose, 'Fever reducer');
      });

      test('entries with nothing at all are dropped', () {
        final ScanResult result = ScanResult.fromJson(<String, dynamic>{
          'genericName': 'Ibuprofen',
          'purpose': <String>['Pain reliever', '', ''],
          'do_not_use': <String>['', '', ''],
        });

        expect(result.medicines, hasLength(1));
      });
    });

    test('null items inside lists become empty strings', () {
      final ScanResult result = ScanResult.fromJson(<String, dynamic>{
        'genericName': 'Ibuprofen',
        'purpose': <dynamic>[null],
        'do_not_use': <dynamic>['Ulcers'],
      });

      expect(result.medicines.first.hasPurpose, isFalse);
      expect(result.medicines.first.warning, 'Ulcers');
    });
  });

  group('Medicine', () {
    test('reports empty sections', () {
      const Medicine medicine =
          Medicine(genericName: 'Ibuprofen', purpose: '   ', warning: '');

      expect(medicine.hasPurpose, isFalse);
      expect(medicine.hasWarning, isFalse);
    });

    test('value equality', () {
      const Medicine a =
          Medicine(genericName: 'Ibuprofen', purpose: 'p', warning: 'w');
      const Medicine b =
          Medicine(genericName: 'Ibuprofen', purpose: 'p', warning: 'w');

      expect(a, equals(b));
      expect(a.hashCode, equals(b.hashCode));
    });
  });
}
