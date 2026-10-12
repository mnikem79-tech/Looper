"""
SMK-37 Looper — точка входа.

Гитара подключается к аудиовходу компьютера, MIDI-клавиатура (M-VAVE SMK-37 Pro
или любая другая) управляет записью/циклами. Слоты лупов можно переключать
с клавиатуры, поверх цикла пишется дубль (overdub) с возможностью undo.
"""
import sys
import traceback


def main() -> int:
    # Проверка зависимостей — понятное сообщение вместо traceback.
    missing = []
    for mod in ("numpy", "sounddevice", "mido"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"Не установлены модули: {', '.join(missing)}.")
        print("Запустите install.bat (один раз), затем start.bat.")
        return 1

    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("Не найден tkinter. Переустановите Python с python.org с галочкой tcl/tk.")
        return 1

    from config import load, save
    from audio_engine import AudioEngine
    from midi_handler import MidiHandler
    from gui import App

    cfg = load()
    engine = AudioEngine(cfg)

    # MIDI-колбэки кладут события в очередь UI (см. App._poll).
    # Ноты идут напрямую в сэмплер — чтобы играть без задержки.
    midi = MidiHandler(
        on_message=lambda key: app.ui_q.put(("midi", key)),
        on_learn=lambda action, key: app.ui_q.put(("learn", action, key)),
        on_note=lambda note, vel: (engine.sampler.note(note, vel)
                                   if engine.sampler is not None and app.kb_play(note)
                                   else None),
        on_sustain=lambda down: (engine.sampler.sustain(down)
                                 if engine.sampler is not None else None),
    )

    app = App(cfg, engine, midi, on_close=save)

    # Автоподключение по сохранённым настройкам.
    try:
        names = midi.list_inputs()
        saved_midi = cfg.get("midi_input", "")
        if saved_midi and any(saved_midi in n for n in names):
            target = next(n for n in names if saved_midi in n)
            midi.open(target)
            app.log(f"MIDI подключён: {target}")
    except Exception as e:
        app.log(f"Автоподключение MIDI не удалось: {e}")

    try:
        import sounddevice as sd

        devices = sd.query_devices()
        saved_in, saved_out = cfg.get("audio_input", ""), cfg.get("audio_output", "")
        in_idx = out_idx = None
        for i, d in enumerate(devices):
            if saved_in and saved_in in d["name"] and d["max_input_channels"] > 0 and in_idx is None:
                in_idx = i
            if saved_out and saved_out in d["name"] and d["max_output_channels"] > 0 and out_idx is None:
                out_idx = i
        if in_idx is not None and out_idx is not None:
            engine.start(in_idx, out_idx)
            app.log("Аудио запущено по сохранённым настройкам")
    except Exception as e:
        app.log(f"Автозапуск аудио не удался: {e}")
        app.log("Выберите устройства вручную и нажмите «Применить»")

    app.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        input("\nПроизошла ошибка. Нажмите Enter для выхода...")
        sys.exit(1)
