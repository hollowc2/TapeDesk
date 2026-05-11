from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import wavfile

SAMPLE_RATE = 44100
RANDOM_SEED = 42

CLICK_VARIATIONS = [
    {"filename": "geiger_click1.wav", "duration": 0.002, "frequency": 1000, "sine_amp": 0.2, "noise_amp": 0.3, "decay": 16, "double": False},
    {"filename": "geiger_click2.wav", "duration": 0.004, "frequency": 3000, "sine_amp": 0.4, "noise_amp": 0.2, "decay": 12, "double": False},
    {"filename": "geiger_click3.wav", "duration": 0.003, "frequency": 0, "sine_amp": 0.0, "noise_amp": 0.5, "decay": 10, "double": False},
    {"filename": "geiger_click4.wav", "duration": 0.008, "frequency": 1200, "sine_amp": 0.3, "noise_amp": 0.3, "decay": 6, "double": False},
    {"filename": "geiger_click5.wav", "duration": 0.002, "frequency": 1200, "sine_amp": 0.3, "noise_amp": 0.3, "decay": 12, "double": True},
    {"filename": "geiger_click6.wav", "duration": 0.005, "frequency": 700, "sine_amp": 0.15, "noise_amp": 0.2, "decay": 8, "double": False},
    {"filename": "geiger_click7.wav", "duration": 0.004, "frequency": 4000, "sine_amp": 0.25, "noise_amp": 0.18, "decay": 10, "double": False},
    {"filename": "geiger_click8.wav", "duration": 0.006, "frequency": 1800, "sine_amp": 0.2, "noise_amp": 0.4, "decay": 4, "double": False},
    {"filename": "geiger_click9.wav", "duration": 0.002, "frequency": 2500, "sine_amp": 0.6, "noise_amp": 0.0, "decay": 20, "double": False},
    {"filename": "geiger_click10.wav", "duration": 0.004, "frequency": 3500, "sine_amp": 0.2, "noise_amp": 0.4, "decay": 10, "double": True},
]


def generate_click_sound(params: dict[str, Any], sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    t = np.linspace(0, params["duration"], int(sample_rate * params["duration"]), False)
    sine_wave = (
        params["sine_amp"] * np.sin(2 * np.pi * params["frequency"] * t)
        if params["frequency"] > 0
        else np.zeros_like(t)
    )
    noise = params["noise_amp"] * np.random.normal(0, 0.2, t.size)
    click = (sine_wave + noise) * np.exp(-params["decay"] * t / params["duration"])

    max_amp = np.max(np.abs(click))
    if max_amp > 0:
        click = click / max_amp * 0.9

    click = (click * 32767).astype(np.int16)

    if params["double"]:
        silence = np.zeros(int(0.001 * sample_rate), dtype=np.int16)
        click = np.concatenate([click, silence, click])

    return click


def main() -> None:
    np.random.seed(RANDOM_SEED)
    sounds_dir = Path(__file__).resolve().parent.parent / "data" / "sounds"
    sounds_dir.mkdir(parents=True, exist_ok=True)

    for params in CLICK_VARIATIONS:
        click = generate_click_sound(params)
        output_path = sounds_dir / params["filename"]
        wavfile.write(str(output_path), SAMPLE_RATE, click)
        print(f"Generated: {output_path}")


if __name__ == "__main__":
    main()
