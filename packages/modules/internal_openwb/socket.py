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
        self.actor_moves = 0
        self.max_current = max_current
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
        GPIO.output(23, GPIO.LOW)
        GPIO.output(26, GPIO.HIGH)
        time.sleep(2)
        GPIO.output(26, GPIO.LOW)
        log.debug("Aktor auf")
        self.actor_moves += 1

    def __close_actor(self):
        GPIO.output(23, GPIO.HIGH)
        GPIO.output(26, GPIO.HIGH)
        time.sleep(3)
        GPIO.output(26, GPIO.LOW)
        log.debug("Aktor zu")
        self.actor_moves += 1

    def cooldown_neccessary(self) -> bool:
        return self.actor_moves >= 10

    def perform_actor_cooldown(self):
        time.sleep(300)
