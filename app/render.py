# app/render.py
from __future__ import annotations
import base64, json, re, struct
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile, ZIP_DEFLATED

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError
from models import Batch, Job, Output, validate_batch

REPO_ROOT = Path(__file__).resolve().parents[1]
HTML_DIR   = REPO_ROOT / "html"

# КОРОТКИЕ имена html
HTML_BY_TYPE: Dict[str, str] = {
    "overlay": "overlay.html",
    "chat":    "chat-typing.html",
    "abc":     "abc-transist.html",
}

# ---------- webm helpers ----------

def _read_vint_py(data: memoryview, offset: int, as_size: bool) -> tuple[int, int, bool]:
    first = data[offset]
    length = 1
    mask = 0x80
    while length <= 8 and (first & mask) == 0:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("invalid vint length")
    value = first if not as_size else (first & (mask - 1 if mask else 0))
    for i in range(1, length):
        value = (value << 8) | data[offset + i]
    unknown = False
    if as_size:
        max_value = (1 << (7 * length)) - 1
        if value == max_value:
            unknown = True
    return length, value, unknown


def _parse_element_py(data: memoryview, offset: int) -> dict | None:
    if offset >= len(data):
        return None
    id_len, element_id, _ = _read_vint_py(data, offset, as_size=False)
    size_len, size_value, unknown = _read_vint_py(data, offset + id_len, as_size=True)
    data_offset = offset + id_len + size_len
    end = len(data) if unknown else data_offset + size_value
    if data_offset > len(data) or end > len(data):
        return None
    return {
        "id": element_id,
        "start": offset,
        "data_offset": data_offset,
        "end": end,
        "size": size_value,
        "unknown": unknown,
    }


def _parse_children_py(data: memoryview, start: int, end: int) -> list[dict]:
    out: list[dict] = []
    offset = start
    while offset < end:
        element = _parse_element_py(data, offset)
        if not element:
            break
        out.append(element)
        offset = element["end"]
    return out


def _scan_webm_py(raw: bytes) -> dict:
    data = memoryview(raw)
    root = _parse_element_py(data, 0)
    if not root:
        raise ValueError("missing EBML header")
    segment = _parse_element_py(data, root["end"])
    if not segment:
        raise ValueError("missing Segment")
    seg_end = len(data) if segment["unknown"] else segment["end"]
    duration = None
    cue_points = 0
    offset = segment["data_offset"]
    while offset < seg_end:
        element = _parse_element_py(data, offset)
        if not element:
            break
        if element["id"] == 0x1549A966:  # Info
            children = _parse_children_py(data, element["data_offset"], element["end"])
            for child in children:
                if child["id"] == 0x4489 and duration is None:
                    payload = data[child["data_offset"]:child["end"]].tobytes()
                    if len(payload) == 4:
                        duration = float(struct.unpack(">f", payload)[0])
                    elif len(payload) >= 8:
                        duration = float(struct.unpack(">d", payload[:8])[0])
        elif element["id"] == 0x1C53BB6B:  # Cues
            cue_children = _parse_children_py(data, element["data_offset"], element["end"])
            for cue_child in cue_children:
                if cue_child["id"] == 0xBB:
                    cue_points += 1
        offset = element["end"]
    return {"duration": duration, "cue_points": cue_points}


def _validate_webm(path: Path) -> tuple[bool, dict]:
    try:
        raw = path.read_bytes()
    except Exception as exc:  # pragma: no cover - filesystem errors
        return False, {"error": f"read failed: {exc}"}
    try:
        meta = _scan_webm_py(raw)
    except Exception as exc:
        return False, {"error": f"parse failed: {exc}"}
    duration = meta.get("duration")
    cue_points = int(meta.get("cue_points", 0) or 0)
    ok = bool(duration) and float(duration) > 0 and cue_points > 0
    return ok, {"duration": float(duration or 0.0), "cue_points": cue_points}


