from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "data" / "sounds"
DEFAULT_BUY_SOUND_PATH = SOUNDS_DIR / "geiger_click7.wav"
DEFAULT_SELL_SOUND_PATH = SOUNDS_DIR / "geiger_click4.wav"


class PlayableSound(Protocol):
    def play(self) -> object:
        ...


class TradeClickPlayer:
    def __init__(
        self,
        buy_sound_path: Path = DEFAULT_BUY_SOUND_PATH,
        sell_sound_path: Path = DEFAULT_SELL_SOUND_PATH,
        buy_sound: PlayableSound | None = None,
        sell_sound: PlayableSound | None = None,
    ):
        self.buy_sound_path = buy_sound_path
        self.sell_sound_path = sell_sound_path
        self._buy_sound = buy_sound
        self._sell_sound = sell_sound
        self._loaded = buy_sound is not None or sell_sound is not None

    def play(self, side: str) -> None:
        if not self._loaded:
            self._load()
        sound = self._sell_sound if side == "sell" else self._buy_sound
        if sound is not None:
            sound.play()

    def _load(self) -> None:
        self._loaded = True
        try:
            import pygame
        except ImportError:
            logger.warning("Level 2 audio unavailable: pygame is not installed")
            return

        try:
            pygame.mixer.init()
        except pygame.error as exc:
            logger.warning("Level 2 audio init failed: %s", exc)
            return

        self._buy_sound = self._load_sound(pygame, self.buy_sound_path, "buy")
        self._sell_sound = self._load_sound(pygame, self.sell_sound_path, "sell")

    @staticmethod
    def _load_sound(pygame: object, path: Path, label: str) -> PlayableSound | None:
        if not path.exists():
            logger.warning("Level 2 %s sound not found: %s", label, path)
            return None
        try:
            return pygame.mixer.Sound(str(path))
        except pygame.error as exc:
            logger.warning("Level 2 %s sound load failed: %s", label, exc)
            return None
