"""Vosk speech recognition worker."""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

LOW_LATENCY_BLOCKSIZE = 128
LOW_LATENCY_QUEUE_BLOCKS = 4
DEFAULT_MAX_ALTERNATIVES = 0
FAST_ENDPOINTER_START_MAX_SECONDS = 0.25
FAST_ENDPOINTER_END_SECONDS = 0.08
FAST_ENDPOINTER_MAX_SECONDS = 1.2
_MODEL_CACHE: dict[str, Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def list_microphones() -> list[tuple[str, str]]:
    pulse_microphones = _list_pulse_microphones()
    if pulse_microphones:
        return pulse_microphones

    try:
        import sounddevice as sd
    except Exception:
        return []

    raw_microphones: list[tuple[int, str, str, bool]] = []
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        default_input = sd.default.device[0]
    except Exception:
        return []

    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) > 0:
            name = str(device.get("name", f"Input {index}"))
            hostapi_index = int(device.get("hostapi", -1))
            hostapi = ""
            if 0 <= hostapi_index < len(hostapis):
                hostapi = str(hostapis[hostapi_index].get("name", ""))
            raw_microphones.append((index, name, hostapi, index == default_input))

    def is_monitor(name: str) -> bool:
        return "monitor" in name.lower()

    def is_obscure_alsa_endpoint(name: str) -> bool:
        prefix = name.lower().split(":", 1)[0].strip()
        hidden_prefixes = {
            "dmix",
            "front",
            "hdmi",
            "iec958",
            "lavrate",
            "samplerate",
            "speexrate",
            "spdif",
            "sysdefault",
            "upmix",
            "vdownmix",
        }
        return prefix in hidden_prefixes or prefix.startswith("surround")

    def is_preferred(index: int, name: str, hostapi: str, is_default: bool) -> bool:
        combined = f"{name} {hostapi}".lower()
        return (
            is_default
            or "pipewire" in combined
            or "pulse" in combined
            or name.lower().strip() == "default"
        )

    preferred = [
        _format_microphone(index, name, hostapi, is_default)
        for index, name, hostapi, is_default in raw_microphones
        if (
            not is_monitor(name)
            and not is_obscure_alsa_endpoint(name)
            and is_preferred(index, name, hostapi, is_default)
        )
    ]
    if preferred:
        return _dedupe_microphones(preferred)

    fallback = [
        _format_microphone(index, name, hostapi, is_default)
        for index, name, hostapi, is_default in raw_microphones
        if not is_monitor(name) and not is_obscure_alsa_endpoint(name)
    ]
    if fallback:
        return _dedupe_microphones(fallback)

    emergency_fallback = [
        _format_microphone(index, name, hostapi, is_default)
        for index, name, hostapi, is_default in raw_microphones
        if not is_monitor(name)
    ]
    return _dedupe_microphones(emergency_fallback)


def _list_pulse_microphones() -> list[tuple[str, str]]:
    sources = _pactl_sources_json()
    if not sources:
        sources = _pactl_sources_text()
    if not sources:
        return []

    default_source = _pactl_default_source()
    microphones: list[tuple[str, str]] = []

    for source in sources:
        name = str(source.get("name") or "").strip()
        if not name or _is_monitor_source(source):
            continue

        description = str(source.get("description") or name).strip()
        label = description
        if name == default_source:
            label = f"{description} (GNOME default)"
        microphones.append((f"pulse:{name}", label))

    return _dedupe_microphones(microphones)


