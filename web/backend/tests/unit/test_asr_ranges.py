"""Regression cases for verifying and slicing a private capture object."""
from dataclasses import replace
from hashlib import sha256

import pytest

from classroom_web.asr_sim.simulator import validate_frame
from classroom_web.domain.asr import Samples, source_frames
from classroom_web.domain.capture import DurableCaptureChunk


def test_clip_verified_pcm_and_keep_epoch_coordinates():
    pcm = bytes(range(200)) * 40
    chunk = DurableCaptureChunk('chunk', 'class', 'epoch', 1, 16_000, 20_000,
                                sha256(pcm).hexdigest(), 'asset', 'recovery', 'receipt')
    frames = source_frames(chunk, pcm, Samples(16_017, 19_219))
    assert [frame.samples for frame in frames] == [
        Samples(16_017, 17_617), Samples(17_617, 19_217), Samples(19_217, 19_219)]
    assert b''.join(frame.pcm for frame in frames) == pcm[34:6_438]
    assert frames[-1].samples.classroom_ms(5_000) == (6_201, 6_202)
    assert validate_frame(frames[0].provider_value(), max_bytes=3200) == (frames[0].pcm, 16_017, 17_617)
    assert frames[0].provider_value()['sha256'] == sha256(frames[0].pcm).hexdigest()
    assert frames[0].source.receipt_id == 'receipt'


def test_reject_wrong_full_object_even_when_selected_pcm_matches():
    pcm = bytes(32)
    chunk = DurableCaptureChunk('chunk', 'class', 'epoch', 0, 0, 16,
                                sha256(pcm).hexdigest(), 'asset', 'live', 'receipt')
    with pytest.raises(ValueError, match='does not match'):
        source_frames(chunk, pcm[:-1] + b'\x01', Samples(0, 4))
    with pytest.raises(ValueError, match='exceed'):
        source_frames(chunk, pcm, Samples(12, 17))
    with pytest.raises(ValueError, match='does not match'):
        source_frames(replace(chunk, end_sample=15), pcm, Samples(0, 4))


def test_intersection_includes_partial_overlap_and_excludes_touching_boundaries():
    assert Samples(10, 22).overlaps(Samples(20, 33))
    assert Samples(20, 33).overlaps(Samples(10, 22))
    assert not Samples(10, 20).overlaps(Samples(20, 33))
    assert Samples(10, 21).contains(Samples(10, 21))
    assert Samples(100, 117).duration_ms == 2
