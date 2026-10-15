"""
Tests for utils.image_processor.

EasyOCR is never invoked. The reader is lazily constructed behind a property, so
each test injects a fake whose ``readtext`` returns canned detections in
EasyOCR's shape: ``(bounding_box, text, confidence)``.
"""

import json
import numpy as np
import pytest

from utils.image_processor import ImageProcessor

BOX = [[0, 0], [10, 0], [10, 10], [0, 10]]


def detection(text, confidence):
    """One EasyOCR-shaped detection tuple."""
    return (BOX, text, confidence)


class FakeReader:
    """Stands in for easyocr.Reader, recording how often OCR actually ran."""

    def __init__(self, result):
        self.result = result
        self.call_count = 0

    def readtext(self, image):
        self.call_count += 1
        return self.result


@pytest.fixture
def processor(tmp_path):
    return ImageProcessor(
        media_dir=str(tmp_path / "media"), cache_dir=str(tmp_path / "cache")
    )


class TestInitialisation:
    def test_creates_directories(self, tmp_path):
        processor = ImageProcessor(
            media_dir=str(tmp_path / "media"), cache_dir=str(tmp_path / "cache")
        )
        assert processor.media_dir.is_dir()
        assert processor.cache_dir.is_dir()

    def test_default_thresholds(self, processor):
        assert processor.min_confidence == 25.00
        assert processor.min_text_length == 3

    def test_reader_is_not_built_eagerly(self, processor):
        """Loading the Torch models is expensive, so it must be deferred."""
        assert processor._ocr_reader is None


class TestCleanOcrResult:
    def test_keeps_confident_long_text(self, processor):
        result = processor._clean_ocr_result([detection("ibuprofen", 0.95)])
        assert result == ["ibuprofen"]

    def test_lowercases_output(self, processor):
        """Downstream matching is case-sensitive, so normalisation matters."""
        assert processor._clean_ocr_result([detection("IBUPROFEN", 0.9)]) == ["ibuprofen"]

    def test_drops_low_confidence(self, processor):
        assert processor._clean_ocr_result([detection("ibuprofen", 0.10)]) == []

    def test_drops_short_text(self, processor):
        assert processor._clean_ocr_result([detection("mg", 0.99)]) == []

    def test_length_threshold_is_exclusive(self, processor):
        """min_text_length is 3, and the check is 'greater than'."""
        assert processor._clean_ocr_result([detection("abc", 0.99)]) == []
        assert processor._clean_ocr_result([detection("abcd", 0.99)]) == ["abcd"]

    def test_confidence_threshold_boundary(self, processor):
        """Confidence is compared as a percentage against 25.0."""
        assert processor._clean_ocr_result([detection("tablet", 0.25)]) == []
        assert processor._clean_ocr_result([detection("tablet", 0.2501)]) == ["tablet"]

    def test_filters_a_mixed_batch(self, processor):
        detections = [
            detection("ibuprofen", 0.98),
            detection("mg", 0.99),          # too short
            detection("noise", 0.05),       # too uncertain
            detection("tablets", 0.80),
        ]
        assert processor._clean_ocr_result(detections) == ["ibuprofen", "tablets"]

    def test_empty_input(self, processor):
        assert processor._clean_ocr_result([]) == []

    def test_malformed_detection_returns_empty(self, processor):
        """Failures are logged and swallowed rather than raised."""
        assert processor._clean_ocr_result([("only", "two")]) == []

    def test_threshold_setters(self, processor):
        processor.set_confidence_threshold(60.0)
        processor.set_min_text_length(5)

        assert processor._clean_ocr_result([detection("tablet", 0.50)]) == []
        assert processor._clean_ocr_result([detection("abcd", 0.99)]) == []
        assert processor._clean_ocr_result([detection("ibuprofen", 0.99)]) == ["ibuprofen"]


