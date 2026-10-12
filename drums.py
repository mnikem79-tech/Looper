"""
Драм-машина лупера: процедурные стили (без сэмпл-файлов).

ПРАКТИКА АДАПТАЦИИ (как у Yamaha в авто-аккомпанементе):
паттерн НЕ сжимается/растягивается под размер — вместо этого у каждого
стиля есть ОТДЕЛЬНЫЙ вариант для каждого размера (2/4, 3/4, 4/4, 5/4, 6/4).
Роли долей сохраняются: бочка — сильная доля и синкопы, малый барабан —
контрдоля (2 и 4 в 4/4), хэт — ровные 8-е или 16-е (НЕ масштабируются).
Все позиции кратны 1/16 доли — удары никогда не слипаются при округлении.

Цикл = такты × блоки (bars_per_block × num_blocks):
- ПЕРВЫЙ такт цикла — крэш (отбивка начала цикла);
- ПОСЛЕДНИЙ такт цикла — брейк (малый барабан восьмыми во второй
  половине, с нарастанием) — отбивка конца цикла.

Таблица событий — по сетке 1/16 доли на весь цикл:
build_events() -> list[list[tuple[instr, velocity]]], индекс = слот 1/16.
"""
import numpy as np

DEFAULT_STYLE = "Поп"


def _p(kick=(), snare=(), hat=0.0, acc=2, openhat=()):
    """Вариант паттерна для одного размера: позиции — в долях ФАКТИЧЕСКОГО такта."""
    return {
        "kick": tuple(kick),
        "snare": tuple(snare),
        "hat": float(hat),        # шаг хэта в долях (0.5 = восьмая, 0.25 = шестнадцатая)
        "acc": int(acc),          # акцент хэта каждый acc-й шаг
        "openhat": tuple(openhat),
    }


# Стиль -> {размер -> вариант}. Размеры 2–6 (как в выборе «Размер»).
STYLES = {
    "Поп": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0,), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
    },
    "Рок": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 2, 2.75), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 2, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 2, 3, 5), snare=(1, 4), hat=0.5),
    },
    "Баллада": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(1,), hat=1.0),
        4: _p(kick=(0,), snare=(2,), hat=1.0),
        5: _p(kick=(0, 3), snare=(2,), hat=1.0),
        6: _p(kick=(0, 3), snare=(2,), hat=1.0),
    },
    "Фанк": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.25, acc=4),
        3: _p(kick=(0, 0.75, 2), snare=(1,), hat=0.25, acc=4),
        4: _p(kick=(0, 0.75, 2.5), snare=(1, 3), hat=0.25, acc=4),
        5: _p(kick=(0, 0.75, 3), snare=(1, 4), hat=0.25, acc=4),
        6: _p(kick=(0, 0.75, 2.5, 4), snare=(1, 4), hat=0.25, acc=4),
    },
    "Хип-хоп": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0, 1.5), snare=(2,), hat=0.5),
        4: _p(kick=(0, 1.5, 2.25), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 1.5, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1.5, 3, 4.5), snare=(1, 4), hat=0.5),
    },
    "Вальс": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(1, 2), hat=1.0),
        4: _p(kick=(0, 2), snare=(1, 3), hat=1.0),
        5: _p(kick=(0, 3), snare=(1, 4), hat=1.0),
        6: _p(kick=(0, 3), snare=(1, 2, 4, 5), hat=1.0),
    },
    "Диско": {
        2: _p(kick=(0, 1), snare=(1,), openhat=(0.5,)),
        3: _p(kick=(0, 1, 2), snare=(1,), openhat=(0.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Электро": {
        2: _p(kick=(0,), snare=(1,), hat=0.25, acc=2),
        3: _p(kick=(0, 2), snare=(1,), hat=0.25, acc=2),
        4: _p(kick=(0, 2, 3.5), snare=(1, 3), hat=0.25, acc=2),
        5: _p(kick=(0, 2, 3.5), snare=(1, 4), hat=0.25, acc=2),
        6: _p(kick=(0, 2, 3.5, 5), snare=(1, 4), hat=0.25, acc=2),
    },
}

STYLE_NAMES = tuple(STYLES.keys())


def _generic_pattern(style: dict, bpb: int) -> dict:
    """Резерв для размеров вне 2–6: правила ролей, БЕЗ сжатия позиций."""
    tmpl = style.get(4) or style.get(3) or next(iter(style.values()))
    kick = (0,) + ((bpb // 2,) if bpb >= 4 else ())
    snare = tuple(b for b in (1, 3, 5) if b < bpb)
    openhat = tuple(b + 0.5 for b in snare if tmpl["openhat"])
    return _p(kick=kick, snare=snare, hat=tmpl["hat"], acc=tmpl["acc"],
              openhat=openhat)


def _pattern(style_name: str, bpb: int) -> dict:
    st = STYLES.get(style_name) or STYLES[DEFAULT_STYLE]
    return st.get(bpb) or _generic_pattern(st, bpb)


def build_events(style: str, beats_per_bar: int, bars_per_block: int,
                 num_blocks: int) -> list:
    """Таблица событий на весь цикл: слот 1/16 -> [(instr, velocity), ...]."""
    bpb = max(1, int(beats_per_bar))
    total_bars = max(1, int(bars_per_block)) * max(1, int(num_blocks))
    slots_per_bar = bpb * 4
    total = total_bars * slots_per_bar
    raw = [dict() for _ in range(total)]
    pat = _pattern(style, bpb)

    def put(bar: int, slot_in_bar: int, instr: str, vel: float) -> None:
        if 0 <= slot_in_bar < slots_per_bar:
            d = raw[bar * slots_per_bar + slot_in_bar]
            d[instr] = max(d.get(instr, 0.0), float(vel))

    for bar in range(total_bars):
        for pos in pat["kick"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "kick", 1.0)
        for pos in pat["snare"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "snare", 1.0)
        for pos in pat["openhat"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "openhat", 0.8)
        if pat["hat"] > 0:
            step = pat["hat"]
            n = max(1, int(round(bpb / step)))
            for i in range(n):
                vel = 0.75 if i % max(1, pat["acc"]) == 0 else 0.5
                put(bar, int(round(i * step * 4.0)) % slots_per_bar, "hat", vel)

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
