"""
Тесты аудиодвижка без оборудования (process_block напрямую).

Константы: SR=48000, BLOCK=256, BPM=120, 4/4 → доля 24000, такт 96000,
блок = 1 такт = 96000, цикл (2 блока) = 192000.
"""
import numpy as np
from pathlib import Path

from audio_engine import AudioEngine, SlotState, RING_CAP

SR = 48000
BLOCK = 256


def make_engine(extra=None) -> AudioEngine:
    cfg = {
        "slots": 4,
        "sample_rate": SR,
        "blocksize": BLOCK,
        "bpm": 120.0,
        "beats_per_bar": 4,
        "bars_per_block": 1,
        "num_blocks": 2,
        "threshold": 0.1,
        "autostart": False,       # False = «Вручную», True = «Авто по звуку»
        "metronome_on": False,
        "monitor_gain": 1.0,
        "master_gain": 1.0,
        "dub_gain": 1.0,
        "max_loop_sec": 30,
    }
    if extra:
        cfg.update(extra)
    e = AudioEngine(cfg)
    e.update_grid()
    return e


def block_signal(v):
    return np.full(BLOCK, float(v), dtype=np.float32)


def process(e: AudioEngine, x):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    n = x.shape[0]
    out = np.zeros((n, 1), dtype=np.float32)
    e.process_block(x, out, n)
    return out[:, 0]


def feed(e: AudioEngine, value, total, chunk=BLOCK):
    done = 0
    while done < total:
        n = min(chunk, total - done)
        x = np.full((n, 1), float(value), dtype=np.float32)
        out = np.zeros((n, 1), dtype=np.float32)
        e.process_block(x, out, n)
        done += n


def flush(e: AudioEngine):
    return process(e, block_signal(0.0))


def feed_out(e: AudioEngine, value, total, chunk=BLOCK):
    """Скормить тишину/сигнал и вернуть выход целиком."""
    out_parts = []
    done = 0
    while done < total:
        n = min(chunk, total - done)
        out_parts.append(process(e, np.full(n, float(value), dtype=np.float32)))
        done += n
    return np.concatenate(out_parts) if out_parts else np.zeros(0, dtype=np.float32)


CYCLE = 192000
BLOCK_LEN = 96000


# ======================================================================
# Запись / воспроизведение («Вручную»)
# ======================================================================
def test_record_and_loop():
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)          # ровно один цикл с позиции 0
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert s.state == SlotState.LOADED
    assert s.length == CYCLE
    assert abs(float(s.data[1000]) - 0.5) < 1e-6
    y = process(e, block_signal(0.0))
    assert np.allclose(y, 0.5, atol=1e-5)   # цикл крутится
    print("OK  запись → цикл")


def test_seamless_wrap():
    """Перенос через конец цикла — без фейдов и щелчков (единый тайминг)."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)
    e.post("toggle_rec")
    flush(e)

    ys = [process(e, block_signal(0.0)) for _ in range(CYCLE // BLOCK + 8)]
    y = np.concatenate(ys)
    assert float(np.min(y)) > 0.45 and float(np.max(y)) < 0.55
    print("OK  бесшовный перенос")


def test_manual_writes_all_blocks():
    """«Вручную»: пишутся ВСЕ блоки без учёта порога — всё зелёное."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.0, BLOCK_LEN)              # блок 0 — тишина, но пишется
    feed(e, 0.5, BLOCK_LEN)              # блок 1 — звук
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert s.length == CYCLE                    # записан весь цикл
    assert abs(float(s.data[5000])) < 1e-6      # тишина записана
    assert abs(float(s.data[BLOCK_LEN + 5000]) - 0.5) < 1e-6
    assert bool(np.all(s.block_flags))          # всё зелёное
    print("OK  вручную — пишутся все блоки")


def test_rerecord_and_undo():
    """Повторная запись ЗАМЕНЯЕТ блоки; Undo откатывает всю последнюю запись."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, CYCLE)
    e.post("toggle_rec")
    flush(e)

    e.post("toggle_rec")                 # повторная запись (замена)
    flush(e)                             # пишет с позиции 256
    feed(e, 0.7, 20000)
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert abs(float(s.data[100]) - 0.3) < 1e-6      # до начала перезаписи
    assert abs(float(s.data[2000]) - 0.7) < 1e-6     # новый кусок (замена, не сумма!)
    assert abs(float(s.data[50000]) - 0.3) < 1e-6    # незаписанная часть блока сохранена
    e.post("undo")
    flush(e)
    assert abs(float(s.data[2000]) - 0.3) < 1e-6     # вернулось
    assert abs(float(s.data[50000]) - 0.3) < 1e-6
    print("OK  перезапись-замена + undo")


def test_rerecord_undo_after_wrap():
    """Перезапись через перенос конца цикла; Undo откатывает всё."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, CYCLE)
    e.post("toggle_rec")
    flush(e)                                     # pos = 256

    feed(e, 0.0, CYCLE - 512)                    # pos = CYCLE - 256
    e.post("toggle_rec")                         # запись через перенос
    feed(e, 0.7, 1000)                           # хвост цикла + начало
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert abs(float(s.data[CYCLE - 200]) - 0.7) < 1e-6
    assert abs(float(s.data[100]) - 0.7) < 1e-6
    assert abs(float(s.data[2000]) - 0.3) < 1e-6     # незаписанная часть блока сохранена
    e.post("undo")
    flush(e)
    assert abs(float(s.data[CYCLE - 200]) - 0.3) < 1e-6
    assert abs(float(s.data[100]) - 0.3) < 1e-6
    assert abs(float(s.data[2000]) - 0.3) < 1e-6
    print("OK  undo после переноса")