def _report_webm_validation(path: Path) -> None:
    ok, meta = _validate_webm(path)
    if ok:
        print(
            f"[webm-validate] ok name={path.name} duration={meta['duration']:.3f}s cues={meta['cue_points']}"
        )
    else:
        print(
            f"[webm-validate] FAIL name={path.name} reason={meta.get('error', 'unknown')}"
        )


async def _dump_webm_logs(page) -> None:
    try:
        logs = await page.evaluate(
            "(() => { const src = window.__WEBM_FIX_LOGS; if (Array.isArray(src)) { const copy = src.slice(); window.__WEBM_FIX_LOGS = []; return copy; } return []; })()"
        )
    except Exception:
        return
    if not logs:
        return
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        level = entry.get("level", "info")
        msg = entry.get("msg", "")
        ts = entry.get("ts")
        if isinstance(ts, (int, float)):
            prefix = f"[webm-fix][{level}][{int(ts)}]"
        else:
            prefix = f"[webm-fix][{level}]"
        print(f"{prefix} {msg}")


# ---------- утилиты ----------
def _slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "clip"

def _outfile_name(idx: int, job: Job) -> str:
    return f"{idx:03d}_{job.type}_{_slug(job.name)}.webm"

async def _fill(page, selector: str, value) -> None:
    el = await page.query_selector(selector)
    if not el or value is None:
        return
    t = (await el.get_attribute("type") or "").lower()
    if t == "checkbox":
        cur = await el.is_checked()
        want = bool(value)
        if want and not cur: await el.check()
        if (not want) and cur: await el.uncheck()
        return
    try:
        await el.fill(str(value))
    except Exception:
        try:
            await page.select_option(selector, str(value))
        except Exception:
            pass

async def _set_file(page, selector: str, path: Path) -> bool:
    el = await page.query_selector(selector)
    if not el or not path.exists():
        return False
    await el.set_input_files(path.as_posix())
    return True

async def _start_preview(page, job_type: str) -> None:
    # автозапуск предпросмотра (для записи экрана)
    launchers = {
        "overlay": ["preview", "runPreview", "start", "play"],
        "chat":    ["runPreview", "preview", "start", "play"],
        "abc":     ["preview", "start", "play"],
    }.get(job_type, ["preview","runPreview","start","play"])
    await page.evaluate("""(names) => {
      const S = (window.STATE||{});
      for (const name of names) {
        const fn = window[name];
        if (typeof fn === 'function') {
          try { fn(S); return true; } catch(_) {}
          try { fn();  return true; } catch(_) {}
      }}
      const sels = ['#btnPreview','#preview','[data-action="preview"]','.preview','button'];
      for (const sel of sels) { const el = document.querySelector(sel); if (el) { el.click(); return true; } }
      return false;
    }""", launchers)

async def _try_export(page, prefer_funcs: List[str], btn_ids: List[str]) -> bool:
    # Запуск экспорта (без предпросмотра)
    code = f"""
(() => {{
  const tryFns = {json.dumps(prefer_funcs)};
  for (const name of tryFns) {{
    const fn = window[name];
    if (typeof fn === 'function') {{ try {{ fn(); return true; }} catch(_){{
    }} }}
  }}
  const ids = {json.dumps(btn_ids)};
  for (const id of ids) {{
    const el = document.querySelector(id);
    if (el) {{ el.click(); return true; }}
  }}
  return false;
}})()
"""
    try:
        return bool(await page.evaluate(code))
    except Exception:
        return False

