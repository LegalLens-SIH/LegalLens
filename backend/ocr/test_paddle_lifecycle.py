from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import numpy as np

from backend.ocr.paddle_ocr_service import PaddleOCRService


def test_concurrent_service_instances_initialize_one_cached_engine(monkeypatch):
    constructor_calls = 0
    calls_lock = threading.Lock()

    class FakePaddleOCR:
        def __init__(self, **_kwargs):
            nonlocal constructor_calls
            with calls_lock:
                constructor_calls += 1
            time.sleep(0.02)

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=FakePaddleOCR))
    language = f"lifecycle_{uuid4().hex}"
    services = [PaddleOCRService(lang=language) for _ in range(8)]

    try:
        with ThreadPoolExecutor(max_workers=len(services)) as executor:
            engines = list(executor.map(lambda service: service._get_engine(), services))
        assert constructor_calls == 1
        assert all(engine is engines[0] for engine in engines)
    finally:
        PaddleOCRService._engine_cache.pop(services[0]._cache_key(), None)


def test_concurrent_predictions_are_serialized_on_the_shared_engine():
    active_predictions = 0
    maximum_concurrent_predictions = 0
    state_lock = threading.Lock()

    class FakeEngine:
        def predict(self, _image, **_kwargs):
            nonlocal active_predictions, maximum_concurrent_predictions
            with state_lock:
                active_predictions += 1
                maximum_concurrent_predictions = max(maximum_concurrent_predictions, active_predictions)
            time.sleep(0.02)
            with state_lock:
                active_predictions -= 1
            return []

    service = PaddleOCRService()
    service._parse_result = lambda raw: raw

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: service._ocr_array(FakeEngine(), np.zeros((1, 1, 3))), range(8)))

    assert results == [[] for _ in range(8)]
    assert maximum_concurrent_predictions == 1
