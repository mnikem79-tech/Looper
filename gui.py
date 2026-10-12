"""
Графический интерфейс лупера (tkinter, тёмная тема).

Вкладки:
- «Лупы» — индикация входа/выхода, метроном, темп/такты/блоки, авто-старт,
  строки лупов (волноформа + такты + блоки + зум), транспорт;
- «Настройки» — устройства, буфер, MIDI-назначения, журнал.

Потокобезопасность: всё обновление UI идёт из главного потока Tk;
MIDI-события приходят через очередь и обрабатываются в _poll().
"""
import queue
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

from audio_engine import SlotState
from drums import STYLE_NAMES

APP_VERSION = "r8.2 (2026-10-12)"

ACTIONS = [
    ("toggle_rec", "Запись / Дубль"),
    ("toggle_play", "Старт / Стоп (пауза всех)"),
    ("undo", "Undo (отменить последнюю запись)"),
    ("clear", "Очистить слот"),
    ("stop_all", "Стоп (всё + сброс)"),
    ("toggle_metronome", "Метроном вкл/выкл"),
    ("toggle_drum", "Драм-машина вкл/выкл"),
    ("next_slot", "Следующий луп"),
    ("prev_slot", "Предыдущий луп"),
] + [(f"slot_{i}", f"Выбрать луп {i}") for i in range(1, 5)]

ACTION_LABELS = dict(ACTIONS)

BG = "#1c1c1e"
PANEL = "#2c2c2e"
FG = "#e8e8ea"
DIM = "#8e8e93"
ACCENT = "#0a84ff"
REC = "#ff453a"
PLAY = "#30d158"
WARN = "#ffd60a"
WAVE = "#64d2ff"

STATE_COLORS = {
    SlotState.EMPTY: DIM,
    SlotState.LOADED: PLAY,
    SlotState.RECORDING: REC,
}

METER_WIDTH = 220
WAVE_BINS = 180
ROW_H = 64


class LoopRow:
    """Строка лупа: номер, статус, дорожка с волноформой/тактами/блоками."""

    def __init__(self, parent, index: int, app: "App"):
        self.index = index
        self.app = app
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill="x", pady=2)

        self.num_btn = tk.Button(
            self.frame, text=f"Луп {index + 1}", width=7,
            bg=PANEL, fg=FG, activebackground="#3a3a3c", activeforeground=FG,
            font=("Segoe UI", 9, "bold"),
            command=lambda n=index: app.select_slot(n),
        )
        self.num_btn.pack(side="left")
        self.state_lbl = tk.Label(self.frame, text="—", width=16, anchor="w",
                                  bg=BG, fg=DIM, font=("Segoe UI", 8))
        self.state_lbl.pack(side="left")

        holder = tk.Frame(self.frame, bg=BG)
        holder.pack(side="left", fill="x", expand=True)
        self.canvas = tk.Canvas(holder, height=ROW_H, bg="#111", highlightthickness=1,
                                highlightbackground=PANEL, xscrollincrement=1)
        self.canvas.pack(fill="x", expand=True)
        self.scroll = tk.Scrollbar(holder, orient="horizontal", command=self.canvas.xview)
        self.scroll.pack(fill="x")
        self.canvas.configure(xscrollcommand=self.scroll.set)

        self.canvas.bind("<Button-1>", lambda _e, n=index: app.select_slot(n))

        self._sig = ""
        self._wave_gen = -1
        self._playhead = None

    def update_row(self, snap_info: dict, selected: bool, params_sig: str) -> None:
        state = snap_info["state"]
        text = f"{state.value} {snap_info['length']:.1f}с"
        if snap_info.get("armed_here") or snap_info.get("waiting"):
            text = "● Ждём звук — запишется с порога"
        self.state_lbl.config(text=text, fg=STATE_COLORS.get(state, FG))
        border = ACCENT if selected else PANEL
        self.canvas.configure(highlightbackground=border)

        # Кнопка лупа: серый — не выбран; зелёный — выбран;
        # красный — идёт запись или ожидание порога для этого лупа.
        if selected and (snap_info.get("recording") or snap_info.get("armed_here")
                         or snap_info.get("waiting")):
            self.num_btn.configure(bg=REC, fg="white",
                                   activebackground="#ff6961", activeforeground="white")
        elif selected:
            self.num_btn.configure(bg=PLAY, fg="#111",
                                   activebackground="#5ee07f", activeforeground="#111")
        else:
            self.num_btn.configure(bg=PANEL, fg=FG,
                                   activebackground="#3a3a3c", activeforeground=FG)

        redraw_full = params_sig != self._sig
        wave_changed = snap_info.get("wave_gen", self._wave_gen) != self._wave_gen
        if redraw_full or wave_changed:
            self._draw(params_sig, snap_info, wave_changed, redraw_full)
        # Позиция курсора воспроизведения — ПО ШИРИНЕ СЕТКИ (с учётом зума)
        frac = snap_info.get("pos", 0.0)
        content_w = self.app.content_width()
        x = frac * content_w
        if self._playhead is None:
            self._playhead = self.canvas.create_line(x, 0, x, ROW_H, fill=WARN, width=2)
        else:
            self.canvas.coords(self._playhead, x, 0, x, ROW_H)

    def _draw(self, params_sig: str, snap_info: dict, wave_changed: bool, full: bool) -> None:
        app = self.app
        content_w = app.content_width()
        self.canvas.configure(scrollregion=(0, 0, content_w, ROW_H),
                              width=max(300, app.viewport_width()))
        if full:
            self.canvas.delete("all")
            self._playhead = None
            self._sig = params_sig
            total_bars = max(1, app.engine.bars_per_block * app.engine.num_blocks)
            bar_w = content_w / total_bars
            # Заливка блоков чередованием
            for b in range(app.engine.num_blocks):
                x0 = b * app.engine.bars_per_block * bar_w
                x1 = x0 + app.engine.bars_per_block * bar_w
                if b % 2 == 1:
                    self.canvas.create_rectangle(x0, 0, x1, ROW_H, fill="#161618", width=0)
            # Линии тактов
            for i in range(total_bars + 1):
                x = i * bar_w
                is_block = (i % app.engine.bars_per_block) == 0
                self.canvas.create_line(x, 0, x, ROW_H,
                                        fill="#4a4a4c" if is_block else "#2c2c2e",
                                        width=2 if is_block else 1)
        # Волноформа: зелёный — порог был достаточным, серый — нет.
        self.canvas.delete("wave")
        wave, hits = app.engine.get_wave(self.index)
        self._wave_gen = snap_info.get("wave_gen", -1)
        half = ROW_H / 2 - 4
        bins = len(wave)
        bw = content_w / bins
        for i, v in enumerate(wave):
            h = min(1.0, float(v)) * half
            if h < 0.5:
                continue
            color = "#30d158" if (i < len(hits) and hits[i]) else "#8e8e93"
            x0 = i * bw
            self.canvas.create_rectangle(
                x0, half - h + 4, x0 + max(bw, 1.0), half + h + 4,
                fill=color, outline="", tags="wave",
            )

    def force_redraw(self) -> None:
        self._sig = ""


