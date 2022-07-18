from enum import IntEnum
import logging
import RPi.GPIO as GPIO
import time
from typing import Dict, Tuple

from modules.common.component_context import SingleComponentUpdateContext
from modules.common.component_state import ChargepointState
from modules.internal_openwb.chargepoint_module import ChargepointModule, InternalOpenWB

log = logging.getLogger(__name__)


def get_default_config() -> Dict:
    return {"id": 0,
            "connection_module": {
                "type": "internal_openwb",
                "configuration": {}
            },
            "power_module": {}}


class ActorState(IntEnum):
    CLOSED = 0,
    OPENED = 1


class Socket(ChargepointModule):
    def __init__(self, max_current: int, config: InternalOpenWB) -> None:
        self.max_current = max_current
        self.cooldown_tracker = CooldownTracker()
        super().__init__(config)

    def set_current(self, current: float) -> None:
        with SingleComponentUpdateContext(self.component_info):
            try:
                actor = ActorState(GPIO.input(19))
            except Exception:
                log.error("Error getting actorstat! Using default '0'.")
                actor = ActorState.OPENED

            if actor == ActorState.CLOSED:
                if current == self.set_current_evse or self.chargepoint_state.plug_state is False:
                    return
            else:
                current = 0
            super().set_current(min(current, self.max_current))

    def get_values(self) -> Tuple[ChargepointState, float]:
        try:
            actor = ActorState(GPIO.input(19))
        except Exception:
            log.error("Error getting actorstat! Using default '0'.")
            actor = ActorState.OPENED
        log.debug("Actor: "+str(actor))
        self.chargepoint_state, self.set_current_evse = super().get_values()
        if self.chargepoint_state.plug_state is True and actor == ActorState.OPENED:
            self.__close_actor()
        if self.chargepoint_state.plug_state is False and actor == ActorState.CLOSED:
            self.__open_actor()
        return self.chargepoint_state, self.set_current_evse

    def __open_actor(self):
        self.__set_actor(open=True)

    def __close_actor(self):
        self.__set_actor(open=False)

    def __set_actor(self, open: bool):
        GPIO.output(23, GPIO.LOW if open else GPIO.HIGH)
        GPIO.output(26, GPIO.HIGH)
        time.sleep(2 if open else 3)
        GPIO.output(26, GPIO.LOW)
        log.debug("Actor opened" if open else "Actor closed")
        self.cooldown_tracker.move()

    def perform_actor_cooldown(self):
        time.sleep(300)


class CooldownTracker:
    def __init__(self, max_movements: int = 10, max_seconds: int = 300):
        self.movement_times = [0.0]*max_movements
        self.max_seconds = max_seconds
        self.counter = 0

    def move(self) -> None:
        self.movement_times[self.counter] = time.time()
        self.counter = (self.counter + 1) % len(self.movement_times)

    def is_cooldown_necessary(self) -> bool:
        return time.time() - self.movement_times[(self.counter + 1) % len(self.movement_times)] < self.max_seconds