def test_monitor():
    e = make_engine()
    y = process(e, block_signal(0.5))
    assert abs(float(y[0]) - 0.5) < 1e-6
    print("OK  мониторинг входа")


# ======================================================================
# «Авто по звуку»: поблочная запись по порогу
# ======================================================================
def test_auto_block_gating():
    """«Авто» + ожидание: с порога — весь блок пишется; без порога — пропуск."""
    e = make_engine({"autostart": True, "threshold": 0.1})
    e.post("toggle_rec")
    feed(e, 0.0, 50000)                   # тишина: ждём порог, ничего не пишется
    feed(e, 0.5, BLOCK_LEN)               # порог — Play/rec с этого момента (цикл с 0)
    feed(e, 0.0, BLOCK_LEN)               # блок 1 без порога — не пишется
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert s.state == SlotState.LOADED
    assert s.length == CYCLE
    assert abs(float(s.data[60000]) - 0.5) < 1e-6
    assert abs(float(s.data[BLOCK_LEN + 5000])) < 1e-6   # блок 1 пустой
    assert bool(s.block_flags[0]) and not bool(s.block_flags[1])
    peaks, hits = e.get_wave(0)
    assert bool(np.all(hits[:120])) and not bool(np.any(hits[120:]))
    print("OK  авто: блок с порогом — весь, без порога — пропуск")


def test_auto_replace_whole_block():
    """Порог в блоке → старое УДАЛЯЕТСЯ, весь блок пишется новый (включая начало)."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.2, CYCLE)                   # база
    e.post("toggle_rec")
    flush(e)

    e.autostart = True
    e.post("toggle_rec")                  # запись заменой
    feed(e, 0.05, 50000)                  # ниже порога — копится (старое играет)
    feed(e, 0.5, BLOCK_LEN - 50000)       # порог в блоке 0 → удаление + весь блок новый
    feed(e, 0.5, CYCLE - BLOCK_LEN - 256)  # блок 1 — порог сразу → пишется весь
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert abs(float(s.data[1000]) - 0.05) < 1e-6           # начало блока — НОВОЕ (не 0.2!)
    assert abs(float(s.data[60000]) - 0.5) < 1e-6           # после порога
    assert abs(float(s.data[BLOCK_LEN + 1000]) - 0.5) < 1e-6  # блок 1 новый
    assert bool(np.all(s.block_flags))
    print("OK  авто: старое удаляется, блок пишется весь новый")


def test_play_old_until_hit():
    """Старый блок ИГРАЕТ до порога; при пороге — удаляется и пишется новый."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, CYCLE)                   # база 0.3
    e.post("toggle_rec")
    flush(e)

    e.autostart = True
    e.post("toggle_rec")                  # запись заменой
    outs = [process(e, block_signal(0.0)) for _ in range(20)]
    assert np.allclose(np.concatenate(outs), 0.3, atol=1e-5)   # старое играет

    feed(e, 0.5, 30000)                   # порог → удаление + новая запись
    s = e.slots[0]
    assert abs(float(s.data[3000])) < 1e-6         # старое удалено (было 0.3!)
    assert abs(float(s.data[10000]) - 0.5) < 1e-6  # новое пишется после порога
    y = process(e, block_signal(0.0))
    assert abs(float(y[0])) < 1e-6                 # старое больше не играет
    e.post("toggle_rec")
    flush(e)
    print("OK  старый блок играет до порога, потом замена")


