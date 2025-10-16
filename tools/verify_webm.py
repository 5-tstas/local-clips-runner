#!/usr/bin/env python3
"""Minimal WebM validator used in CI/e2e tests.

The validator does not rely on ffprobe/ffmpeg. Instead it performs a
lightweight EBML parse that is sufficient to inspect the container
metadata we care about (DocType, duration, dimensions, codec and frame
timing).

Only the subset of the Matroska/WebM specification that is required for
the generated files is implemented here. The parser is intentionally
simple and fails loudly whenever it encounters unexpected structures.
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


# -- EBML helpers -----------------------------------------------------------


class EBMLParseError(RuntimeError):
    """Raised when the EBML stream is malformed or unsupported."""


def _read_vint(data: memoryview, offset: int) -> Tuple[int, int, int]:
    """Reads an EBML variable-length integer.

    Returns a tuple (value, length, new_offset).
    """

    if offset >= len(data):
        raise EBMLParseError("Unexpected end of stream while reading vint")

    first = data[offset]
    mask = 0x80
    length = 1
    while length <= 8:
        if first & mask:
            break
        mask >>= 1
        length += 1
    if length > 8:
        raise EBMLParseError("Invalid vint length")

    value = first & (mask - 1)
    offset += 1
    for _ in range(length - 1):
        if offset >= len(data):
            raise EBMLParseError("Unexpected end of stream in vint payload")
        value = (value << 8) | data[offset]
        offset += 1
    return value, length, offset


def _read_element(data: memoryview, offset: int) -> Tuple[int, int, int, int]:
    elem_id, id_len, offset = _read_vint(data, offset)
    size, size_len, offset = _read_vint(data, offset)
    return elem_id, size, id_len + size_len, offset


def _read_float(data: memoryview) -> float:
    if len(data) == 4:
        return struct.unpack(">f", data)[0]
    if len(data) == 8:
        return struct.unpack(">d", data)[0]
    raise EBMLParseError(f"Unsupported float size: {len(data)}")


def _read_uint(data: memoryview) -> int:
    value = 0
    for byte in data:
        value = (value << 8) | byte
    return value


def _read_ascii(data: memoryview) -> str:
    return data.tobytes().decode("ascii", errors="replace")


# -- Matroska/WebM inspection ----------------------------------------------


@dataclass
class WebMInfo:
    doc_type: str
    duration_s: Optional[float]
    timecode_scale_ns: int
    video_track: Optional["VideoTrack"]

    @property
    def fps(self) -> Optional[float]:
        if not self.video_track or self.video_track.default_duration_ns is None:
            return None
        if self.video_track.default_duration_ns <= 0:
            return None
        return 1_000_000_000 / self.video_track.default_duration_ns


@dataclass
class VideoTrack:
    codec_id: str
    pixel_width: int
    pixel_height: int
    default_duration_ns: Optional[int]


EBML_HEADER_ID = 0x1A45DFA3
SEGMENT_ID = 0x18538067
INFO_ID = 0x1549A966
TIMECODE_SCALE_ID = 0x2AD7B1
DURATION_ID = 0x4489
TRACKS_ID = 0x1654AE6B
TRACK_ENTRY_ID = 0xAE
TRACK_TYPE_ID = 0x83
CODEC_ID = 0x86
DEFAULT_DURATION_ID = 0x23E383
VIDEO_ID = 0xE0
PIXEL_WIDTH_ID = 0xB0
PIXEL_HEIGHT_ID = 0xBA
DOC_TYPE_ID = 0x4282


def _parse_info(segment: memoryview) -> Tuple[Optional[float], int]:
    offset = 0
    duration: Optional[float] = None
    timecode_scale = 1_000_000  # default per spec (1 ms)
    end = len(segment)
    while offset < end:
        elem_id, size, _, offset = _read_element(segment, offset)
        payload = segment[offset : offset + size]
        if elem_id == TIMECODE_SCALE_ID:
            timecode_scale = _read_uint(payload)
        elif elem_id == DURATION_ID:
            duration = _read_float(payload)
        offset += size
    return duration, timecode_scale


def _parse_video_track(payload: memoryview) -> VideoTrack:
    offset = 0
    pixel_width = pixel_height = None
    while offset < len(payload):
        elem_id, size, _, offset = _read_element(payload, offset)
        data = payload[offset : offset + size]
        if elem_id == PIXEL_WIDTH_ID:
            pixel_width = _read_uint(data)
        elif elem_id == PIXEL_HEIGHT_ID:
            pixel_height = _read_uint(data)
        offset += size
    if pixel_width is None or pixel_height is None:
        raise EBMLParseError("Video track missing dimensions")
    return VideoTrack(codec_id="", pixel_width=pixel_width, pixel_height=pixel_height, default_duration_ns=None)


def _parse_tracks(segment: memoryview) -> Optional[VideoTrack]:
    offset = 0
    end = len(segment)
    video_track: Optional[VideoTrack] = None
    while offset < end:
        elem_id, size, _, offset = _read_element(segment, offset)
        if elem_id != TRACK_ENTRY_ID:
            offset += size
            continue

        payload = segment[offset : offset + size]
        offset += size

        t_offset = 0
        track_type = None
        codec_id = ""
        default_duration: Optional[int] = None
        video_payload: Optional[memoryview] = None

        while t_offset < len(payload):
            t_elem_id, t_size, _, t_offset = _read_element(payload, t_offset)
            t_data = payload[t_offset : t_offset + t_size]
            if t_elem_id == TRACK_TYPE_ID:
                track_type = _read_uint(t_data)
            elif t_elem_id == CODEC_ID:
                codec_id = _read_ascii(t_data)
            elif t_elem_id == DEFAULT_DURATION_ID:
                default_duration = _read_uint(t_data)
            elif t_elem_id == VIDEO_ID:
                video_payload = t_data
            t_offset += t_size

        if track_type == 1 and video_payload is not None:
            track = _parse_video_track(video_payload)
            track.codec_id = codec_id
            track.default_duration_ns = default_duration
            video_track = track
    return video_track


def _parse_doc_type(header: memoryview) -> str:
    offset = 0
    end = len(header)
    doc_type = ""
    while offset < end:
        elem_id, size, _, offset = _read_element(header, offset)
        data = header[offset : offset + size]
        if elem_id == DOC_TYPE_ID:
            doc_type = _read_ascii(data)
        offset += size
    return doc_type


def inspect_webm(path: Path) -> WebMInfo:
    raw = path.read_bytes()
    buf = memoryview(raw)
    offset = 0

    # EBML header
    elem_id, size, consumed, offset = _read_element(buf, offset)
    if elem_id != EBML_HEADER_ID:
        raise EBMLParseError("Missing EBML header")
    doc_type = _parse_doc_type(buf[offset : offset + size])
    offset += size

    # Segment
    elem_id, seg_size, _, offset = _read_element(buf, offset)
    if elem_id != SEGMENT_ID:
        raise EBMLParseError("Missing Segment element")
    segment = buf[offset : offset + seg_size] if seg_size != (1 << (7 * 8)) - 1 else buf[offset:]

    seg_offset = 0
    duration_val: Optional[float] = None
    timecode_scale = 1_000_000
    video_track: Optional[VideoTrack] = None
    while seg_offset < len(segment):
        elem_id, size, _, seg_offset = _read_element(segment, seg_offset)
        payload = segment[seg_offset : seg_offset + size]
        if elem_id == INFO_ID:
            duration_val, timecode_scale = _parse_info(payload)
        elif elem_id == TRACKS_ID:
            video_track = _parse_tracks(payload)
        seg_offset += size

    if duration_val is not None:
        duration_s = duration_val * (timecode_scale / 1_000_000_000)
    else:
        duration_s = None

    return WebMInfo(
        doc_type=doc_type,
        duration_s=duration_s,
        timecode_scale_ns=timecode_scale,
        video_track=video_track,
    )


# -- Validation -------------------------------------------------------------


def verify_webm(path: Path) -> Dict[str, object]:
    info = inspect_webm(path)
    errors = []

    if info.doc_type.lower() != "webm":
        errors.append(f"DocType must be 'webm', got '{info.doc_type}'")

    if info.video_track is None:
        errors.append("Missing video track")
    else:
        if info.video_track.codec_id not in {"V_VP9", "V_VP8"}:
            errors.append(f"Unexpected codec {info.video_track.codec_id}")
        if info.video_track.pixel_width != 1280 or info.video_track.pixel_height != 720:
            errors.append(
                f"Resolution must be 1280x720, got {info.video_track.pixel_width}x{info.video_track.pixel_height}"
            )
        fps = info.fps
        if fps is None:
            errors.append("Unable to determine FPS")
        elif not (28 <= fps <= 32):
            errors.append(f"FPS must be close to 30, got {fps:.2f}")

    if info.duration_s is None or info.duration_s <= 0.5:
        errors.append(f"Duration must be > 0.5s, got {info.duration_s!r}")

    result = {
        "path": str(path),
        "doc_type": info.doc_type,
        "duration_s": info.duration_s,
        "fps": info.fps,
        "width": info.video_track.pixel_width if info.video_track else None,
        "height": info.video_track.pixel_height if info.video_track else None,
        "codec": info.video_track.codec_id if info.video_track else None,
        "errors": errors,
    }

    if errors:
        raise ValueError("; ".join(errors))
    return result


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify WebM container constraints")
    parser.add_argument("files", nargs="+", type=Path, help="WebM files to inspect")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    args = parser.parse_args(argv)

    results: Dict[str, Dict[str, object]] = {}
    rc = 0
    for file in args.files:
        try:
            info = verify_webm(file)
            results[str(file)] = info
            if not args.json:
                print(
                    f"{file}: codec={info['codec']} size={info['width']}x{info['height']} "
                    f"fps={info['fps']:.2f} duration={info['duration_s']:.3f}s"
                )
        except Exception as exc:  # noqa: BLE001 - surfacing validation failure
            rc = 1
            if args.json:
                results[str(file)] = {"error": str(exc)}
            else:
                print(f"{file}: ERROR {exc}")

    if args.json:
        import json

        print(json.dumps(results, indent=2, sort_keys=True))

    return rc


if __name__ == "__main__":
    raise SystemExit(main())

