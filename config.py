"""Загрузка и сохранение конфигурации лупера."""
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "looper_config.json"

DEFAULTS = {
    "midi_input": "",
    "audio_input": "",
    "audio_output": "",
    "sample_rate": 48000,
    "blocksize": 256,
    "monitor_gain": 1.0,
    "master_gain": 1.0,
    "dub_gain": 1.0,
    "max_loop_sec": 180,
    "slots": 4,
    "mapping": {},
    "wasapi_exclusive": True,
    "bpm": 120,
    "beats_per_bar": 4,
    "bars_per_block": 4,
    "num_blocks": 2,
    "autostart": False,
    "threshold": 0.05,
    "metronome_on": False,
    "metro_sound": "wood",
    "metro_gain": 0.5,
    "drum_on": False,
    "drum_style": "Поп",
    "drum_gain": 0.7,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for key in DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    except Exception:
        pass
    return cfg


def save(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass
