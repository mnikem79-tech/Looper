"""
Аудиодвижок лупера.

Единый тайминг: одна «полоска тайминга» на все лупы. Цикл всегда
фиксированной длины = такты × блоки («Свободная длина» убрана). Все лупы
одной длины, играют с ОДНОЙ позиции транспорта — в разнобой не расходятся.

Транспорт:
- «Старт/Стоп» — пауза/продолжить (позиция остаётся, все вместе);
  из полного стопа — старт с начала цикла на границе такта.
- «Стоп» — всё стоп, позиция на начало, запись выключена до следующей команды.
- «Запись/Дубль» — запускает проигрывание всех лупов и пишет только выбранный.

Запись поблочно:
- «Вручную» — пишутся все блоки без учёта порога.
- «Авто по звуку» — блок пишется ЦЕЛИКОМ (включая начало и конец), если порог
  был превышен где-то в блоке; без превышения блок в луп не пишется
  (остаётся прошлый материал) и рисуется серым. Зелёный — весь блок с порогом.
- Переключение дорожки при записи: новая пишет с текущей позиции тайминга.

Метроном и драм-машина (в цикл не пишутся): при работающем транспорте —
по позиции цикла, включение на лету — со следующего такта. Драм-машина
подстраивается под темп, размер и количество тактов (см. drums.py).
Режимы звука: «duplex» или «split» (обход PortAudio -9993).
"""
import math
import os
import queue
import threading
import wave
from enum import Enum
from pathlib import Path

import numpy as np

import drums

# ASIO-поддержка в sounddevice (Windows) включается переменной окружения
# ДО импорта модуля. Колёса sounddevice >= 0.5.1 содержат обе версии DLL.
os.environ.setdefault("SD_ENABLE_ASIO", "1")

RING_CAP = 1 << 16   # кольцевой буфер входа в режиме split (~1.4 c при 48 кГц)
WAVE_BINS = 240      # точек огибающей для волноформы


def _import_sd():
    """Ленивый импорт sounddevice с фолбэком, если ASIO-DLL не загрузилась."""
    import sys

    os.environ.setdefault("SD_ENABLE_ASIO", "1")
    try:
        import sounddevice as sd
        return sd
    except Exception:
        if os.environ.get("SD_ENABLE_ASIO") == "1":
            os.environ["SD_ENABLE_ASIO"] = "0"
            for m in [m for m in list(sys.modules) if m.startswith("sounddevice")]:
                del sys.modules[m]
            import sounddevice as sd
            return sd
        raise


class SlotState(Enum):
    EMPTY = "Пусто"
    LOADED = "Готов"
    RECORDING = "Запись..."


class Slot:
    def __init__(self, capacity: int, n_blocks: int = 1):
        self.capacity = capacity
        self.data = np.zeros(capacity, dtype=np.float32)
        self.undo = np.zeros(capacity, dtype=np.float32)
        self.length = 0        # 0 = пусто; иначе = длина цикла (единая для всех)
        self.undo_length = 0
        self.playhead = 0      # позиция транспорта (общая, для GUI)
        self.state = SlotState.EMPTY
        self.has_undo = False
        self.undo_flags = np.zeros(max(1, n_blocks), dtype=bool)
        # Зелёный/серый по блокам: True — блок с порогом (или записанный вручную)
        self.block_flags = np.zeros(max(1, n_blocks), dtype=bool)
        self.muted = False      # канал приглушён (кнопка лупа; запись не трогается)
        # Огибающая и «хиты» для окраски волноформы
        self.wave_gen = 0
        self._wave_cache = None
        self._wave_hits = None
        self._wave_gen_seen = -1
        # Поблочная запись
        self.rec_active = False    # этот слот сейчас пишется
        self.rec_replace = True    # True = «Запись», False = «Дубль» (суммирование)
        self.cur_block = -1        # индекс текущего блока записи
        self.block_base = 0        # позиция цикла, с которой пишется текущий блок
        self.block_hit = False     # порог превышен в текущем блоке
        self.block_segs = []       # накопление до порога («Авто по звуку»)
        self.wait_hit = False      # «Авто»: ждём первый порог (ничего не пишем)
        self._first_block = True   # первый блок может быть неполным (старт с середины)

    def has_content(self) -> bool:
        return self.length > 0

    def reset(self) -> None:
        self.length = 0
        self.undo_length = 0
        self.playhead = 0
        self.state = SlotState.EMPTY
        self.has_undo = False
        # ВАЖНО: стираем сами сэмплы, иначе старый материал «воскресает»
        # при следующей частичной записи (length становится полным).
        self.data[:] = 0.0
        self.undo[:] = 0.0
        self.block_flags = np.zeros(max(1, len(self.block_flags)), dtype=bool)
        self.rec_active = False
        self.wait_hit = False
        self.cur_block = -1
        self.block_segs = []
        self.wave_gen += 1


