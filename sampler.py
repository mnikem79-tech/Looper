"""
MIDI-сэмплер: играет звуки из папки sounds/ чистым numpy (без внешних
зависимостей, кроме уже нужного numpy).

Звуки запечены со звуковой библиотеки GeneralUser GS (см. tools/bake_sounds.py)
как моно WAV 44100 Гц по 4 корневым нотам на инструмент; между ними и выше/
ниже — питч-шифт ресемплингом. Ударные — одношоты по клавишам GM-кита.

Потокобезопасно: note()/sustain()/set_instrument() зовутся из MIDI-потока,
render() — из аудиопотока.
"""
from __future__ import annotations

import json
import threading
import wave
from pathlib import Path

import numpy as np

ATTACK_S = 0.005
RELEASE_S = 0.12
QUICK_RELEASE_S = 0.04
MAX_VOICES = 24


class _Voice:
    __slots__ = ("data", "step", "pos", "amp", "state", "env", "attack_step",
                 "attack_left", "release_step", "release_left", "note",
                 "sustained", "one_shot", "born")

    def __init__(self, data, step, amp, note, one_shot, born):
        self.data = data
        self.step = step
        self.pos = 0.0
        self.amp = amp
        self.note = note
        self.one_shot = one_shot
        self.born = born
        self.sustained = False
        self.state = "attack"
        self.env = 0.0
        self.attack_left = max(1, int(ATTACK_S * SR_OUT))
        self.attack_step = 1.0 / self.attack_left
        self.release_step = 0.0
        self.release_left = 0

    def start_release(self, seconds: float) -> None:
        if self.state == "release":
            return
        self.state = "release"
        self.release_left = max(1, int(seconds * SR_OUT))
        self.release_step = self.env / self.release_left


# частота вывода — ставится при создании Sampler (нужна в _Voice)
SR_OUT = 48000


