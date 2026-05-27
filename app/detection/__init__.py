"""Notable-event detection engine.

Pure, database-free logic. The public entry point is
``app.detection.detector.detect_events``.
"""

from app.detection.detector import detect_events

__all__ = ["detect_events"]