def test_auto_no_hit_keeps_previous():
    """Проход без порога — прошлая запись остаётся (ничего не пишем)."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, CYCLE)
    e.post("toggle_rec")
    flush(e)

    e.autostart = True
    e.post("toggle_rec")                  # запись заменой
    feed(e, 0.0, CYCLE)                   # ни одного порога
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert abs(float(s.data[5000]) - 0.3) < 1e-6
    assert abs(float(s.data[150000]) - 0.3) < 1e-6
    print("OK  без порога — прошлая запись сохраняется")


def test_auto_empty_stays_empty():
    """Полный цикл тишины в «Авто» — слот остаётся пустым."""
    e = make_engine({"autostart": True, "threshold": 0.1})
    e.post("toggle_rec")
    feed(e, 0.0, CYCLE)
    e.post("toggle_rec")
    flush(e)
    assert e.slots[0].state == SlotState.EMPTY
    assert e.slots[0].length == 0
    print("OK  пустой цикл — слот остаётся пустым")


def test_clear_zeroes():
    """«Очистить» стирает сэмплы: старый материал не «воскресает» при новой записи."""
    e = make_engine({"autostart": True, "threshold": 0.1})
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)                   # записали 0.5
    e.post("toggle_rec")
    flush(e)
    e.post("clear")
    flush(e)
    assert e.slots[0].state == SlotState.EMPTY

    e.post("toggle_rec")                  # новая запись: порог только в блоке 1
    feed(e, 0.0, BLOCK_LEN)               # блок 0 без порога
    feed(e, 0.3, BLOCK_LEN)               # блок 1 с порогом
    e.post("toggle_rec")
    flush(e)
    s = e.slots[0]
    assert abs(float(s.data[5000])) < 1e-6              # блок 0 пуст (не «воскрес»!)
    assert abs(float(s.data[BLOCK_LEN + 5000]) - 0.3) < 1e-6
    print("OK  очистка стирает дорожку полностью")


# ======================================================================
# Транспорт: единый тайминг
# ======================================================================
def test_pause_resume_stop():
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, 12000)
    e.post("toggle_rec")
    flush(e)
    assert e.transport == "running"

    p0 = e._pos
    e.post("toggle_play")                 # пауза
    flush(e)
    assert e.transport == "paused"
    assert e._pos == p0                   # позиция остаётся
    feed(e, 0.0, 5000)                    # на паузе не двигается
    assert e._pos == p0

    e.post("toggle_play")                 # продолжаем все разом
    flush(e)
    assert e.transport == "running"
    assert e._pos == (p0 + BLOCK) % CYCLE

    e.post("stop_all")                    # «Стоп»: всё стоп + сброс
    flush(e)
    assert e.transport == "stopped"
    assert e._pos == 0
    assert not any(s.rec_active for s in e.slots)
    print("OK  пауза/стоп транспорта")


def test_start_immediate():
    """Старт из стопа — сразу с начала цикла (отложенного старта больше нет)."""
    e = make_engine()
    feed(e, 0.0, 2560)                    # время уже идёт, позиция — нет
    e.post("toggle_play")
    flush(e)
    assert e.transport == "running"       # сразу, без ожидания границы такта
    assert e._pos == BLOCK

    e.post("stop_all")
    flush(e)
    assert e.transport == "stopped" and e._pos == 0

    e.post("toggle_rec")                  # запись из стопа — тоже сразу, с начала
    flush(e)
    assert e.transport == "running"
    assert e._pos == BLOCK
    assert e.slots[0].rec_active
    print("OK  старт из стопа — сразу с начала цикла")


def test_auto_arm_starts_at_sound():
    """«Авто по звуку» + Play не запущен: ждём порог — с него Play/rec."""
    e = make_engine({"autostart": True, "threshold": 0.1})
    e.post("toggle_rec")
    flush(e)
    assert e._rec_armed                       # в ожидании
    assert e.transport == "stopped"
    assert not e.slots[0].rec_active

    feed(e, 0.0, 3000)                        # тишина — не стартует
    assert e._rec_armed and e.transport == "stopped"

    feed(e, 0.4, BLOCK)                       # звук выше порога — старт отсюда
    assert not e._rec_armed
    assert e.transport == "running"
    assert e.slots[0].rec_active

    e.post("toggle_rec")                      # конец записи
    flush(e)
    s = e.slots[0]
    assert s.state == SlotState.LOADED
    # запись началась с момента порога: это позиция 0 цикла (блок 1)
    assert bool(s.block_flags[0])
    assert abs(float(s.data[0]) - 0.4) < 1e-6
    print("OK  авто: ожидание порога → Play/rec с момента звука")


def test_auto_waits_for_sound_visual():
    """«Авто» + Запись при работающем Play: до порога «Ждём звук», пишется с порога."""
    e = make_engine({"autostart": True, "threshold": 0.5})
    e.post("toggle_play")                 # проигрывание уже запущено
    flush(e)
    e.post("toggle_rec")                  # авто: запись ждёт звук, а не стартует сразу
    flush(e)
    s = e.slots[0]
    assert s.rec_active and s.wait_hit
    assert e.snapshot()["slots"][0]["waiting"] is True   # UI: «Ждём звук»

    feed(e, 0.05, 12000)                  # тише порога — ничего не пишется
    assert s.wait_hit
    assert float(np.max(np.abs(s.data[:12000]))) == 0.0

    feed(e, 0.8, 3000)                    # порог — запись С ЭТОГО момента
    assert not s.wait_hit
    assert e.snapshot()["slots"][0]["waiting"] is False
    assert bool(s.block_flags[0])
    e.post("toggle_rec")
    flush(e)
    # начало блока — из накопленного до порога («включая начало»)
    assert abs(float(s.data[11000]) - 0.05) < 1e-6
    assert abs(float(s.data[13000]) - 0.8) < 1e-6
    print("OK  авто: «Ждём звук» до порога даже при запущенном Play")


def test_auto_dub_mixed_hits_two_laps():
    """Сценарий пользователя: дубль в «Авто», звук только в блоке 0 — на 2-м круге
    тихие блоки (и старый блок 1) НЕ затираются."""
    e = make_engine()                     # базовый цикл — вручную
    e.post("toggle_rec")
    feed(e, 0.2, CYCLE)                   # база 0.2 во всех блоках
    e.post("toggle_rec")
    e.post("stop_all")                    # позиция ровно на начале цикла
    flush(e)

    e.autostart = True
    e.threshold = 0.5
    e.post("toggle_rec")                  # авто: ждём звук (старт с порога = позиция 0)
    feed(e, 0.6, BLOCK_LEN)               # круг 1, блок 0: порог → замена
    feed(e, 0.05, BLOCK_LEN)              # круг 1, блок 1: тише порога → не трогать
    feed(e, 0.05, BLOCK_LEN)              # круг 2, блок 0: тише порога → не трогать!
    feed(e, 0.05, BLOCK_LEN)              # круг 2, блок 1: тише порога → не трогать
    e.post("toggle_rec")
    flush(e)

    s = e.slots[0]
    assert abs(float(s.data[1000]) - 0.6) < 1e-6              # блок 0 — новый
    assert abs(float(s.data[BLOCK_LEN + 1000]) - 0.2) < 1e-6  # блок 1 — СТАРЫЙ цел
    assert bool(s.block_flags[0]) and bool(s.block_flags[1])
    print("OK  дубль в «Авто»: без порога блок не затирается (2 круга)")


def test_wrap_no_rewrite_without_threshold():
    """Без порога блок не перезаписывается даже на следующем круге."""
    # 1 блок = весь цикл: перенос круга раньше сбивал флаг порога —
    # запись шла без звука (без порога).
    e = make_engine({"autostart": True, "threshold": 0.5, "num_blocks": 1})
    cyc = int(e._cycle_len)
    e.post("toggle_rec")
    feed(e, 0.8, cyc)                     # порог → пишется весь блок
    feed(e, 0.2, cyc + 5000)              # полный круг без порога + кусок
    e.post("toggle_rec")
    flush(e)
    s = e.slots[0]
    assert s.state == SlotState.LOADED
    assert abs(float(s.data[1000]) - 0.8) < 1e-6        # НЕ переписано на 0.2
    assert abs(float(s.data[cyc // 2]) - 0.8) < 1e-6
    assert bool(s.block_flags[0])

    # и «зелёный» флаг не гаснет без порога (4 блока в цикле)
    e2 = make_engine({"autostart": True, "threshold": 0.5})
    e2.post("toggle_rec")
    feed(e2, 0.8, BLOCK_LEN)              # блок 0 — порог → пишется
    feed(e2, 0.0, CYCLE - BLOCK_LEN)      # остальные — без порога
    feed(e2, 0.2, CYCLE + 5000)           # круг без порога + кусок
    e2.post("toggle_rec")
    flush(e2)
    s2 = e2.slots[0]
    assert abs(float(s2.data[1000]) - 0.8) < 1e-6
    assert bool(s2.block_flags[0]) and not bool(s2.block_flags[1])
    print("OK  без порога на круге — старое не перезаписывается")


def test_metronome_only_when_running():
    """Метроном — только при проигрывании; выкл/вкл на лету — по правилам."""
    e = make_engine({"metronome_on": False, "bpm": 120, "beats_per_bar": 4,
                     "metro_gain": 1.0, "metro_sound": "beep"})
    e.metronome_on = True                  # галочка стоит, но транспорт стоп
    y = process(e, block_signal(0.0))
    assert float(np.max(np.abs(y))) == 0.0        # стоп — не звучит

    e.post("toggle_play")
    feed(e, 0.0, 24000 - 256)                     # pos → 23744 (до 2-й доли)
    y = process(e, block_signal(0.0))            # [23744,24000): между долями
    assert float(np.max(np.abs(y))) == 0.0
    y = process(e, block_signal(0.0))            # [24000,24256): доля 2
    assert float(np.max(np.abs(y))) > 0.1

    e.metronome_on = False                       # выключили на лету
    y = process(e, block_signal(0.0))            # возможен короткий хвост щелчка
    y = process(e, block_signal(0.0))            # дальше — точно тишина
    assert float(np.max(np.abs(y))) == 0.0

    e.metronome_on = True                        # включили на лету
    y = process(e, block_signal(0.0))            # не сразу — ждём границу такта
    assert float(np.max(np.abs(y))) == 0.0
    print("OK  метроном: только при проигрывании, выкл — сразу")


def test_metronome_join_midrun():
    """Включение метронома на лету — со следующего такта (1-я доля — акцент)."""
    e = make_engine({"metronome_on": False, "bpm": 120, "beats_per_bar": 4,
                     "metro_gain": 1.0, "metro_sound": "beep"})
    e.post("toggle_play")
    feed(e, 0.0, 50000)                          # позиция 50000 (середина такта)
    e.post("toggle_play")                        # пауза
    flush(e)
    assert e.transport == "paused" and e._pos == 50000

    e.metronome_on = True                        # включение на паузе
    y = process(e, block_signal(0.0))            # пауза — не звучит
    assert float(np.max(np.abs(y))) == 0.0
    assert e._metro_skip == 1                    # пропустить долю 72000

    e.post("toggle_play")                        # продолжаем
    flush(e)
    feed(e, 0.0, 72000 - 50256 - 256)            # до позиции 71744
    y = process(e, block_signal(0.0))            # [71744,72000)
    assert float(np.max(np.abs(y))) == 0.0
    y = process(e, block_signal(0.0))            # [72000,72256): доля — но пропуск
    assert float(np.max(np.abs(y))) == 0.0       # щелчка нет (ждём такт)
    feed(e, 0.0, 96000 - 72256 - 256)            # до позиции 95744
    y = process(e, block_signal(0.0))            # [95744,96000)
    assert float(np.max(np.abs(y))) == 0.0
    y = process(e, block_signal(0.0))            # [96000,96256): 1-я доля такта
    assert float(np.max(np.abs(y))) > 0.1        # вступление со следующего такта
    print("OK  метроном: включение на лету — со следующего такта")


def test_switch_during_recording():
    """Переключение при записи: новая дорожка пишет с текущего тайминга."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, 48000)                   # полблока на лупе 0
    e.post("select_slot", 1)
    flush(e)

    s0, s1 = e.slots[0], e.slots[1]
    assert s0.state == SlotState.LOADED and not s0.rec_active
    assert s1.rec_active
    assert abs(float(s0.data[1000]) - 0.5) < 1e-6
    assert float(s0.data[48000 + 5000]) == 0.0     # дальше не писался
    assert float(s1.data[1000]) == 0.0             # новая — с места тайминга
    feed(e, 0.25, 20000)
    assert abs(float(s1.data[48256 + 500]) - 0.25) < 1e-6
    assert e.slots[0].playhead == e.slots[1].playhead   # одна позиция
    print("OK  переключение дорожки — запись с текущего тайминга")


