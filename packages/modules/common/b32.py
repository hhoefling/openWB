#!/usr/bin/env python3
from typing import Callable, List, Tuple

from modules.common import modbus
from modules.common.fault_state import FaultState
from modules.common.modbus import ModbusDataType


def exceptions_to_fault_state(delegate: Callable):
    def wrapper(*args, **kwargs):
        try:
            delegate(args, kwargs)
        except Exception as e:
            if isinstance(e, FaultState):
                raise
            else:
                raise FaultState.error(__name__ + " " + str(type(e)) + " " + str(e)) from e

    return wrapper


class B32:
    def __init__(self, modbus_id: int, client: modbus.ModbusTcpClient_) -> None:
        self.client = client
        self.id = modbus_id

    @exceptions_to_fault_state
    def get_imported(self) -> float:
        return self.client.read_holding_registers(0x5000, ModbusDataType.UINT_64, unit=self.id) / 1000

    @exceptions_to_fault_state
    def get_frequency(self) -> float:
        return self.client.read_holding_registers(0x5B2C, ModbusDataType.INT_16, unit=self.id) / 100

    @exceptions_to_fault_state
    def get_currents(self) -> List[float]:
        return [val / 10 for val in self.client.read_holding_registers(
            0x5B0C, [ModbusDataType.UINT_32]*3, unit=self.id)]

    @exceptions_to_fault_state
    def get_power(self) -> Tuple[List[float], float]:
        power = self.client.read_input_registers(0x0C, ModbusDataType.INT_32, unit=self.id) / 100
        return [0]*3, power

    @exceptions_to_fault_state
    def get_voltages(self) -> List[float]:
        return [val / 10 for val in self.client.read_holding_registers(
            0x5B00, [ModbusDataType.UINT_32]*3, unit=self.id)]
