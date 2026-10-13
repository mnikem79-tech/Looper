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

У каждого жанра — по три варианта (1 — основа, 2 — плотнее/с римом,
3 — синкопы/«призраки»): звуки одни и те же, отличаются только рисунки.

Таблица событий — по сетке 1/16 доли на весь цикл:
build_events() -> list[list[tuple[instr, velocity]]], индекс = слот 1/16.
"""
import numpy as np

DEFAULT_STYLE = "Поп 1"


def _p(kick=(), snare=(), hat=0.0, acc=2, openhat=(), rim=(), ghost=()):
    """Вариант паттерна для одного размера: позиции — в долях ФАКТИЧЕСКОГО такта."""
    return {
        "kick": tuple(kick),
        "snare": tuple(snare),
        "hat": float(hat),        # шаг хэта в долях (0.5 = восьмая, 0.25 = шестнадцатая)
        "acc": int(acc),          # акцент хэта каждый acc-й шаг
        "openhat": tuple(openhat),
        "rim": tuple(rim),        # рим-клик (боковой удар) — для лёгких стилей
        "ghost": tuple(ghost),    # тихие «призраки» малого барабана
    }


# Стиль -> {размер -> вариант}. Размеры 2–6 (как в выборе «Размер»).
STYLES = {
    # ---------------- Поп ----------------
    "Поп 1": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0,), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
    },
    "Поп 2": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.5, openhat=(1.5,)),
        3: _p(kick=(0, 2.5), snare=(1, 2), hat=0.5, openhat=(2.5,)),
        4: _p(kick=(0, 2.5, 3.5), snare=(1, 3), hat=0.5, openhat=(3.5,)),
        5: _p(kick=(0, 3, 4.5), snare=(1, 4), hat=0.5, openhat=(4.5,)),
        6: _p(kick=(0, 3, 5.5), snare=(1, 4), hat=0.5, openhat=(5.5,)),
    },
    "Поп 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.25, acc=4, ghost=(1.75,)),
        3: _p(kick=(0,), snare=(1, 2), hat=0.25, acc=4, ghost=(2.75,)),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.25, acc=4, ghost=(0.75, 2.75)),
        5: _p(kick=(0, 3), snare=(1, 4), hat=0.25, acc=4, ghost=(0.75, 2.75, 4.75)),
        6: _p(kick=(0, 3), snare=(1, 4), hat=0.25, acc=4, ghost=(0.75, 2.75, 5.75)),
    },
    # ---------------- Рок ----------------
    "Рок 1": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 2, 2.75), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 2, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 2, 3, 5), snare=(1, 4), hat=0.5),
    },
    "Рок 2": {
        2: _p(kick=(0, 0.5), snare=(1,), hat=0.5),
        3: _p(kick=(0, 1.5, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 1.5, 2, 2.75), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 1.5, 2, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1.5, 2, 3, 5), snare=(1, 4), hat=0.5),
    },
    "Рок 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.5, openhat=(0.5,), ghost=(0.75,)),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5, openhat=(2.5,), ghost=(0.75,)),
        4: _p(kick=(0, 2), snare=(1, 3), hat=0.5, openhat=(2.5,), ghost=(2.75, 3.75)),
        5: _p(kick=(0, 2, 3), snare=(1, 4), hat=0.5, openhat=(3.5,), ghost=(3.75,)),
        6: _p(kick=(0, 2, 3, 5), snare=(1, 4), hat=0.5, openhat=(5.5,), ghost=(3.75, 5.75)),
    },
    # ---------------- Баллада ----------------
    "Баллада 1": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(1,), hat=1.0),
        4: _p(kick=(0,), snare=(2,), hat=1.0),
        5: _p(kick=(0, 3), snare=(2,), hat=1.0),
        6: _p(kick=(0, 3), snare=(2,), hat=1.0),
    },
    "Баллада 2": {
        2: _p(kick=(0,), rim=(1,), hat=1.0),
        3: _p(kick=(0,), rim=(1,), hat=1.0),
        4: _p(kick=(0,), rim=(2,), hat=1.0),
        5: _p(kick=(0, 3), rim=(2,), hat=1.0),
        6: _p(kick=(0, 3), rim=(2,), hat=1.0),
    },
    "Баллада 3": {
        2: _p(kick=(0,), snare=(1,), hat=1.0, openhat=(1.5,), ghost=(1.75,)),
        3: _p(kick=(0,), snare=(1,), hat=1.0, ghost=(1.75, 2.75)),
        4: _p(kick=(0,), snare=(2,), hat=1.0, openhat=(3.5,), ghost=(1.75, 3.75)),
        5: _p(kick=(0, 3), snare=(2,), hat=1.0, ghost=(1.75, 4.75)),
        6: _p(kick=(0, 3), snare=(2,), hat=1.0, ghost=(1.75, 3.75, 5.75)),
    },
    # ---------------- Фанк ----------------
    "Фанк 1": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.25, acc=4),
        3: _p(kick=(0, 0.75, 2), snare=(1,), hat=0.25, acc=4),
        4: _p(kick=(0, 0.75, 2.5), snare=(1, 3), hat=0.25, acc=4),
        5: _p(kick=(0, 0.75, 3), snare=(1, 4), hat=0.25, acc=4),
        6: _p(kick=(0, 0.75, 2.5, 4), snare=(1, 4), hat=0.25, acc=4),
    },
    "Фанк 2": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.25, acc=4, rim=(0.75,), ghost=(0.25, 1.75)),
        3: _p(kick=(0, 0.75, 2), snare=(1,), hat=0.25, acc=4, rim=(2.25,), ghost=(1.75,)),
        4: _p(kick=(0, 0.75, 2.5), snare=(1, 3), hat=0.25, acc=4,
               rim=(1.75,), ghost=(0.25, 2.75, 3.75)),
        5: _p(kick=(0, 0.75, 3), snare=(1, 4), hat=0.25, acc=4,
               rim=(1.75,), ghost=(2.75, 4.75)),
        6: _p(kick=(0, 0.75, 2.5, 4), snare=(1, 4), hat=0.25, acc=4,
               rim=(2.75,), ghost=(1.75, 3.75, 5.75)),
    },
    "Фанк 3": {
        2: _p(kick=(0, 0.75, 1.5), snare=(1,), hat=0.25, acc=4, openhat=(1.75,)),
        3: _p(kick=(0, 0.75, 2, 2.75), snare=(1,), hat=0.25, acc=4, openhat=(2.5,)),
        4: _p(kick=(0, 0.75, 2, 2.5, 3.5), snare=(1, 3), hat=0.25, acc=4, openhat=(3.75,)),
        5: _p(kick=(0, 0.75, 2, 3, 3.75), snare=(1, 4), hat=0.25, acc=4, openhat=(4.5,)),
        6: _p(kick=(0, 0.75, 2, 2.5, 4, 4.75), snare=(1, 4), hat=0.25, acc=4, openhat=(5.5,)),
    },
    # ---------------- Хип-хоп ----------------
    "Хип-хоп 1": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0, 1.5), snare=(2,), hat=0.5),
        4: _p(kick=(0, 1.5, 2.25), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 1.5, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1.5, 3, 4.5), snare=(1, 4), hat=0.5),
    },
    "Хип-хоп 2": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(2,), hat=0.5),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 3.5), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 3.5), snare=(1, 4), hat=0.5),
    },
    "Хип-хоп 3": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.25, acc=4, ghost=(1.75,)),
        3: _p(kick=(0, 1.5), snare=(2,), hat=0.25, acc=4, ghost=(1.75,)),
        4: _p(kick=(0, 1.5, 2.25), snare=(1, 3), hat=0.25, acc=4, ghost=(0.75, 3.75)),
        5: _p(kick=(0, 1.5, 3), snare=(1, 4), hat=0.25, acc=4, ghost=(2.75,)),
        6: _p(kick=(0, 1.5, 3, 4.5), snare=(1, 4), hat=0.25, acc=4, ghost=(2.75, 5.75)),
    },
    # ---------------- Вальс ----------------
    "Вальс 1": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(1, 2), hat=1.0),
        4: _p(kick=(0, 2), snare=(1, 3), hat=1.0),
        5: _p(kick=(0, 3), snare=(1, 4), hat=1.0),
        6: _p(kick=(0, 3), snare=(1, 2, 4, 5), hat=1.0),
    },
    "Вальс 2": {
        2: _p(kick=(0,), rim=(1,), hat=1.0),
        3: _p(kick=(0,), rim=(1, 2), hat=1.0),
        4: _p(kick=(0, 2), rim=(1, 3), hat=1.0),
        5: _p(kick=(0, 3), rim=(1, 4), hat=1.0),
        6: _p(kick=(0, 3), rim=(1, 2, 4, 5), hat=1.0),
    },
    "Вальс 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0, 2.5), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 2), snare=(1, 3), hat=0.5, openhat=(3.5,)),
        5: _p(kick=(0, 3), snare=(1, 4), hat=0.5, openhat=(4.5,)),
        6: _p(kick=(0, 3), snare=(1, 2, 4, 5), hat=0.5),
    },
    # ---------------- Диско ----------------
    "Диско 1": {
        2: _p(kick=(0, 1), snare=(1,), openhat=(0.5,)),
        3: _p(kick=(0, 1, 2), snare=(1,), openhat=(0.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Диско 2": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.25, acc=4, openhat=(0.5,)),
        3: _p(kick=(0, 1, 2), snare=(1,), hat=0.25, acc=4, openhat=(0.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), hat=0.25, acc=4,
               openhat=(1.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4), hat=0.25, acc=4,
               openhat=(1.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4), hat=0.25, acc=4,
               openhat=(1.5, 3.5, 5.5)),
    },
    "Диско 3": {
        2: _p(kick=(0, 1), snare=(1,), openhat=(0.5,), ghost=(1.75,)),
        3: _p(kick=(0, 1, 2), snare=(1,), openhat=(0.5, 2.5), ghost=(2.75,)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), openhat=(0.5, 1.5, 2.5, 3.5),
               ghost=(0.75, 2.75)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5), ghost=(2.75, 4.75)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4),
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5), ghost=(2.75, 5.75)),
    },
    # ---------------- Электро ----------------
    "Электро 1": {
        2: _p(kick=(0,), snare=(1,), hat=0.25, acc=2),
        3: _p(kick=(0, 2), snare=(1,), hat=0.25, acc=2),
        4: _p(kick=(0, 2, 3.5), snare=(1, 3), hat=0.25, acc=2),
        5: _p(kick=(0, 2, 3.5), snare=(1, 4), hat=0.25, acc=2),
        6: _p(kick=(0, 2, 3.5, 5), snare=(1, 4), hat=0.25, acc=2),
    },
    "Электро 2": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.25, acc=4, openhat=(0.5, 1.5)),
        3: _p(kick=(0, 1, 2), snare=(1,), hat=0.25, acc=4, openhat=(0.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Электро 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.5, openhat=(0.5,)),
        3: _p(kick=(0, 2.5), snare=(1,), hat=0.5),
        4: _p(kick=(0, 1.5, 2.5), snare=(1, 3), hat=0.5, openhat=(3.5,)),
        5: _p(kick=(0, 1.5, 3.5), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1.5, 2.5, 4.5), snare=(1, 4), hat=0.5),
    },
    # ---------------- Панк ----------------
    "Панк 1": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.25, acc=2),
        3: _p(kick=(0, 2), snare=(1,), hat=0.25, acc=2),
        4: _p(kick=(0, 2), snare=(1, 3), hat=0.25, acc=2),
        5: _p(kick=(0, 2, 3), snare=(1, 4), hat=0.25, acc=2),
        6: _p(kick=(0, 2, 3, 5), snare=(1, 4), hat=0.25, acc=2),
    },
    "Панк 2": {
        2: _p(kick=(0, 0.5), snare=(1,), hat=0.25, acc=2),
        3: _p(kick=(0, 0.5, 2), snare=(1,), hat=0.25, acc=2),
        4: _p(kick=(0, 1.5, 2, 3.5), snare=(1, 3), hat=0.25, acc=2),
        5: _p(kick=(0, 1.5, 2, 3, 4.5), snare=(1, 4), hat=0.25, acc=2),
        6: _p(kick=(0, 1.5, 2, 3, 4, 5.5), snare=(1, 4), hat=0.25, acc=2),
    },
    "Панк 3": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.5),
        3: _p(kick=(0, 1, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4), hat=0.5),
    },
    # ---------------- Гранж ----------------
    "Гранж 1": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.5),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5, openhat=(3.5,)),
        5: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5),
    },
    "Гранж 2": {
        2: _p(kick=(0, 1.25), snare=(1,), hat=0.5),
        3: _p(kick=(0, 1.75), snare=(1,), hat=0.5, ghost=(2.75,)),
        4: _p(kick=(0, 1.75, 2.75), snare=(1, 3), hat=0.5, ghost=(3.75,)),
        5: _p(kick=(0, 1.75, 3.25), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 1.75, 3.25, 5), snare=(1, 4), hat=0.5),
    },
    "Гранж 3": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0, 2), snare=(1,), hat=1.0),
        4: _p(kick=(0, 2.5), snare=(2,), hat=1.0),
        5: _p(kick=(0, 3), snare=(2,), hat=1.0),
        6: _p(kick=(0, 3), snare=(2,), hat=1.0),
    },
    # ---------------- Хардкор ----------------
    "Хардкор 1": {
        2: _p(kick=(0, 0.5), snare=(1,), hat=0.25, acc=2),
        3: _p(kick=(0, 0.5, 2), snare=(1,), hat=0.25, acc=2),
        4: _p(kick=(0, 0.5, 2, 2.5), snare=(1, 3), hat=0.25, acc=2),
        5: _p(kick=(0, 0.5, 2, 2.5, 4), snare=(1, 4), hat=0.25, acc=2),
        6: _p(kick=(0, 0.5, 2, 2.5, 4, 4.5), snare=(1, 4), hat=0.25, acc=2),
    },
    "Хардкор 2": {
        2: _p(kick=(0,), snare=(0.5, 1, 1.5), hat=0.25, acc=2),
        3: _p(kick=(0, 1.5), snare=(0.5, 1, 2, 2.5), hat=0.25, acc=2),
        4: _p(kick=(0, 1, 2, 3), snare=(0.5, 1.5, 2.5, 3.5), hat=0.25, acc=4),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(0.5, 1.5, 2.5, 3.5, 4.5),
               hat=0.25, acc=4),
        6: _p(kick=(0, 1, 2, 3, 4, 5),
               snare=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5), hat=0.25, acc=4),
    },
    "Хардкор 3": {
        2: _p(kick=(0, 1), snare=(1,), hat=1.0),
        3: _p(kick=(0, 1, 2), snare=(1, 2), hat=1.0),
        4: _p(kick=(0, 2), snare=(1, 3), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 2, 3), snare=(1, 4), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 3), snare=(1, 4), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    # ---------------- Метал ----------------
    "Метал 1": {
        2: _p(kick=(0, 0.5, 1, 1.5), snare=(1,), hat=0.5),
        3: _p(kick=(0, 0.5, 1, 1.5, 2, 2.5), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5),
               snare=(1, 4), hat=0.5),
    },
    "Метал 2": {
        2: _p(kick=(0, 0.25, 0.5, 1, 1.25, 1.5), snare=(1,), hat=0.5),
        3: _p(kick=(0, 0.25, 0.5, 1, 1.25, 1.5, 2, 2.25, 2.5), snare=(1,), hat=0.5),
        4: _p(kick=(0, 0.25, 0.5, 1, 1.25, 1.5, 2, 2.25, 2.5,
                    3, 3.25, 3.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 0.25, 0.5, 1, 1.25, 1.5, 2, 2.25, 2.5,
                    3, 3.25, 3.5, 4, 4.25, 4.5), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 0.25, 0.5, 1, 1.25, 1.5, 2, 2.25, 2.5,
                    3, 3.25, 3.5, 4, 4.25, 4.5, 5, 5.25, 5.5),
               snare=(1, 4), hat=0.5),
    },
    "Метал 3": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0, 1.5), snare=(1,), hat=1.0),
        4: _p(kick=(0, 1.5, 2.75), snare=(2,), hat=1.0, openhat=(3.5,)),
        5: _p(kick=(0, 1.5, 3.75), snare=(2,), hat=1.0),
        6: _p(kick=(0, 1.5, 3.75), snare=(2, 5), hat=1.0),
    },
    # ---------------- Фолк ----------------
    "Фолк 1": {
        2: _p(kick=(0,), snare=(1,), hat=0.5),
        3: _p(kick=(0,), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 2), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 3), snare=(1, 4), hat=0.5),
    },
    "Фолк 2": {
        2: _p(kick=(0,), rim=(1,), hat=0.25, acc=4),
        3: _p(kick=(0,), rim=(1, 2), hat=0.25, acc=4),
        4: _p(kick=(0, 2), rim=(1, 3), hat=0.25, acc=4),
        5: _p(kick=(0, 3), rim=(1, 4), hat=0.25, acc=4),
        6: _p(kick=(0, 3), rim=(1, 4), hat=0.25, acc=4),
    },
    "Фолк 3": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.5, ghost=(1.75,)),
        3: _p(kick=(0, 2.5), snare=(1, 2), hat=0.5),
        4: _p(kick=(0, 2, 3.5), snare=(1, 3), hat=0.5, ghost=(3.75,)),
        5: _p(kick=(0, 3, 4.5), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 3, 5.5), snare=(1, 4), hat=0.5, ghost=(5.75,)),
    },
    # ---------------- Сальса ----------------
    "Сальса 1": {
        2: _p(kick=(1.5,), rim=(0, 1.5), hat=1.0),
        3: _p(kick=(2.5,), rim=(0, 1.5), hat=1.0),
        4: _p(kick=(1.5, 3), rim=(0, 1.5, 3), hat=1.0),
        5: _p(kick=(1.5, 4), rim=(0, 1.5, 4), hat=1.0),
        6: _p(kick=(1.5, 3, 5), rim=(0, 1.5, 3, 5), hat=1.0),
    },
    "Сальса 2": {
        2: _p(kick=(1.5,), rim=(0.75,), hat=1.0, ghost=(1.75,)),
        3: _p(kick=(2.5,), rim=(0.75,), hat=1.0, ghost=(1.75, 2.75)),
        4: _p(kick=(1.5, 3), rim=(0.75, 2.75), hat=1.0, ghost=(2.25, 3.75)),
        5: _p(kick=(1.5, 4), rim=(0.75, 2.75), hat=1.0, ghost=(4.75,)),
        6: _p(kick=(1.5, 3, 5), rim=(0.75, 2.75, 4.75), hat=1.0),
    },
    "Сальса 3": {
        2: _p(kick=(1.5,), rim=(0, 1.5), hat=0.5, ghost=(1.75,)),
        3: _p(kick=(2.5,), rim=(0, 1.5), hat=0.5),
        4: _p(kick=(1.5, 3), rim=(0, 1.5, 3), hat=0.5,
               openhat=(3.5,), ghost=(0.75, 2.75)),
        5: _p(kick=(1.5, 4), rim=(0, 1.5, 4), hat=0.5),
        6: _p(kick=(1.5, 3, 5), rim=(0, 1.5, 3, 5), hat=0.5),
    },
    # ---------------- Боса-нова ----------------
    "Боса-нова 1": {
        2: _p(kick=(0,), rim=(0, 1.5), hat=0.5),
        3: _p(kick=(0, 2), rim=(0, 1.5), hat=0.5),
        4: _p(kick=(0, 2), rim=(0, 1.5, 2.5), hat=0.5),
        5: _p(kick=(0, 2), rim=(0, 1.5, 2.5), hat=0.5),
        6: _p(kick=(0, 3), rim=(0, 1.5, 2.5, 4.5), hat=0.5),
    },
    "Боса-нова 2": {
        2: _p(kick=(0,), rim=(0.75,), hat=0.5),
        3: _p(kick=(0, 2), rim=(0.75,), hat=0.5),
        4: _p(kick=(0, 2), rim=(0.75, 2.75), hat=0.5),
        5: _p(kick=(0, 2), rim=(0.75, 2.75), hat=0.5),
        6: _p(kick=(0, 3), rim=(0.75, 2.75, 4.75), hat=0.5),
    },
    "Боса-нова 3": {
        2: _p(kick=(0,), rim=(0, 1.5), hat=1.0),
        3: _p(kick=(0, 2), rim=(0, 1.5), hat=1.0, ghost=(2.75,)),
        4: _p(kick=(0, 2), rim=(0, 1.5, 2.5), hat=1.0, ghost=(3.75,)),
        5: _p(kick=(0, 2), rim=(0, 1.5, 2.5), hat=1.0),
        6: _p(kick=(0, 3), rim=(0, 1.5, 2.5, 4.5), hat=1.0),
    },
    # ---------------- Регги ----------------
    "Регги 1": {
        2: _p(kick=(1,), snare=(1,), hat=0.5, openhat=(0.5, 1.5)),
        3: _p(kick=(2,), snare=(2,), hat=0.5, openhat=(0.5, 1.5, 2.5)),
        4: _p(kick=(2,), snare=(2,), hat=0.5, openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(3,), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(3,), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Регги 2": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.5, openhat=(0.5, 1.5)),
        3: _p(kick=(0, 1, 2), snare=(2,), hat=0.5, openhat=(0.5, 1.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(2,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Регги 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.5, openhat=(0.5, 1.5), ghost=(1.75,)),
        3: _p(kick=(0, 2.5), snare=(2,), hat=0.5,
               openhat=(0.5, 1.5, 2.5)),
        4: _p(kick=(0, 2.5), snare=(2,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5), ghost=(3.75,)),
        5: _p(kick=(0, 3.5), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 3.5), snare=(3,), hat=0.5,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5), ghost=(5.75,)),
    },
    # ---------------- Хаус ----------------
    "Хаус 1": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.25, acc=4, openhat=(0.5, 1.5)),
        3: _p(kick=(0, 1, 2), snare=(1,), hat=0.25, acc=4, openhat=(0.5, 1.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4), hat=0.25, acc=4,
               openhat=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)),
    },
    "Хаус 2": {
        2: _p(kick=(0, 1), snare=(1,), hat=0.25, acc=4,
               openhat=(0.5, 1.5), ghost=(1.75,)),
        3: _p(kick=(0, 1, 2), snare=(1,), hat=0.25, acc=4,
               openhat=(0.5, 2.5), ghost=(2.75,)),
        4: _p(kick=(0, 1, 2, 3), snare=(1, 3), hat=0.25, acc=4,
               openhat=(1.5, 3.5), ghost=(0.75, 2.75)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(1, 4), hat=0.25, acc=4,
               openhat=(1.5, 3.5, 4.5), ghost=(2.75, 4.75)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(1, 4), hat=0.25, acc=4,
               openhat=(1.5, 3.5, 5.5), ghost=(2.75, 5.75)),
    },
    "Хаус 3": {
        2: _p(kick=(0, 1), hat=0.25, acc=4, openhat=(0.5, 1.5)),
        3: _p(kick=(0, 1, 2), hat=0.25, acc=4, openhat=(0.5, 2.5)),
        4: _p(kick=(0, 1, 2, 3), snare=(3,), hat=0.25, acc=4,
               openhat=(0.5, 2.5)),
        5: _p(kick=(0, 1, 2, 3, 4), snare=(4,), hat=0.25, acc=4,
               openhat=(0.5, 2.5)),
        6: _p(kick=(0, 1, 2, 3, 4, 5), snare=(4,), hat=0.25, acc=4,
               openhat=(0.5, 2.5, 4.5)),
    },
    # ---------------- Блюз ----------------
    "Блюз 1": {
        2: _p(kick=(0, 1.5), snare=(1,), hat=0.5),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5),
        5: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5),
        6: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5),
    },
    "Блюз 2": {
        2: _p(kick=(0,), snare=(1,), hat=1.0),
        3: _p(kick=(0,), snare=(1,), hat=1.0, rim=(2.5,)),
        4: _p(kick=(0,), snare=(1, 3), hat=1.0, rim=(2.5,)),
        5: _p(kick=(0, 3), snare=(1, 4), hat=1.0),
        6: _p(kick=(0, 3), snare=(1, 4), hat=1.0),
    },
    "Блюз 3": {
        2: _p(kick=(0,), snare=(1,), hat=0.5, openhat=(1.5,), ghost=(1.75,)),
        3: _p(kick=(0, 2), snare=(1,), hat=0.5, openhat=(2.5,), ghost=(2.75,)),
        4: _p(kick=(0, 2.5), snare=(1, 3), hat=0.5, openhat=(3.5,),
               ghost=(0.75, 2.75)),
        5: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5, ghost=(2.75,)),
        6: _p(kick=(0, 2.5, 4), snare=(1, 4), hat=0.5, ghost=(2.75, 5.75)),
    },
}

STYLE_NAMES = tuple(STYLES.keys())

# Уровни инструментов (чтобы сумма одновременных ударов не клиппилась)
LEVELS = {
    "kick": 1.0,
    "snare": 0.75,
    "fsnare": 0.7,
    "hat": 0.5,
    "openhat": 0.45,
    "crash": 0.5,
}


def canonical(name: str) -> str:
    """Старые имена из настроек («Поп») → текущие («Поп 1»)."""
    name = str(name or "")
    if name in STYLES:
        return name
    if name + " 1" in STYLES:
        return name + " 1"
    return DEFAULT_STYLE


def _generic_pattern(style: dict, bpb: int) -> dict:
    """Резерв для размеров вне 2–6: правила ролей, БЕЗ сжатия позиций."""
    tmpl = style.get(4) or style.get(3) or next(iter(style.values()))
    kick = (0,) + ((bpb // 2,) if bpb >= 4 else ())
    snare = tuple(b for b in (1, 3, 5) if b < bpb)
    openhat = tuple(b + 0.5 for b in snare if tmpl["openhat"])
    return _p(kick=kick, snare=snare, hat=tmpl["hat"], acc=tmpl["acc"],
              openhat=openhat)


def _pattern(style_name: str, bpb: int) -> dict:
    key = canonical(style_name)
    st = STYLES[key]
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
            d[instr] = max(d.get(instr, 0.0),
                           float(vel) * LEVELS.get(instr, 1.0))

    for bar in range(total_bars):
        for pos in pat["kick"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "kick", 1.0)
        for pos in pat["snare"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "snare", 1.0)
        for pos in pat.get("rim", ()):
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "fsnare", 0.85)
        for pos in pat.get("ghost", ()):
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "snare", 0.3)
        open_slots = {int(round(pos * 4.0)) % slots_per_bar
                      for pos in pat["openhat"]}
        for pos in pat["openhat"]:
            put(bar, int(round(pos * 4.0)) % slots_per_bar, "openhat", 0.8)
        if pat["hat"] > 0:
            step = pat["hat"]
            n = max(1, int(round(bpb / step)))
            for i in range(n):
                sl = int(round(i * step * 4.0)) % slots_per_bar
                if sl in open_slots:
                    continue  # открытый хэт уже стоит — закрытый не дублируем
                vel = 0.75 if i % max(1, pat["acc"]) == 0 else 0.5
                put(bar, sl, "hat", vel)

    # крэш — ПЕРВЫЙ такт цикла (начало цикла)
    put(0, 0, "crash", 1.0)

    # брейк — ПОСЛЕДНИЙ такт цикла (вторая половина, восьмыми малым)
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
    # Сглаживание краёв (fade 2 мс) — БЕЗ щелчков/треска на стыках
    n_fade = min(max(1, len(y) // 4), max(4, int(sr * 0.002)))
    fade_in = np.linspace(0.0, 1.0, n_fade, dtype=np.float32)
    y[:n_fade] *= fade_in
    y[-n_fade:] *= fade_in[::-1]
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak).astype(np.float32)
