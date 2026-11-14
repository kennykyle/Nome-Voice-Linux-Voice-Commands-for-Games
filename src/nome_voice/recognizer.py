"""Vosk speech recognition worker.

How a command gets decided (see CommandRecognitionSession):

* Two Vosk recognizers hear the same audio.
* The quick one is asked for its result as soon as the speaker has been silent for the
  response-speed delay (150/250/400 ms). If that result is a complete command that no
  longer phrase starts with, it fires right away.
* The main one uses Vosk's normal end-of-speech detection and catches everything else:
  phrases said with pauses, commands that are the start of longer commands, and noisy
  rooms where silence can't be detected.
* Whichever path fires resets the other, so one utterance fires at most one command.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .guard_words import DEFAULT_GUARD_LEVEL, build_guard_words
from .matcher import is_unambiguous_command, normalize_phrase
from .paths import cache_dir

BLOCK_MS = 10
AUDIO_QUEUE_SECONDS = 2.0
DEFAULT_MAX_ALTERNATIVES = 0
DEFAULT_RESPONSE_SPEED = "balanced"
# silenceMs: how long the speaker must be quiet before a complete command fires.
# mainEndpoint: Vosk end-of-speech rules (rule2/3/4 trailing silence, seconds) for the
# main recognizer; None keeps the model's own settings.
RESPONSE_SPEEDS: dict[str, dict[str, Any]] = {
    "fast": {"silenceMs": 150, "mainEndpoint": None},
    "balanced": {"silenceMs": 250, "mainEndpoint": None},
    "patient": {"silenceMs": 400, "mainEndpoint": (1.0, 1.25, 1.5)},
}
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


class EndOfSpeechDetector:
    """Energy-based voice activity tracking with an adaptive noise floor.

    Only used to notice that the speaker has gone quiet; Vosk still decides what was
    said. In loud rooms where silence can't be detected, the main recognizer's own
    end-of-speech detection takes over, so this can only make things faster.
    """

    def __init__(
        self,
        frame_ms: int = BLOCK_MS,
        margin_db: float = 12.0,
        min_speech_db: float = -60.0,
        speech_start_ms: int = 60,
    ) -> None:
        self.frame_ms = frame_ms
        self.margin_db = margin_db
        self.min_speech_db = min_speech_db
        self.speech_start_ms = speech_start_ms
        self.floor_db = -60.0
        self.in_speech = False
        self.voiced_ms = 0
        self.silent_ms = 0

    def update(self, audio_bytes: bytes) -> None:
        level_db = _level_db(audio_bytes)
        if level_db < self.floor_db:
            self.floor_db = level_db
        else:
            rise = 0.001 if self.in_speech else 0.02
            self.floor_db += (level_db - self.floor_db) * rise
        if level_db > self.floor_db + self.margin_db and level_db > self.min_speech_db:
            self.voiced_ms += self.frame_ms
            self.silent_ms = 0
            if self.voiced_ms >= self.speech_start_ms:
                self.in_speech = True
        else:
            self.silent_ms += self.frame_ms
            if self.silent_ms >= 40:
                self.voiced_ms = 0

    def end_utterance(self) -> None:
        self.in_speech = False
        self.voiced_ms = 0
        self.silent_ms = 0


class CommandRecognitionSession:
    """Feed audio blocks to a quick and a main Vosk recognizer; return final results.

    feed() returns a list of (path, raw_vosk_result, ended_ms) tuples, where path is
    "quick" or "main" and ended_ms is how long the speaker had been quiet when the
    result was decided (None when silence couldn't be measured).
    """

    def __init__(
        self,
        main_recognizer,
        quick_recognizer,
        command_phrases: list[str],
        silence_ms: int,
        frame_ms: int = BLOCK_MS,
    ) -> None:
        self.main = main_recognizer
        self.quick = quick_recognizer
        self.command_phrases = {normalize_phrase(phrase) for phrase in command_phrases}
        self.command_phrases.discard("")
        self.silence_ms = max(frame_ms, int(silence_ms))
        self.vad = EndOfSpeechDetector(frame_ms)
        self._checked = False

    def feed(self, audio_bytes: bytes) -> list[tuple[str, dict[str, Any], int | None]]:
        self.vad.update(audio_bytes)
        main_final = self.main.AcceptWaveform(audio_bytes)
        quick_final = self.quick.AcceptWaveform(audio_bytes)

        if main_final:
            raw = json.loads(self.main.Result())
            ended_ms = self._ended_ms()
            self.quick.Reset()
            self._end_utterance()
            return [("main", raw, ended_ms)] if _raw_text(raw) else []

        if quick_final:
            raw = json.loads(self.quick.Result())
            if is_unambiguous_command(_raw_text(raw), self.command_phrases):
                return [self._fire_quick(raw)]

        if self.vad.voiced_ms:
            self._checked = False
        if self.vad.in_speech and not self._checked and self.vad.silent_ms >= self.silence_ms:
            self._checked = True
            raw = json.loads(self.quick.FinalResult())
            if is_unambiguous_command(_raw_text(raw), self.command_phrases):
                return [self._fire_quick(raw)]
        return []

    def _fire_quick(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any], int | None]:
        ended_ms = self._ended_ms()
        self.main.Reset()
        self._end_utterance()
        return ("quick", raw, ended_ms)

    def _ended_ms(self) -> int | None:
        return self.vad.silent_ms if self.vad.in_speech and self.vad.silent_ms else None

    def _end_utterance(self) -> None:
        self.vad.end_utterance()
        self._checked = False


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
        response_speed: str = DEFAULT_RESPONSE_SPEED,
        guard_level: str = DEFAULT_GUARD_LEVEL,
    ) -> None:
        self.model_path = model_path
        self.microphone_id = microphone_id or "default"
        # command phrases; "[unk]" is always added to the grammar by the recognizer
        self.phrases = [
            normalize_phrase(phrase) for phrase in phrases if phrase and phrase != "[unk]"
        ]
        self.phrases = [phrase for phrase in dict.fromkeys(self.phrases) if phrase]
        self.on_heard = on_heard
        self.on_error = on_error
        self.on_status = on_status
        self.on_volume = on_volume
        self.on_debug = on_debug
        self.max_alternatives = _clamped_int(max_alternatives, 0, 10, DEFAULT_MAX_ALTERNATIVES)
        self.response_speed = (
            response_speed if response_speed in RESPONSE_SPEEDS else DEFAULT_RESPONSE_SPEED
        )
        self.guard_level = guard_level
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

        block_queue_size = max(4, int(AUDIO_QUEUE_SECONDS * 1000 / BLOCK_MS))
        audio_queue: queue.Queue[bytes] = queue.Queue(maxsize=block_queue_size)
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
            speed = RESPONSE_SPEEDS[self.response_speed]
            model_dir = self.model_path
            if speed["mainEndpoint"] is not None:
                try:
                    model_dir = prepare_model_variant(self.model_path, speed["mainEndpoint"])
                except Exception as exc:
                    self.on_status(f"Could not apply patient timing, using model defaults: {exc}")
                    model_dir = self.model_path
            model = _get_model(Model, model_dir)
            grammar = self._build_grammar(model)
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
                speed["silenceMs"],
            )
        except Exception as exc:
            self.on_error(f"Speech recognition failed: {exc}")
        finally:
            _restore_pulse_source(previous_pulse_source, pulse_source)
            self.on_status("Paused")

    def _build_grammar(self, model) -> str:
        find_word = getattr(model, "vosk_model_find_word", None)

        def is_known_word(word: str) -> bool:
            if not callable(find_word):
                return True
            try:
                return find_word(word) >= 0
            except Exception:
                return True

        unknown = sorted(
            {word for phrase in self.phrases for word in phrase.split() if not is_known_word(word)}
        )
        if unknown:
            self.on_status(
                "Speech model does not know these words, so phrases using them can't fire: "
                + ", ".join(unknown)
            )
        guard = build_guard_words(self.phrases, is_known_word, self.guard_level)
        self._emit_debug(
            f"Recognition debug: response={self.response_speed}, guard={self.guard_level} "
            f"({len(guard)} guard words), commands={len(self.phrases)}"
        )
        return json.dumps([*self.phrases, *guard, "[unk]"])

    def _listen_with_sample_rate_fallback(
        self,
        sounddevice_module,
        recognizer_class,
        model,
        grammar: str,
        audio_callback,
        audio_queue: queue.Queue[bytes],
        microphone_index: int | None,
        silence_ms: int,
    ) -> None:
        last_error: Exception | None = None

        for sample_rate in _sample_rate_candidates(sounddevice_module, microphone_index):
            if self._stop.is_set():
                return
            try:
                self.on_status(f"Trying microphone at {sample_rate} Hz")
                main = recognizer_class(model, sample_rate, grammar)
                quick = recognizer_class(model, sample_rate, grammar)
                for recognizer in (main, quick):
                    _configure_vosk_recognizer(recognizer, self.max_alternatives)
                session = CommandRecognitionSession(main, quick, self.phrases, silence_ms)
                blocksize = max(1, sample_rate * BLOCK_MS // 1000)

                with sounddevice_module.RawInputStream(
                    samplerate=sample_rate,
                    blocksize=blocksize,
                    dtype="int16",
                    channels=1,
                    latency="low",
                    callback=audio_callback,
                    device=microphone_index,
                ):
                    self.on_status(f"Listening at {sample_rate} Hz")
                    while not self._stop.is_set():
                        try:
                            data = audio_queue.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        for path, raw_result, ended_ms in session.feed(data):
                            result = parse_vosk_result(raw_result)
                            if not result["text"]:
                                continue
                            result["path"] = path
                            result["endedMs"] = ended_ms
                            result["decidedAt"] = time.monotonic()
                            self.on_heard(result)
                            self._emit_debug(vosk_result_debug_summary(result))
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
                        blocksize=max(1, sample_rate * BLOCK_MS // 1000),
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


def _configure_vosk_recognizer(recognizer, max_alternatives: int) -> None:
    method = getattr(recognizer, "SetWords", None)
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


def prepare_model_variant(model_path: Path, endpoint_rules: tuple[float, float, float]) -> Path:
    """Return a copy of a Vosk model folder with different end-of-speech timing.

    The vosk Python package (0.3.45, the newest release) has no API for end-of-speech
    timing; Vosk reads it from conf/model.conf when the model loads. The copy is made
    of symlinks plus one rewritten model.conf, so it costs no disk space. Older "v1"
    models without conf/model.conf can't be tuned and are returned unchanged.
    """
    source = Path(model_path).expanduser().resolve()
    conf_path = source / "conf" / "model.conf"
    if not conf_path.is_file():
        return source

    rule2, rule3, rule4 = (float(value) for value in endpoint_rules)
    overrides = {
        "--endpoint.rule2.min-trailing-silence": rule2,
        "--endpoint.rule3.min-trailing-silence": rule3,
        "--endpoint.rule4.min-trailing-silence": rule4,
    }
    lines = []
    seen = set()
    for line in conf_path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip()
        if key in overrides:
            line = f"{key}={overrides[key]}"
            seen.add(key)
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in overrides.items() if key not in seen)
    conf_text = "\n".join(lines) + "\n"

    digest = hashlib.sha1(f"{source}\n{conf_text}".encode("utf-8")).hexdigest()[:16]
    variants = cache_dir() / "model-variants"
    target = variants / f"{source.name}-{digest}"
    if (target / "conf" / "model.conf").is_file():
        return target

    variants.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=variants))
    try:
        for entry in source.iterdir():
            if entry.name != "conf":
                os.symlink(entry, staging / entry.name)
        (staging / "conf").mkdir()
        for entry in (source / "conf").iterdir():
            if entry.name != "model.conf":
                os.symlink(entry, staging / "conf" / entry.name)
        (staging / "conf" / "model.conf").write_text(conf_text, encoding="utf-8")
        try:
            os.rename(staging, target)
        except OSError:
            if not (target / "conf" / "model.conf").is_file():
                raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return target


def _raw_text(raw_result: dict[str, Any]) -> str:
    alternatives = raw_result.get("alternatives")
    if isinstance(alternatives, list) and alternatives:
        first = alternatives[0]
        if isinstance(first, dict):
            return str(first.get("text", "")).strip()
        return ""
    return str(raw_result.get("text", "")).strip()


def _level_db(audio_bytes: bytes) -> float:
    if not audio_bytes:
        return -120.0
    try:
        samples = memoryview(audio_bytes).cast("h")
    except (TypeError, ValueError):
        return -120.0
    count = len(samples)
    if not count:
        return -120.0
    total = 0
    for sample in samples:
        total += sample * sample
    rms = math.sqrt(total / count)
    if rms <= 0:
        return -120.0
    return 20.0 * math.log10(rms / 32768.0)


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