# ---------- основной рендер ----------
WEBM_FIX_INIT_SCRIPT = """
(function(){
  if (window.__WEBM_FIXER_READY__) { return; }
  window.__WEBM_FIXER_READY__ = true;
  const MediaRec = window.MediaRecorder;
  const OriginalBlob = window.Blob;
  if (!MediaRec || !OriginalBlob) { return; }

  const LOG_PREFIX = '[webm-fix]';
  const logs = window.__WEBM_FIX_LOGS = window.__WEBM_FIX_LOGS || [];
  const pushLog = (level, parts) => {
    try {
      const msg = parts.map((v) => {
        if (v instanceof Error) { return v.message; }
        if (typeof v === 'object') { try { return JSON.stringify(v); } catch(_){} }
        return String(v);
      }).join(' ');
      logs.push({ ts: Date.now(), level, msg });
    } catch(_) {}
  };
  const log = (...args) => { pushLog('info', args); try { console.log(LOG_PREFIX, ...args); } catch(_){} };
  const warn = (...args) => { pushLog('warn', args); try { console.warn(LOG_PREFIX, ...args); } catch(_){} };

  let seq = 0;
  const recorderInfo = new WeakMap();
  const chunkLookup = new WeakMap();

  const ensureInfo = (recorder, reset) => {
    let info = recorderInfo.get(recorder);
    if (!info) {
      info = {};
      recorderInfo.set(recorder, info);
    }
    const captureAttached = Boolean(info.captureAttached);
    const stopWrappers = info.stopWrappers || new Map();
    if (reset && stopWrappers.size) {
      stopWrappers.clear();
    }
    if (reset || !info.readyPromise) {
      info.id = ++seq;
      info.startTime = 0;
      info.stopTime = 0;
      info.durationMs = 0;
      info.mimeType = recorder.mimeType || '';
      info.buffers = [];
      info.chunkBlobs = [];
      info.pending = 0;
      info.stopped = false;
      info.ready = false;
      info.patched = false;
      info.captureAttached = captureAttached;
      info.stopWrappers = stopWrappers;
      info.onstopOriginal = null;
      info.readyPromise = new Promise((resolve) => {
        info._resolveReady = () => {
          if (!info.ready) {
            info.ready = true;
            resolve();
          }
        };
      });
    }
    return info;
  };

  const toUint8 = (data) => {
    if (!data) { return new Uint8Array(); }
    if (data instanceof Uint8Array) { return data; }
    if (data instanceof ArrayBuffer) { return new Uint8Array(data); }
    if (ArrayBuffer.isView(data)) { return new Uint8Array(data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength)); }
    return new Uint8Array();
  };

  const concatBytes = (arrays) => {
    let total = 0;
    for (const arr of arrays) { total += arr ? arr.length : 0; }
    const out = new Uint8Array(total);
    let offset = 0;
    for (const arr of arrays) {
      if (!arr || !arr.length) { continue; }
      out.set(arr, offset);
      offset += arr.length;
    }
    return out;
  };

  const readVint = (bytes, offset, asSize) => {
    const first = bytes[offset];
    if (first === undefined) { throw new Error('out of range'); }
    let length = 1;
    let mask = 0x80;
    while (length <= 8 && (first & mask) === 0) { mask >>= 1; length += 1; }
    if (length > 8) { throw new Error('bad vint'); }
    let value = BigInt(first & (asSize ? (mask - 1) : 0xff));
    for (let i = 1; i < length; i++) {
      value = (value << 8n) | BigInt(bytes[offset + i]);
    }
    let unknown = false;
    if (asSize) {
      const allOnes = (1n << BigInt(length * 7)) - 1n;
      if (value === allOnes) {
        unknown = true;
      }
    }
    return { length, value, unknown };
  };

  const encodeVint = (value, minLen = 1) => {
    let v = BigInt(value);
    for (let len = minLen; len <= 8; len++) {
      const max = (1n << BigInt(len * 7)) - 1n;
      if (v <= max) {
        const out = new Uint8Array(len);
        let tmp = v;
        for (let i = len - 1; i >= 0; i--) {
          out[i] = Number(tmp & 0xffn);
          tmp >>= 8n;
        }
        out[0] |= 1 << (8 - len);
        return out;
      }
    }
    throw new Error('value too large');
  };

  const encodeUint = (value) => {
    let v = BigInt(value);
    if (v === 0n) { return new Uint8Array([0]); }
    const bytes = [];
    while (v > 0n) {
      bytes.push(Number(v & 0xffn));
      v >>= 8n;
    }
    bytes.reverse();
    return new Uint8Array(bytes);
  };

  const encodeFloat64 = (value) => {
    const arr = new ArrayBuffer(8);
    new DataView(arr).setFloat64(0, Number(value), false);
    return new Uint8Array(arr);
  };

  const idToBytes = (id) => {
    const bytes = [];
    let shift = 24;
    while (shift >= 0) {
      const b = (id >> shift) & 0xff;
      if (bytes.length || b !== 0 || shift === 0) { bytes.push(b); }
      shift -= 8;
    }
    return new Uint8Array(bytes);
  };

  const readUInt = (bytes, offset, length) => {
    let value = 0n;
    for (let i = 0; i < length; i++) {
      value = (value << 8n) | BigInt(bytes[offset + i]);
    }
    return value;
  };

  const parseElement = (bytes, offset) => {
    if (offset >= bytes.length) { return null; }
    const id = readVint(bytes, offset, false);
    const size = readVint(bytes, offset + id.length, true);
    const dataOffset = offset + id.length + size.length;
    const end = size.unknown ? bytes.length : (dataOffset + Number(size.value));
    return {
      id: Number(id.value),
      size: size,
      start: offset,
      dataOffset,
      end,
      headerLength: id.length + size.length,
    };
  };

  const parseChildren = (bytes, start, end) => {
    const children = [];
    let offset = start;
    while (offset < end) {
      const el = parseElement(bytes, offset);
      if (!el || el.start === el.end) { break; }
      children.push(el);
      offset = el.end;
    }
    return children;
  };

  const scanWebM = (bytes) => {
    const first = parseElement(bytes, 0);
    if (!first) { throw new Error('No EBML header'); }
    let offset = first.end;
    const segment = parseElement(bytes, offset);
    if (!segment) { throw new Error('No Segment'); }
    const segEnd = segment.size.unknown ? bytes.length : segment.end;
    let info = null;
    let cues = null;
    const clusters = [];
    let timecodeScale = 1000000n;
    offset = segment.dataOffset;
    while (offset < segEnd) {
      const el = parseElement(bytes, offset);
      if (!el) { break; }
      if (el.id === 0x1549A966) {
        info = el;
        info.children = parseChildren(bytes, el.dataOffset, el.end);
        for (const child of info.children) {
          if (child.id === 0x2AD7B1) {
            const size = child.end - child.dataOffset;
            timecodeScale = readUInt(bytes, child.dataOffset, size || 1);
          }
        }
      } else if (el.id === 0x1C53BB6B) {
        cues = el;
      } else if (el.id === 0x1F43B675) {
        const cluster = el;
        const children = parseChildren(bytes, cluster.dataOffset, cluster.end);
        let timecode = 0n;
        for (const child of children) {
          if (child.id === 0xE7) {
            timecode = readUInt(bytes, child.dataOffset, child.end - child.dataOffset);
            break;
          }
        }
        clusters.push({ start: cluster.start, timecode });
      }
      offset = el.end;
    }
    return { segment, info, cues, clusters, timecodeScale: Number(timecodeScale || 1000000n) };
  };

  const encodeElement = (id, data) => {
    const payload = toUint8(data);
    const header = concatBytes([idToBytes(id), encodeVint(payload.length)]);
    return concatBytes([header, payload]);
  };

  const adjuster = (patches) => (offset) => {
    let delta = 0;
    for (const patch of patches) {
      if (patch.start <= offset) {
        delta += patch.delta;
      }
    }
    return offset + delta;
  };

  const applyPatches = (bytes, patches) => {
    if (!patches.length) { return bytes; }
    patches.sort((a, b) => b.start - a.start);
    let result = bytes;
    for (const patch of patches) {
      const before = result.slice(0, patch.start);
      const after = result.slice(patch.end);
      result = concatBytes([before, patch.data, after]);
    }
    return result;
  };

  const patchWebM = (buffers, durationMs) => {
    try {
      const merged = concatBytes(buffers.map(toUint8));
      if (!merged.length) { throw new Error('empty data'); }
      const info = scanWebM(merged);
      const patches = [];
      const durationSeconds = Math.max(0, Number(durationMs || 0) / 1000);
      const durationElem = encodeElement(0x4489, encodeFloat64(durationSeconds));
      let infoPatch;
      if (info.info) {
        const pieces = [];
        let hasDur = false;
        for (const child of info.info.children || []) {
          if (child.id === 0x4489) { hasDur = true; continue; }
          pieces.push(merged.slice(child.start, child.end));
        }
        pieces.push(durationElem);
        const data = concatBytes(pieces);
        const header = concatBytes([idToBytes(0x1549A966), encodeVint(data.length)]);
        const replacement = concatBytes([header, data]);
        infoPatch = {
          start: info.info.start,
          end: info.info.end,
          data: replacement,
        };
      } else {
        const timecodeElem = encodeElement(0x2AD7B1, encodeUint(info.timecodeScale || 1000000));
        const data = concatBytes([timecodeElem, durationElem]);
        const header = concatBytes([idToBytes(0x1549A966), encodeVint(data.length)]);
        const replacement = concatBytes([header, data]);
        infoPatch = {
          start: info.segment.dataOffset,
          end: info.segment.dataOffset,
          data: replacement,
        };
      }
      patches.push({ ...infoPatch, delta: infoPatch.data.length - (infoPatch.end - infoPatch.start) });
      const adjust = adjuster(patches);
      const cuePoints = [];
      for (const cluster of info.clusters || []) {
        const finalOffset = adjust(cluster.start);
        const rel = Math.max(0, finalOffset - info.segment.dataOffset);
        const cueTime = encodeElement(0xB3, encodeUint(cluster.timecode));
        const cueTrack = encodeElement(0xF7, encodeUint(1));
        const cuePos = encodeElement(0xF1, encodeUint(rel));
        const positions = encodeElement(0xB7, concatBytes([cueTrack, cuePos]));
        cuePoints.push(encodeElement(0xBB, concatBytes([cueTime, positions])));
      }
      let cuesPatch = null;
      if (cuePoints.length) {
        const cuesData = concatBytes(cuePoints);
        const cuesBytes = encodeElement(0x1C53BB6B, cuesData);
        if (info.cues) {
          cuesPatch = {
            start: info.cues.start,
            end: info.cues.end,
            data: cuesBytes,
          };
        } else {
          const insertPos = info.segment.size.unknown ? merged.length : info.segment.end;
          cuesPatch = {
            start: insertPos,
            end: insertPos,
            data: cuesBytes,
          };
        }
        patches.push({ ...cuesPatch, delta: cuesPatch.data.length - (cuesPatch.end - cuesPatch.start) });
      } else {
        warn('no clusters found for cues');
      }
      const finalBytes = applyPatches(merged, patches);
      return finalBytes;
    } catch (err) {
      warn('patch failed', err);
      return null;
    }
  };

  const origStart = MediaRec.prototype.start;
  MediaRec.prototype.start = function(...args) {
    const info = ensureInfo(this, true);
    info.startTime = performance.now();
    info.mimeType = this.mimeType || (args?.[1]?.mimeType) || '';
    log('start', info.id, 't=', info.startTime.toFixed(3));
    if (!info.captureAttached) {
      const capture = (event) => {
        const chunk = event?.data;
        if (!chunk || !chunk.size) { return; }
        const meta = ensureInfo(this, false);
        meta.chunkBlobs.push(chunk);
        const index = meta.chunkBlobs.length - 1;
        meta.pending += 1;
        chunkLookup.set(chunk, { info: meta, index });
        chunk.arrayBuffer().then((buf) => {
          meta.buffers[index] = new Uint8Array(buf);
        }).catch((err) => {
          warn('chunk read failed', err);
          meta.buffers[index] = new Uint8Array();
        }).finally(() => {
          meta.pending -= 1;
          if (meta.stopped && meta.pending === 0) {
            meta._resolveReady();
          }
        });
      };
      this.addEventListener('dataavailable', capture);
      info.captureAttached = true;
    }
    return origStart.apply(this, args);
  };

  const origStop = MediaRec.prototype.stop;
  MediaRec.prototype.stop = function(...args) {
    const info = ensureInfo(this, false);
    info.stopTime = performance.now();
    info.durationMs = Math.max(0, info.stopTime - info.startTime);
    info.stopped = true;
    log('stop', info.id, 't=', info.stopTime.toFixed(3), 'dur=', info.durationMs.toFixed(3));
    if (info.pending === 0) { info._resolveReady(); }
    return origStop.apply(this, args);
  };

  const origAdd = MediaRec.prototype.addEventListener;
  MediaRec.prototype.addEventListener = function(type, listener, options) {
    if (type === 'stop' && typeof listener === 'function') {
      const info = ensureInfo(this, false);
      const wrapped = function(event) {
        info.readyPromise.then(() => listener.call(this, event));
      };
      info.stopWrappers.set(listener, wrapped);
      return origAdd.call(this, type, wrapped, options);
    }
    return origAdd.call(this, type, listener, options);
  };

  const origRemove = MediaRec.prototype.removeEventListener;
  MediaRec.prototype.removeEventListener = function(type, listener, options) {
    if (type === 'stop' && typeof listener === 'function') {
      const info = recorderInfo.get(this);
      if (info && info.stopWrappers?.has(listener)) {
        const wrapped = info.stopWrappers.get(listener);
        info.stopWrappers.delete(listener);
        return origRemove.call(this, type, wrapped, options);
      }
    }
    return origRemove.call(this, type, listener, options);
  };

  const stopDescriptor = Object.getOwnPropertyDescriptor(MediaRec.prototype, 'onstop');
  if (stopDescriptor && stopDescriptor.configurable) {
    Object.defineProperty(MediaRec.prototype, 'onstop', {
      configurable: true,
      enumerable: stopDescriptor.enumerable,
      get() {
        const info = recorderInfo.get(this);
        return info?.onstopOriginal || stopDescriptor.get?.call(this);
      },
      set(handler) {
        if (typeof handler !== 'function') {
          const info = recorderInfo.get(this);
          if (info) { info.onstopOriginal = null; }
          stopDescriptor.set?.call(this, handler);
          return;
        }
        const info = ensureInfo(this, false);
        info.onstopOriginal = handler;
        const wrapped = (event) => {
          info.readyPromise.then(() => handler.call(this, event));
        };
        stopDescriptor.set?.call(this, wrapped);
      }
    });
  }

  window.Blob = function(parts, options) {
    const blob = function(origParts, origOptions) {
      return new OriginalBlob(origParts, origOptions);
    };
    try {
      const opts = options || {};
      const type = typeof opts.type === 'string' ? opts.type : '';
      if (!type.toLowerCase().includes('webm')) {
        return blob(parts, options);
      }
      const arrays = Array.isArray(parts) ? parts : [];
      let targetInfo = null;
      const buffers = [];
      for (const part of arrays) {
        if (part instanceof Blob && chunkLookup.has(part)) {
          const meta = chunkLookup.get(part);
          targetInfo = meta.info;
          const stored = meta.info?.buffers?.[meta.index];
          if (stored) {
            buffers[meta.index] = stored;
          }
        } else if (part instanceof ArrayBuffer || ArrayBuffer.isView(part) || part instanceof Uint8Array) {
          buffers.push(toUint8(part));
        }
      }
      if (!targetInfo || !targetInfo.ready) {
        if (!targetInfo) { warn('webm blob without recorder info'); }
        return blob(parts, options);
      }
      const ordered = [];
      for (let i = 0; i < targetInfo.buffers.length; i++) {
        if (targetInfo.buffers[i]) {
          ordered.push(targetInfo.buffers[i]);
        }
      }
      if (ordered.length === 0) { return blob(parts, options); }
      const patched = patchWebM(ordered, targetInfo.durationMs);
      if (patched) {
        targetInfo.patched = true;
        log('patched blob', targetInfo.id, 'durationMs=', targetInfo.durationMs.toFixed(3));
        return blob([patched], { ...opts, type });
      }
    } catch (err) {
      warn('blob override failed', err);
    }
    return blob(parts, options);
  };
  window.Blob.prototype = OriginalBlob.prototype;
})();
"""


