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
from drums import STYLE_NAMES, canonical

APP_VERSION = "r8.8 (2026-10-13)"

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

# ----------------------------------------------------------------------
# Палитра: современная тёмная рабочая станция (см. описание стиля)
# ----------------------------------------------------------------------
BG = "#0B0E12"          # фон приложения
PANEL = "#141920"        # основная поверхность
PANEL2 = "#1A2028"       # приподнятая поверхность
CTRL = "#202832"         # поверхность контролов
BORDER = "#2A333E"       # тонкая граница
INSET = "#0B0E12"        # внутренние поля (метры, списки, дорожки ползунков)
FG = "#EEF3F8"           # основной текст
DIM = "#8D9AA8"          # вторичный текст
MUTED = "#63707D"        # приглушённый текст
ACCENT = "#66B7FF"       # выбор и фокус
REC = "#FF6571"          # запись
PLAY = "#4BD39A"         # воспроизведение
WARN = "#EDBD60"         # ожидание сигнала
WAVE = "#4FA98F"         # волноформа (приглушённый зелёно-бирюзовый)

HOVER = "#2A333E"        # наведение обычной кнопки
DOWN = "#151B22"         # нажатие

# Сетка тактов в дорожках
BAR_A = "#141920"        # такт чётный
BAR_B = "#1E2530"        # такт нечётный (заметно светлее/темнее)
BAR_LINE = "#2A333E"     # тонкая линия такта
BLOCK_LINE = "#55636E"   # заметная линия блока
WAVE_MISS = "#5C6874"    # блок без порога

STATE_COLORS = {
    SlotState.EMPTY: DIM,
    SlotState.LOADED: PLAY,
    SlotState.RECORDING: REC,
}

METER_WIDTH = 220
WAVE_BINS = 180
ROW_H = 64

# Шрифт выбираем из установленных (Segoe UI Variable → Segoe UI → Inter)
FONT_FAMILY = "Segoe UI"
FONT = (FONT_FAMILY, 9)
FONT_B = (FONT_FAMILY, 9, "bold")
FONT_BIG = (FONT_FAMILY, 11, "bold")
FONT_NUM = ("Consolas", 10, "bold")   # табличные цифры для показаний


def _pick_font_family(root) -> str:
    try:
        import tkinter.font as tkfont
        fams = {f.lower() for f in tkfont.families(root)}
        for cand in ("Segoe UI Variable", "Segoe UI", "Inter"):
            if cand.lower() in fams:
                return cand
    except Exception:
        pass
    return "Segoe UI"