class App(tk.Tk):
    def __init__(self, cfg: dict, engine, midi, on_close=None):
        super().__init__()
        self.cfg = cfg
        self.engine = engine
        self.midi = midi
        self._on_close = on_close
        self.ui_q: "queue.Queue" = queue.Queue()
        self.mapping: dict = dict(cfg.get("mapping", {}))
        self.learn_action = None
        self.learn_started = 0.0

        self.title(f"SMK-37 Looper — гитарный лупер с MIDI-контроллером  [{APP_VERSION}]")
        self.configure(bg=BG)
        self.geometry("1020x800")
        self.minsize(920, 700)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.tab_loops = tk.Frame(self.notebook, bg=BG)
        self.tab_settings = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_loops, text="   Лупы   ")
        self.notebook.add(self.tab_settings, text="   Настройки   ")

        self._build_loops_tab()
        self._build_settings_tab()

        self._refresh_devices()
        self._refresh_mapping_widgets()
        self._bind_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._poll)

    # ------------------------------------------------------------------
    # Общие помощники
    # ------------------------------------------------------------------
    def _label(self, parent, text, **kw):
        kw.setdefault("bg", BG)
        kw.setdefault("fg", FG)
        return tk.Label(parent, text=text, **kw)

    def _panel(self, parent, title):
        frame = tk.Frame(parent, bg=PANEL, pady=8, padx=10)
        frame.pack(fill="x", padx=10, pady=4)
        self._label(frame, title, font=("Segoe UI", 9, "bold"), bg=PANEL).pack(anchor="w")
        return frame

    def content_width(self) -> int:
        base = 700
        try:
            zoom = float(self.zoom_var.get())
        except Exception:
            zoom = 100.0
        return int(base * zoom / 100.0)

    def viewport_width(self) -> int:
        return max(300, self.winfo_width() - 180)

    # ==================================================================
    # Вкладка «Лупы»
    # ==================================================================
    def _build_loops_tab(self) -> None:
        # --- Индикация звука ---
        row = tk.Frame(self.tab_loops, bg=BG, pady=6)
        row.pack(fill="x", padx=10)
        self._label(row, "Вход:", width=6, anchor="w").pack(side="left")
        self.in_meter = tk.Canvas(row, width=METER_WIDTH, height=14, bg="#111", highlightthickness=0)
        self.in_meter.pack(side="left", padx=4)
        self._label(row, "Выход:", width=6, anchor="w").pack(side="left", padx=(16, 0))
        self.out_meter = tk.Canvas(row, width=METER_WIDTH, height=14, bg="#111", highlightthickness=0)
        self.out_meter.pack(side="left", padx=4)
        self.status_label = self._label(row, "● Не подключено", fg=DIM,
                                        font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side="right")

        # --- Метроном + драм-машина + темп ---
        m = self._panel(self.tab_loops, "Метроном / Драм-машина")
        self.metro_var = tk.BooleanVar(value=bool(self.cfg.get("metronome_on", False)))
        tk.Checkbutton(m, text="Метроном вкл.", variable=self.metro_var, bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG, selectcolor=PANEL,
                       highlightthickness=0,
                       command=self._apply_loops_params).pack(side="left")

        # Кнопка «Драм-машина» — рядом с метрономом; спойлер снизу
        self.drum_var = tk.BooleanVar(value=bool(self.cfg.get("drum_on", False)))
        self.drum_btn = tk.Button(
            m, text="Драм-машина", width=12,
            bg=PANEL, fg=FG, activebackground="#3a3a3c", activeforeground=FG,
            font=("Segoe UI", 9, "bold"),
            command=self._toggle_drum,
        )
        self.drum_btn.pack(side="left", padx=(14, 0))
        self.metro_panel = m

        self._label(m, "Звук:", bg=PANEL).pack(side="left", padx=(14, 0))
        self.metro_sound_var = tk.StringVar(value=str(self.cfg.get("metro_sound", "wood")))
        ttk.Combobox(m, textvariable=self.metro_sound_var, width=12, state="readonly",
                     values=("wood", "electronic", "beep", "rim")).pack(side="left", padx=4)
        self.metro_sound_var.trace_add("write", lambda *_: self._apply_loops_params())

        self._label(m, "Громкость:", bg=PANEL).pack(side="left", padx=(10, 0))
        self.metro_gain_var = tk.DoubleVar(value=float(self.cfg.get("metro_gain", 0.5)))
        tk.Scale(m, from_=0.0, to=1.0, resolution=0.05, orient="horizontal", length=100,
                 variable=self.metro_gain_var, bg=PANEL, fg=FG, highlightthickness=0,
                 command=lambda _v: self._apply_loops_params()).pack(side="left")
        self._label(m, "Темп и размер — общие с циклом (см. «Запись»).",
                    bg=PANEL, fg=DIM).pack(side="left", padx=(14, 0))

        # --- Спойлер драм-машины: стиль + предпрослушивание ---
        d = self._panel(self.tab_loops, "Драм-машина (стили)")
        self.drum_style_var = tk.StringVar(value=str(self.cfg.get("drum_style", "Поп")))
        self._label(d, "Стиль:", bg=PANEL).pack(side="left")
        ttk.Combobox(d, textvariable=self.drum_style_var, width=12, state="readonly",
                     values=STYLE_NAMES).pack(side="left", padx=4)
        self.drum_style_var.trace_add("write", lambda *_: self._apply_loops_params())

        self.drum_prev_btn = tk.Button(
            d, text="▶ Предпрослушивание", width=18,
            bg=PANEL, fg=FG, activebackground="#3a3a3c", activeforeground=FG,
            command=self._toggle_drum_preview,
        )
        self.drum_prev_btn.pack(side="left", padx=(14, 0))

        self._label(d, "Громкость:", bg=PANEL).pack(side="left", padx=(14, 0))
        self.drum_gain_var = tk.DoubleVar(value=float(self.cfg.get("drum_gain", 0.7)))
        tk.Scale(d, from_=0.0, to=1.5, resolution=0.05, orient="horizontal", length=100,
                 variable=self.drum_gain_var, bg=PANEL, fg=FG, highlightthickness=0,
                 command=lambda _v: self._apply_loops_params()).pack(side="left")
        self._label(d, "Играет с транспортом; крэш — 1-й такт цикла, брейк — последний. В лупы не пишется.",
                    bg=PANEL, fg=DIM).pack(side="left", padx=(14, 0))
        self.drum_panel = d

        # --- Темп / такты / блоки (ОБЩИЙ темп для метронома и цикла) ---
        b = self._panel(self.tab_loops,
                        "Запись: темп, такты и блоки (общий темп; длина цикла = такты × блоки)")
        self._label(b, "Темп:", bg=PANEL).pack(side="left")
        self.bpm_var = tk.StringVar(value=str(int(float(self.cfg.get("bpm", 120)))))
        tk.Spinbox(b, from_=30, to=300, width=5, textvariable=self.bpm_var,
                   bg="#111", fg=FG, buttonbackground=PANEL,
                   command=self._apply_loops_params).pack(side="left", padx=4)
        self.bpm_var.trace_add("write", lambda *_: self._apply_loops_params())
        self._label(b, "BPM", bg=PANEL).pack(side="left")

        self._label(b, "Размер:", bg=PANEL).pack(side="left", padx=(14, 0))
        self.meter_var = tk.StringVar(value=str(self.cfg.get("beats_per_bar", 4)))
        ttk.Combobox(b, textvariable=self.meter_var, width=5, state="readonly",
                     values=("2", "3", "4", "5", "6")).pack(side="left", padx=4)
        self.meter_var.trace_add("write", lambda *_: self._apply_loops_params())
        self._label(b, "/4", bg=PANEL).pack(side="left")

        self._label(b, "Тактов в блоке:", bg=PANEL).pack(side="left", padx=(18, 0))
        self.bars_var = tk.StringVar(value=str(self.cfg.get("bars_per_block", 4)))
        tk.Spinbox(b, from_=1, to=64, width=4, textvariable=self.bars_var, bg="#111", fg=FG,
                   buttonbackground=PANEL, command=self._apply_loops_params).pack(side="left", padx=4)
        self.bars_var.trace_add("write", lambda *_: self._apply_loops_params())

        self._label(b, "Блоков:", bg=PANEL).pack(side="left", padx=(12, 0))
        self.blocks_var = tk.StringVar(value=str(self.cfg.get("num_blocks", 2)))
        tk.Spinbox(b, from_=1, to=64, width=4, textvariable=self.blocks_var, bg="#111", fg=FG,
                   buttonbackground=PANEL, command=self._apply_loops_params).pack(side="left", padx=4)
        self.blocks_var.trace_add("write", lambda *_: self._apply_loops_params())

        self.total_label = self._label(b, "", bg=PANEL, fg=WARN,
                                       font=("Segoe UI", 9, "bold"))
        self.total_label.pack(side="left", padx=(14, 0))

        # --- Режим записи / порог ---
        a = self._panel(self.tab_loops, "Режим записи")
        self.autostart_var = tk.StringVar(value="auto" if self.cfg.get("autostart") else "manual")
        tk.Radiobutton(a, text="Вручную (все блоки)", variable=self.autostart_var,
                       value="manual", bg=PANEL, fg=FG, activebackground=PANEL,
                       activeforeground=FG, selectcolor=PANEL, highlightthickness=0,
                       command=self._apply_loops_params).pack(side="left")
        tk.Radiobutton(a, text="Авто по звуку (блоки по порогу)", variable=self.autostart_var,
                       value="auto", bg=PANEL, fg=FG, activebackground=PANEL,
                       activeforeground=FG, selectcolor=PANEL, highlightthickness=0,
                       command=self._apply_loops_params).pack(side="left", padx=(10, 0))

        self._label(a, "Порог:", bg=PANEL).pack(side="left", padx=(18, 0))
        self.thr_var = tk.DoubleVar(value=float(self.cfg.get("threshold", 0.05)))
        tk.Scale(a, from_=0.01, to=1.0, resolution=0.01, orient="horizontal", length=130,
                 variable=self.thr_var, bg=PANEL, fg=FG, highlightthickness=0,
                 command=lambda _v: self._apply_loops_params()).pack(side="left")

        self._label(a, "Блок с порогом — весь зелёный и пишется целиком; без порога — серый, в луп не пишется.",
                    bg=PANEL, fg=DIM).pack(side="left", padx=(14, 0))

        # --- Строки лупов + зум ---
        loops_frame = tk.Frame(self.tab_loops, bg=BG)
        loops_frame.pack(fill="both", expand=True, padx=10, pady=(6, 2))
        head = tk.Frame(loops_frame, bg=BG)
        head.pack(fill="x")
        self._label(head, "Лупы (клик — выбор):", font=("Segoe UI", 9, "bold")).pack(side="left")
        self._label(head, "Масштаб:", ).pack(side="left", padx=(18, 0))
        self.zoom_var = tk.DoubleVar(value=float(self.cfg.get("zoom", 100)))
        tk.Scale(head, from_=50, to=400, resolution=10, orient="horizontal", length=220,
                 variable=self.zoom_var, bg=BG, fg=FG, highlightthickness=0,
                 command=lambda _v: self._zoom_changed()).pack(side="left")

        self.rows = []
        for i in range(self.engine.n_slots):
            self.rows.append(LoopRow(loops_frame, i, self))

        # --- Транспорт ---
        self._build_transport()

        info = tk.Frame(self.tab_loops, bg=BG)
        info.pack(fill="x", padx=12, pady=(2, 6))
        self._label(
            info,
            "Все лупы играют в одном тайминге (единая полоска), длина цикла = такты × блоки. "
            "«Старт/Стоп» — пауза/продолжить все разом; «Стоп» — всё стоп и сброс на начало. "
            "«Запись» пишет только выбранный луп (кнопка лупа — красная), остальные играют; "
            "повторное нажатие останавливает запись (кнопка снова зелёная). "
            "Запись идёт заменой блоками: старый блок играет до порога, при пороге удаляется "
            "и пишется новый до конца блока; без порога блок не трогается — даже на следующем "
            "круге (свежий шанс на каждом проходе). "
            "«Авто по звуку»: если Play ещё не запущен, «Запись» встаёт в ожидание — проигрывание "
            "и запись стартуют с момента превышения порога. "
            "Метроном звучит только при проигрывании (первый запуск — всегда с 1-й доли такта, "
            "акцент на ней); выключение убирает щелчки сразу, включение на лету — со следующего такта. "
            "Горячие клавиши: R, Space, U, C, S, M, 1–4, [ ].",
            fg=DIM, wraplength=960, justify="left",
        ).pack(anchor="w")

    def _build_transport(self) -> None:
        frame = tk.Frame(self.tab_loops, bg=BG, pady=4)
        frame.pack(fill="x", padx=10)

        tk.Button(
            frame, text="●  Запись / Дубль", width=18, height=2,
            bg=REC, fg="white", activebackground="#ff6961",
            font=("Segoe UI", 11, "bold"),
            command=lambda: self.do_action("toggle_rec"),
        ).pack(side="left", padx=4)

        tk.Button(
            frame, text="▶  Старт / Стоп", width=16, height=2,
            bg=PLAY, fg="#111", activebackground="#5ee07f",
            font=("Segoe UI", 11, "bold"),
            command=lambda: self.do_action("toggle_play"),
        ).pack(side="left", padx=4)

        tk.Button(
            frame, text="↩  Undo", width=11, height=2, bg=PANEL, fg=FG,
            font=("Segoe UI", 10),
            command=lambda: self.do_action("undo"),
        ).pack(side="left", padx=4)

        tk.Button(
            frame, text="✕  Очистить", width=11, height=2, bg=PANEL, fg=FG,
            font=("Segoe UI", 10),
            command=lambda: self.do_action("clear"),
        ).pack(side="left", padx=4)

        tk.Button(
            frame, text="■  Стоп", width=11, height=2, bg=PANEL, fg=FG,
            font=("Segoe UI", 10),
            command=lambda: self.do_action("stop_all"),
        ).pack(side="left", padx=4)

        tk.Button(
            frame, text="💾 Экспорт WAV", width=13, height=2, bg=PANEL, fg=FG,
            font=("Segoe UI", 10), command=self.export_wav,
        ).pack(side="left", padx=4)

        self._sync_drum_ui()

    def _zoom_changed(self) -> None:
        self.cfg["zoom"] = float(self.zoom_var.get())
        for r in self.rows:
            r.force_redraw()

    def select_slot(self, n: int) -> None:
        self.engine.post("select_slot", n)

    # ==================================================================
    # Вкладка «Настройки»
    # ==================================================================
    def _build_settings_tab(self) -> None:
        self._build_devices_bar()
        self._build_mapping()
        self._build_log()

    def _build_devices_bar(self) -> None:
        bar = tk.Frame(self.tab_settings, bg=PANEL, pady=8, padx=10)
        bar.pack(fill="x", padx=10, pady=(8, 4))

        self._label(bar, "MIDI:", bg=PANEL, font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.midi_var = tk.StringVar()
        self.midi_box = ttk.Combobox(bar, textvariable=self.midi_var, width=32, state="readonly")
        self.midi_box.grid(row=0, column=1, padx=4)

        self._label(bar, "Аудио вход:", bg=PANEL, font=("Segoe UI", 9, "bold")).grid(row=0, column=2, sticky="w")
        self.in_var = tk.StringVar()
        self.in_box = ttk.Combobox(bar, textvariable=self.in_var, width=34, state="readonly")
        self.in_box.grid(row=0, column=3, padx=4)

        self._label(bar, "Аудио выход:", bg=PANEL, font=("Segoe UI", 9, "bold")).grid(row=0, column=4, sticky="w")
        self.out_var = tk.StringVar()
        self.out_box = ttk.Combobox(bar, textvariable=self.out_var, width=34, state="readonly")
        self.out_box.grid(row=0, column=5, padx=4)

        row2 = tk.Frame(bar, bg=PANEL)
        row2.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        self._label(row2, "Частота:", bg=PANEL).pack(side="left")
        self.sr_var = tk.StringVar(value=str(self.cfg.get("sample_rate", 48000)))
        ttk.Combobox(row2, textvariable=self.sr_var, width=8, state="readonly",
                     values=("44100", "48000", "96000")).pack(side="left", padx=4)

        self._label(row2, "Буфер:", bg=PANEL).pack(side="left", padx=(12, 0))
        self.bs_var = tk.StringVar(value=str(self.cfg.get("blocksize", 256)))
        ttk.Combobox(row2, textvariable=self.bs_var, width=6, state="readonly",
                     values=("64", "128", "256", "512", "1024")).pack(side="left", padx=4)

        self.excl_var = tk.BooleanVar(value=bool(self.cfg.get("wasapi_exclusive", True)))
        tk.Checkbutton(
            row2, text="Экскл. WASAPI (низкая задержка)", variable=self.excl_var,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=PANEL, highlightthickness=0,
        ).pack(side="left", padx=(12, 0))

        self._label(row2, "Мониторинг:", bg=PANEL).pack(side="left", padx=(12, 0))
        self.mon_var = tk.DoubleVar(value=float(self.cfg.get("monitor_gain", 1.0)))
        tk.Scale(row2, from_=0.0, to=1.0, resolution=0.05, orient="horizontal", length=110,
                 variable=self.mon_var, bg=PANEL, fg=FG, highlightthickness=0,
                 command=lambda _v: self.engine.set_gains(monitor=float(_v))).pack(side="left")

        self._label(row2, "Громкость лупов:", bg=PANEL).pack(side="left", padx=(12, 0))
        self.mst_var = tk.DoubleVar(value=float(self.cfg.get("master_gain", 1.0)))
        tk.Scale(row2, from_=0.0, to=1.2, resolution=0.05, orient="horizontal", length=110,
                 variable=self.mst_var, bg=PANEL, fg=FG, highlightthickness=0,
                 command=lambda _v: self.engine.set_gains(master=float(_v))).pack(side="left")

        tk.Button(row2, text="Применить", bg=ACCENT, fg="white", activebackground="#3498db",
                  command=self.apply_devices, font=("Segoe UI", 9, "bold")).pack(side="left", padx=10)
        tk.Button(row2, text="Обновить списки", bg=PANEL, fg=FG,
                  command=self._refresh_devices).pack(side="left")

        self.in_map = {}
        self.out_map = {}

    def _build_mapping(self) -> None:
        frame = tk.Frame(self.tab_settings, bg=BG, pady=4)
        frame.pack(fill="both", padx=10)
        self._label(frame, "MIDI-назначения (нажмите «Назначить», затем кнопку на SMK-37):",
                    font=("Segoe UI", 9, "bold")).pack(anchor="w")
        grid = tk.Frame(frame, bg=BG)
        grid.pack(fill="x", pady=4)
        self.map_widgets = {}
        for idx, (action, label) in enumerate(ACTIONS):
            r = idx % 8
            c = 0 if idx < 8 else 3
            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=r, column=c, sticky="w", padx=6, pady=1)
            tk.Label(cell, text=label, bg=BG, fg=FG, width=26, anchor="w").pack(side="left")
            val = tk.Label(cell, text="—", bg=PANEL, fg=WARN, width=16)
            val.pack(side="left", padx=4)
            btn = tk.Button(cell, text="Назначить", bg=ACCENT, fg="white", width=9,
                            command=lambda a=action: self.start_learn(a))
            btn.pack(side="left", padx=2)
            tk.Button(cell, text="×", bg=PANEL, fg=FG, width=2,
                      command=lambda a=action: self.clear_binding(a)).pack(side="left")
            self.map_widgets[action] = (val, btn)

    def _build_log(self) -> None:
        frame = tk.Frame(self.tab_settings, bg=BG)
        frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self._label(frame, "Журнал:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.log_text = tk.Text(frame, height=8, bg="#111", fg=DIM, relief="flat",
                                font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log(f"Готово. Версия {APP_VERSION} — при любых вопросах называйте её.")
        self.log("Устройства — здесь; запись и лупы — во вкладке «Лупы».")

    # ------------------------------------------------------------------
    # Параметры вкладки «Лупы»
    # ------------------------------------------------------------------
    def _apply_loops_params(self) -> None:
        try:
            self.engine.bpm = max(30.0, min(300.0, float(self.bpm_var.get())))
        except Exception:
            pass
        try:
            self.engine.beats_per_bar = max(1, min(12, int(self.meter_var.get())))
        except Exception:
            pass
        try:
            self.engine.bars_per_block = max(1, min(64, int(self.bars_var.get())))
        except Exception:
            pass
        try:
            self.engine.num_blocks = max(1, min(64, int(self.blocks_var.get())))
        except Exception:
            pass
        self.engine.update_grid()
        self.engine.metronome_on = bool(self.metro_var.get())
        self.engine.metro_sound = self.metro_sound_var.get()
        self.engine.metro_gain = float(self.metro_gain_var.get())
        self.engine.autostart = self.autostart_var.get() == "auto"
        self.engine.threshold = float(self.thr_var.get())
        self.engine.drum_on = bool(self.drum_var.get())
        self.engine.drum_style = self.drum_style_var.get()
        self.engine.drum_gain = float(self.drum_gain_var.get())

        self.cfg.update({
            "bpm": self.engine.bpm,
            "beats_per_bar": self.engine.beats_per_bar,
            "bars_per_block": self.engine.bars_per_block,
            "num_blocks": self.engine.num_blocks,
            "metronome_on": self.engine.metronome_on,
            "metro_sound": self.engine.metro_sound,
            "metro_gain": self.engine.metro_gain,
            "autostart": self.engine.autostart,
            "threshold": self.engine.threshold,
            "drum_on": self.engine.drum_on,
            "drum_style": self.engine.drum_style,
            "drum_gain": self.engine.drum_gain,
        })

        total_bars = self.engine.fixed_total_bars()
        self.total_label.config(
            text=f"Итого: {total_bars} такт. ≈ {self.engine.fixed_total_sec():.1f} с"
        )
        for r in self.rows:
            r.force_redraw()

    # ------------------------------------------------------------------
    # Драм-машина
    # ------------------------------------------------------------------
    def _toggle_drum(self) -> None:
        self.drum_var.set(not self.drum_var.get())
        self._apply_loops_params()
        self._sync_drum_ui()
        self.log("Драм-машина: " + ("включена (вступление с такта)" if self.drum_var.get()
                                    else "выключена"))

    def _sync_drum_ui(self) -> None:
        """Вид кнопки и видимость спойлера — по состоянию драм-машины."""
        on = bool(self.drum_var.get())
        self.drum_btn.configure(
            bg=PLAY if on else PANEL,
            fg="#111" if on else FG,
            activebackground="#5ee07f" if on else "#3a3a3c",
            activeforeground="#111" if on else FG,
        )
        if on:
            self.drum_panel.pack(fill="x", padx=10, after=self.metro_panel)
        else:
            self.drum_panel.pack_forget()

    def _toggle_drum_preview(self) -> None:
        if not self.engine.running:
            self.log("Звук не запущен — предпрослушивание недоступно")
            return
        if self.engine.transport == "running":
            self.log("Транспорт уже играет — драмы звучат вместе с ним")
            return
        self.engine.post("toggle_drum_preview")

    def _loops_params_sig(self) -> str:
        return (f"{self.engine.bpm:.2f}|{self.engine.beats_per_bar}|"
                f"{self.engine.bars_per_block}|{self.engine.num_blocks}|"
                f"{float(self.zoom_var.get())}")

    # ------------------------------------------------------------------
    # Устройства
    # ------------------------------------------------------------------
    def _refresh_devices(self) -> None:
        try:
            import sounddevice as sd

            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            short_api = {
                "MME": "MME",
                "Windows WASAPI": "WASAPI",
                "Windows DirectSound": "DirectSound",
                "Windows WDM-KS": "WDM-KS",
                "ASIO": "ASIO",
            }
            ins, in_map = [], {}
            outs, out_map = [], {}
            for i, d in enumerate(devices):
                api_idx = d.get("hostapi", 0)
                api = hostapis[api_idx]["name"] if api_idx < len(hostapis) else "?"
                api = short_api.get(api, api)
                rate = int(d.get("default_samplerate", 0))
                if d["max_input_channels"] > 0:
                    name = f"{api}: {d['name']}  [{rate // 1000}к]"
                    ins.append(name)
                    in_map[name] = i
                if d["max_output_channels"] > 0:
                    name = f"{api}: {d['name']}  [{rate // 1000}к]"
                    outs.append(name)
                    out_map[name] = i
            self.in_map, self.out_map = in_map, out_map
            self.in_box["values"] = ins
            self.out_box["values"] = outs
            saved_in = self.cfg.get("audio_input", "")
            saved_out = self.cfg.get("audio_output", "")
            self.in_var.set(next((v for v in ins if saved_in and saved_in in v), ins[0] if ins else ""))
            self.out_var.set(next((v for v in outs if saved_out and saved_out in v), outs[0] if outs else ""))
        except Exception as e:
            self.log(f"Не удалось получить список аудиоустройств: {e}")

        names = self.midi.list_inputs()
        self.midi_box["values"] = names
        saved_midi = self.cfg.get("midi_input", "")
        if names:
            self.midi_var.set(next((v for v in names if saved_midi and saved_midi in v), names[0]))

    def apply_devices(self) -> None:
        try:
            in_name, out_name = self.in_var.get(), self.out_var.get()
            if not in_name or not out_name:
                raise RuntimeError("Выберите аудио вход и выход")
            self.engine.sample_rate = int(self.sr_var.get())
            self.engine.blocksize = int(self.bs_var.get())
            self.engine.wasapi_exclusive = bool(self.excl_var.get())
            self.engine.start(self.in_map[in_name], self.out_map[out_name])
            self.cfg["audio_input"] = in_name
            self.cfg["audio_output"] = out_name
            self.cfg["sample_rate"] = self.engine.sample_rate
            self.cfg["blocksize"] = self.engine.blocksize
            self.cfg["wasapi_exclusive"] = self.engine.wasapi_exclusive
            self.sr_var.set(str(self.engine.sample_rate))
            mode_txt = ("дуплекс" if self.engine.mode == "duplex"
                        else "раздельные потоки (вход и выход независимо)")
            if "ASIO" in self.engine.in_api or "ASIO" in self.engine.out_api:
                mode_txt += ", ASIO"
            elif self.engine.exclusive:
                mode_txt += ", эксклюзивный WASAPI"
            self.log(f"Аудио запущено ({mode_txt}): {in_name} → {out_name} "
                     f"({self.engine.sample_rate} Гц, буфер {self.engine.blocksize})")
            self.log(f"Оценка задержки мониторинга: ≈{self.engine.latency_ms():.0f} мс.")
            if self.engine.mode == "split":
                self.log("Вход и выход открыты раздельно — так работает при разных "
                         "host API у устройств. Синхронизация компенсируется автоматически.")
            if "MME" in in_name or "MME" in out_name or "DirectSound" in in_name or "DirectSound" in out_name:
                self.log("Совет: MME/DirectSound дают большую задержку — по возможности "
                         "берите WASAPI- или ASIO-версии тех же устройств из списка.")
        except Exception as e:
            self.log(f"Ошибка аудио: {e}")
            messagebox.showerror("Аудио", f"Не удалось открыть аудио:\n{e}")

        try:
            midi_name = self.midi_var.get()
            if midi_name:
                self.midi.open(midi_name)
                self.cfg["midi_input"] = midi_name
                self.log(f"MIDI подключён: {midi_name}")
        except Exception as e:
            self.log(f"Ошибка MIDI: {e}")
            messagebox.showerror("MIDI", f"Не удалось открыть MIDI:\n{e}")

    # ------------------------------------------------------------------
    # MIDI-learn и действия
    # ------------------------------------------------------------------
    def start_learn(self, action: str) -> None:
        if not self.midi.port_name:
            messagebox.showwarning("MIDI", "Сначала подключите MIDI-устройство («Применить»).")
            return
        self.learn_action = action
        self.learn_started = time.time()
        self.midi.start_learn(action)
        btn = self.map_widgets[action][1]
        btn.config(text="Ждите...", bg=WARN, fg="#111")
        self.log(f"MIDI-learn: нажмите кнопку на клавиатуре для «{ACTION_LABELS[action]}»")

    def clear_binding(self, action: str) -> None:
        self.mapping = {k: v for k, v in self.mapping.items() if v != action}
        self.cfg["mapping"] = self.mapping
        self._refresh_mapping_widgets()
        self.log(f"Назначение сброшено: {ACTION_LABELS[action]}")

    def do_action(self, action: str) -> None:
        if not self.engine.running and action in (
                "toggle_rec", "toggle_play", "stop_all", "toggle_drum_preview"):
            self.log("Сначала запустите аудио («Применить» в верхней панели)")
            return
        self.engine.post(action)
        self.log(f"→ {ACTION_LABELS.get(action, action)}")

    def _dispatch_key(self, key: str) -> None:
        action = self.mapping.get(key)
        if action:
            self.do_action(action)

    def _refresh_mapping_widgets(self) -> None:
        key_by_action = {}
        for key, action in self.mapping.items():
            key_by_action.setdefault(action, []).append(key)
        for action, (val, btn) in self.map_widgets.items():
            keys = key_by_action.get(action, [])
            val.config(text=", ".join(keys) if keys else "—")
            btn.config(text="Назначить", bg=ACCENT, fg="white")

    # ------------------------------------------------------------------
    # Прочее
    # ------------------------------------------------------------------
    def export_wav(self) -> None:
        n = self.engine.current_slot
        out_dir = Path(__file__).resolve().parent / "exports"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"loop_slot{n + 1}_{datetime.now():%Y%m%d_%H%M%S}.wav"
        try:
            self.engine.export_slot_wav(n, path)
            self.log(f"Луп {n + 1} сохранён: {path.name}")
        except Exception as e:
            self.log(f"Экспорт не удался: {e}")

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 300:
            self.log_text.delete("1.0", "50.0")

    def _bind_hotkeys(self) -> None:
        self.bind("<space>", lambda _e: self.do_action("toggle_play"))
        self.bind("r", lambda _e: self.do_action("toggle_rec"))
        self.bind("u", lambda _e: self.do_action("undo"))
        self.bind("c", lambda _e: self.do_action("clear"))
        self.bind("s", lambda _e: self.do_action("stop_all"))
        self.bind("m", lambda _e: self.do_action("toggle_metronome"))
        self.bind("]", lambda _e: self.do_action("next_slot"))
        self.bind("[", lambda _e: self.do_action("prev_slot"))
        for i in range(1, 5):
            self.bind(str(i), lambda _e, n=i: self.engine.post("select_slot", n - 1))

    # ------------------------------------------------------------------
    # Опрос очередей и обновление UI
    # ------------------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                item = self.ui_q.get_nowait()
                kind = item[0]
                if kind == "learn":
                    _, action, key = item
                    if self.learn_action == action:
                        self.mapping = {k: v for k, v in self.mapping.items() if v != action}
                        self.mapping[key] = action
                        self.cfg["mapping"] = self.mapping
                        self.learn_action = None
                        self.midi.cancel_learn()
                        self._refresh_mapping_widgets()
                        self.log(f"Назначено: «{ACTION_LABELS[action]}» ← {key}")
                elif kind == "midi":
                    self._dispatch_key(item[1])
        except queue.Empty:
            pass

        if self.learn_action and time.time() - self.learn_started > 15:
            self.log("MIDI-learn отменён по таймауту")
            self.midi.cancel_learn()
            self.learn_action = None
            self._refresh_mapping_widgets()

        self._update_from_snapshot_safe()
        self.after(80, self._poll)

    def _update_from_snapshot_safe(self) -> None:
        """Ошибки UI не должны убивать цикл опроса (иначе индикаторы замирают)."""
        try:
            self._update_from_snapshot()
        except Exception:
            import traceback
            self.log("Ошибка обновления интерфейса:")
            for line in traceback.format_exc().strip().splitlines()[-6:]:
                self.log("   " + line)

    def _update_from_snapshot(self) -> None:
        # Синхронизация драм-машины (состояние могло измениться с MIDI)
        if self.drum_var.get() != self.engine.drum_on:
            self.drum_var.set(self.engine.drum_on)
            self._sync_drum_ui()
        preview = bool(getattr(self.engine, "drum_preview", False))
        if getattr(self, "_preview_shown", None) != preview:
            self._preview_shown = preview
            self.drum_prev_btn.config(
                text="■ Стоп прослушивания" if preview else "▶ Предпрослушивание",
                bg=REC if preview else PANEL,
                fg="white" if preview else FG,
                activebackground="#ff6961" if preview else "#3a3a3c",
                activeforeground="white" if preview else FG,
            )
        snap = self.engine.snapshot()
        current = snap["current"]
        sig = self._loops_params_sig()
        for i, info in enumerate(snap["slots"]):
            if i >= len(self.rows):
                break
            info["wave_gen"] = self.engine.slots[i].wave_gen
            info["armed_here"] = bool(snap.get("armed")) and i == current
            self.rows[i].update_row(info, selected=(i == current), params_sig=sig)

        self._draw_meter(self.in_meter, snap["in_peak"], threshold=self.engine.threshold)
        self._draw_meter(self.out_meter, snap["out_peak"])

        # Журнал: видимые события ожидания/порога (диагностика «Ждём звук»).
        cur_info = snap["slots"][current] if current < len(snap["slots"]) else {}
        waiting_now = bool(snap.get("armed") or cur_info.get("waiting"))
        if waiting_now and not getattr(self, "_was_waiting", False):
            self.log(f"Авто по звуку: ждём превышения порога {self.engine.threshold:.2f} — "
                     "запись начнётся со звука")
        elif not waiting_now and getattr(self, "_was_waiting", False):
            if cur_info.get("recording"):
                self.log("Порог превышен — запись и проигрывание с этого момента")
            else:
                self.log("Ожидание звука завершено")
        self._was_waiting = waiting_now

        if self.engine.running:
            cur = self.engine.slots[current]
            state = cur.state
            text = f"● Луп {current + 1}: {state.value}"
            tr = snap["transport"]
            if waiting_now:
                text = f"● Луп {current + 1}: Ждём звук — запишется с порога"
            elif tr == "paused":
                text = f"❚❚ Пауза — все лупы вместе на позиции {snap['pos'] * 100:.0f}%"
            elif state == SlotState.RECORDING:
                text += (f"  блок {snap['fixed_block'] + 1}/{snap['fixed_blocks']}"
                         f"  такт {int(snap['fixed_block_frac'] * self.engine.bars_per_block) + 1}"
                         f"/{self.engine.bars_per_block}")
            self.status_label.config(text=text, fg=STATE_COLORS.get(state, FG))
        else:
            self.status_label.config(text="● Не подключено", fg=DIM)

    @staticmethod
    def _draw_meter(canvas: tk.Canvas, peak: float, threshold: float = None) -> None:
        canvas.delete("all")
        w = int(canvas["width"])
        filled = int(w * min(1.0, peak))
        color = PLAY if peak < 0.7 else (WARN if peak < 0.95 else REC)
        canvas.create_rectangle(0, 0, filled, 14, fill=color, width=0)
        if threshold is not None:
            # Красная полоска порога — двигается вместе с регулятором.
            tx = max(0, min(w, int(w * threshold)))
            canvas.create_line(tx, 0, tx, 14, fill=REC, width=2)

    def _close(self) -> None:
        self.midi.cancel_learn()
        self.midi.close()
        self.engine.stop()
        self.cfg["monitor_gain"] = float(self.mon_var.get())
        self.cfg["master_gain"] = float(self.mst_var.get())
        self.cfg["mapping"] = self.mapping
        self._apply_loops_params()
        if self._on_close:
            self._on_close(self.cfg)
        self.destroy()

    def run(self) -> None:
        self.mainloop()
