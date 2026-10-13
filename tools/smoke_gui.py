"""
Смоук-тест GUI: полная сборка окна под Xvfb (только для разработки).
Ловит падения вида «обратный вызов сработал до создания виджета».
"""
import sys

sys.path.insert(0, "/home/user/smk37-looper")

from audio_engine import AudioEngine
from config import load
from gui import App


class StubMidi:
    learn_target = None
    port_name = ""

    def start_learn(self, _a):
        pass

    def cancel_learn(self):
        pass

    def close(self):
        pass

    def open(self, _name):
        pass

    @staticmethod
    def list_inputs():
        return []


def main() -> int:
    cfg = load()
    engine = AudioEngine(cfg)
    app = App(cfg, engine, StubMidi(), on_close=None)
    app.update()
    print("OK  окно собрано")

    # взаимодействия, которые раньше падали
    app._toggle_drum_panel()
    app.update()
    app._toggle_drum_panel()
    app.update()
    app._on_drum_check()
    app._apply_kb_mode()
    app._fill_instr_list()
    app._demo()
    app._apply_instr_gain()
    app.update()
    print("OK  драм-панель/инструменты")

    # ползунки: var.set тоже зовёт callback
    app.thr_var.set(0.2)
    app.metro_gain_var.set(0.7)
    app.drum_gain_var.set(0.9)
    app.instr_gain_var.set(0.5)
    app.mon_var.set(0.8)
    app.mst_var.set(1.0)
    app.inp_gain_var.set(1.5)
    app.update()
    print("OK  ползунки")

    # смена размера окна → автоширина дорожек
    app.geometry("1280x860")
    app.update()
    app.geometry("950x700")
    app.update()
    print("OK  ресайз")

    # несколько циклов опроса (снапшот, волноформы, статусы)
    for _ in range(4):
        app._update_from_snapshot()
        app.update()
    print("OK  снапшот/индикаторы")

    app.destroy()
    print("GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
