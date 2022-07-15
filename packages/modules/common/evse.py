#!/usr/bin/env python3
import logging
from enum import IntEnum
from typing import Callable, Tuple

from modules.common import modbus
from modules.common.fault_state import FaultState
from modules.common.modbus import ModbusDataType

log = logging.getLogger(__name__)


def exceptions_to_fault_state(delegate: Callable):
    def wrapper(*args, **kwargs):
        try:
            return delegate(*args, **kwargs)
        except Exception as e:
            if isinstance(e, FaultState):
                raise
            else:
                raise FaultState.error(__name__ + " " + str(type(e)) + " " + str(e)) from e

    return wrapper


class EvseState(IntEnum):
    READY = 1
    EV_PRESENT = 2
    CHARGING = 3
    CHARGING_WITH_VENTILATION = 4
    FAILURE = 5


class Evse:
    def __init__(self, modbus_id: int, client: modbus.ModbusSerialClient_) -> None:
        self.client = client
        self.id = modbus_id

    @exceptions_to_fault_state
    def get_plug_charge_state(self) -> Tuple[bool, bool, float]:
        set_current, _, state = self.client.read_holding_registers(1000, [ModbusDataType.UINT_16]*3, unit=self.id)
        log.debug("Gesetzte Stromstärke EVSE: "+str(set_current) +
                  ", Status: "+str(state)+", Modbus-ID: "+str(self.id))
        if state == EvseState.READY:
            plug_state = False
            charge_state = False
        elif(state == EvseState.EV_PRESENT or
                ((state == EvseState.CHARGING or state == EvseState.CHARGING_WITH_VENTILATION) and
                 set_current == 0)):
            plug_state = True
            charge_state = False
        elif (state == EvseState.CHARGING or state == EvseState.CHARGING_WITH_VENTILATION) and set_current > 0:
            plug_state = True
            charge_state = True
        else:
            raise FaultState.error("Unbekannter Zustand der EVSE: State " +
                                   str(state)+", Sollstromstärke: "+str(set_current))
        return plug_state, charge_state, set_current

    @exceptions_to_fault_state
    def get_firmware_version(self) -> None:
        log.debug(
            "FW-Version: "+str(self.client.read_holding_registers(1005, ModbusDataType.UINT_16, unit=self.id)))

    @exceptions_to_fault_state
    def set_current(self, current: int) -> None:
        self.client.delegate.write_registers(1000, current, unit=self.id)
