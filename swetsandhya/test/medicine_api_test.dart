import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:swetsandhya/src/models/scan_result.dart';
import 'package:swetsandhya/src/services/medicine_api.dart';

/// Builds an API whose transport returns a canned response, and records the
/// request it was given so the multipart shape can be asserted.
({MedicineApi api, List<http.BaseRequest> requests}) apiReturning(
  int statusCode,
  Object body,
) {
  final List<http.BaseRequest> requests = <http.BaseRequest>[];

  final MockClient client = MockClient((http.Request request) async {
    requests.add(request);
    return http.Response(
      body is String ? body : jsonEncode(body),
      statusCode,
      headers: <String, String>{'content-type': 'application/json'},
    );
  });

  return (
    api: MedicineApi(baseUrl: 'http://test.local', client: client),
    requests: requests,
  );
}

final Uint8List kBytes = Uint8List.fromList(<int>[1, 2, 3, 4]);

Future<ScanResult> identify(MedicineApi api, {String name = 'pill.jpg'}) =>
    api.identify(imageBytes: kBytes, fileName: name);

void main() {
  const Map<String, dynamic> successBody = <String, dynamic>{
    'genericName': 'Ibuprofen',
    'purpose': <String>['Pain reliever'],
    'do_not_use': <String>['If allergic to NSAIDs'],
  };

  group('successful scan', () {
    test('returns the parsed medicines', () async {
      final result = apiReturning(200, successBody);

      final ScanResult scan = await identify(result.api);

      expect(scan.medicines, hasLength(1));
      expect(scan.medicines.first.genericName, 'Ibuprofen');
    });

    test('posts to /api/upload', () async {
      final result = apiReturning(200, successBody);

      await identify(result.api);

      expect(result.requests.single.url.path, '/api/upload');
      expect(result.requests.single.method, 'POST');
    });

    test('sends the image under the photo field as multipart', () async {
      final result = apiReturning(200, successBody);

      await identify(result.api);

      final http.Request request = result.requests.single as http.Request;
      expect(
        request.headers['content-type'],
        contains('multipart/form-data'),
      );
      expect(request.body, contains('name="photo"'));
      expect(request.body, contains('filename="pill.jpg"'));
    });

    test('declares the content type from the file extension', () async {
      final result = apiReturning(200, successBody);

      await identify(result.api, name: 'shot.png');

      expect((result.requests.single as http.Request).body,
          contains('image/png'));
    });
  });

  group('error responses', () {
    Future<ScanException> captureError(MedicineApi api) async {
      try {
        await identify(api);
        fail('expected a ScanException');
      } on ScanException catch (error) {
        return error;
      }
    }

    test('422 explains that no medicine was found', () async {
      final result = apiReturning(422, <String, dynamic>{'error': 'none'});

      final ScanException error = await captureError(result.api);

      expect(error.statusCode, 422);
      expect(error.message, contains('No medicine name'));
    });

    test('422 is not offered as retryable', () async {
      final result = apiReturning(422, <String, dynamic>{'error': 'none'});

      // Retrying the identical photo cannot succeed, so the UI must not
      // suggest it.
      expect((await captureError(result.api)).isRetryable, isFalse);
    });

    test('415 explains the file was not a readable image', () async {
      final result = apiReturning(415, <String, dynamic>{'error': 'bad'});

      expect((await captureError(result.api)).message, contains('JPEG'));
    });

    test('500 is retryable', () async {
      final result = apiReturning(500, <String, dynamic>{'error': 'boom'});

      final ScanException error = await captureError(result.api);

      expect(error.isRetryable, isTrue);
      expect(error.message, contains('Try again'));
    });

    test('unknown status falls back to the server detail', () async {
      final result =
          apiReturning(418, <String, dynamic>{'detail': 'I am a teapot'});

      expect((await captureError(result.api)).message, 'I am a teapot');
    });

    test('unknown status with no detail still gives a message', () async {
      final result = apiReturning(418, 'not json at all');

      expect((await captureError(result.api)).message, contains('418'));
    });

    test('200 with no medicines is treated as a failed scan', () async {
      final result = apiReturning(200, <String, dynamic>{
        'genericName': '',
        'purpose': <String>[],
        'do_not_use': <String>[],
      });

      final ScanException error = await captureError(result.api);

      expect(error.isRetryable, isFalse);
      expect(error.message, contains('No medicine name'));
    });

    test('malformed success body does not crash the parser', () async {
      final result = apiReturning(200, 'this is not json');

      await expectLater(identify(result.api), throwsA(isA<ScanException>()));
    });

    test('a transport failure becomes a ScanException', () async {
      final MedicineApi api = MedicineApi(
        baseUrl: 'http://test.local',
        client: MockClient((http.Request request) async {
          throw http.ClientException('connection reset');
        }),
      );

      await expectLater(identify(api), throwsA(isA<ScanException>()));
    });
  });
}