class AudioEngine:
    def __init__(self, config: dict):
        self.cfg = config
        self.cmd_q: "queue.Queue[tuple]" = queue.Queue()
        self.n_slots = int(config.get("slots", 4))
        self.sample_rate = int(config.get("sample_rate", 48000))
        self.blocksize = int(config.get("blocksize", 256))
        self.monitor_gain = float(config.get("monitor_gain", 1.0))
        self.master_gain = float(config.get("master_gain", 1.0))
        self.dub_gain = float(config.get("dub_gain", 1.0))
        self.max_loop_sec = float(config.get("max_loop_sec", 180))
        self.wasapi_exclusive = bool(config.get("wasapi_exclusive", True))

        # --- темп / такты / блоки (длина цикла = такты × блоки) ---
        self.bpm = float(config.get("bpm", 120))
        self.beats_per_bar = int(config.get("beats_per_bar", 4))
        self.bars_per_block = int(config.get("bars_per_block", 4))
        self.num_blocks = int(config.get("num_blocks", 2))

        # --- запись: «Вручную» (все блоки) / «Авто по звуку» (по порогу) ---
        self.autostart = bool(config.get("autostart", False))
        self.threshold = float(config.get("threshold", 0.05))
        self._env = 0.0

        # --- метроном (звучит только при работающем транспорте) ---
        self.metronome_on = bool(config.get("metronome_on", False))
        self.metro_sound = str(config.get("metro_sound", "wood"))
        self.metro_gain = float(config.get("metro_gain", 0.5))
        self._clicks = {}
        self._click_tail = np.zeros(0, dtype=np.float32)
        self._metro_on_prev = self.metronome_on
        self._metro_skip = 0      # сколько долей пропустить (вступление с такта)

        # --- драм-машина (как метроном: только при работающем транспорте) ---
        self.drum_on = bool(config.get("drum_on", False))
        self.drum_style = str(config.get("drum_style", drums.DEFAULT_STYLE))
        self.drum_gain = float(config.get("drum_gain", 0.7))
        self._drum_on_prev = self.drum_on
        self._drum_skip = 0       # сколько 1/16 пропустить (вступление с такта)
        self._drum_tail = np.zeros(0, dtype=np.float32)
        self._drum_hits = {}      # кэш синтезированных ударов
        self._drum_events = None
        self._drum_events_key = None
        self._preview_active = False
        self._preview_pos = 0
        self._abs_samples = 0

        # --- единый транспорт ---
        # "stopped" | "running" | "paused"
        self.transport = "stopped"
        self._pos = 0             # позиция внутри цикла, сэмплы
        self._rec_armed = False   # «Авто по звуку»: ждём превышения порога

        capacity = max(int(self.sample_rate * self.max_loop_sec), self.sample_rate)
        self._capacity = capacity
        self.slots = [Slot(capacity, self.num_blocks) for _ in range(self.n_slots)]
        self.current_slot = 0

        self._block_len = 0
        self._cycle_len = 0
        self.update_grid()

        # Рабочие буферы (переиспользуются в колбэке)
        bs = self.blocksize
        self._mono_in = np.zeros(bs, dtype=np.float32)
        self._mix = np.zeros(bs, dtype=np.float32)
        self._out = np.zeros(bs, dtype=np.float32)
        self._drum_bus = np.zeros(bs, dtype=np.float32)
        self._split_in = np.zeros(bs, dtype=np.float32)

        # Кольцевой буфер для режима split
        self._ring = np.zeros(RING_CAP, dtype=np.float32)
        self._mask = RING_CAP - 1
        self._wpos = 0
        self._rpos = 0
        self._ring_lock = threading.Lock()
        self._resample_ratio = 1.0

        self.in_peak = 0.0
        self.out_peak = 0.0
        self.xruns = 0
        self.mode = ""  # "duplex" | "split" | ""
        self.exclusive = False
        self.in_api = ""
        self.out_api = ""
        self._streams = []
        self.in_device = None
        self.out_device = None

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------
    def post(self, action: str, arg=None) -> None:
        self.cmd_q.put((action, arg))

    def set_gains(self, monitor=None, master=None, dub=None) -> None:
        if monitor is not None:
            self.monitor_gain = float(monitor)
        if master is not None:
            self.master_gain = float(master)
        if dub is not None:
            self.dub_gain = float(dub)

    def fixed_total_bars(self) -> int:
        return max(1, self.bars_per_block) * max(1, self.num_blocks)

    def fixed_total_sec(self) -> float:
        spb = 60.0 / max(self.bpm, 1.0)
        return spb * self.beats_per_bar * self.fixed_total_bars()

    def bar_len_samples(self) -> float:
        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        return spb * max(1, self.beats_per_bar)

    def update_grid(self) -> None:
        """Пересчёт длины блока/цикла при смене темпа, размера, тактов или блоков.

        Темп ОБЩИЙ: он управляет и метрономом, и длительностью тактов/цикла.
        При смене темпа (или размера) уже записанный материал пересчитывается
        пропорционально (как на ленте) — метроном, сетка и материал остаются
        в одном темпе. Смена «тактов в блоке»/«блоков» материал не растягивает.
        """
        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        bar_len = max(1, int(round(spb * max(1, self.beats_per_bar))))
        block_len = max(1, int(bar_len * max(1, self.bars_per_block)))
        nb = max(1, int(self.num_blocks))
        cycle = block_len * nb
        if cycle > self._capacity:
            nb = max(1, self._capacity // block_len)
            cycle = block_len * nb
        old_bar = getattr(self, "_bar_len", 0)
        old_cycle = getattr(self, "_cycle_len", 0)
        retime = old_bar > 0 and bar_len != old_bar and old_cycle > 0
        self._bar_len = bar_len
        self._block_len = block_len
        self._cycle_len = cycle
        self.num_blocks = nb
        for s in self.slots:
            if retime and s.length > 0:
                self._retime_slot(s, old_cycle, cycle)
            if len(s.block_flags) != nb:
                flags = np.zeros(nb, dtype=bool)
                m = min(nb, len(s.block_flags))
                flags[:m] = s.block_flags[:m]
                s.block_flags = flags
                uflags = np.zeros(nb, dtype=bool)
                um = min(nb, len(s.undo_flags))
                uflags[:um] = s.undo_flags[:um]
                s.undo_flags = uflags
            if s.length > 0:
                s.length = cycle
            s.wave_gen += 1
        if self._pos >= cycle:
            self._pos = 0

    def _retime_slot(self, s: Slot, old_len: int, new_len: int) -> None:
        """Пересчёт материала слота под новый темп (пропорционально, «как на ленте»)."""
        if old_len <= 0 or new_len <= 0 or old_len == new_len:
            return
        if s.length > 0:
            src = s.data[:old_len]
            src_ext = np.concatenate([src, src[:1]])   # сшивка переноса цикла
            x_old = np.linspace(0.0, 1.0, num=old_len + 1)
            x_new = np.linspace(0.0, 1.0, num=new_len, endpoint=False)
            s.data[:new_len] = np.interp(x_new, x_old, src_ext).astype(np.float32)
            if new_len < old_len:
                s.data[new_len:old_len] = 0.0
        if s.has_undo and s.undo_length > 1:
            r = new_len / float(old_len)
            u_old = s.undo_length
            u_new = max(2, int(round(u_old * r)))
            src = s.undo[:u_old]
            src_ext = np.concatenate([src, src[:1]])
            x_old = np.linspace(0.0, 1.0, num=u_old + 1)
            x_new = np.linspace(0.0, 1.0, num=u_new, endpoint=False)
            s.undo[:u_new] = np.interp(x_new, x_old, src_ext).astype(np.float32)
            if u_new < u_old:
                s.undo[u_new:u_old] = 0.0
            s.undo_length = u_new
        s.wave_gen += 1

    # ------------------------------------------------------------------
    # Открытие/закрытие звука (duplex → split фолбэк, ASIO/экскл. WASAPI)
    # ------------------------------------------------------------------
    def start(self, in_device, out_device) -> None:
        sd = _import_sd()
        self.stop()
        in_info = sd.query_devices(in_device)
        out_info = sd.query_devices(out_device)
        in_ch = min(2, max(1, int(in_info["max_input_channels"])))
        out_ch = min(2, max(1, int(out_info["max_output_channels"])))

        self.in_api = self._hostapi_name(sd, in_device)
        self.out_api = self._hostapi_name(sd, out_device)

        rates = []
        for r in (
            self.sample_rate,
            int(in_info.get("default_samplerate", 0)),
            int(out_info.get("default_samplerate", 0)),
            48000,
            44100,
        ):
            r = int(r)
            if r > 0 and r not in rates:
                rates.append(r)

        wasapi_pair = "WASAPI" in self.in_api and "WASAPI" in self.out_api
        use_excl = self.wasapi_exclusive and wasapi_pair
        excl_order = (True, False) if use_excl else (False,)

        errors = []
        for exclusive in excl_order:
            for sr in rates:
                try:
                    stream = sd.Stream(
                        samplerate=sr,
                        blocksize=self.blocksize,
                        device=(in_device, out_device),
                        channels=(in_ch, out_ch),
                        dtype="float32",
                        latency="low",
                        extra_settings=self._extra_settings(sd, exclusive),
                        callback=self._audio_callback,
                    )
                    stream.start()
                    self._streams = [stream]
                    self.sample_rate = sr
                    self.mode = "duplex"
                    self.exclusive = bool(exclusive and wasapi_pair)
                    self.in_device, self.out_device = in_device, out_device
                    self.update_grid()
                    self._on_streams_started()
                    return
                except Exception as e:
                    tag = " экскл." if exclusive and wasapi_pair else ""
                    errors.append(f"дуплекс {sr} Гц{tag}: {e}")

        for exclusive in excl_order:
            try:
                self._start_split(in_device, out_device, in_ch, out_ch, rates, errors, exclusive)
                self.in_device, self.out_device = in_device, out_device
                return
            except Exception as e:
                tag = " экскл." if exclusive and wasapi_pair else ""
                errors.append(f"раздельные потоки{tag}: {e}")

        raise RuntimeError(
            "Не удалось открыть аудио. Обычно это значит, что вход и выход\n"
            "работают в разных режимах (host API) или с разными частотами.\n"
            "Попробуйте выбрать пару с одинаковым API (например, оба WASAPI)\n"
            "или подключить вход и выход через одну звуковую карту.\n\n"
            + "\n".join(errors)
        )

    @staticmethod
    def _hostapi_name(sd, dev) -> str:
        d = sd.query_devices(dev)
        apis = sd.query_hostapis()
        idx = int(d.get("hostapi", 0))
        return apis[idx]["name"] if idx < len(apis) else ""

    def _extra_settings(self, sd, exclusive: bool):
        try:
            if "ASIO" in self.in_api or "ASIO" in self.out_api:
                return sd.AsioSettings()
            if exclusive and "WASAPI" in self.in_api and "WASAPI" in self.out_api:
                return sd.WasapiSettings(exclusive=True)
        except Exception:
            pass
        return None

    def latency_ms(self) -> float:
        base = 1000.0 * self.blocksize / max(1, self.sample_rate)
        return base * (3.0 if self.mode == "split" else 2.0)

    def _start_split(self, in_device, out_device, in_ch, out_ch, rates, errors,
                     exclusive: bool = False) -> None:
        sd = _import_sd()

        out_stream = in_stream = None
        sr_out = None
        for sr in rates:
            try:
                out_stream = sd.OutputStream(
                    samplerate=sr,
                    blocksize=self.blocksize,
                    device=out_device,
                    channels=out_ch,
                    dtype="float32",
                    latency="low",
                    extra_settings=self._extra_settings(sd, exclusive),
                    callback=self._output_callback,
                )
                out_stream.start()
                sr_out = sr
                break
            except Exception as e:
                errors.append(f"выход {sr} Гц: {e}")
        if out_stream is None:
            raise RuntimeError("не удалось открыть выход")

        seen = []
        for sr in [sr_out] + list(rates):
            if sr is None or sr in seen:
                continue
            seen.append(sr)
            try:
                with self._ring_lock:
                    self._wpos = 0
                    self._rpos = 0
                self._resample_ratio = float(sr_out) / float(sr)
                in_stream = sd.InputStream(
                    samplerate=sr,
                    blocksize=self.blocksize,
                    device=in_device,
                    channels=in_ch,
                    dtype="float32",
                    latency="low",
                    extra_settings=self._extra_settings(sd, exclusive),
                    callback=self._input_callback,
                )
                in_stream.start()
                break
            except Exception as e:
                errors.append(f"вход {sr} Гц: {e}")
                in_stream = None
        if in_stream is None:
            out_stream.stop()
            out_stream.close()
            raise RuntimeError("не удалось открыть вход")

        self.sample_rate = sr_out
        self._streams = [in_stream, out_stream]
        self.mode = "split"
        self.exclusive = bool(exclusive and "WASAPI" in self.in_api and "WASAPI" in self.out_api)
        self.update_grid()
        self._on_streams_started()

    def _on_streams_started(self) -> None:
        """Старт звука: очистить «призрачные» команды (нажатые до старта) и сбросить транспорт."""
        try:
            while True:
                self.cmd_q.get_nowait()
        except queue.Empty:
            pass
        self._stop_recording()
        self.transport = "stopped"
        self._pos = 0

    def stop(self) -> None:
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams = []
        self.mode = ""
        self._clicks.clear()

    @property
    def running(self) -> bool:
        return bool(self._streams)

    def snapshot(self) -> dict:
        slots = []
        frac = (self._pos / self._cycle_len) if self._cycle_len > 0 else 0.0
        block_len = max(1, self._block_len)
        for s in self.slots:
            slots.append(
                {
                    "state": s.state,
                    "length": (s.length / self.sample_rate) if s.length > 0 else 0.0,
                    "pos": frac,
                    "has_undo": s.has_undo,
                    "recording": s.rec_active,
                    "waiting": bool(s.rec_active and s.wait_hit),
                    "muted": s.muted,
                    "blocks": [bool(v) for v in s.block_flags],
                }
            )
        return {
            "current": self.current_slot,
            "slots": slots,
            "in_peak": self.in_peak,
            "out_peak": self.out_peak,
            "xruns": self.xruns,
            "mode": self.mode,
            "env": self._env,
            "transport": self.transport,
            "armed": self._rec_armed,
            "pos": frac,
            "cycle_sec": self._cycle_len / self.sample_rate if self._cycle_len else 0.0,
            "fixed_total_sec": self.fixed_total_sec(),
            "fixed_block": min(self.num_blocks - 1, self._pos // block_len),
            "fixed_blocks": max(1, self.num_blocks),
            "fixed_block_frac": (self._pos % block_len) / block_len,
        }

    def get_wave(self, n: int) -> tuple:
        """(пики, «зелёные» бины) — огибающая слота n для отрисовки.

        Цвет бина — цвет его блока: весь блок зелёный (с порогом / записан
        вручную) или серый.
        """
        s = self.slots[n]
        bins = WAVE_BINS
        length = max(1, self._cycle_len)
        if s._wave_cache is not None and s._wave_gen_seen == s.wave_gen:
            return s._wave_cache, s._wave_hits
        if s.length <= 0:
            out = np.zeros(bins, dtype=np.float32)
            hits = np.zeros(bins, dtype=bool)
        else:
            d = np.abs(s.data[:length])
            edges = np.linspace(0, length, bins + 1).astype(np.int64)
            out = np.zeros(bins, dtype=np.float32)
            for i in range(bins):
                a = edges[i]
                b = min(max(edges[i + 1], a + 1), length)
                if a < length:
                    out[i] = float(np.max(d[a:b]))
            nb = max(1, len(s.block_flags))
            hits = np.zeros(bins, dtype=bool)
            for i in range(bins):
                bi = min(nb - 1, int(i * nb / bins))
                hits[i] = bool(s.block_flags[bi])
        s._wave_cache = out
        s._wave_hits = hits
        s._wave_gen_seen = s.wave_gen
        return out, s._wave_hits

    def export_slot_wav(self, n: int, path: Path) -> None:
        s = self.slots[n]
        length = s.length if s.length > 0 else 0
        if length <= 0:
            raise ValueError("Слот пуст — нечего сохранять")
        data = np.clip(s.data[:length], -1.0, 1.0)
        pcm = (data * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(pcm.tobytes())

    # ------------------------------------------------------------------
    # Кольцевой буфер (режим split)
    # ------------------------------------------------------------------
    def _ring_write(self, x: np.ndarray) -> None:
        n = len(x)
        w0 = self._wpos & self._mask
        first = min(n, RING_CAP - w0)
        self._ring[w0:w0 + first] = x[:first]
        if first < n:
            self._ring[:n - first] = x[first:]
        self._wpos += n

    def _ring_read_into(self, buf: np.ndarray) -> None:
        n = len(buf)
        buf[:] = 0.0
        avail = self._wpos - self._rpos
        take = min(n, avail)
        if take > 0:
            r0 = self._rpos & self._mask
            first = min(take, RING_CAP - r0)
            buf[:first] = self._ring[r0:r0 + first]
            if first < take:
                buf[first:take] = self._ring[:take - first]
            self._rpos += take
        if self._wpos - self._rpos > 3 * self.blocksize:
            self._rpos = self._wpos - 2 * self.blocksize

    # ------------------------------------------------------------------
    # Аудио-колбэки и обработка блока
    # ------------------------------------------------------------------
    def _audio_callback(self, indata, outdata, frames, time_info, status) -> None:
        if status:
            self.xruns += 1
        self.process_block(indata, outdata, frames)

    def _input_callback(self, indata, frames, time_info, status) -> None:
        if status:
            self.xruns += 1
        if frames > self._mono_in.shape[0]:
            self._mono_in = np.zeros(frames, dtype=np.float32)
        if indata.ndim == 1:
            mono = indata[:frames]
        elif indata.shape[1] == 1:
            mono = indata[:frames, 0]
        else:
            mono = self._mono_in[:frames]
            np.mean(indata[:frames], axis=1, out=mono)

        ratio = self._resample_ratio
        if abs(ratio - 1.0) < 1e-9:
            x = mono
        else:
            n_out = max(1, int(round(frames * ratio)))
            pos = np.arange(n_out, dtype=np.float64) / ratio
            x = np.interp(pos, np.arange(frames, dtype=np.float64), mono).astype(np.float32)

        with self._ring_lock:
            self._ring_write(x)
            if self._wpos - self._rpos > RING_CAP:
                self._rpos = self._wpos - RING_CAP

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            self.xruns += 1
        if frames > self._split_in.shape[0]:
            self._split_in = np.zeros(frames, dtype=np.float32)
        x = self._split_in[:frames]
        with self._ring_lock:
            self._ring_read_into(x)
        self.process_block(x.reshape(frames, 1), outdata, frames)

    def process_block(self, indata, outdata, frames: int) -> None:
        """Основная обработка одного блока (вызывается и из тестов)."""
        if frames > self._mono_in.shape[0]:
            self._mono_in = np.zeros(frames, dtype=np.float32)
            self._mix = np.zeros(frames, dtype=np.float32)
            self._out = np.zeros(frames, dtype=np.float32)

        if indata.ndim == 1:
            x = indata[:frames]
        elif indata.shape[1] == 1:
            x = indata[:frames, 0]
        else:
            x = self._mono_in[:frames]
            np.mean(indata[:frames], axis=1, out=x)

        abs_start = self._abs_samples

        # 1. Команды — ПЕРВЫМИ, на границе блока (явная команда важнее триггера).
        try:
            while True:
                action, arg = self.cmd_q.get_nowait()
                self._apply_command(action, arg)
        except queue.Empty:
            pass

        block_peak = float(np.max(np.abs(x))) if frames else 0.0
        self._env = max(block_peak, self._env * 0.85)

        # 2. «Авто по звуку» + ожидание: Play/rec запускается С МОМЕНТА
        #    превышения порога (если play ещё не был запущен).
        if (self._rec_armed and self.transport != "running"
                and block_peak >= self.threshold):
            self._rec_armed = False
            self._transport_start()
            self._start_recording(self.slots[self.current_slot])

        p0 = self._pos
        cycle = self._cycle_len
        running = self.transport == "running"

        # 3–4. Воспроизведение и поблочная запись выбранного лупа.
        #     Запись: в блоке со старым материалом он ИГРАЕТ, пока не превышен
        #     порог («Авто»); при пороге старая запись блока УДАЛЯЕТСЯ и пишется
        #     новая до конца блока. Пишущийся блок не воспроизводится.
        mix = self._mix[:frames]
        mix[:] = 0.0
        if running:
            for s in self.slots:
                if s.rec_active:
                    self._rec_and_render(s, x, p0, frames, mix)
                elif s.length > 0:
                    self._render_at(s, p0, frames, mix)

        outm = self._out[:frames]
        np.multiply(mix, self.master_gain, out=outm)
        outm += x * self.monitor_gain

        # 5. Метроном — ТОЛЬКО при работающем транспорте. Новый запуск —
        #    всегда с 1-й доли (акцент); включение на лету — с ближайшего такта.
        if self.metronome_on != self._metro_on_prev:
            if self.metronome_on:
                self._metro_skip = self._beats_to_next_bar()
            else:
                self._metro_skip = 0
                self._click_tail = np.zeros(0, dtype=np.float32)  # убрать хвост
            self._metro_on_prev = self.metronome_on
        if self.metronome_on and running:
            self._render_metro_span(outm, p0, frames, cycle)

        # 5б. Драм-машина — ТОЛЬКО при работающем транспорте (как метроном),
        #     включение на лету — со следующего такта. Предпрослушивание
        #     играет без транспорта один цикл и выключается само.
        if self.drum_on != self._drum_on_prev:
            if self.drum_on:
                self._drum_skip = self._slots_to_next_bar()
            else:
                self._drum_skip = 0
                self._drum_tail = np.zeros(0, dtype=np.float32)  # убрать хвост
            self._drum_on_prev = self.drum_on
        if self.drum_on and running:
            bus = self._drum_bus[:frames]
            bus[:] = 0.0
            self._render_drums_span(bus, p0, frames)
            outm += np.tanh(bus)               # мягкий лимитер без клиппинга
        elif self._preview_active and not running:
            bus = self._drum_bus[:frames]
            bus[:] = 0.0
            self._render_drums_span(bus, self._preview_pos, frames)
            outm += np.tanh(bus)
            self._preview_pos += frames
            if self._preview_pos >= self._cycle_len:
                self._preview_active = False
                self._drum_tail = np.zeros(0, dtype=np.float32)

        # 6. Пики, позиция, кольцо.
        self.out_peak = float(np.max(np.abs(outm))) if frames else 0.0
        if running:
            self._pos = (p0 + frames) % cycle if cycle else 0
            for s in self.slots:
                s.playhead = self._pos
        self._abs_samples += frames

        try:
            outdata[:frames] = outm.reshape(frames, 1)
        except Exception:
            try:
                outdata[:frames, 0] = outm
            except Exception:
                pass

        if self.mode == "duplex":
            self.in_peak = block_peak
        else:
            self.in_peak = block_peak

    # ------------------------------------------------------------------
    # Воспроизведение с позиции цикла
    # ------------------------------------------------------------------
    def _render_at(self, s: Slot, p0: int, frames: int, mix: np.ndarray) -> None:
        cycle = self._cycle_len
        if cycle <= 0 or s.muted:
            return
        i = 0
        pos = p0
        while i < frames:
            take = min(frames - i, cycle - pos)
            mix[i:i + take] += s.data[pos:pos + take]
            i += take
            pos = 0 if pos + take >= cycle else pos + take

    # ------------------------------------------------------------------
    # Поблочная запись (замена блоками)
    # ------------------------------------------------------------------
    def _rec_and_render(self, s: Slot, x: np.ndarray, p0: int, frames: int,
                        mix: np.ndarray) -> None:
        """Запись выбранного лупа с общего тайминга (+ его старый звук до порога)."""
        cycle = self._cycle_len
        if cycle <= 0:
            return
        i = 0
        pos = p0
        while i < frames:
            take = min(frames - i, cycle - pos)
            self._rec_seg(s, x[i:i + take], pos, take, mix, i)
            i += take
            if pos + take >= cycle:
                pos = 0
                # Перенос в начало цикла: блок начинается заново — иначе
                # «флаг порога» старого блока остаётся и запись идёт без порога.
                self._rec_leave_block(s)
            else:
                pos += take

    def _rec_seg(self, s: Slot, chunk: np.ndarray, pos: int, n: int,
                 mix: np.ndarray, off: int) -> None:
        """Сегмент на позиции цикла pos (без переноса): пишет и/или играет."""
        block_len = self._block_len
        if block_len <= 0:
            return
        i = 0
        p = pos
        while i < n:
            bi = p // block_len
            b_end = (bi + 1) * block_len
            if s.cur_block != bi:
                self._rec_leave_block(s)
                s.cur_block = bi
                s.block_hit = False
                s.block_segs = []
                if s._first_block:
                    s.block_base = p        # первый блок — с места старта
                    s._first_block = False
                else:
                    s.block_base = bi * block_len
                # «Вручную» — блок сразу зелёный, пишем прогрессивно (замена);
                # «Авто» — серый, старый блок играет до порога, удаление — по порогу.
                if 0 <= bi < len(s.block_flags) and not self.autostart:
                    s.block_flags[bi] = True
                # «Авто»: флаг — ТОЛЬКО по порогу. Без порога блок не трогается
                # (и материал, и флаг остаются прежними — важно при переносе круга).
                s.wave_gen += 1
            take = min(n - i, b_end - p)
            part = chunk[i:i + take]
            if not self.autostart:
                # «Вручную»: пишем весь блок без учёта порога; старое не играет.
                self._rec_commit(s, part, p)
            elif s.block_hit:
                # Порог уже был — пишем новое до конца блока, старое не играет.
                self._rec_commit(s, part, p)
            else:
                peak = float(np.max(np.abs(part))) if part.size else 0.0
                if peak >= self.threshold:
                    # Порог! Старая запись блока УДАЛЯЕТСЯ, пишем новое целиком:
                    # начало блока (накопленное до порога) + всё до конца блока.
                    s.block_hit = True
                    s.wait_hit = False         # запись реально началась
                    if 0 <= bi < len(s.block_flags):
                        s.block_flags[bi] = True
                    self._delete_block(s)
                    self._rec_flush(s)
                    self._rec_commit(s, part, p)
                    s.wave_gen += 1
                else:
                    # Порога нет: копим вход, СТАРЫЙ БЛОК ИГРАЕТ.
                    s.block_segs.append(part.copy())
                    if s.length > 0 and not s.muted:
                        mix[off + i:off + i + take] += s.data[p:p + take]
            i += take
            p += take

    def _delete_block(self, s: Slot) -> None:
        """Удалить старую запись текущего блока (от block_base до конца блока)."""
        if s.cur_block < 0:
            return
        end = min((s.cur_block + 1) * self._block_len, s.capacity)
        a = min(s.block_base, s.capacity)
        if end > a:
            s.data[a:end] = 0.0

    def _rec_flush(self, s: Slot) -> None:
        """Дозаписать накопленное начало блока (с block_base) после порога."""
        p = s.block_base
        for seg in s.block_segs:
            self._rec_commit(s, seg, p)
            p += len(seg)
        s.block_segs = []

    def _rec_commit(self, s: Slot, chunk: np.ndarray, pos: int) -> None:
        n = len(chunk)
        if n == 0:
            return
        end = min(pos + n, s.capacity)
        m = end - pos
        s.data[pos:end] = chunk[:m]       # замена (не сложение!)
        if s.length < self._cycle_len:
            s.length = self._cycle_len
        s.wave_gen += 1

    def _rec_leave_block(self, s: Slot) -> None:
        """Конец блока: без порога — вход отбрасывается, старое остаётся."""
        if s.cur_block < 0:
            return
        s.block_segs = []
        s.cur_block = -1

    def _start_recording(self, s: Slot) -> None:
        if s.rec_active:
            return
        s.rec_active = True
        s.rec_replace = True
        s.cur_block = -1
        s._first_block = True
        s.block_hit = False
        s.block_segs = []
        s.wait_hit = bool(self.autostart)  # «Авто»: до первого порога «Ждём звук»
        s.state = SlotState.RECORDING
        # Одноуровневый Undo: откат всей последней записи.
        s.undo[:] = s.data
        s.undo_length = s.length
        if len(s.undo_flags) == len(s.block_flags):
            s.undo_flags[:] = s.block_flags
        else:
            s.undo_flags = s.block_flags.copy()
        s.has_undo = True
        s.wave_gen += 1

    def _stop_recording(self) -> None:
        for s in self.slots:
            if s.rec_active:
                self._rec_leave_block(s)
                s.rec_active = False
                s.cur_block = -1
                s.block_segs = []
                s.state = SlotState.LOADED if s.length > 0 else SlotState.EMPTY
                s.wave_gen += 1

    # ------------------------------------------------------------------
    # Транспорт
    # ------------------------------------------------------------------
    def _transport_start(self) -> None:
        """Новый запуск — всегда с начала цикла (метроном с 1-й доли)."""
        self._pos = 0
        self.transport = "running"
        self._metro_skip = 0
        self._drum_skip = 0
        self._preview_active = False

    def _transport_toggle(self) -> None:
        """«Старт/Стоп»: пауза/продолжить все дорожки разом."""
        if self.transport == "running":
            self._stop_recording()
            self._rec_armed = False
            self.transport = "paused"            # позиция остаётся
        elif self.transport == "paused":
            self.transport = "running"           # продолжаем с той же позиции
        else:  # stopped
            self._transport_start()

    def _transport_stop(self) -> None:
        """«Стоп»: всё стоп, позиция на начало, запись выключена."""
        self._stop_recording()
        self._rec_armed = False
        self.transport = "stopped"
        self._pos = 0

    # ------------------------------------------------------------------
    # Метроном (только при работающем транспорте)
    # ------------------------------------------------------------------
    def _beats_to_next_bar(self) -> int:
        """Сколько долей пропустить, чтобы выйти на границу такта (1-я доля)."""
        if self.transport not in ("running", "paused"):
            return 0                            # начнёт с транспортом — с доли 1
        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        bar_len = spb * max(1, self.beats_per_bar)
        pos = float(self._pos)
        k0 = int(math.ceil(pos / spb - 1e-9))   # ближайшая доля
        kb = int(math.ceil(pos / bar_len - 1e-9)) * max(1, self.beats_per_bar)
        return max(0, kb - k0)

    def _render_metro_span(self, out: np.ndarray, start: int, frames: int,
                           wrap: int) -> None:
        """Щелчки на долях позиции цикла: доля 1 такта — акцент, остальные слабые."""
        if len(self._click_tail):
            t = min(len(self._click_tail), frames)
            out[:t] += self._click_tail[:t]
            self._click_tail = self._click_tail[t:]

        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        base = float(start)
        end = base + frames
        k0 = int(math.ceil(base / spb - 1e-9))
        k = k0
        while k * spb < end:
            off = int(round(k * spb - base))
            if self._metro_skip > 0:
                self._metro_skip -= 1           # вступление с ближайшего такта
            else:
                accent = (k % max(1, self.beats_per_bar)) == 0
                self._mix_click(out, off, self._get_click(accent))
            k += 1

    def _mix_click(self, out: np.ndarray, off: int, click: np.ndarray) -> None:
        n = len(click)
        if off >= len(out):
            return
        if off < 0:
            click = click[-off:]
            n = len(click)
            off = 0
            if n == 0:
                return
        take = min(n, len(out) - off)
        out[off:off + take] += click[:take] * self.metro_gain
        if take < n:
            tail = (click[take:] * self.metro_gain).astype(np.float32)
            need = len(tail)
            if len(self._click_tail) < need:
                self._click_tail = np.concatenate((
                    self._click_tail,
                    np.zeros(need - len(self._click_tail), dtype=np.float32),
                ))
            self._click_tail[:need] += tail

    def _get_click(self, accent: bool) -> np.ndarray:
        key = (self.metro_sound, accent, self.sample_rate)
        click = self._clicks.get(key)
        if click is None:
            click = self._make_click(self.metro_sound, accent)
            self._clicks[key] = click
        return click

    def _make_click(self, kind: str, accent: bool) -> np.ndarray:
        sr = self.sample_rate
        dur = 0.04 if accent else 0.028
        n = max(32, int(sr * dur))
        t = np.arange(n, dtype=np.float32) / sr
        rng = np.random.default_rng(7)
        noise = rng.standard_normal(n).astype(np.float32)
        if kind == "electronic":
            f = 1600.0 if accent else 1000.0
            y = np.sin(2 * np.pi * f * t) * np.exp(-t * 90.0)
        elif kind == "beep":
            f = 1320.0 if accent else 880.0
            y = np.sin(2 * np.pi * f * t) * np.exp(-t * 60.0)
        elif kind == "rim":
            f = 2600.0 if accent else 2100.0
            y = (noise * 0.7 + 0.3 * np.sin(2 * np.pi * f * t)) * np.exp(-t * 120.0)
        else:  # wood
            f = 1900.0 if accent else 1300.0
            y = (0.55 * noise + 0.45 * np.sin(2 * np.pi * f * t)) * np.exp(-t * 70.0)
        peak = float(np.max(np.abs(y))) or 1.0
        return (y / peak).astype(np.float32)

    # ------------------------------------------------------------------
    # Драм-машина (только при работающем транспорте; см. drums.py)
    # ------------------------------------------------------------------
    @property
    def drum_preview(self) -> bool:
        return self._preview_active

    def _drum_table(self) -> list:
        key = (self.drum_style, self.beats_per_bar,
               self.bars_per_block, self.num_blocks)
        if key != self._drum_events_key:
            self._drum_events = drums.build_events(*key)
            self._drum_events_key = key
        return self._drum_events

    def _slots_to_next_bar(self) -> int:
        """Сколько 1/16 пропустить, чтобы выйти на границу такта (1-я доля)."""
        if self.transport not in ("running", "paused"):
            return 0                            # начнёт с транспортом — с доли 1
        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        slot_len = spb / 4.0
        slots_per_bar = max(1, self.beats_per_bar) * 4
        pos = float(self._pos)
        k0 = int(math.ceil(pos / slot_len - 1e-9))
        kb = int(math.ceil(pos / (slot_len * slots_per_bar) - 1e-9)) * slots_per_bar
        return max(0, kb - k0)

    def _render_drums_span(self, out: np.ndarray, start: int, frames: int) -> None:
        """Удары по сетке 1/16 позиции цикла; хвосты ударов дорисовываются."""
        if len(self._drum_tail):
            t = min(len(self._drum_tail), frames)
            out[:t] += self._drum_tail[:t]
            self._drum_tail = self._drum_tail[t:]

        table = self._drum_table()
        n_slots = len(table)
        if n_slots == 0:
            return
        spb = self.sample_rate * 60.0 / max(self.bpm, 1.0)
        slot_len = spb / 4.0
        base = float(start)
        end = base + frames
        m = int(math.ceil(base / slot_len - 1e-9))
        while m * slot_len < end:
            if self._drum_skip > 0:
                self._drum_skip -= 1           # вступление с ближайшего такта
            else:
                evs = table[m % n_slots]
                if evs:
                    off = int(round(m * slot_len - base))
                    for name, vel in evs:
                        self._mix_drum(out, off, self._get_hit(name),
                                       vel * self.drum_gain)
            m += 1

    def _mix_drum(self, out: np.ndarray, off: int, hit: np.ndarray,
                  gain: float) -> None:
        n = len(hit)
        if off >= len(out):
            return
        if off < 0:
            hit = hit[-off:]
            n = len(hit)
            off = 0
            if n == 0:
                return
        take = min(n, len(out) - off)
        out[off:off + take] += hit[:take] * gain
        if take < n:
            # Хвост НАКАПЛИВАЕТСЯ (суммируется), а не дописывается в конец:
            # одновременные инструменты должны звучать ВМЕСТЕ, а не по очереди.
            tail = (hit[take:] * gain).astype(np.float32)
            need = len(tail)
            if len(self._drum_tail) < need:
                self._drum_tail = np.concatenate((
                    self._drum_tail,
                    np.zeros(need - len(self._drum_tail), dtype=np.float32),
                ))
            self._drum_tail[:need] += tail

    def _get_hit(self, name: str) -> np.ndarray:
        hit = self._drum_hits.get((name, self.sample_rate))
        if hit is None:
            hit = drums.make_hit(name, self.sample_rate)
            self._drum_hits[(name, self.sample_rate)] = hit
        return hit

    # ------------------------------------------------------------------
    # Команды
    # ------------------------------------------------------------------
    def _apply_command(self, action: str, arg) -> None:
        if action == "toggle_rec":
            self._toggle_rec()
        elif action == "toggle_play":
            self._transport_toggle()
        elif action == "stop_all":
            self._transport_stop()
        elif action == "undo":
            self._undo(self.slots[self.current_slot])
        elif action == "clear":
            self._clear(self.slots[self.current_slot])
        elif action == "select_slot" and arg is not None:
            self._switch_slot(int(arg))
        elif action == "toggle_mute":
            idx = self.current_slot if arg is None else int(arg) % self.n_slots
            self.slots[idx].muted = not self.slots[idx].muted
        elif action == "slot_click" and arg is not None:
            # Клик по кнопке лупа: выбор; повторный клик по выбранному — mute.
            n = int(arg) % self.n_slots
            if n == self.current_slot:
                self.slots[n].muted = not self.slots[n].muted
            else:
                self._switch_slot(n)
        elif action.startswith("slot_"):
            # MIDI/клавиши: «Выбрать луп N» (1..4) — только выбор.
            try:
                self._switch_slot(int(action[5:]) - 1)
            except ValueError:
                pass
        elif action == "next_slot":
            self._switch_slot((self.current_slot + 1) % self.n_slots)
        elif action == "prev_slot":
            self._switch_slot((self.current_slot - 1) % self.n_slots)
        elif action == "toggle_metronome":
            self.metronome_on = not self.metronome_on
        elif action == "toggle_drum":
            self.drum_on = not self.drum_on
        elif action == "toggle_drum_preview":
            if self._preview_active:
                self._preview_active = False
                self._drum_tail = np.zeros(0, dtype=np.float32)
            else:
                self._preview_active = True
                self._preview_pos = 0
        elif action == "set_bpm" and arg is not None:
            self.bpm = max(30.0, min(300.0, float(arg)))
            self.update_grid()

    def _toggle_rec(self) -> None:
        s = self.slots[self.current_slot]
        # Повторное нажатие в режиме ожидания — снять ожидание.
        if self._rec_armed and self.transport != "running":
            self._rec_armed = False
            return
        if s.rec_active:
            self._stop_recording()                     # конец записи
            return
        if self.autostart and self.transport == "stopped":
            # «Авто по звуку» + Play не запущен: ждём превышения порога —
            # с этого момента стартует проигрывание и запись.
            self._rec_armed = True
            return
        # «Запись/Дубль» запускает проигрывание всех лупов и пишет выбранный.
        if self.transport == "stopped":
            self._transport_start()                    # новый запуск — с начала
        elif self.transport == "paused":
            self.transport = "running"                 # продолжаем с позиции
        self._start_recording(s)

    def _switch_slot(self, new_idx: int) -> None:
        new_idx %= self.n_slots
        if new_idx == self.current_slot:
            return
        was_rec = any(s.rec_active for s in self.slots)
        if was_rec:
            self._stop_recording()
        self.current_slot = new_idx
        if was_rec and self.transport == "running":
            # новая дорожка пишет с текущей позиции общего тайминга
            self._start_recording(self.slots[new_idx])

    def _clear(self, s: Slot) -> None:
        if s.rec_active:
            self._rec_leave_block(s)
            s.rec_active = False
        s.reset()
        s.wave_gen += 1

    def _undo(self, s: Slot) -> None:
        """Откат последней записи/дубля (один уровень)."""
        if s.has_undo:
            s.data[:] = s.undo
            s.length = s.undo_length if s.undo_length > 0 else 0
            if len(s.undo_flags) == len(s.block_flags):
                s.block_flags[:] = s.undo_flags
            s.has_undo = False
            s.wave_gen += 1
            if s.length <= 0:
                s.state = SlotState.EMPTY
            else:
                s.state = SlotState.LOADED
