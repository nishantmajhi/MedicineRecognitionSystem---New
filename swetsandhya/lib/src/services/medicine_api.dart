import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';

import '../models/scan_result.dart';

/// A scan that did not produce a result.
///
/// [message] is written for the person holding the phone: it says what happened
/// and what to do next, never what the HTTP layer called it.
class ScanException implements Exception {
  const ScanException(this.message, {this.statusCode, this.isRetryable = true});

  final String message;
  final int? statusCode;

  /// Whether trying the same photo again could plausibly work. A 422 ("no
  /// medicine name found") is not retryable — the user needs a better photo,
  /// and offering "Try again" on the same image just wastes their time.
  final bool isRetryable;

  @override
  String toString() => 'ScanException($statusCode): $message';
}

/// Talks to the Medicine Recognition System FastAPI backend.
class MedicineApi {
  MedicineApi({required this.baseUrl, http.Client? client})
      : _client = client ?? http.Client();

  /// Root of the backend, without a trailing slash.
  ///
  /// On the Android emulator `localhost` belongs to the device, not your
  /// machine — use `http://10.0.2.2:8000`. On a physical phone use the LAN
  /// address of the computer running `main.py`.
  final String baseUrl;

  final http.Client _client;

  static const Duration _timeout = Duration(seconds: 45);

  /// Uploads one photo and returns the medicines recognised in it.
  ///
  /// Takes bytes rather than a path so the same call works for a camera
  /// capture, a gallery pick, and a test with no file system.
  ///
  /// Throws [ScanException] for every failure, so callers have exactly one
  /// error type to handle.
  Future<ScanResult> identify({
    required Uint8List imageBytes,
    required String fileName,
  }) async {
    final Uri endpoint = Uri.parse('$baseUrl/api/upload');

    late final http.Response response;
    try {
      final http.MultipartRequest request =
          http.MultipartRequest('POST', endpoint)
            ..files.add(
              http.MultipartFile.fromBytes(
                // The field name the backend expects.
                'photo',
                imageBytes,
                filename: fileName,
                contentType: _mediaTypeFor(fileName),
              ),
            );

      final http.StreamedResponse streamed =
          await _client.send(request).timeout(_timeout);
      response = await http.Response.fromStream(streamed);
    } on SocketException {
      throw const ScanException(
        "Can't reach the server. Check that it's running and that this phone "
        'is on the same network.',
      );
    } on http.ClientException {
      throw const ScanException(
        'The connection dropped mid-upload. Try again.',
      );
    } catch (_) {
      throw const ScanException(
        'The server took too long to answer. Try again.',
      );
    }

    return _handleResponse(response);
  }

  ScanResult _handleResponse(http.Response response) {
    if (response.statusCode == 200) {
      final Map<String, dynamic> body = _decode(response.body);
      final ScanResult result = ScanResult.fromJson(body);

      if (result.isEmpty) {
        throw const ScanException(
          'No medicine name was found on this package. Try filling more of the '
          'frame with the printed name.',
          statusCode: 200,
          isRetryable: false,
        );
      }
      return result;
    }

    throw ScanException(
      _messageForStatus(response.statusCode, response.body),
      statusCode: response.statusCode,
      // A rejected photo needs a different photo, not a repeat of this one.
      isRetryable: response.statusCode >= 500,
    );
  }

  String _messageForStatus(int statusCode, String body) {
    switch (statusCode) {
      case 406:
        return 'The photo did not reach the server. Take it again.';
      case 415:
        return "That file isn't an image the server can read. Use a JPEG or PNG.";
      case 422:
        return 'No medicine name was found on this package. Move closer and '
            'keep the printed name inside the frame.';
      case 500:
        return 'The server hit an error reading this image. Try again in a moment.';
      default:
        final Map<String, dynamic> decoded = _decode(body);
        final Object? detail = decoded['error'] ?? decoded['detail'];
        if (detail is String && detail.isNotEmpty) return detail;
        return 'The server answered with an unexpected error ($statusCode).';
    }
  }

  Map<String, dynamic> _decode(String body) {
    try {
      final Object? decoded = jsonDecode(body);
      return decoded is Map<String, dynamic> ? decoded : <String, dynamic>{};
    } on FormatException {
      return <String, dynamic>{};
    }
  }

  MediaType _mediaTypeFor(String fileName) {
    final String name = fileName.toLowerCase();
    if (name.endsWith('.png')) return MediaType('image', 'png');
    if (name.endsWith('.webp')) return MediaType('image', 'webp');
    return MediaType('image', 'jpeg');
  }

  void dispose() => _client.close();
}
