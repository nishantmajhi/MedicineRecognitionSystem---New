# SwetSandhya

The mobile client for the Medicine Recognition System. Point the camera at a
medicine package; the photo goes to the MRS backend, which runs OCR, matches the
text against the NDC generic-name list, and returns what the medicine is used
for and who should avoid it.

## Design

Everything on screen is derived from the web client's stylesheets, so the phone
app and the browser page read as one product. The palette is defined once in
`lib/src/theme/palette.dart`:

| CSS (`static/*.css`)     | Token                             | Value     |
|--------------------------|-----------------------------------|-----------|
| `navy`                   | `Palette.accent`                  | `#000080` |
| `#fafafa`                | `Palette.surface`                 | `#FAFAFA` |
| `#f8f8f8`                | `Palette.surfaceSunken`           | `#F8F8F8` |
| `grey`                   | `Palette.outline`                 | `#808080` |
| `#000` / `#555` / `#999` | `ink` / `inkMuted` / `inkFaint`   | text ramp |
| `rgba(0, 0, 0, 0.2)`     | `Palette.shadow`                  | card shadow |
| `0.8rem` / `1rem`        | `radiusCard` / `radiusLarge`      | 12.8 / 16 |

The source palette is greyscale with exactly one accent, which is what makes a
non-distracting scanner straightforward: navy marks only the control you should
reach for (the shutter) and the thing you should read first (the medicine name).
Adding a second hue would undo that, so don't.

Two deliberate omissions, both in service of "non-distracting":

- **No animated scan line.** It's the obvious flourish for a scanner, and it
  pulls the eye away from the label you're trying to aim at.
- **No home screen.** Launching the app puts you straight in the viewfinder.

The framing window uses corner brackets rather than a full outline, so nothing
is drawn around the text you're reading, and it is wider than tall because
generic names are printed as long single words across a box face.

## Setup

This repository holds the Dart sources, the lint config, and the platform
permission files. The generated platform scaffolding is not checked in.

```bash
cd swetsandhya

# Generate the android/ and ios/ scaffolding around the existing sources.
# Existing files are preserved.
flutter create --org np.edu.mrs --project-name swetsandhya .

flutter pub get
```

Then apply the two permission edits:

- **Android** — `android/app/src/main/AndroidManifest.xml` in this repo already
  has `CAMERA`, `INTERNET`, and the cleartext-HTTP config. If `flutter create`
  overwrote it, re-apply those entries.
- **iOS** — merge the keys from `ios/Runner/Info.plist.additions` into
  `ios/Runner/Info.plist`. iOS shows those strings verbatim in the permission
  dialog.

## Running

Start the backend first:

```bash
cd ..            # the MedicineRecognitionSystem root
python main.py   # serves on :8000
```

Then point the app at it. The default is the Android emulator's host address:

| Where the app runs | `MRS_BASE_URL`                        |
|--------------------|---------------------------------------|
| Android emulator   | `http://10.0.2.2:8000` *(default)*    |
| iOS simulator      | `http://localhost:8000`               |
| Physical phone     | `http://<your-computer-LAN-IP>:8000`  |

```bash
flutter run --dart-define=MRS_BASE_URL=http://192.168.1.5:8000
```

For a physical phone, the phone and the computer must be on the same network,
and the backend must bind `0.0.0.0` (it already does).

## Tests

```bash
flutter test
```

Covers response parsing (`test/scan_result_test.dart`), the HTTP client and its
error mapping against a `MockClient` (`test/medicine_api_test.dart`), and the
result sheet, viewfinder geometry and palette tokens (`test/widget_test.dart`).

`ScannerScreen` is not covered: it depends on the `camera` plugin's platform
channels, which need an integration test on a real device rather than a widget
test. The logic worth testing was kept out of it deliberately.

## Structure

```
lib/
  main.dart                      entry point, backend URL, theme installation
  src/
    models/scan_result.dart      parses the backend's parallel-array response
    services/medicine_api.dart   multipart upload, status-to-message mapping
    screens/scanner_screen.dart  camera lifecycle and scan flow
    widgets/
      viewfinder_overlay.dart    scrim and corner brackets (CustomPainter)
      capture_bar.dart           gallery, shutter, torch
      result_sheet.dart          draggable sheet of medicine cards
    theme/
      palette.dart               tokens lifted from the web CSS
      app_theme.dart             ThemeData built on the palette
```

### A note on the response shape

The backend answers with three parallel fields rather than a list of objects:

```json
{
  "genericName": "Ibuprofen, Aspirin",
  "purpose":     ["Pain reliever", "Fever reducer"],
  "do_not_use":  ["If allergic to NSAIDs", "Stomach ulcers"]
}
```

`ScanResult.fromJson` zips them back into one card per medicine. It does so
defensively — a short, missing or wrongly-typed list yields blank sections
rather than a range error, because crashing mid-scan is a much worse outcome
than a card with one empty field.