class TestProcessImage:
    def test_returns_cleaned_text(self, processor, sample_image):
        processor._ocr_reader = FakeReader([detection("ibuprofen", 0.95)])
        assert processor.process_image(sample_image, "pill.jpg", "hash1") == ["ibuprofen"]

    def test_saves_image_to_media_directory(self, processor, sample_image):
        processor._ocr_reader = FakeReader([])
        processor.process_image(sample_image, "pill.jpg", "hash1")
        assert (processor.media_dir / "pill.jpg").exists()

    def test_writes_a_cache_file(self, processor, sample_image):
        processor._ocr_reader = FakeReader([detection("ibuprofen", 0.95)])
        processor.process_image(sample_image, "pill.jpg", "hash1")
        assert (processor.cache_dir / "hash1.ocr").exists()

    def test_second_call_reuses_cache(self, processor, sample_image):
        reader = FakeReader([detection("ibuprofen", 0.95)])
        processor._ocr_reader = reader

        first = processor.process_image(sample_image, "pill.jpg", "hash1")
        second = processor.process_image(sample_image, "pill.jpg", "hash1")

        assert first == second == ["ibuprofen"]
        assert reader.call_count == 1, "cached OCR should not re-run the model"

    def test_different_hash_triggers_new_ocr(self, processor, sample_image):
        reader = FakeReader([detection("ibuprofen", 0.95)])
        processor._ocr_reader = reader

        processor.process_image(sample_image, "a.jpg", "hash1")
        processor.process_image(sample_image, "b.jpg", "hash2")

        assert reader.call_count == 2

    def test_no_detections_returns_empty_list(self, processor, sample_image):
        processor._ocr_reader = FakeReader([])
        assert processor.process_image(sample_image, "blank.jpg", "hash1") == []

    def test_ocr_failure_returns_none(self, processor, sample_image):
        """None signals a server error; [] signals 'nothing readable'."""

        class ExplodingReader:
            def readtext(self, image):
                raise RuntimeError("model failed to load")

        processor._ocr_reader = ExplodingReader()
        assert processor.process_image(sample_image, "pill.jpg", "hash1") is None

    def test_existing_media_file_is_not_overwritten(self, processor, sample_image):
        processor._ocr_reader = FakeReader([])
        processor.media_dir.mkdir(parents=True, exist_ok=True)
        target = processor.media_dir / "pill.jpg"
        target.write_bytes(b"original placeholder")

        processor.process_image(sample_image, "pill.jpg", "hash1")

        assert target.read_bytes() == b"original placeholder"


class TestCacheRoundTrip:
    def test_cached_payload_round_trips(self, processor, sample_image):
        """
        The cache stores JSON, with numpy scalars converted to plain numbers.

        It previously stored repr() of the EasyOCR result and read it back with
        eval(), which executes whatever the file contains. This pins down that
        the result survives the round trip and that the reader does not have to
        run the model again.
        """
        detections = [
            ([[np.int32(0), np.int32(0)]], "ibuprofen", np.float64(0.95)),
        ]
        processor._ocr_reader = FakeReader(detections)

        processor.process_image(sample_image, "pill.jpg", "hash1")

        cached_text = (processor.cache_dir / "hash1.ocr").read_text()
        assert json.loads(cached_text) == [[[[0, 0]], "ibuprofen", 0.95]]

        reader = FakeReader([])
        processor._ocr_reader = reader
        assert processor.process_image(sample_image, "pill.jpg", "hash1") == ["ibuprofen"]
        assert reader.call_count == 0

    def test_the_cache_is_not_executed(self, processor, sample_image):
        """
        A cache file is data. Reading one must not run it.

        Until 2026-09-05 the reader called eval() on the file's contents, so
        anything able to write into the cache directory could run code in the
        server process — and the upload path, before it was fixed, allowed
        exactly that through a crafted filename.
        """
        processor.cache_dir.mkdir(parents=True, exist_ok=True)
        marker = processor.cache_dir.parent / "executed.txt"
        payload = (
            "__import__('pathlib').Path(r'" + str(marker).replace("\\", "\\\\") + "').write_text('x')"
        )
        (processor.cache_dir / "hash1.ocr").write_text(payload)
        processor._ocr_reader = FakeReader([([[0, 0]], "ibuprofen", 0.9)])

        processor.process_image(sample_image, "pill.jpg", "hash1")

        assert not marker.exists(), "cache contents must never be executed"

    def test_unreadable_cache_is_ignored_and_ocr_reruns(self, processor, sample_image):
        """
        A cache that cannot be parsed costs a re-run, not the request.

        It used to abort the whole request: eval() raised, process_image()
        returned None, and the endpoint turned that into a 500.
        """
        processor.cache_dir.mkdir(parents=True, exist_ok=True)
        (processor.cache_dir / "hash1.ocr").write_text("this is not valid json")

        reader = FakeReader([([[0, 0]], "ibuprofen", 0.9)])
        processor._ocr_reader = reader

        assert processor.process_image(sample_image, "pill.jpg", "hash1") == ["ibuprofen"]
        assert reader.call_count == 1, "the model should be re-run when the cache is unusable"

    def test_a_cache_holding_the_wrong_shape_is_ignored(self, processor, sample_image):
        processor.cache_dir.mkdir(parents=True, exist_ok=True)
        (processor.cache_dir / "hash1.ocr").write_text('{"not": "a list"}')

        reader = FakeReader([([[0, 0]], "paracetamol", 0.9)])
        processor._ocr_reader = reader

        assert processor.process_image(sample_image, "pill.jpg", "hash1") == ["paracetamol"]
        assert reader.call_count == 1
