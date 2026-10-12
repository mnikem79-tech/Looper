"""
Приём MIDI-сообщений (mido/python-rtmidi) и режим MIDI-learn.

Потокобезопасность: колбэки вызываются из MIDI-потока, поэтому
GUI должен передавать их в свой поток через очередь (см. looper.py).
Ноты инструмента идут напрямую в сэмплер (on_note) — без очереди,
иначе задержка при игре будет заметной.
"""
import threading


class MidiHandler:
    def __init__(self, on_message, on_learn, on_note=None, on_sustain=None):
        """
        on_message(key)          — нажата кнопка (key вида 'note:36' или 'cc:64')
        on_learn(action, key)    — завершено назначение в режиме learn
        on_note(note, vel)       — нота клавиатуры (vel=0 — отпускание)
        on_sustain(down)         — педаль сустейна (CC64)
        """
        self.on_message = on_message
        self.on_learn = on_learn
        self.on_note = on_note
        self.on_sustain = on_sustain
        self._port = None
        self._thread = None
        self._running = False
        self.learn_target = None
        self.port_name = ""

    @staticmethod
    def list_inputs() -> list:
        try:
            import mido
        except Exception:
            return []
        names = []
        for n in mido.get_input_names():
            if n not in names:
                names.append(n)
        return names

    def open(self, name: str) -> None:
        self.close()
        import mido

        self._port = mido.open_input(name)
        self.port_name = name
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
        self._port = None
        self.port_name = ""

    def start_learn(self, action: str) -> None:
        self.learn_target = action

    def cancel_learn(self) -> None:
        self.learn_target = None

    def _run(self) -> None:
        try:
            for msg in self._port:
                if not self._running:
                    break

                t = getattr(msg, "type", "")

                # Педаль сустейна — всегда напрямую (кроме режима learn).
                if t == "control_change" and getattr(msg, "control", -1) == 64:
                    if not self.learn_target and self.on_sustain:
                        self.on_sustain(getattr(msg, "value", 0) >= 64)

                # Ноты клавиатуры — напрямую в сэмплер (вне очереди UI).
                if t in ("note_on", "note_off"):
                    vel = int(getattr(msg, "velocity", 0))
                    is_off = (t == "note_off") or (vel == 0)
                    if not self.learn_target and self.on_note:
                        self.on_note(int(msg.note), 0 if is_off else vel)
                    if is_off:
                        continue
                    key = f"note:{msg.note}"
                elif t == "control_change" and getattr(msg, "value", 0) > 0:
                    key = f"cc:{msg.control}"
                else:
                    continue

                if self.learn_target:
                    action = self.learn_target
                    self.learn_target = None
                    self.on_learn(action, key)
                else:
                    self.on_message(key)
        except Exception:
            # Порт закрыт или отключён — поток завершается.
            pass