class LoopRow:
    """Строка лупа: номер, статус, дорожка с волноформой/тактами/блоками."""

    def __init__(self, parent, index: int, app: "App"):
        self.index = index
        self.app = app
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(fill="x", pady=2)

        self.num_btn = tk.Button(
            self.frame, text=f"Луп {index + 1}", width=10,
            bg=CTRL, fg=FG, activebackground=HOVER, activeforeground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            font=FONT_BIG,
            command=lambda n=index: app.click_slot(n),
        )
        self.num_btn.pack(side="left", fill="y")

        holder = tk.Frame(self.frame, bg=BG)
        holder.pack(side="left", fill="x", expand=True)
        self.canvas = tk.Canvas(holder, height=ROW_H, bg=BAR_A, highlightthickness=1,
                                highlightbackground=BORDER)
        self.canvas.pack(fill="x", expand=True)

        self.canvas.bind("<Button-1>", lambda _e, n=index: app.select_slot(n))
        self._last_w = 0
        self.canvas.bind("<Configure>", self._on_resize)

        self._sig = ""
        self._wave_gen = -1
        self._playhead = None

    def _on_resize(self, event) -> None:
        # Ширина окна изменилась — перерисуем сетку под новую ширину.
        if abs(event.width - self._last_w) > 2:
            self._last_w = event.width
            self.force_redraw()

    def _cw(self) -> int:
        """Фактическая ширина дорожки — вся волноформа всегда в неё вмещается."""
        w = self.canvas.winfo_width()
        return w if w > 2 else max(300, self.app.viewport_width())

    def update_row(self, snap_info: dict, selected: bool, params_sig: str) -> None:
        border = ACCENT if selected else BORDER
        self.canvas.configure(highlightbackground=border)

        # Кнопка лупа: серый — не выбран; зелёный — выбран; жёлтый — приглушён;
        # красный — идёт запись или ожидание порога для этого лупа.
        muted = snap_info.get("muted", False)
        if selected and (snap_info.get("recording") or snap_info.get("armed_here")
                         or snap_info.get("waiting")):
            self.num_btn.configure(bg=REC, fg="#0B0E12",
                                   activebackground="#FF8A92", activeforeground="#0B0E12")
        elif muted:
            self.num_btn.configure(bg=WARN, fg="#0B0E12",
                                   activebackground="#F2CC7E", activeforeground="#0B0E12")
        elif selected:
            self.num_btn.configure(bg=PLAY, fg="#0B0E12",
                                   activebackground="#6BE0AE", activeforeground="#0B0E12")
        else:
            self.num_btn.configure(bg=CTRL, fg=FG,
                                   activebackground=HOVER, activeforeground=FG)

        redraw_full = params_sig != self._sig
        wave_changed = snap_info.get("wave_gen", self._wave_gen) != self._wave_gen
        if redraw_full or wave_changed:
            self._draw(params_sig, snap_info, wave_changed, redraw_full)
        # Позиция курсора воспроизведения — по всей ширине дорожки
        frac = snap_info.get("pos", 0.0)
        x = frac * self._cw()
        if self._playhead is None:
            self._playhead = self.canvas.create_line(x, 0, x, ROW_H, fill=WARN, width=2)
        else:
            self.canvas.coords(self._playhead, x, 0, x, ROW_H)

    def _draw(self, params_sig: str, snap_info: dict, wave_changed: bool, full: bool) -> None:
        app = self.app
        w = self._cw()
        if full:
            self.canvas.delete("all")
            self._playhead = None
            self._sig = params_sig
            total_bars = max(1, app.engine.bars_per_block * app.engine.num_blocks)
            bar_w = w / total_bars
            # Такты цветом: каждый следующий — чередованием светлее/темнее
            for i in range(total_bars):
                x0 = i * bar_w
                self.canvas.create_rectangle(
                    x0, 0, x0 + bar_w + 1, ROW_H,
                    fill=BAR_A if i % 2 == 0 else BAR_B, width=0)
            # Линии тактов (тонкие) и блоков (заметные полоски)
            for i in range(total_bars + 1):
                x = i * bar_w
                is_block = (i % app.engine.bars_per_block) == 0
                self.canvas.create_line(x, 0, x, ROW_H,
                                        fill=BLOCK_LINE if is_block else BAR_LINE,
                                        width=3 if is_block else 1)
        # Волноформа: зелёно-бирюзовый — порог был достаточным, серый — нет.
        self.canvas.delete("wave")
        wave, hits = app.engine.get_wave(self.index)
        self._wave_gen = snap_info.get("wave_gen", -1)
        half = ROW_H / 2 - 4
        bins = len(wave)
        bw = w / bins
        for i, v in enumerate(wave):
            h = min(1.0, float(v)) * half
            if h < 0.5:
                continue
            color = WAVE if (i < len(hits) and hits[i]) else WAVE_MISS
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
        global FONT_FAMILY, FONT, FONT_B, FONT_BIG
        FONT_FAMILY = _pick_font_family(self)
        FONT = (FONT_FAMILY, 9)
        FONT_B = (FONT_FAMILY, 9, "bold")
        FONT_BIG = (FONT_FAMILY, 11, "bold")
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
        self._setup_styles()

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)
        self.tab_loops = tk.Frame(self.notebook, bg=BG)
        self.tab_midi = tk.Frame(self.notebook, bg=BG)
        self.tab_settings = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(self.tab_loops, text="   Лупы   ")
        self.notebook.add(self.tab_midi, text="   MIDI   ")
        self.notebook.add(self.tab_settings, text="   Настройки   ")

        self._build_loops_tab()
        self._build_midi_tab()
        self._build_settings_tab()

        self._refresh_devices()
        self._refresh_mapping_widgets()
        self._bind_hotkeys()

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._poll)

    # ------------------------------------------------------------------
    # Общие помощники
    # ------------------------------------------------------------------
    def _setup_styles(self) -> None:
        """Единый тёмный стиль для ttk-виджетов (вкладки, комбобоксы)."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox", fieldbackground=INSET, background=CTRL,
                        foreground=FG, arrowcolor=FG, bordercolor=BORDER,
                        selectbackground=INSET, selectforeground=FG,
                        lightcolor=BORDER, darkcolor=BORDER)
        style.map("TCombobox",
                  fieldbackground=[("readonly", INSET)],
                  foreground=[("readonly", FG)],
                  selectbackground=[("readonly", INSET)],
                  selectforeground=[("readonly", FG)],
                  arrowcolor=[("readonly", FG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=CTRL, foreground=DIM,
                        padding=(16, 7), font=(FONT_FAMILY, 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "#0B0E12")])

    def _label(self, parent, text, **kw):
        kw.setdefault("bg", BG)
        kw.setdefault("fg", FG)
        kw.setdefault("font", FONT)
        # tk.Label не понимает кортежные padx/pady (это синтаксис pack) —
        # приводим к числу, чтобы окно никогда не падало на этом
        for key in ("padx", "pady"):
            if key in kw and isinstance(kw[key], (tuple, list)):
                kw[key] = kw[key][0]
        return tk.Label(parent, text=text, **kw)

    def _hint(self, parent, text):
        """Подсказка мелким шрифтом — отдельной строкой, не в ряду."""
        lbl = tk.Label(parent, text=text, bg=PANEL, fg=DIM,
                       font=(FONT_FAMILY, 8), justify="left", anchor="w",
                       wraplength=940)
        lbl.pack(fill="x", pady=(6, 0))
        return lbl

    def _panel(self, parent, title):
        frame = tk.Frame(parent, bg=PANEL, pady=10, padx=12,
                         highlightthickness=1, highlightbackground=BORDER)
        frame.pack(fill="x", padx=12, pady=5)
        self._label(frame, title, font=FONT_B, bg=PANEL).pack(anchor="w")
        return frame

    def _button(self, parent, text, command, main=False, rec=False, play=False,
                width=None, height=1, font=None, bg=None, fg=None, **kw):
        """Кнопка в общем стиле: тёмная поверхность, тонкая граница, голубой фокус."""
        kw.setdefault("relief", "flat")
        kw.setdefault("bd", 0)
        kw.setdefault("highlightthickness", 1)
        kw.setdefault("highlightbackground", BORDER)
        kw.setdefault("highlightcolor", ACCENT)
        kw.setdefault("font", font or (FONT_BIG if main else FONT))
        kw.setdefault("activebackground", HOVER)
        kw.setdefault("activeforeground", FG)
        if rec:
            kw.setdefault("highlightbackground", REC)
        if play:
            kw.setdefault("highlightbackground", PLAY)
        if bg is not None:
            kw["bg"] = bg
        else:
            kw.setdefault("bg", CTRL)
        if fg is not None:
            kw["fg"] = fg
        else:
            kw.setdefault("fg", FG)
        if width is not None:
            kw["width"] = width
        kw["height"] = height
        kw["command"] = command
        return tk.Button(parent, text=text, **kw)

    def _scale(self, parent, var, from_, to, resolution, command, length=130):
        """Ползунок с тонкой дорожкой и значением в процентах рядом."""
        wrap = tk.Frame(parent, bg=parent["bg"] if isinstance(parent, tk.Widget) else PANEL)
        wrap_bg = wrap["bg"]

        def _cmd(v):
            try:
                val_lbl.config(text=f"{float(v) * 100:.0f}%")
            except Exception:
                pass
            command(v)

        sc = tk.Scale(wrap, from_=from_, to=to, resolution=resolution,
                      orient="horizontal", length=length, variable=var,
                      bg=wrap_bg, fg=FG, troughcolor=INSET, bd=0,
                      highlightthickness=1, highlightbackground=BORDER,
                      activebackground=ACCENT, sliderrelief="flat",
                      showvalue=False, command=_cmd)
        sc.pack(side="left")
        val_lbl = tk.Label(wrap, text="0%", width=5, bg=wrap_bg, fg=DIM,
                           font=FONT, anchor="w")
        val_lbl.pack(side="left", padx=(6, 0))
        # При СБОРКЕ окна инициализируем только подпись: command НЕ вызываем,
        # иначе он сработает до создания остальных виджетов (падение на старте).
        try:
            val_lbl.config(text=f"{float(var.get()) * 100:.0f}%")
        except Exception:
            pass
        return wrap

    def content_width(self) -> int:
        # Вся сетка цикла всегда вмещается в ширину окна — зума больше нет.
        return self.viewport_width()

    def viewport_width(self) -> int:
        return max(300, self.winfo_width() - 180)

    # ==================================================================
    # Вкладка «Лупы»
    # ==================================================================
    def _build_loops_tab(self) -> None:
        # --- Индикация звука ---
        row = tk.Frame(self.tab_loops, bg=BG, pady=8)
        row.pack(fill="x", padx=12)
        self._label(row, "ВХОД", width=6, anchor="w", font=FONT_B, fg=DIM).pack(side="left")
        self.in_meter = tk.Canvas(row, width=METER_WIDTH, height=14, bg=INSET,
                                  highlightthickness=1, highlightbackground=BORDER)
        self.in_meter.pack(side="left", padx=(4, 16))
        self._label(row, "ВЫХОД", width=6, anchor="w", font=FONT_B, fg=DIM).pack(side="left")
        self.out_meter = tk.Canvas(row, width=METER_WIDTH, height=14, bg=INSET,
                                   highlightthickness=1, highlightbackground=BORDER)
        self.out_meter.pack(side="left", padx=4)
        self.status_label = self._label(row, "● Не подключено", fg=DIM,
                                        font=(FONT_FAMILY, 10, "bold"))
        self.status_label.pack(side="right")

        # --- Метроном / Драм-машина ---
        m = self._panel(self.tab_loops, "Метроном / Драм-машина")
        mtop = tk.Frame(m, bg=PANEL)
        mtop.pack(fill="x")
        self.metro_var = tk.BooleanVar(value=bool(self.cfg.get("metronome_on", False)))
        tk.Checkbutton(mtop, text="Метроном вкл.", variable=self.metro_var, bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG, selectcolor=CTRL,
                       highlightthickness=0, font=FONT,
                       command=self._apply_loops_params).pack(side="left")

        self._label(mtop, "Звук щелчка:", bg=PANEL).pack(side="left", padx=(18, 0))
        self.metro_sound_var = tk.StringVar(value=str(self.cfg.get("metro_sound", "wood")))
        ttk.Combobox(mtop, textvariable=self.metro_sound_var, width=12, state="readonly",
                     values=("wood", "electronic", "beep", "rim")).pack(side="left", padx=4)
        self.metro_sound_var.trace_add("write", lambda *_: self._apply_loops_params())

        self._label(mtop, "Громкость:", bg=PANEL).pack(side="left", padx=(12, 0))
        self.metro_gain_var = tk.DoubleVar(value=float(self.cfg.get("metro_gain", 0.5)))
        self._scale(mtop, self.metro_gain_var, 0.0, 1.0, 0.05,
                    lambda _v: self._apply_loops_params(), length=110).pack(side="left")

        # Кнопка «Драм-машина» — под метрономом: шеврон раскрывает настройки,
        # тумблер рядом включает/выключает барабаны.
        dbot = tk.Frame(m, bg=PANEL)
        dbot.pack(fill="x", pady=(6, 0))
        self._drum_open = True
        self.drum_btn = tk.Button(
            dbot, text="▾  Драм-машина", width=15,
            bg=PANEL2, fg=FG, activebackground=HOVER, activeforeground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            font=FONT_B, command=self._toggle_drum_panel,
        )
        self.drum_btn.pack(side="left")
        self.drum_var = tk.BooleanVar(value=bool(self.cfg.get("drum_on", False)))
        tk.Checkbutton(dbot, text="Драм вкл.", variable=self.drum_var, bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG, selectcolor=CTRL,
                       highlightthickness=0, font=FONT,
                       command=self._on_drum_check).pack(side="left", padx=(12, 0))
        self.metro_panel = m

        # Панель настроек драм-машины (раскрывается шевроном)
        self.drum_panel = tk.Frame(m, bg=PANEL2, pady=8, padx=10,
                                   highlightthickness=1, highlightbackground=BORDER)
        dtop = tk.Frame(self.drum_panel, bg=PANEL2)
        dtop.pack(fill="x")
        self.drum_style_var = tk.StringVar(value=canonical(self.cfg.get("drum_style", "Поп")))
        self._label(dtop, "Стиль:", bg=PANEL2).pack(side="left")
        ttk.Combobox(dtop, textvariable=self.drum_style_var, width=15, state="readonly",
                     values=STYLE_NAMES).pack(side="left", padx=4)
        self.drum_style_var.trace_add("write", lambda *_: self._apply_loops_params())

        self.drum_prev_btn = tk.Button(
            dtop, text="▶  Предпрослушивание", width=19,
            bg=CTRL, fg=FG, activebackground=HOVER, activeforeground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT, font=FONT,
            command=self._toggle_drum_preview,
        )
        self.drum_prev_btn.pack(side="left", padx=(18, 0))

        self._label(dtop, "Громкость:", bg=PANEL2).pack(side="left", padx=(18, 0))
        self.drum_gain_var = tk.DoubleVar(value=float(self.cfg.get("drum_gain", 0.7)))
        self._scale(dtop, self.drum_gain_var, 0.0, 1.5, 0.05,
                    lambda _v: self._apply_loops_params(), length=110).pack(side="left")
        self._hint_drum = tk.Label(
            self.drum_panel,
            text="Играет вместе с транспортом: крэш — на первом такте цикла, "
                 "брейк — на последнем. В лупы не пишется.",
            bg=PANEL2, fg=DIM, font=(FONT_FAMILY, 8), justify="left", anchor="w",
            wraplength=900)
        self._hint_drum.pack(fill="x", pady=(6, 0))
        self._sync_drum_ui()
        self._hint(m, "Темп и размер — общие с циклом (см. «Запись»). "
                      "Щелчки только при проигрывании, в лупы не пишутся.")

        # --- Темп / такты / блоки (ОБЩИЙ темп для метронома и цикла) ---
        b = self._panel(self.tab_loops, "Запись: темп, такты и блоки (общий темп)")
        r1 = tk.Frame(b, bg=PANEL)
        r1.pack(fill="x", pady=(2, 0))
        self._label(r1, "ТЕМП", bg=PANEL, width=13, anchor="w", fg=DIM).pack(side="left")
        self.bpm_var = tk.StringVar(value=str(int(float(self.cfg.get("bpm", 120)))))
        tk.Spinbox(r1, from_=30, to=300, width=5, textvariable=self.bpm_var,
                   bg=INSET, fg=FG, buttonbackground=CTRL, font=FONT_NUM,
                   relief="flat", bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT,
                   command=self._apply_loops_params).pack(side="left", padx=4)
        self.bpm_var.trace_add("write", lambda *_: self._apply_loops_params())
        self._label(r1, "BPM", bg=PANEL, width=4, anchor="w").pack(side="left")

        self._label(r1, "Размер:", bg=PANEL).pack(side="left", padx=(18, 0))
        self.meter_var = tk.StringVar(value=str(self.cfg.get("beats_per_bar", 4)))
        ttk.Combobox(r1, textvariable=self.meter_var, width=5, state="readonly",
                     values=("2", "3", "4", "5", "6")).pack(side="left", padx=4)
        self.meter_var.trace_add("write", lambda *_: self._apply_loops_params())
        self._label(r1, "/4", bg=PANEL).pack(side="left")

        r2 = tk.Frame(b, bg=PANEL)
        r2.pack(fill="x", pady=(8, 0))
        self._label(r2, "Тактов в блоке:", bg=PANEL, width=13, anchor="w").pack(side="left")
        self.bars_var = tk.StringVar(value=str(self.cfg.get("bars_per_block", 4)))
        tk.Spinbox(r2, from_=1, to=64, width=5, textvariable=self.bars_var,
                   bg=INSET, fg=FG, buttonbackground=CTRL, font=FONT_NUM,
                   relief="flat", bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT,
                   command=self._apply_loops_params).pack(side="left", padx=4)
        self.bars_var.trace_add("write", lambda *_: self._apply_loops_params())

        self._label(r2, "Блоков:", bg=PANEL).pack(side="left", padx=(18, 0))
        self.blocks_var = tk.StringVar(value=str(self.cfg.get("num_blocks", 2)))
        tk.Spinbox(r2, from_=1, to=64, width=5, textvariable=self.blocks_var,
                   bg=INSET, fg=FG, buttonbackground=CTRL, font=FONT_NUM,
                   relief="flat", bd=0, highlightthickness=1,
                   highlightbackground=BORDER, highlightcolor=ACCENT,
                   command=self._apply_loops_params).pack(side="left", padx=4)
        self.blocks_var.trace_add("write", lambda *_: self._apply_loops_params())

        self.total_label = self._label(r2, "", bg=PANEL, fg=WARN, font=FONT_NUM)
        self.total_label.pack(side="left", padx=(24, 0))

        # --- Режим записи / порог ---
        a = self._panel(self.tab_loops, "Режим записи")
        atop = tk.Frame(a, bg=PANEL)
        atop.pack(fill="x")
        self.autostart_var = tk.StringVar(value="auto" if self.cfg.get("autostart") else "manual")
        tk.Radiobutton(atop, text="Вручную (все блоки)", variable=self.autostart_var,
                       value="manual", bg=PANEL, fg=FG, activebackground=PANEL,
                       activeforeground=FG, selectcolor=CTRL, highlightthickness=0,
                       font=FONT, command=self._apply_loops_params).pack(side="left")
        tk.Radiobutton(atop, text="Авто по звуку (блоки по порогу)", variable=self.autostart_var,
                       value="auto", bg=PANEL, fg=FG, activebackground=PANEL,
                       activeforeground=FG, selectcolor=CTRL, highlightthickness=0,
                       font=FONT, command=self._apply_loops_params).pack(side="left", padx=(12, 0))

        self._label(atop, "Порог:", bg=PANEL).pack(side="left", padx=(24, 0))
        self.thr_var = tk.DoubleVar(value=float(self.cfg.get("threshold", 0.05)))
        self._scale(atop, self.thr_var, 0.01, 1.0, 0.01,
                    lambda _v: self._apply_loops_params(), length=150).pack(side="left")
        self._hint(a, "Блок с порогом — весь зелёный и пишется целиком; без порога — "
                      "серый, в луп не пишется. «Авто»: если Play не запущен, «Запись» "
                      "ждёт звук — проигрывание и запись стартуют с момента превышения порога.")

        # --- Строки лупов (ширина подстраивается под окно) ---
        loops_frame = tk.Frame(self.tab_loops, bg=BG)
        loops_frame.pack(fill="both", expand=True, padx=12, pady=(8, 2))
        head = tk.Frame(loops_frame, bg=BG)
        head.pack(fill="x")
        self._label(head, "Лупы (клик — выбор; повторный клик по выбранному — вкл/выкл звук):",
                    font=FONT_B).pack(side="left")
        self._label(head, "Все блоки всегда вмещаются в ширину окна",
                    fg=MUTED, font=(FONT_FAMILY, 8)).pack(side="right")

        self.rows = []
        for i in range(self.engine.n_slots):
            self.rows.append(LoopRow(loops_frame, i, self))

        # --- Транспорт ---
        self._build_transport()

        info = tk.Frame(self.tab_loops, bg=BG)
        info.pack(fill="x", padx=12, pady=(2, 8))
        self._label(
            info,
            "Все лупы играют в одном тайминге; длина цикла = такты × блоки. "
            "«Запись» пишет выбранный луп заменой блоками: без порога блок не трогается. "
            "«Старт/Стоп» — пауза всех разом; «Стоп» — всё стоп и сброс на начало. "
            "Горячие клавиши: R — запись/дубль, Space — старт/стоп, U — undo, "
            "C — очистить, S — стоп, M — метроном, 1–4 — выбор лупа, "
            "[ ] — луп назад/вперёд.",
            fg=MUTED, wraplength=980, justify="left", font=(FONT_FAMILY, 8),
        ).pack(anchor="w")

    def _build_transport(self) -> None:
        frame = tk.Frame(self.tab_loops, bg=BG, pady=6)
        frame.pack(fill="x", padx=12)

        self._rec_btn = self._button(
            frame, "●  Запись / Дубль", lambda: self.do_action("toggle_rec"),
            main=True, rec=True, width=18, height=2)
        self._rec_btn.pack(side="left", padx=(0, 8))

        self._play_btn = self._button(
            frame, "▶  Старт / Стоп", lambda: self.do_action("toggle_play"),
            main=True, play=True, width=16, height=2)
        self._play_btn.pack(side="left", padx=(0, 8))

        self._button(frame, "■  Стоп", lambda: self.do_action("stop_all"),
                     width=11, height=2).pack(side="left", padx=(0, 8))
        self._button(frame, "↺  Undo", lambda: self.do_action("undo"),
                     width=11, height=2).pack(side="left", padx=(0, 8))
        self._button(frame, "✕  Очистить", lambda: self.do_action("clear"),
                     width=11, height=2).pack(side="left", padx=(0, 8))
        self._button(frame, "⇩  Экспорт WAV", self.export_wav,
                     width=13, height=2).pack(side="left", padx=(0, 8))

        self._sync_drum_ui()

    def select_slot(self, n: int) -> None:
        self.engine.post("select_slot", n)

    def click_slot(self, n: int) -> None:
        """Клик по кнопке лупа: выбор; повторный клик по выбранному — вкл/выкл mute."""
        self.engine.post("slot_click", n)

    # ==================================================================
    # Вкладка «MIDI» — инструмент клавиатуры и назначения
    # ==================================================================
    def _build_midi_tab(self) -> None:
        # --- Режим клавиатуры ---
        top = self._panel(self.tab_midi, "Клавиатура")
        self.kb_var = tk.StringVar(value=str(self.cfg.get("kb_mode", "instr")))
        tk.Radiobutton(top, text="Играть инструмент (ноты звучат)",
                       variable=self.kb_var, value="instr", bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG,
                       selectcolor=PANEL, highlightthickness=0, font=FONT,
                       command=self._apply_kb_mode).pack(side="left")
        tk.Radiobutton(top, text="Только управление",
                       variable=self.kb_var, value="ctrl", bg=PANEL, fg=FG,
                       activebackground=PANEL, activeforeground=FG,
                       selectcolor=PANEL, highlightthickness=0, font=FONT,
                       command=self._apply_kb_mode).pack(side="left", padx=(16, 0))
        self._hint(top, "Ноты с назначенными действиями (см. «MIDI-назначения» ниже) "
                        "всегда работают как управление. Педаль сустейна (CC64) поддерживается. "
                        "Инструмент звучит всегда и пишется в луп вместе с гитарой — как живая игра.")

        # --- Инструмент ---
        inst = self._panel(self.tab_midi, "Инструмент")
        irow = tk.Frame(inst, bg=PANEL)
        irow.pack(fill="x")

        left = tk.Frame(irow, bg=PANEL)
        left.pack(side="left", anchor="n")
        self._label(left, "Категория:", bg=PANEL).pack(anchor="w")
        s = self.engine.sampler
        groups = s.groups() if s and s.ok else []
        self.instr_group_var = tk.StringVar()
        ttk.Combobox(left, textvariable=self.instr_group_var, width=18,
                     state="readonly", values=groups).pack(anchor="w", pady=(2, 6))
        self.instr_group_var.trace_add("write", lambda *_: self._fill_instr_list())

        self._label(left, "Инструмент:", bg=PANEL).pack(anchor="w")
        self.instr_list = tk.Listbox(left, height=10, width=28, bg=INSET, fg=FG,
                                     selectbackground=ACCENT, selectforeground="#0B0E12",
                                     highlightthickness=1, highlightbackground=BORDER,
                                     font=FONT, relief="flat", bd=0,
                                     exportselection=False, activestyle="none")
        self.instr_list.pack(anchor="w", pady=(2, 0))
        self.instr_list.bind("<<ListboxSelect>>", self._instr_selected)
        self._instr_rows: list = []

        right = tk.Frame(irow, bg=PANEL)
        right.pack(side="left", anchor="n", padx=(24, 0))
        self._button(right, "▶  Демо", self._demo, width=14).pack(anchor="w")
        self._label(right, "Громкость инструмента:", bg=PANEL).pack(anchor="w", pady=(12, 0))
        self.instr_gain_var = tk.DoubleVar(value=float(self.cfg.get("instr_gain", 1.0)))
        self._scale(right, self.instr_gain_var, 0.0, 1.5, 0.05,
                    lambda _v: self._apply_instr_gain(), length=150).pack(anchor="w")

        if s and s.ok:
            idx = int(self.cfg.get("instr_index", 0))
            if not (0 <= idx < len(s.instruments)):
                idx = 0
            s.set_instrument(idx)
            self.cfg["instr_index"] = idx
            self.instr_group_var.set(s.instruments[idx]["group"])
        else:
            self._hint(inst, "Папка sounds/ не найдена — инструменты недоступны. "
                             "Скачайте полный комплект (smk37-looper.zip) и распакуйте "
                             "поверх папки программы.")

        # --- MIDI-назначения (переехали из «Настроек») ---
        self._build_mapping()

    def _apply_kb_mode(self) -> None:
        self.cfg["kb_mode"] = self.kb_var.get()
        mode = "клавиши играют инструмент" if self.kb_var.get() == "instr" else "клавиши — только управление"
        self.log(f"Режим клавиатуры: {mode}")

    def _apply_instr_gain(self) -> None:
        g = float(self.instr_gain_var.get())
        self.cfg["instr_gain"] = g
        if self.engine.sampler is not None:
            self.engine.sampler.set_gain(g)

    def _fill_instr_list(self) -> None:
        self.instr_list.delete(0, "end")
        self._instr_rows = []
        s = self.engine.sampler
        if not (s and s.ok):
            return
        for idx, name in s.names_in_group(self.instr_group_var.get()):
            self.instr_list.insert("end", name)
            self._instr_rows.append(idx)
        cur = s.index
        if cur in self._instr_rows:
            pos = self._instr_rows.index(cur)
            self.instr_list.selection_clear(0, "end")
            self.instr_list.selection_set(pos)
            self.instr_list.activate(pos)

    def _instr_selected(self, _event=None) -> None:
        sel = self.instr_list.curselection()
        if not sel or not self._instr_rows:
            return
        idx = self._instr_rows[sel[0]]
        s = self.engine.sampler
        if s and idx != s.index:
            s.set_instrument(idx)
            self.cfg["instr_index"] = idx
            self.log(f"Инструмент: {s.instruments[idx]['name']}")

    def _demo(self) -> None:
        s = self.engine.sampler
        if not (s and s.ok):
            self.log("Звуки не найдены (папка sounds/).")
            return
        inst = s.instruments[s.index]
        if inst["drums"]:
            pattern = [(36, 110, 0), (38, 100, 220), (36, 110, 440),
                       (38, 100, 660), (46, 95, 880)]
            step_off = 200
        else:
            pattern = [(60, 90, 0), (64, 90, 200), (67, 90, 400), (72, 100, 600)]
            step_off = 180
        for key, vel, t in pattern:
            self.after(t, lambda k=key, v=vel: s.note(k, v))
            self.after(t + step_off, lambda k=key: s.note(k, 0))
        self.log(f"Демо: {inst['name']}")

    def kb_play(self, note: int) -> bool:
        """Может ли нота звучать как инструмент (не занята управлением)."""
        if str(self.cfg.get("kb_mode", "instr")) != "instr":
            return False
        return f"note:{note}" not in self.mapping

    # ==================================================================
    # Вкладка «Настройки»
    # ==================================================================
    def _build_settings_tab(self) -> None:
        self._build_devices_bar()
        self._build_log()

    def _build_devices_bar(self) -> None:
        # --- Устройства ---
        bar = self._panel(self.tab_settings, "Устройства")
        r1 = tk.Frame(bar, bg=PANEL)
        r1.pack(fill="x")

        self._label(r1, "MIDI:", bg=PANEL, width=11, anchor="w", font=FONT_B).pack(side="left")
        self.midi_var = tk.StringVar()
        self.midi_box = ttk.Combobox(r1, textvariable=self.midi_var, width=30, state="readonly")
        self.midi_box.pack(side="left", padx=(2, 16))

        self._label(r1, "Аудио вход:", bg=PANEL, width=11, anchor="w", font=FONT_B).pack(side="left")
        self.in_var = tk.StringVar()
        self.in_box = ttk.Combobox(r1, textvariable=self.in_var, width=32, state="readonly")
        self.in_box.pack(side="left", padx=(2, 16))

        self._label(r1, "Аудио выход:", bg=PANEL, width=12, anchor="w", font=FONT_B).pack(side="left")
        self.out_var = tk.StringVar()
        self.out_box = ttk.Combobox(r1, textvariable=self.out_var, width=32, state="readonly")
        self.out_box.pack(side="left", padx=2)

        r2 = tk.Frame(bar, bg=PANEL)
        r2.pack(fill="x", pady=(8, 0))

        self._label(r2, "Частота:", bg=PANEL).pack(side="left")
        self.sr_var = tk.StringVar(value=str(self.cfg.get("sample_rate", 48000)))
        ttk.Combobox(r2, textvariable=self.sr_var, width=8, state="readonly",
                     values=("44100", "48000", "96000")).pack(side="left", padx=(4, 16))

        self._label(r2, "Буфер:", bg=PANEL).pack(side="left")
        self.bs_var = tk.StringVar(value=str(self.cfg.get("blocksize", 256)))
        ttk.Combobox(r2, textvariable=self.bs_var, width=6, state="readonly",
                     values=("64", "128", "256", "512", "1024")).pack(side="left", padx=(4, 16))

        self.excl_var = tk.BooleanVar(value=bool(self.cfg.get("wasapi_exclusive", True)))
        tk.Checkbutton(
            r2, text="Экскл. WASAPI (низкая задержка)", variable=self.excl_var,
            bg=PANEL, fg=FG, activebackground=PANEL, activeforeground=FG,
            selectcolor=CTRL, highlightthickness=0, font=FONT,
        ).pack(side="left")

        self._button(r2, "Применить", self.apply_devices, width=12, bg=ACCENT,
                     fg="#0B0E12", activebackground="#8CC8FF",
                     activeforeground="#0B0E12", font=FONT_B).pack(side="left", padx=(24, 8))
        self._button(r2, "Обновить списки", self._refresh_devices,
                     width=14).pack(side="left")

        self.in_map = {}
        self.out_map = {}

        # --- Громкости ---
        g = self._panel(self.tab_settings, "Громкости")
        self._label(g, "Мониторинг (вход в наушниках):", bg=PANEL).pack(side="left")
        self.mon_var = tk.DoubleVar(value=float(self.cfg.get("monitor_gain", 1.0)))
        self._scale(g, self.mon_var, 0.0, 1.0, 0.05,
                    lambda _v: self.engine.set_gains(monitor=float(_v)),
                    length=130).pack(side="left", padx=(4, 24))

        self._label(g, "Громкость лупов:", bg=PANEL).pack(side="left")
        self.mst_var = tk.DoubleVar(value=float(self.cfg.get("master_gain", 1.0)))
        self._scale(g, self.mst_var, 0.0, 1.2, 0.05,
                    lambda _v: self.engine.set_gains(master=float(_v)),
                    length=130).pack(side="left", padx=4)

    def _build_mapping(self) -> None:
        frame = tk.Frame(self.tab_midi, bg=BG, pady=6)
        frame.pack(fill="both", padx=12)
        self._label(frame, "MIDI-назначения (нажмите «Назначить», затем кнопку на SMK-37):",
                    font=FONT_B).pack(anchor="w", pady=(0, 6))
        grid = tk.Frame(frame, bg=BG)
        grid.pack(fill="x", pady=4)
        self.map_widgets = {}
        for idx, (action, label) in enumerate(ACTIONS):
            r = idx % 8
            c = 0 if idx < 8 else 3
            cell = tk.Frame(grid, bg=BG)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 28), pady=2)
            tk.Label(cell, text=label, bg=BG, fg=FG, width=26, anchor="w",
                     font=FONT).pack(side="left")
            val = tk.Label(cell, text="—", bg=INSET, fg=WARN, width=16, font=FONT,
                           relief="flat", bd=0, highlightthickness=1,
                           highlightbackground=BORDER)
            val.pack(side="left", padx=4)
            btn = self._button(cell, "Назначить", lambda a=action: self.start_learn(a),
                               width=9, bg=ACCENT, fg="#0B0E12",
                               activebackground="#8CC8FF", activeforeground="#0B0E12")
            btn.pack(side="left", padx=2)
            self._button(cell, "×", lambda a=action: self.clear_binding(a),
                         width=2).pack(side="left", padx=(2, 0))
            self.map_widgets[action] = (val, btn)

    def _build_log(self) -> None:
        frame = tk.Frame(self.tab_settings, bg=BG)
        frame.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        self._label(frame, "Журнал:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.log_text = tk.Text(frame, height=8, bg=INSET, fg=DIM, relief="flat",
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
        try:
            self.engine.metronome_on = bool(self.metro_var.get())
            self.engine.metro_sound = self.metro_sound_var.get()
            self.engine.metro_gain = float(self.metro_gain_var.get())
            self.engine.autostart = self.autostart_var.get() == "auto"
            self.engine.threshold = float(self.thr_var.get())
            self.engine.drum_on = bool(self.drum_var.get())
            self.engine.drum_style = self.drum_style_var.get()
            self.engine.drum_gain = float(self.drum_gain_var.get())
        except AttributeError:
            return  # окно ещё не полностью собрано — вызов преждевременный

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

        try:
            total_bars = self.engine.fixed_total_bars()
            self.total_label.config(
                text=f"Итого: {total_bars} такт. ≈ {self.engine.fixed_total_sec():.1f} с"
            )
            for r in self.rows:
                r.force_redraw()
        except AttributeError:
            pass

    # ------------------------------------------------------------------
    # Драм-машина
    # ------------------------------------------------------------------
    def _toggle_drum_panel(self) -> None:
        """Шеврон «▸ Драм-машина» — показать/скрыть настройки (без вкл/выкл)."""
        self._drum_open = not getattr(self, "_drum_open", True)
        self._sync_drum_ui()

    def _on_drum_check(self) -> None:
        """Тумблер «Драм вкл.» — включить/выключить барабаны."""
        self._apply_loops_params()
        self.log("Драм-машина: " + ("включена (вступление с такта)" if self.drum_var.get()
                                    else "выключена"))

    def _sync_drum_ui(self) -> None:
        """Вид шеврона и видимость панели настроек драм-машины."""
        open_ = bool(getattr(self, "_drum_open", True))
        self.drum_btn.configure(
            text=("▾  " if open_ else "▸  ") + "Драм-машина",
            bg=PANEL2 if open_ else CTRL,
        )
        if open_:
            self.drum_panel.pack(fill="x", pady=(8, 0))
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
                f"{self.engine.bars_per_block}|{self.engine.num_blocks}")

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
        btn.config(text="Ждите...", bg=WARN, fg="#0B0E12")
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
            btn.config(text="Назначить", bg=ACCENT, fg="#0B0E12")

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
                text="■ Стоп прослушивания" if preview else "▶  Предпрослушивание",
                bg=REC if preview else CTRL,
                fg="#0B0E12" if preview else FG,
                activebackground="#FF8A92" if preview else HOVER,
                activeforeground="#0B0E12" if preview else FG,
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

        # Главные кнопки: яркая заливка только в активном состоянии.
        is_rec = any(inf.get("recording") or inf.get("armed_here") or inf.get("waiting")
                     for inf in snap["slots"])
        self._rec_btn.configure(
            bg=REC if is_rec else CTRL,
            fg="#0B0E12" if is_rec else FG,
            activebackground="#FF8A92" if is_rec else HOVER,
            activeforeground="#0B0E12" if is_rec else FG,
        )
        is_play = snap.get("transport") == "running"
        self._play_btn.configure(
            bg=PLAY if is_play else CTRL,
            fg="#0B0E12" if is_play else FG,
            activebackground="#6BE0AE" if is_play else HOVER,
            activeforeground="#0B0E12" if is_play else FG,
        )

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