async def render_job(idx: int, job: Job, output: Output, out_dir: Path) -> Path:
    html_name = HTML_BY_TYPE[job.type]
    html_path = HTML_DIR / html_name
    if not html_path.exists():
        raise FileNotFoundError(f"Не найден HTML: {html_path}")

    # Слить глобальные стили (output) в payload (локальные перекрывают)
    payload = job.payload.dict()
    for k in ("bgColor","textColor","fontFamily","cpsPrompt","cpsAnswer","pauseSentence","pauseComma","fps","soundOn","thinkSec"):
        v = getattr(output, k, None)
        if v is not None and k not in payload:
            payload[k] = v

    # Передаём STATE как base64(JSON) и глушим автостарт (?autostart=0)
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
    url = html_path.as_uri() + "?autostart=0&data=" + b64

    w, h = output.size
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_videos = out_dir / ".tmp_videos"
    tmp_videos.mkdir(exist_ok=True)

    async with async_playwright() as p:
        # ВСЕГДА включаем запись как фолбэк
        browser = await p.chromium.launch()
        context = await browser.new_context(
            viewport={"width": w, "height": h},
            accept_downloads=True,
            record_video_dir=str(tmp_videos),
            record_video_size={"width": w, "height": h},
        )
        page = await context.new_page()
        try:
            await page.add_init_script(WEBM_FIX_INIT_SCRIPT)
        except Exception:
            pass
        await page.goto(url)

        # убрать «серый» старт — фон/цвет сразу
        await page.evaluate("""(() => {
          const S = (window.STATE||{});
          if (S.bgColor)  document.body.style.background = S.bgColor;
          if (S.textColor)document.body.style.color      = S.textColor;
        })()""")

        # дождёмся шрифтов (если есть)
        try:
            await page.wait_for_function(
                "() => (document.fonts ? document.fonts.ready.then(() => true) : true)",
                timeout=5000
            )
        except PWTimeoutError:
            pass

        # ===== тип-специфическая подготовка =====
        if job.type == "overlay":
            t  = payload.get("title") or ""
            st = payload.get("subtitle") or ""
            body = payload.get("body") or []
            if isinstance(body, list): body = "\n".join(str(x) for x in body)
            await _fill(page, "#title", t)
            await _fill(page, "#subtitle", st)
            await _fill(page, "#body", body)

            # 1) Попытка ЭКСПОРТА
            got = False
            try:
                started = await _try_export(page, ["exportWebM"], ["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    await _dump_webm_logs(page)
                    _report_webm_validation(dst)
                    got = True
            except Exception:
                got = False

            # 2) Фолбэк — запись
            if not got:
                await _start_preview(page, "overlay")
                duration_ms = max(500, job.durationSec * 1000)
                try:
                    await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 3000)
                except PWTimeoutError:
                    await page.wait_for_timeout(duration_ms)
                video = page.video
                await _dump_webm_logs(page)
                await context.close(); await browser.close()
                src = Path(await video.path())  # type: ignore[arg-type]
                dst = out_dir / _outfile_name(idx, job)
                src.replace(dst)
                _report_webm_validation(dst)
                return dst

            await context.close(); await browser.close()
            return out_dir / _outfile_name(idx, job)

        elif job.type == "chat":
            # Заполняем поля, НО экспорт НЕ используем — только запись экрана (устраняем зависания)
            lines = payload.get("lines") or []
            md = "\n\n".join(lines) if isinstance(lines, list) else str(lines)
            await _fill(page, "#answer", md)
            if payload.get("prompt"): await _fill(page, "#prompt", payload.get("prompt"))

            # скорости/паузы/FPS/звук
            def _num(v, d): 
                try: return int(v) if v is not None else d
                except: return d
            cps_prompt = _num(payload.get("cpsPrompt"), 14)
            cps_answer = _num(payload.get("cpsAnswer"), 20)
            pause_sentence = _num(payload.get("pauseSentence"), 220)
            pause_comma    = _num(payload.get("pauseComma"), 110)
            fps            = _num(payload.get("fps"), 30)
            think_sec      = _num(payload.get("thinkSec"), 2)

            for sel, val in [
                ("#cpsPrompt", str(cps_prompt)),
                ("#cpsAnswer", str(cps_answer)),
                ("#pauseSentence", str(pause_sentence)),
                ("#pauseComma", str(pause_comma)),
                ("#fps", str(fps)),
            ]:
                await _fill(page, sel, val)
            await _fill(page, "#soundOn", "")  # выкл звук в headless

            # автопревью и запись до конца (или до оценки времени)
            await _start_preview(page, "chat")

            # оценка длительности
            txt_prompt = payload.get("prompt") or ""
            text = (txt_prompt + "\n" + md)
            sent = len(re.findall(r"[.!?…]", text))
            comm = len(re.findall(r"[,;:]", text))
            est_ms = int(
                1000 * think_sec
                + (len(txt_prompt) * 1000) / max(1, cps_prompt)
                + (len(md) * 1000) / max(1, cps_answer)
                + sent * pause_sentence
                + comm * pause_comma
                + 1500  # хвост после печати
            )
            duration_ms = max(3000, min(est_ms, 120000))  # 3с..120с

            try:
                await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 2000)
            except PWTimeoutError:
                await page.wait_for_timeout(duration_ms)

            video = page.video
            await _dump_webm_logs(page)
            await context.close(); await browser.close()
            src = Path(await video.path())  # type: ignore[arg-type]
            dst = out_dir / _outfile_name(idx, job)
            src.replace(dst)
            _report_webm_validation(dst)
            return dst

        else:  # ABC
            images = payload.get("images") or []
            if not (isinstance(images, list) and len(images) >= 3):
                raise ValueError("Для abc нужно 3 файла в payload.images")
            def _abs(p: str) -> Path:
                pt = Path(p)
                return pt if pt.is_absolute() else (REPO_ROOT / p).resolve()
            await _set_file(page, "#fA", _abs(images[0]))
            await _set_file(page, "#fB", _abs(images[1]))
            await _set_file(page, "#fC", _abs(images[2]))

            got = False
            try:
                started = await _try_export(page, ["exportWebM"], ["#exportBtn","#btnExport","#export"])
                if started:
                    async with page.expect_download(timeout=120000) as dl:
                        pass
                    download = await dl.value
                    dst = out_dir / _outfile_name(idx, job)
                    await download.save_as(dst.as_posix())
                    await _dump_webm_logs(page)
                    _report_webm_validation(dst)
                    got = True
            except Exception:
                got = False

            if not got:
                await _start_preview(page, "abc")
                duration_ms = max(500, job.durationSec * 1000)
                try:
                    await page.wait_for_function("() => window.__CLIP_DONE__ === true", timeout=duration_ms + 3000)
                except PWTimeoutError:
                    await page.wait_for_timeout(duration_ms)
                video = page.video
                await _dump_webm_logs(page)
                await context.close(); await browser.close()
                src = Path(await video.path())  # type: ignore[arg-type]
                dst = out_dir / _outfile_name(idx, job)
                src.replace(dst)
                _report_webm_validation(dst)
                return dst

            await context.close(); await browser.close()
            return out_dir / _outfile_name(idx, job)

async def render_batch(batch: Batch, out_dir: Path) -> Path:
    validate_batch(batch)
    out_dir.mkdir(parents=True, exist_ok=True)

    outs: List[Path] = []
    for i, job in enumerate(batch.jobs, start=1):
        f = await render_job(i, job, batch.output, out_dir)
        outs.append(f)

    zip_path = out_dir / "clips.zip"
    with ZipFile(zip_path, "w", ZIP_DEFLATED) as zf:
        for f in outs:
            zf.write(f, arcname=f.name)
    return zip_path




