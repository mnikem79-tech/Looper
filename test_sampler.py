"""
Тесты MIDI-сэмплера (sampler.py) на звуках из sounds/.
Запуск: python test_sampler.py
"""
import sys
from pathlib import Path

import numpy as np

from sampler import Sampler

SOUNDS = Path(__file__).resolve().parent / "sounds"
FAILED = []


def check(name, cond):
    print(("OK  " if cond else "FAIL") + "  " + name)
    if not cond:
        FAILED.append(name)


def fresh(sr=48000):
    return Sampler(SOUNDS, sr)


def main():
    s = fresh()
    check("банк загружен", s.ok and len(s.instruments) >= 18)
    check("категории на месте",
          set(s.groups()) >= {"Фортепиано", "Органы", "Гитары", "Басы",
                              "Струнные и хор", "Духовые", "Синтезаторы", "Ударные"})

    # --- обычный инструмент: звук есть ---
    s.set_instrument(0)
    s.note(60, 100)
    buf = s.render(480)
    check("нота звучит (не тишина)", float(np.abs(buf).max()) > 0.01)

    # --- отпускание: спад и освобождение ---
    s.note(60, 0)
    peak1 = float(np.abs(s.render(480)).max())
    s.render(4800)
    s.render(4800)
    peak2 = float(np.abs(s.render(480)).max())
    check("спад после отпускания", peak2 < peak1 or peak2 < 1e-4)
    s.all_off()

    # --- питч-шифт: разные ноты дают разный сигнал ---
    s.note(48, 100)
    low = s.render(4096).copy()
    s.all_off()
    s.note(72, 100)
    high = s.render(4096).copy()
    s.all_off()
    check("низкая и высокая — разные", not np.allclose(low, high))

    # --- педаль сустейна: звук держится после отпускания ---
    s.sustain(True)
    s.note(64, 100)
    s.render(240)
    s.note(64, 0)                     # отпустили, но педаль нажата
    held = float(np.abs(s.render(2048)).max())
    check("сустейн держит ноту", held > 0.01)
    s.sustain(False)                  # педаль отпущена — нота гаснет
    s.render(24000)
    after = float(np.abs(s.render(2048)).max())
    check("после педали гаснет", after < held)
    s.all_off()

    # --- педаль не мешает одношотам (ударные) ---
    drums = [i for i, inst in enumerate(s.instruments) if inst["drums"]][0]
    s.set_instrument(drums)
    s.sustain(True)
    s.note(36, 110)
    kick = s.render(2048).copy()
    s.note(36, 0)
    check("бочка играет", float(np.abs(kick).max()) > 0.05)
    s.sustain(False)
    s.all_off()

    # --- ударные: разные клавиши — разные звуки ---
    s.note(38, 110)
    snare = s.render(2048).copy()
    s.all_off()
    check("бочка и малый — разные", not np.allclose(kick, snare))

    # --- полифония не падает ---
    s.set_instrument(0)
    for k in range(40, 70):
        s.note(k, 90)
    out = s.render(1024)
    check("аккорд из 30 нот", float(np.abs(out).max()) > 0.01)
    s.all_off()

    # --- тишина без нот ---
    z = s.render(1024)
    check("тишина без нот", float(np.abs(z).max()) == 0.0)

    # --- другой sample_rate (как у движка) ---
    s2 = fresh(44100)
    s2.set_instrument(0)
    s2.note(60, 100)
    check("рендер на 44100", float(np.abs(s2.render(256)).max()) > 0.01)

    # --- gain ---
    s2.set_gain(0.1)
    quiet = float(np.abs(s2.render(2048)).max())
    s2.set_gain(1.0)
    loud = float(np.abs(s2.render(2048)).max())
    check("gain работает", quiet < loud)

    # --- интеграция с движком: слышно в выходе и пишется в луп ---
    from audio_engine import AudioEngine
    eng = AudioEngine({
        "slots": 4, "sample_rate": 48000, "blocksize": 256,
        "bpm": 120.0, "beats_per_bar": 4, "bars_per_block": 1,
        "num_blocks": 2, "threshold": 0.1, "autostart": False,
    })
    check("движок видит сэмплер", eng.sampler is not None)
    if eng.sampler is not None:
        eng.sampler.note(60, 100)

        def block(sig=None):
            x = (sig if sig is not None else np.zeros(256, dtype=np.float32))
            out = np.zeros((256, 1), dtype=np.float32)
            eng.process_block(x.reshape(-1, 1), out, 256)
            return out[:, 0]

        y = block()
        check("инструмент слышен в выходе", float(np.abs(y).max()) > 0.01)

        # пишется в луп вместе с входом (вход — тишина)
        eng.sampler.all_off()
        eng.post("toggle_rec")
        eng.sampler.note(60, 110)
        for _ in range(192000 // 256):
            block()
        eng.sampler.all_off()
        eng.post("toggle_rec")
        block()
        slot = eng.slots[0]
        rec_peak = float(np.abs(slot.data[:max(1, slot.length)]).max())
        check("инструмент пишется в луп", slot.length > 0 and rec_peak > 0.01)

    print()
    if FAILED:
        print("Провалено:", FAILED)
        sys.exit(1)
    print("Все тесты сэмплера пройдены ✔")


if __name__ == "__main__":
    main()