def test_multi_slot_mix():
    """Два лупа играют одновременно (микс) с одной позиции."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.3, CYCLE)
    e.post("toggle_rec")
    flush(e)
    e.post("select_slot", 1)
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)                   # покрывает позицию целиком
    e.post("toggle_rec")
    flush(e)

    y = process(e, block_signal(0.0))
    assert np.allclose(y, 0.8, atol=1e-5)
    assert e.slots[0].playhead == e.slots[1].playhead
    print("OK  микс двух слотов")


def test_tempo_retimes_material():
    """Общий темп: смена BPM пересчитывает и сетку, и записанный материал."""
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE // 2)          # первая половина цикла — 0.5
    feed(e, 0.25, CYCLE // 2)         # вторая — 0.25
    e.post("toggle_rec")
    flush(e)
    assert e._cycle_len == CYCLE

    e.bpm = 60.0                      # вдвое медленнее → такты вдвое ДЛИННЕЕ по времени
    e.update_grid()
    assert e._cycle_len == 2 * CYCLE   # цикл вдвое длиннее
    s = e.slots[0]
    assert s.length == e._cycle_len
    assert abs(float(s.data[100000]) - 0.5) < 1e-5    # первая половина растянута
    assert abs(float(s.data[300000]) - 0.25) < 1e-5   # вторая половина

    e.bpm = 120.0                     # обратно — материал сжимается
    e.update_grid()
    assert e._cycle_len == CYCLE
    assert abs(float(s.data[50000]) - 0.5) < 1e-5
    assert abs(float(s.data[150000]) - 0.25) < 1e-5
    print("OK  темп общий: материал пересчитывается вместе с сеткой")


def test_metronome_clicks():
    """Метроном: доля 1 такта — акцент, остальные слабые; в цикл не пишется."""
    e = make_engine({
        "metronome_on": True, "bpm": 120, "beats_per_bar": 4,
        "metro_gain": 1.0, "metro_sound": "beep",
    })
    e.post("toggle_play")                 # старт сразу, с начала цикла (доля 1)
    chunks = [process(e, block_signal(0.0))[:, ] for _ in range(200)]
    y = np.concatenate(chunks)            # 51200 сэмплов ≈ 2.1 с
    assert float(np.max(np.abs(y[:300]))) > 0.1          # доля 1
    assert float(np.max(np.abs(y[10000:12000]))) == 0.0  # между долями
    assert float(np.max(np.abs(y[23950:24150]))) > 0.1   # доля 2 (24000)
    e1 = float(np.sum(y[:300].astype(np.float64) ** 2))  # акцент (1-я доля)
    e2 = float(np.sum(y[23950:24150].astype(np.float64) ** 2))  # слабая
    assert e1 > e2 > 1.0                                 # акцент громче по энергии
    print("OK  метроном")


# ======================================================================
# Волноформа, кольцо, экспорт
# ======================================================================
def test_drum_events_adapt():
    """Таблица драм-машины: крэш — 1-й такт, брейк — последний; размер и такты."""
    from drums import build_events

    # 4/4, 2 такта цикла (такты=1, блоки=2): слот 1/16, такт = 16 слотов
    t = build_events("Поп", 4, 1, 2)
    assert len(t) == 32
    names0 = [n for n, _v in t[0]]
    assert "crash" in names0 and "kick" in names0          # начало цикла
    assert all("crash" not in [n for n, _v in s] for s in t[1:])
    fill = [i for i, s in enumerate(t) if any(n == "fsnare" for n, _v in s)]
    assert fill and all(i >= 24 for i in fill)             # брейк в последнем такте
    # «Диско»: бочка на каждую долю (слоты 0, 4, 8, 12 такта)
    td = build_events("Диско", 4, 1, 1)
    kicks = [i for i, s in enumerate(td) if any(n == "kick" for n, _v in s)]
    assert set(kicks) >= {0, 4, 8, 12}
    # размер 3/4: такт = 12 слотов, паттерн сжимается под размер
    t3 = build_events("Диско", 3, 1, 1)
    assert len(t3) == 12
    kicks3 = [i for i, s in enumerate(t3) if any(n == "kick" for n, _v in s)]
    assert 0 in kicks3
    assert kicks3 != [0, 4, 8, 12]                          # масштабирование
    # количество тактов: 2×2 = 4 такта — брейк на 4-м, крэш только на 1-м
    t4 = build_events("Рок", 4, 2, 2)
    assert len(t4) == 64
    crash_bars = sorted({i // 16 for i, s in enumerate(t4)
                         if any(n == "crash" for n, _v in s)})
    fill_bars = sorted({i // 16 for i, s in enumerate(t4)
                        if any(n == "fsnare" for n, _v in s)})
    assert crash_bars == [0]
    assert fill_bars == [3]
    print("OK  драм-таблица (стили/размер/такты)")


def test_drum_meter_variants():
    """У каждого размера — СВОЙ вариант паттерна (без сжатия): слоты точные."""
    from drums import build_events

    def hits(t, instr):
        return [i for i, s in enumerate(t) if any(n == instr for n, _v in s)]

    # Поп 4/4: бочка на 1 и «и» 3-й (слоты 0, 10), малый на 2 и 4 (4, 12)
    t4 = build_events("Поп", 4, 1, 1)
    assert set(hits(t4, "kick")) == {0, 10}
    assert set(hits(t4, "snare")) == {4, 12}
    # Поп 3/4: бочка на 1 (слот 0), малый на 2 и 3 (слоты 4, 8) — своя музыка!
    t3 = build_events("Поп", 3, 1, 1)
    assert set(hits(t3, "kick")) == {0}
    assert set(hits(t3, "snare")) == {4, 8}
    # Рок 5/4: бочка 1, 3, «и» 4 (слоты 0, 8, 12), малый 2 и 5 (4, 16)
    t5 = build_events("Рок", 5, 1, 1)
    assert set(hits(t5, "kick")) == {0, 8, 12}
    assert set(hits(t5, "snare")) == {4, 16}
    # Хэт: ровные 8-е в Поп (каждый 2-й слот) и 16-е в Электро (каждый слот)
    hats_p = hits(build_events("Поп", 4, 1, 1), "hat")
    assert hats_p == list(range(0, 16, 2))
    hats_e = hits(build_events("Электро", 4, 1, 1), "hat")
    assert hats_e == list(range(16))
    # ВСЕ позиции — целые слоты 1/16 (нет полуслотов → нет «хаоса»)
    for style in ("Поп", "Рок", "Фанк", "Диско"):
        for meter in (2, 3, 4, 5, 6):
            t = build_events(style, meter, 1, 1)
            assert all(isinstance(i, int) for i in range(len(t)))
            assert sum(len(s) for s in t) > 0
    print("OK  варианты паттерна по размерам (точные слоты)")


def test_drum_tails_sum():
    """Одновременные удары: хвосты СУММИРУЮТСЯ, а не проигрываются по очереди."""
    e = make_engine()
    a = np.ones(1000, dtype=np.float32)          # «бочка» — длинный хвост
    b = np.ones(1000, dtype=np.float32) * 0.5    # «хэт» — с ней же, но позже в блоке
    out = np.zeros(BLOCK, dtype=np.float32)
    e._mix_drum(out, 0, a, 1.0)
    e._mix_drum(out, 100, b, 1.0)
    # оба хвоста начинаются со следующего блока и должны лежать ДРУГ НА ДРУГЕ
    assert len(e._drum_tail) == 844              # max(1000-256, 1000-156)
    assert abs(float(e._drum_tail[0]) - 1.5) < 1e-6     # 1.0 + 0.5 в нуле
    assert abs(float(e._drum_tail[743]) - 1.5) < 1e-6
    assert abs(float(e._drum_tail[744]) - 0.5) < 1e-6   # хвост «а» кончился
    print("OK  хвосты одновременных ударов суммируются")


def test_drum_hits_clean():
    """Удары без щелчков: начинаются с нуля, нормированы, без клиппинга."""
    from drums import make_hit
    for kind in ("kick", "snare", "fsnare", "hat", "openhat", "crash"):
        h = make_hit(kind, SR)
        assert abs(float(h[0])) < 1e-3, kind              # без щелчка в начале
        assert abs(float(h[-1])) < 1e-2, kind             # и в конце
        assert float(np.max(np.abs(h))) <= 1.0 + 1e-6, kind
    print("OK  звуки ударов чистые (без щелчков)")


def test_drum_no_clip_when_overlapping():
    """Сумма одновременных ударов не клиппится (мягкий лимитер)."""
    e = make_engine({"drum_on": True, "drum_gain": 1.5, "monitor_gain": 0.0,
                     "drum_style": "Диско", "bars_per_block": 2, "num_blocks": 2})
    e.post("toggle_play")
    y = np.concatenate([process(e, block_signal(0.0)) for _ in range(400)])
    assert float(np.max(np.abs(y))) <= 1.0 + 1e-6
    print("OK  без клиппинга при наложении")


def test_drum_plays_with_transport():
    """Драмы звучат только с транспортом; отбивка тактов = длина такта."""
    e = make_engine({"drum_on": True, "drum_gain": 1.0, "monitor_gain": 0.0,
                     "drum_style": "Диско", "bars_per_block": 1, "num_blocks": 2})
    y = feed_out(e, 0.0, 5000)                # стоп — тишина
    assert float(np.max(np.abs(y))) == 0.0
    e.post("toggle_play")
    y = np.concatenate([process(e, block_signal(0.0)) for _ in range(400)])
    # такт = 96000 (BPM 120, 4/4); крэш+бочка — на 0 и на 96000
    assert float(np.max(np.abs(y[:800]))) > 0.05
    assert float(np.max(np.abs(y[95500:96500]))) > 0.05
    e.post("stop_all")
    y = feed_out(e, 0.0, 5000)
    assert float(np.max(np.abs(y))) == 0.0
    print("OK  драмы только с транспортом")


def test_drum_follows_tempo():
    """Темп общий: BPM 60 — такт вдвое длиннее."""
    e = make_engine({"drum_on": True, "drum_gain": 1.0, "monitor_gain": 0.0,
                     "drum_style": "Диско", "bpm": 60.0,
                     "bars_per_block": 1, "num_blocks": 1})
    e.post("toggle_play")
    y = np.concatenate([process(e, block_signal(0.0)) for _ in range(900)])
    # такт = 192000: второй такт цикла (в цикле 1 такт — просто следующий круг)
    assert float(np.max(np.abs(y[:800]))) > 0.05
    assert float(np.max(np.abs(y[191500:192500]))) > 0.05
    print("OK  драмы подстраиваются под темп")


def test_drum_not_recorded():
    """Драм-машина не пишется в лупы (только в выход)."""
    e = make_engine({"drum_on": True, "drum_gain": 1.0, "monitor_gain": 0.0,
                     "autostart": False})
    e.post("toggle_play")
    e.post("toggle_rec")
    feed(e, 0.0, CYCLE)                       # тишина на входе + драмы на выходе
    e.post("toggle_rec")
    flush(e)
    s = e.slots[0]
    assert s.length == CYCLE
    assert float(np.max(np.abs(s.data[:CYCLE]))) == 0.0
    print("OK  драмы в луп не пишутся")


def test_drum_preview():
    """Предпрослушивание без транспорта: один цикл и само выключение."""
    e = make_engine({"drum_on": False, "drum_gain": 1.0, "monitor_gain": 0.0,
                     "drum_style": "Диско", "bars_per_block": 1, "num_blocks": 2})
    e.post("toggle_drum_preview")
    y = np.concatenate([process(e, block_signal(0.0)) for _ in range(800)])
    assert float(np.max(np.abs(y[:800]))) > 0.05          # играет
    assert float(np.max(np.abs(y[192000:200000]))) == 0.0  # цикл кончился — тишина
    assert e.drum_preview is False
    print("OK  предпрослушивание (цикл и стоп)")


def test_drum_styles_differ():
    """Разные стили звучат по-разному."""
    outs = []
    for style in ("Поп", "Диско"):
        e = make_engine({"drum_on": True, "drum_gain": 1.0, "monitor_gain": 0.0,
                         "drum_style": style, "bars_per_block": 1, "num_blocks": 1})
        e.post("toggle_play")
        outs.append(np.concatenate([process(e, block_signal(0.0)) for _ in range(400)]))
    assert float(np.max(np.abs(outs[0] - outs[1]))) > 0.1
    print("OK  стили различаются")


def test_drum_style_library():
    """Библиотека стилей: 3 варианта на жанр, все размеры, рим/призраки, алиасы."""
    from drums import STYLE_NAMES, build_events, canonical

    # каждый стиль собирается для всех размеров и содержит бочку/малый
    for name in STYLE_NAMES:
        for bpb in (2, 3, 4, 5, 6):
            t = build_events(name, bpb, 2, 2)
            assert t, name
            assert any(any(n in ("kick", "snare") for n, _v in s) for s in t), (name, bpb)

    # у каждого жанра ровно 3 варианта, и все три — РАЗНЫЕ рисунки
    genres = {}
    for name in STYLE_NAMES:
        genres.setdefault(name.rsplit(" ", 1)[0], []).append(name)
    assert len(genres) >= 18, sorted(genres)
    for g, names in genres.items():
        assert len(names) == 3, (g, names)
        sigs = {tuple(tuple(s) for s in build_events(n, 4, 1, 1)) for n in names}
        assert len(sigs) == 3, g

    # рим-клик и «призраки» реально попадают в таблицу событий
    assert any(any(n == "fsnare" for n, _v in s)
               for s in build_events("Боса-нова 1", 4, 1, 1))
    assert any(any(n == "snare" for n, _v in s)
               for s in build_events("Фанк 2", 4, 1, 1))

    # алиасы старых имён из настроек работают
    assert canonical("Поп") == "Поп 1"
    assert canonical("Поп 2") == "Поп 2"
    assert canonical("выдумка") == "Поп 1"
    assert build_events("Поп", 4, 1, 1) == build_events("Поп 1", 4, 1, 1)
    print(f"OK  библиотека стилей ({len(STYLE_NAMES)} стилей = 3 × {len(genres)} жанров)")


def test_mute_slot():
    """Mute глушит воспроизведение слота, но запись не трогает."""
    e = make_engine({"monitor_gain": 0.0})
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)
    e.post("toggle_rec")
    flush(e)
    y = process(e, block_signal(0.0))
    assert float(np.max(np.abs(y))) > 0.2                # луп слышен
    e.post("toggle_mute", 0)
    flush(e)
    y = process(e, block_signal(0.0))
    assert float(np.max(np.abs(y))) == 0.0               # приглушён
    s = e.slots[0]
    assert s.muted
    assert s.length == CYCLE                             # запись цела
    assert float(np.max(np.abs(s.data[:CYCLE]))) > 0.4
    e.post("toggle_mute", 0)
    flush(e)
    y = process(e, block_signal(0.0))
    assert float(np.max(np.abs(y))) > 0.2                # снова слышен
    print("OK  mute слота (запись цела)")


def test_slot_click():
    """Клик по кнопке лупа: выбор; повторный клик по выбранному — вкл/выкл mute."""
    e = make_engine()
    e.post("slot_click", 0)          # слот 0 и так выбран → mute
    flush(e)
    assert e.slots[0].muted and e.current_slot == 0
    e.post("slot_click", 1)          # другой слот → выбор, mute не включается
    flush(e)
    assert e.current_slot == 1 and not e.slots[1].muted
    assert e.slots[0].muted          # mute соседа сохранился
    e.post("slot_click", 1)          # тот же → mute
    flush(e)
    assert e.slots[1].muted
    e.post("slot_click", 1)          # тот же → снова активен
    flush(e)
    assert not e.slots[1].muted
    e.post("slot_3")                 # MIDI «Выбрать луп 3» — только выбор
    flush(e)
    assert e.current_slot == 2 and not e.slots[2].muted
    print("OK  клик по слоту: выбор / mute")


def test_get_wave():
    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)
    e.post("toggle_rec")
    flush(e)

    peaks, hits = e.get_wave(0)
    assert peaks.shape == hits.shape
    assert float(np.min(peaks)) > 0.45
    assert bool(np.all(hits))             # вручную — всё зелёное
    p2, h2 = e.get_wave(0)
    assert p2 is peaks                    # кэш
    e.post("clear")
    flush(e)
    p3, h3 = e.get_wave(0)
    assert float(np.max(p3)) == 0.0
    assert not bool(np.any(h3))
    print("OK  волноформа + блоки")


def test_ring_buffer():
    e = make_engine()
    x = (np.arange(1000, dtype=np.float32) % 3) + 0.5
    with e._ring_lock:
        e._ring_write(x)
    buf = np.zeros(1000, dtype=np.float32)
    with e._ring_lock:
        e._ring_read_into(buf)
    assert np.allclose(buf, x)

    # Недобор входа → нули в хвосте
    with e._ring_lock:
        e._ring_write(np.ones(10, dtype=np.float32))
    buf = np.zeros(20, dtype=np.float32)
    with e._ring_lock:
        e._ring_read_into(buf)
    assert np.allclose(buf[:10], 1.0) and np.allclose(buf[10:], 0.0)


def test_ring_read_latency_clamp():
    e = make_engine()
    big = (np.arange(2000, dtype=np.float32) % 7) + 0.25
    with e._ring_lock:
        e._ring_write(big)
    buf = np.zeros(100, dtype=np.float32)
    with e._ring_lock:
        e._ring_read_into(buf)
    assert np.allclose(buf, big[:100])             # первое чтение — начало
    buf2 = np.zeros(100, dtype=np.float32)
    with e._ring_lock:
        e._ring_read_into(buf2)
    # clamp: при большом отставании чтение прыгает к «свежему» краю
    assert np.allclose(buf2, big[1488:1588])
    print("OK  ограничение задержки кольца")


def test_export(tmp="test_export.wav"):
    import os

    e = make_engine()
    e.post("toggle_rec")
    feed(e, 0.5, CYCLE)
    e.post("toggle_rec")
    flush(e)
    e.export_slot_wav(0, Path(tmp))
    assert os.path.getsize(tmp) > 1000
    os.remove(tmp)
    print("OK  экспорт WAV")


if __name__ == "__main__":
    test_record_and_loop()
    test_seamless_wrap()
    test_manual_writes_all_blocks()
    test_rerecord_and_undo()
    test_rerecord_undo_after_wrap()
    test_monitor()
    test_auto_block_gating()
    test_auto_replace_whole_block()
    test_play_old_until_hit()
    test_auto_no_hit_keeps_previous()
    test_auto_empty_stays_empty()
    test_clear_zeroes()
    test_pause_resume_stop()
    test_start_immediate()
    test_auto_arm_starts_at_sound()
    test_auto_waits_for_sound_visual()
    test_auto_dub_mixed_hits_two_laps()
    test_wrap_no_rewrite_without_threshold()
    test_switch_during_recording()
    test_multi_slot_mix()
    test_tempo_retimes_material()
    test_metronome_clicks()
    test_metronome_only_when_running()
    test_metronome_join_midrun()
    test_drum_events_adapt()
    test_drum_meter_variants()
    test_drum_tails_sum()
    test_drum_hits_clean()
    test_drum_no_clip_when_overlapping()
    test_drum_plays_with_transport()
    test_drum_follows_tempo()
    test_drum_not_recorded()
    test_drum_preview()
    test_drum_styles_differ()
    test_drum_style_library()
    test_mute_slot()
    test_slot_click()
    test_get_wave()
    test_ring_buffer()
    test_ring_read_latency_clamp()
    test_export()
    print("\nВсе тесты пройдены ✔")