def _pactl_sources_json() -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(
            ["pactl", "-f", "json", "list", "sources"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
        sources = json.loads(output)
    except Exception:
        return []

    if not isinstance(sources, list):
        return []

    normalized: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        properties = source.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        normalized.append(
            {
                "name": source.get("name"),
                "description": (
                    properties.get("node.description")
                    or properties.get("device.description")
                    or source.get("description")
                ),
                "monitor_of_sink": source.get("monitor_of_sink"),
                "properties": properties,
            }
        )
    return normalized


def _pactl_sources_text() -> list[dict[str, Any]]:
    try:
        output = subprocess.check_output(
            ["pactl", "list", "sources"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except Exception:
        return []

    sources: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Source #"):
            if current is not None:
                sources.append(current)
            current = {"properties": {}}
            continue
        if current is None:
            continue

        if line.startswith("Name:"):
            current["name"] = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            current["description"] = line.split(":", 1)[1].strip()
        elif line.startswith("Monitor of Sink:"):
            current["monitor_of_sink"] = line.split(":", 1)[1].strip()
        elif "=" in line:
            key, value = line.split("=", 1)
            current["properties"][key.strip()] = value.strip().strip('"')

    if current is not None:
        sources.append(current)

    for source in sources:
        properties = source.get("properties")
        if isinstance(properties, dict):
            source["description"] = (
                properties.get("node.description")
                or properties.get("device.description")
                or source.get("description")
            )
    return sources


def _pactl_default_source() -> str | None:
    try:
        output = subprocess.check_output(
            ["pactl", "get-default-source"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except Exception:
        return None
    return output.strip() or None


def _is_monitor_source(source: dict[str, Any]) -> bool:
    name = str(source.get("name") or "").lower()
    description = str(source.get("description") or "").lower()
    monitor_of_sink = str(source.get("monitor_of_sink") or "").strip().lower()
    properties = source.get("properties")
    media_class = ""
    if isinstance(properties, dict):
        media_class = str(properties.get("media.class") or "").lower()

    return (
        name.endswith(".monitor")
        or "monitor" in description
        or (monitor_of_sink not in {"", "n/a", "none", "null"})
        or media_class == "audio/sink"
    )


def _format_microphone(index: int, name: str, hostapi: str, is_default: bool) -> tuple[str, str]:
    label = name
    if hostapi:
        label = f"{name} [{hostapi}]"
    if is_default:
        label = f"Default - {label}"
    return f"sd:{index}", label


def _dedupe_microphones(microphones: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for microphone_id, label in microphones:
        key = " ".join(label.lower().replace("default - ", "").split())
        if key in seen:
            continue
        seen.add(key)
        deduped.append((microphone_id, label))
    return deduped


class VoiceRecognizer:
    def __init__(
        self,
        model_path: Path,
        microphone_id: str | None,
        phrases: list[str],
        on_heard: Callable[[dict[str, Any]], None],
        on_error: Callable[[str], None],
        on_status: Callable[[str], None],
        on_volume: Callable[[float], None] | None = None,
        on_debug: Callable[[str], None] | None = None,
        max_alternatives: int = DEFAULT_MAX_ALTERNATIVES,
    ) -> None:
        self.model_path = model_path
        self.microphone_id = microphone_id or "default"
        self.phrases = phrases
        self.on_heard = on_heard
        self.on_error = on_error
        self.on_status = on_status
        self.on_volume = on_volume
        self.on_debug = on_debug
        self.max_alternatives = _clamped_int(max_alternatives, 0, 10, DEFAULT_MAX_ALTERNATIVES)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        pulse_source = _pulse_source_from_microphone_id(self.microphone_id)
        previous_pulse_source = os.environ.get("PULSE_SOURCE")
        if pulse_source:
            os.environ["PULSE_SOURCE"] = pulse_source

        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
            try:
                from vosk import EndpointerMode
            except Exception:
                EndpointerMode = None
        except Exception as exc:
            self.on_error(f"Speech dependencies are missing: {exc}")
            _restore_pulse_source(previous_pulse_source, pulse_source)
            return

        if not self.model_path.exists():
            self.on_error(f"Vosk model was not found: {self.model_path}")
            _restore_pulse_source(previous_pulse_source, pulse_source)
            return

        if not self.phrases:
            self.on_error("The active profile has no spoken phrases")
            _restore_pulse_source(previous_pulse_source, pulse_source)
            return

        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=LOW_LATENCY_QUEUE_BLOCKS)
        last_volume_emit = 0.0

        def audio_callback(indata, frames, time_info, status) -> None:
            nonlocal last_volume_emit
            if status:
                self.on_status(str(status))
            audio_bytes = bytes(indata)
            try:
                audio_queue.put_nowait(audio_bytes)
            except queue.Full:
                try:
                    audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    audio_queue.put_nowait(audio_bytes)
                except queue.Full:
                    pass

            now = time.monotonic()
            if self.on_volume is not None and now - last_volume_emit >= 0.05:
                last_volume_emit = now
                self.on_volume(_audio_level(audio_bytes))

        try:
            SetLogLevel(-1)
            self.on_status("Loading speech model...")
            model = _get_model(Model, self.model_path)
            grammar = json.dumps(self.phrases)
            microphone_index = _sounddevice_index_for_microphone_id(sd, self.microphone_id)
            if pulse_source:
                self.on_status(f"Using GNOME microphone: {pulse_source}")
            self._listen_with_sample_rate_fallback(
                sd,
                KaldiRecognizer,
                model,
                grammar,
                audio_callback,
                audio_queue,
                microphone_index,
                EndpointerMode,
            )
        except Exception as exc:
            self.on_error(f"Speech recognition failed: {exc}")
        finally:
            _restore_pulse_source(previous_pulse_source, pulse_source)
            self.on_status("Paused")

    def _listen_with_sample_rate_fallback(
        self,
        sounddevice_module,
        recognizer_class,
        model,
        grammar: str,
        audio_callback,
        audio_queue: queue.Queue[bytes],
        microphone_index: int | None,
        endpointer_mode,
    ) -> None:
        last_error: Exception | None = None

        for sample_rate in _sample_rate_candidates(sounddevice_module, microphone_index):
            if self._stop.is_set():
                return
            try:
                self.on_status(f"Trying microphone at {sample_rate} Hz")
                recognizer = recognizer_class(model, sample_rate, grammar)
                _configure_vosk_recognizer(recognizer, self.max_alternatives, endpointer_mode)
                self._emit_debug(
                    "Recognition debug: low-latency audio "
                    f"block={LOW_LATENCY_BLOCKSIZE}, queue={LOW_LATENCY_QUEUE_BLOCKS}, "
                    f"maxAlternatives={self.max_alternatives}, "
                    f"endpointerEnd={FAST_ENDPOINTER_END_SECONDS:.2f}s"
                )

                with sounddevice_module.RawInputStream(
                    samplerate=sample_rate,
                    blocksize=LOW_LATENCY_BLOCKSIZE,
                    dtype="int16",
                    channels=1,
                    latency="low",
                    callback=audio_callback,
                    device=microphone_index,
                ):
                    self.on_status(f"Listening at {sample_rate} Hz")
                    while not self._stop.is_set():
                        try:
                            data = audio_queue.get(timeout=0.005)
                        except queue.Empty:
                            continue

                        if recognizer.AcceptWaveform(data):
                            raw_result = json.loads(recognizer.Result())
                            result = parse_vosk_result(raw_result)
                            if result["text"]:
                                self._emit_debug(vosk_result_debug_summary(result))
                                self.on_heard(result)
                    return
            except Exception as exc:
                last_error = exc
                self.on_status(f"{sample_rate} Hz failed: {exc}")

        raise RuntimeError(f"No usable microphone sample rate found. Last error: {last_error}")

    def _emit_debug(self, message: str) -> None:
        if self.on_debug is not None and message:
            self.on_debug(message)


class MicLevelMonitor:
    """Lightweight microphone meter that does not run speech recognition."""

    def __init__(
        self,
        microphone_id: str | None,
        on_volume: Callable[[float], None],
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> None:
        self.microphone_id = microphone_id or "default"
        self.on_volume = on_volume
        self.on_error = on_error
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        pulse_source = _pulse_source_from_microphone_id(self.microphone_id)
        previous_pulse_source = os.environ.get("PULSE_SOURCE")
        if pulse_source:
            os.environ["PULSE_SOURCE"] = pulse_source

        try:
            import sounddevice as sd
        except Exception as exc:
            self._emit_error(f"Microphone level monitor unavailable: {exc}")
            _restore_pulse_source(previous_pulse_source, pulse_source)
            return

        last_error: Exception | None = None
        last_volume_emit = 0.0

        def audio_callback(indata, frames, time_info, status) -> None:
            nonlocal last_volume_emit
            if status:
                self._emit_status(str(status))
            now = time.monotonic()
            if now - last_volume_emit < 0.05:
                return
            last_volume_emit = now
            self.on_volume(_audio_level(bytes(indata)))

        try:
            microphone_index = _sounddevice_index_for_microphone_id(sd, self.microphone_id)
            for sample_rate in _sample_rate_candidates(sd, microphone_index):
                if self._stop.is_set():
                    return
                try:
                    with sd.RawInputStream(
                        samplerate=sample_rate,
                        blocksize=LOW_LATENCY_BLOCKSIZE,
                        dtype="int16",
                        channels=1,
                        latency="low",
                        callback=audio_callback,
                        device=microphone_index,
                    ):
                        self._emit_status(f"Mic level monitor active at {sample_rate} Hz")
                        while not self._stop.is_set():
                            time.sleep(0.05)
                        return
                except Exception as exc:
                    last_error = exc

            if not self._stop.is_set():
                raise RuntimeError(f"No usable microphone sample rate found. Last error: {last_error}")
        except Exception as exc:
            self._emit_error(f"Microphone level monitor failed: {exc}")
        finally:
            _restore_pulse_source(previous_pulse_source, pulse_source)

    def _emit_status(self, message: str) -> None:
        if self.on_status is not None and message:
            self.on_status(message)

    def _emit_error(self, message: str) -> None:
        if self.on_error is not None and message:
            self.on_error(message)


def _configure_vosk_recognizer(recognizer, max_alternatives: int, endpointer_mode) -> None:
    for method_name in ("SetWords",):
        method = getattr(recognizer, method_name, None)
        if callable(method):
            try:
                method(True)
            except Exception:
                pass

    method = getattr(recognizer, "SetMaxAlternatives", None)
    if callable(method):
        try:
            method(max(0, int(max_alternatives)))
        except Exception:
            pass

    method = getattr(recognizer, "SetEndpointerMode", None)
    if callable(method) and endpointer_mode is not None:
        try:
            method(endpointer_mode.SHORT)
        except Exception:
            pass

    method = getattr(recognizer, "SetEndpointerDelays", None)
    if callable(method):
        try:
            method(
                FAST_ENDPOINTER_START_MAX_SECONDS,
                FAST_ENDPOINTER_END_SECONDS,
                FAST_ENDPOINTER_MAX_SECONDS,
            )
        except Exception:
            pass


def parse_vosk_result(raw_result: dict[str, Any]) -> dict[str, Any]:
    alternatives: list[dict[str, Any]] = []
    raw_alternatives = raw_result.get("alternatives")

    if isinstance(raw_alternatives, list):
        for index, alternative in enumerate(raw_alternatives):
            if not isinstance(alternative, dict):
                continue
            text = str(alternative.get("text", "")).strip()
            if not text:
                continue
            alternatives.append(
                {
                    "index": index,
                    "text": text,
                    "confidence": _coerce_confidence(
                        alternative.get("confidence", alternative.get("conf"))
                    ),
                    "score": _coerce_float(alternative.get("confidence", alternative.get("conf"))),
                    "words": _coerce_words(alternative.get("result")),
                }
            )
    else:
        text = str(raw_result.get("text", "")).strip()
        if text:
            alternatives.append(
                {
                    "index": 0,
                    "text": text,
                    "confidence": _average_word_confidence(_coerce_words(raw_result.get("result"))),
                    "words": _coerce_words(raw_result.get("result")),
                }
            )

    return {
        "text": alternatives[0]["text"] if alternatives else "",
        "alternatives": alternatives,
        "raw": raw_result,
    }


def vosk_result_debug_summary(result: dict[str, Any]) -> str:
    alternatives = result.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        return ""

    primary = alternatives[0]
    text = str(primary.get("text", "")).strip()
    confidence = _coerce_float(primary.get("confidence"))
    words = primary.get("words") if isinstance(primary.get("words"), list) else []
    if confidence is None:
        confidence = _average_word_confidence(words)

    if confidence is None:
        score = _coerce_float(primary.get("score"))
        if score is not None:
            return f"Recognition debug: Vosk final '{text}' score={score:.2f}; confidence unavailable"
        return f"Recognition debug: Vosk final '{text}' confidence unavailable"

    word_parts = []
    for item in words[:8]:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip()
        word_confidence = _coerce_float(item.get("confidence"))
        if word and word_confidence is not None:
            word_parts.append(f"{word}={word_confidence:.2f}")
    word_summary = ", ".join(word_parts)
    if len(words) > 8:
        word_summary = f"{word_summary}, ..."

    suffix = f"; words: {word_summary}" if word_summary else ""
    return f"Recognition debug: Vosk final '{text}' confidence={confidence:.2f}{suffix}"


def _coerce_words(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    words: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        word = str(item.get("word", "")).strip()
        if not word:
            continue
        coerced = {"word": word}
        confidence = _coerce_float(item.get("conf"))
        if confidence is not None:
            coerced["confidence"] = confidence
        for key in ("start", "end"):
            value_float = _coerce_float(item.get(key))
            if value_float is not None:
                coerced[key] = value_float
        words.append(coerced)
    return words


def _average_word_confidence(words: list[dict[str, Any]]) -> float | None:
    confidences = [
        confidence
        for word in words
        if (confidence := _coerce_float(word.get("confidence"))) is not None
    ]
    if not confidences:
        return None
    return sum(confidences) / len(confidences)


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_confidence(value: Any) -> float | None:
    confidence = _coerce_float(value)
    if confidence is None or confidence < 0.0 or confidence > 1.0:
        return None
    return confidence


def _clamped_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _pulse_source_from_microphone_id(microphone_id: str | None) -> str | None:
    if not microphone_id or not microphone_id.startswith("pulse:"):
        return None
    return microphone_id.split(":", 1)[1] or None


def _restore_pulse_source(previous_pulse_source: str | None, active_pulse_source: str | None) -> None:
    if active_pulse_source is None:
        return
    if previous_pulse_source is None:
        os.environ.pop("PULSE_SOURCE", None)
    else:
        os.environ["PULSE_SOURCE"] = previous_pulse_source


def _sounddevice_index_for_microphone_id(sounddevice_module, microphone_id: str | None) -> int | None:
    if microphone_id in (None, "default"):
        return _preferred_pulse_portaudio_device(sounddevice_module)

    if microphone_id and microphone_id.startswith("sd:"):
        try:
            return int(microphone_id.split(":", 1)[1])
        except ValueError:
            return None

    if microphone_id and microphone_id.startswith("pulse:"):
        pulse_index = _preferred_pulse_portaudio_device(sounddevice_module)
        if pulse_index is not None:
            return pulse_index

    return None


def _preferred_pulse_portaudio_device(sounddevice_module) -> int | None:
    try:
        devices = sounddevice_module.query_devices()
        hostapis = sounddevice_module.query_hostapis()
    except Exception:
        return None

    fallback: int | None = None
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue

        name = str(device.get("name", ""))
        hostapi_index = int(device.get("hostapi", -1))
        hostapi = ""
        if 0 <= hostapi_index < len(hostapis):
            hostapi = str(hostapis[hostapi_index].get("name", ""))

        combined = f"{name} {hostapi}".lower()
        if "pulse" in combined or "pipewire" in combined:
            return index
        if fallback is None and name.lower().strip() == "default":
            fallback = index

    return fallback


def _sample_rate_candidates(sounddevice_module, microphone_index: int | None) -> list[int]:
    rates = [
        16000,
        _device_sample_rate(sounddevice_module, microphone_index),
        _global_sample_rate(sounddevice_module),
        48000,
        44100,
        32000,
        22050,
        8000,
    ]
    return _unique_rates([rate for rate in rates if rate is not None])


def _get_model(model_class, model_path: Path):
    cache_key = str(model_path.expanduser().resolve())
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(cache_key)
        if model is None:
            model = model_class(cache_key)
            _MODEL_CACHE[cache_key] = model
        return model


def _unique_rates(sample_rates: list[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for sample_rate in sample_rates:
        if sample_rate <= 0 or sample_rate in seen:
            continue
        seen.add(sample_rate)
        unique.append(sample_rate)
    return unique


def _device_sample_rate(sounddevice_module, microphone_index: int | None) -> int:
    try:
        if microphone_index is None:
            device = sounddevice_module.query_devices(kind="input")
        else:
            device = sounddevice_module.query_devices(microphone_index, "input")
        sample_rate = _coerce_sample_rate(device.get("default_samplerate"))
    except Exception:
        sample_rate = None

    return sample_rate or 16000


def _global_sample_rate(sounddevice_module) -> int | None:
    try:
        return _coerce_sample_rate(sounddevice_module.default.samplerate)
    except Exception:
        return None


def _coerce_sample_rate(value) -> int | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value in (None, "None"):
        return None

    try:
        sample_rate = int(round(float(value)))
    except Exception:
        return None

    if sample_rate <= 0:
        return None
    return sample_rate


def _audio_level(audio_bytes: bytes) -> float:
    if not audio_bytes:
        return 0.0

    try:
        samples = memoryview(audio_bytes).cast("h")
    except (TypeError, ValueError):
        return 0.0

    if not samples:
        return 0.0

    step = max(1, len(samples) // 512)
    total = 0
    count = 0
    for sample in samples[::step]:
        total += int(sample) * int(sample)
        count += 1

    if count == 0:
        return 0.0

    rms = math.sqrt(total / count)
    return max(0.0, min(1.0, rms / 9000.0))