class Sampler:
    def __init__(self, sounds_dir, sample_rate: int):
        global SR_OUT
        SR_OUT = int(sample_rate)
        self.sample_rate = int(sample_rate)
        self.gain = 1.0
        self.ok = False
        self.instruments: list = []
        self.index = 0
        self._lock = threading.Lock()
        self._voices: list = []
        self._sustain = False
        self._born = 0
        self._buf = np.zeros(256, dtype=np.float32)
        self._load(Path(sounds_dir))

    # ------------------------------------------------------------------
    # Загрузка банка
    # ------------------------------------------------------------------
    def _load(self, sounds_dir: Path) -> None:
        manifest = sounds_dir / "manifest.json"
        if not manifest.is_file():
            return
        try:
            bank = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            return
        src_rate = int(bank.get("sample_rate", 44100))
        self._rate_ratio = self.sample_rate / src_rate
        for inst in bank.get("instruments", []):
            entry = {
                "name": inst.get("name", "?"),
                "group": inst.get("group", "Прочее"),
                "program": inst.get("program"),
                "roots": {},      # key -> (массив, корневая нота)
                "drums": {},      # key -> массив
            }
            try:
                if "drums" in inst:
                    for key, fname in inst["drums"].items():
                        entry["drums"][int(key)] = self._read_wav(sounds_dir / fname)
                else:
                    for key, fname in inst["roots"].items():
                        entry["roots"][int(key)] = self._read_wav(sounds_dir / fname)
            except Exception:
                continue
            self.instruments.append(entry)
        self.ok = bool(self.instruments)

    @staticmethod
    def _read_wav(path: Path) -> np.ndarray:
        with wave.open(str(path), "rb") as w:
            raw = w.readframes(w.getnframes())
        return (np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0)

    # ------------------------------------------------------------------
    # Управление
    # ------------------------------------------------------------------
    def groups(self) -> list:
        out = []
        for inst in self.instruments:
            if inst["group"] not in out:
                out.append(inst["group"])
        return out

    def names_in_group(self, group: str) -> list:
        """[(bank_index, название), ...] в порядке банка."""
        return [(i, inst["name"]) for i, inst in enumerate(self.instruments)
                if inst["group"] == group]

    def set_instrument(self, index: int) -> None:
        with self._lock:
            if 0 <= index < len(self.instruments):
                self.index = index
                self._voices.clear()

    def set_gain(self, gain: float) -> None:
        self.gain = float(gain)

    def sustain(self, down: bool) -> None:
        with self._lock:
            self._sustain = bool(down)
            if not down:
                for v in self._voices:
                    if v.sustained:
                        v.sustained = False
                        v.start_release(RELEASE_S)

    def all_off(self) -> None:
        with self._lock:
            self._voices.clear()
            self._sustain = False

    def note(self, key: int, vel: int) -> None:
        """vel > 0 — нажатие; vel == 0 — отпускание."""
        with self._lock:
            if vel <= 0:
                self._note_off(int(key))
            else:
                self._note_on(int(key), min(127, int(vel)) / 127.0)

    # ------------------------------------------------------------------
    # Голоса
    # ------------------------------------------------------------------
    def _note_on(self, key: int, vel: float) -> None:
        if not self.instruments:
            return
        inst = self.instruments[self.index]
        self._born += 1

        # повторное нажатие той же ноты — старый голос на быстрый спад
        for v in self._voices:
            if v.note == key and v.state != "release":
                v.start_release(QUICK_RELEASE_S)

        if inst["drums"]:
            data = inst["drums"].get(key)
            if data is None:
                return
            step = self._rate_ratio          # без питч-шифта
            self._add(_Voice(data, step, vel ** 1.3, key, True, self._born))
            return

        # ближайшая корневая нота
        roots = inst["roots"]
        if not roots:
            return
        root = min(roots, key=lambda k: abs(k - key))
        data = roots[root]
        step = (2.0 ** ((key - root) / 12.0)) * self._rate_ratio
        self._add(_Voice(data, step, vel ** 1.3, key, False, self._born))

    def _add(self, v: _Voice) -> None:
        if len(self._voices) >= MAX_VOICES:
            oldest = min(self._voices,
                         key=lambda x: (x.state == "release", x.born))
            oldest.start_release(QUICK_RELEASE_S)
            if len(self._voices) >= MAX_VOICES:
                self._voices.remove(oldest)
        self._voices.append(v)

    def _note_off(self, key: int) -> None:
        for v in self._voices:
            if v.note == key and v.state != "release":
                if self._sustain and not v.one_shot:
                    v.sustained = True
                else:
                    v.start_release(RELEASE_S)

    # ------------------------------------------------------------------
    # Рендер
    # ------------------------------------------------------------------
    def render(self, n: int) -> np.ndarray:
        """Моно-микс всех голосов на n сэмплов (внутренний буфер)."""
        if n > self._buf.shape[0]:
            self._buf = np.zeros(n, dtype=np.float32)
        out = self._buf[:n]
        out[:] = 0.0
        with self._lock:
            if self._voices:
                alive = []
                for v in self._voices:
                    if self._render_voice(v, out, n):
                        alive.append(v)
                self._voices = alive
        if self.gain != 1.0:
            out *= self.gain
        return out

    def _render_voice(self, v: _Voice, out: np.ndarray, n: int) -> bool:
        data = v.data
        end = len(data) - 1
        if v.pos >= end:
            return False
        max_take = int((end - v.pos) / v.step) + 1
        take = min(n, max_take)
        if take <= 0:
            return False

        env = self._voice_env(v, take)
        if env is None:
            return False

        p = v.pos + v.step * np.arange(take, dtype=np.float64)
        i0 = np.minimum(p.astype(np.int64), end - 1)   # клип к границам
        frac = (p - i0).astype(np.float32)
        seg = data[i0] + (data[i0 + 1] - data[i0]) * frac
        out[:take] += seg * env * v.amp

        v.pos += v.step * take
        return v.pos < end

    def _voice_env(self, v: _Voice, take: int) -> np.ndarray | None:
        env = np.empty(take, dtype=np.float32)
        i = 0
        if v.state == "attack":
            k = min(take, v.attack_left)
            env[:k] = v.env + v.attack_step * np.arange(1, k + 1, dtype=np.float32)
            v.env = float(min(1.0, env[k - 1]))
            v.attack_left -= k
            i = k
            if v.attack_left <= 0:
                v.state = "hold"
                v.env = 1.0
        if i < take and v.state == "hold":
            env[i:] = v.env
            i = take
        if i < take and v.state == "release":
            k = min(take - i, v.release_left)
            seg = v.env + v.release_step * np.arange(1, k + 1, dtype=np.float32)
            seg = np.maximum(seg, 0.0)
            env[i:i + k] = seg
            v.env = float(seg[k - 1]) if k else v.env
            v.release_left -= k
            i += k
            if i < take:
                env[i:] = 0.0
                v.release_left = 0
            if v.release_left <= 0:
                return None if i == 0 else env
        return env
