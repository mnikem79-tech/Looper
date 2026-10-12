"""
Драм-машина лупера: процедурные стили (без сэмпл-файлов).

Стиль описывает удары в каноническом такте из 4 долей (позиция — доля,
можно дробные: 0.25 = 1/16). При другом размере (beats_per_bar != 4)
позиции МАСШТАБИРУЮТСЯ: p_факт = p × beats_per_bar / 4 — паттерн
подстраивается под размер такта.

Цикл = такты × блоки (bars_per_block × num_blocks):
- ПЕРВЫЙ такт цикла — крэш (отбивка начала цикла);
- ПОСЛЕДНИЙ такт цикла — брейк (малый барабан восьмыми во второй
  половине, с нарастанием) — отбивка конца цикла.
Так драм-машина подстраивается и под количество тактов.

Таблица событий — по сетке 1/16 доли на весь цикл:
build_events() -> list[list[tuple[instr, velocity]]], индекс = слот 1/16.
"""
import numpy as np

DEFAULT_STYLE = "Поп"


def _hats(step: float, accent_every: int = 2):
    """Хэт: step — шаг в долях (0.5 = восьмая, 0.25 = шестнадцатая)."""
    n = int(round(4.0 / step))
    return [(i * step, 0.75 if i % accent_every == 0 else 0.5) for i in range(n)]


# Удары в каноническом такте 4/4: instr -> [(позиция_в_долях, velocity)]
STYLES = {
    "Поп": {
        "kick": [(0.0, 1.0), (2.5, 0.9)],
        "snare": [(1.0, 1.0), (3.0, 1.0)],
        "hat": _hats(0.5),
    },
    "Рок": {
        "kick": [(0.0, 1.0), (2.0, 0.9), (2.75, 0.8)],
        "snare": [(1.0, 1.0), (3.0, 1.0)],
        "hat": _hats(0.5),
    },
    "Баллада": {
        "kick": [(0.0, 0.9), (2.0, 0.7)],
        "snare": [(2.0, 0.9)],
        "hat": _hats(1.0),
    },
    "Фанк": {
        "kick": [(0.0, 1.0), (0.75, 0.7), (2.5, 0.9), (3.5, 0.6)],
        "snare": [(1.0, 1.0), (3.0, 1.0)],
        "hat": _hats(0.25, accent_every=4),
    },
    "Хип-хоп": {
        "kick": [(0.0, 1.0), (1.5, 0.8), (2.25, 0.85)],
        "snare": [(1.0, 1.0), (3.0, 1.0)],
        "hat": [(0.0, 0.7), (0.5, 0.45), (1.25, 0.6), (1.5, 0.45),
                (2.0, 0.7), (2.5, 0.45), (3.25, 0.6), (3.5, 0.45)],
    },
    "Вальс": {   # ставьте размер 3/4 — получается настоящий вальс
        "kick": [(0.0, 1.0)],
        "snare": [(4.0 / 3.0, 0.85), (8.0 / 3.0, 0.9)],
        "hat": [(0.0, 0.7), (4.0 / 3.0, 0.5), (8.0 / 3.0, 0.5)],
    },
    "Диско": {
        "kick": [(0.0, 1.0), (1.0, 1.0), (2.0, 1.0), (3.0, 1.0)],
        "snare": [(1.0, 0.9), (3.0, 0.9)],
        "openhat": [(0.5, 0.8), (1.5, 0.8), (2.5, 0.8), (3.5, 0.8)],
    },
    "Электро": {
        "kick": [(0.0, 1.0), (2.0, 1.0), (3.5, 0.8)],
        "snare": [(1.0, 1.0), (3.0, 1.0)],
        "hat": _hats(0.25, accent_every=2),
    },
}

STYLE_NAMES = tuple(STYLES.keys())


def build_events(style: str, beats_per_bar: int, bars_per_block: int,
                 num_blocks: int) -> list:
    """Таблица событий на весь цикл: слот 1/16 -> [(instr, velocity), ...]."""
    bpb = max(1, int(beats_per_bar))
    total_bars = max(1, int(bars_per_block)) * max(1, int(num_blocks))
    slots_per_bar = bpb * 4
    total = total_bars * slots_per_bar
    raw = [dict() for _ in range(total)]
    scale = bpb / 4.0                      # канонический такт 4/4 -> фактический
    pat = STYLES.get(style) or STYLES[DEFAULT_STYLE]

    def put(bar: int, slot_in_bar: int, instr: str, vel: float) -> None:
        if 0 <= slot_in_bar < slots_per_bar:
            d = raw[bar * slots_per_bar + slot_in_bar]
            d[instr] = max(d.get(instr, 0.0), float(vel))

    # удары паттерна (масштаб под размер)
    for bar in range(total_bars):
        for instr, evs in pat.items():
            for pos, vel in evs:
                slot = int(round(pos * scale * 4.0)) % slots_per_bar
                put(bar, slot, instr, vel)

    # крэш — ПЕРВЫЙ такт цикла (начало цикла)
    put(0, 0, "crash", 1.0)

    # брейк — ПОСЛЕДНИЙ такт цикла (вторая половина, восьмые малым)
    if total_bars >= 2:
        last = total_bars - 1
        half = slots_per_bar // 2
        cnt = max(1, (slots_per_bar - half + 1) // 2)
        for i in range(cnt):
            sl = half + i * 2
            if sl < slots_per_bar:
                put(last, sl, "fsnare", 0.55 + 0.45 * i / max(1, cnt - 1))

    return [[(k, v) for k, v in sorted(d.items())] for d in raw]


def make_hit(kind: str, sr: int) -> np.ndarray:
    """Синтез одного удара (детерминированный, без файлов)."""
    rng = np.random.default_rng(7)
    if kind == "kick":
        n = max(64, int(sr * 0.22))
        t = np.arange(n, dtype=np.float32) / sr
        f = 45.0 + 115.0 * np.exp(-t * 18.0)          # свип вниз
        ph = np.cumsum(2.0 * np.pi * f / sr)
        y = np.sin(ph) * np.exp(-t * 16.0)
        y += rng.standard_normal(n).astype(np.float32) * np.exp(-t * 400.0) * 0.4
    elif kind in ("snare", "fsnare"):
        n = max(64, int(sr * (0.12 if kind == "fsnare" else 0.16)))
        t = np.arange(n, dtype=np.float32) / sr
        noise = rng.standard_normal(n).astype(np.float32)
        tone = np.sin(2 * np.pi * 190.0 * t) * np.exp(-t * 24.0) * 0.5
        y = noise * np.exp(-t * (34.0 if kind == "fsnare" else 26.0)) + tone
        if kind == "fsnare":
            y *= 1.25
    elif kind in ("hat", "openhat"):
        n = max(32, int(sr * (0.05 if kind == "hat" else 0.28)))
        t = np.arange(n, dtype=np.float32) / sr
        noise = rng.standard_normal(n).astype(np.float32)
        hp = np.diff(np.concatenate(([0.0], noise)))  # простой ВЧ-фильтр
        y = hp * np.exp(-t * (110.0 if kind == "hat" else 14.0))
    else:  # crash
        n = max(64, int(sr * 1.1))
        t = np.arange(n, dtype=np.float32) / sr
        noise = rng.standard_normal(n).astype(np.float32)
        hp = np.diff(np.concatenate(([0.0], noise)))
        y = hp * np.exp(-t * 4.5)
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak).astype(np.float32)
