"""
Запекание звуковой библиотеки sounds/ из SoundFont (только для разработки).

На ПК пользователя этот скрипт НЕ нужен — папка sounds/ уже лежит в комплекте.
Запуск (нужны pyTinySoundFont и GeneralUser-GS.sf2):

    python tools/bake_sounds.py path/to/GeneralUser-GS.sf2

Сэмплы запекаются как моно WAV 44100 Гц и играются в приложении чистым
numpy-сэмплером (sampler.py) — без сторонних библиотек в рантайме.
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
ROOTS = (36, 48, 60, 72)          # C2, C3, C4, C5 — питч-шифт ±6 полутонов
NOTE_SECONDS = 2.5                # длительность запечённой ноты
DRUM_SECONDS = 1.6

# (программа GM, название, категория)
INSTRUMENTS = [
    (0,  "Акустическое пианино", "Фортепиано"),
    (4,  "Электропиано", "Фортепиано"),
    (16, "Орган", "Органы"),
    (18, "Рок-орган", "Органы"),
    (24, "Нейлоновая гитара", "Гитары"),
    (25, "Стальная гитара", "Гитары"),
    (27, "Чистая эл. гитара", "Гитары"),
    (29, "Овердрайв", "Гитары"),
    (33, "Бас-гитара", "Басы"),
    (38, "Синт-бас", "Басы"),
    (48, "Струнные", "Струнные и хор"),
    (52, "Хор", "Струнные и хор"),
    (61, "Духовые", "Духовые"),
    (73, "Флейта", "Духовые"),
    (81, "Синт-лид", "Синтезаторы"),
    (89, "Тёплый пэд", "Синтезаторы"),
    (98, "Кристалл", "Синтезаторы"),
]

# Клавиши GM-ударных (банк 128)
DRUM_KEYS = [35, 36, 37, 38, 39, 40, 41, 42, 43, 45, 46, 47, 48, 49, 51, 56]


def find_preset(presets, bank, number):
    for p in presets:
        if p["bank"] == bank and p["preset"] == number:
            return p
    raise SystemExit(f"Пресет bank={bank} num={number} не найден")


def render_note(font, preset, key, seconds):
    n = int(SR * seconds)
    frames, buf = tsf.SynthNote(font, preset, key, 1.0, n)
    a = np.frombuffer(buf, dtype=np.float32).reshape(-1, 2)[:n].mean(axis=1).astype(np.float32)
    peak = float(np.abs(a).max())
    if peak > 1e-6:
        a *= 0.89 / peak
    # мягкие края — без щелчков (барабаны-одношоты бывают короче запроса)
    fade_in = min(int(SR * 0.003), len(a) // 2)
    a[:fade_in] *= np.linspace(0.0, 1.0, fade_in, dtype=np.float32)
    tail = min(int(SR * 0.15), len(a) // 2)
    a[-tail:] *= np.linspace(1.0, 0.0, tail, dtype=np.float32)
    return a


def write_wav(path, data):
    pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    global tsf
    import pyTinySoundFont as tsf

    # Патч бага pyTinySoundFont: при выключенном lowpass словарь voice['lowpass']
    # без коэффициентов, а SynthVoice читает их безусловно (KeyError 'a0').
    _orig_synth_voice = tsf.SF2Synth.SynthVoice

    def _synth_voice_patched(input_samples, num_samples, voice, *args, **kw):
        lp = voice["lowpass"]
        lp.setdefault("a0", 1.0)
        lp.setdefault("a1", 0.0)
        lp.setdefault("b1", 0.0)
        lp.setdefault("b2", 0.0)
        return _orig_synth_voice(input_samples, num_samples, voice, *args, **kw)

    tsf.SF2Synth.SynthVoice = _synth_voice_patched

    if len(sys.argv) < 2:
        raise SystemExit("python tools/bake_sounds.py path/to/GeneralUser-GS.sf2")
    sf2_path = Path(sys.argv[1])
    out_dir = Path(__file__).resolve().parent.parent / "sounds"
    out_dir.mkdir(exist_ok=True)

    print("Загрузка", sf2_path.name, "...")
    sf2 = tsf.LoadSF2(str(sf2_path))
    samples = sf2[1]
    presets = tsf.LoadPresets(sf2)

    bank = {"sample_rate": SR, "instruments": []}

    for prog, name, group in INSTRUMENTS:
        preset = find_preset(presets, 0, prog)
        roots = {}
        for key in ROOTS:
            audio = render_note(samples, preset, key, NOTE_SECONDS)
            fname = f"p{prog}_{key}.wav"
            write_wav(out_dir / fname, audio)
            roots[str(key)] = fname
        bank["instruments"].append({
            "name": name, "group": group, "program": prog, "roots": roots,
        })
        print(f"  {name:22s} ok")

    drum_preset = find_preset(presets, 128, 0)
    drums = {}
    for key in DRUM_KEYS:
        audio = render_note(samples, drum_preset, key, DRUM_SECONDS)
        fname = f"drum_{key}.wav"
        write_wav(out_dir / fname, audio)
        drums[str(key)] = fname
    bank["instruments"].append({
        "name": "Ударные (GM-кит)", "group": "Ударные",
        "program": None, "drums": drums,
    })
    print("  Ударные (GM-кит)       ok")

    (out_dir / "manifest.json").write_text(
        json.dumps(bank, ensure_ascii=False, indent=1), encoding="utf-8")
    files = sorted(out_dir.glob("*.wav"))
    total = sum(f.stat().st_size for f in files)
    print(f"Готово: {len(files)} wav, {total/1e6:.1f} МБ → {out_dir}")


if __name__ == "__main__":
    main()
