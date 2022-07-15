#!/usr/bin/env python3

from modules.common import modbus
from typing import Callable, List, Tuple
from modules.common.fault_state import FaultState
from modules.common.modbus import ModbusDataType


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


class Lovato:
    def __init__(self, modbus_id: int, client: modbus.ModbusTcpClient_) -> None:
        self.client = client
        self.id = modbus_id

    @exceptions_to_fault_state
    def get_voltages(self) -> List[float]:
        return [val / 100 for val in self.client.read_input_registers(
            0x0001, [ModbusDataType.INT_32]*3, unit=self.id)]

    @exceptions_to_fault_state
    def get_power(self) -> Tuple[List[float], float]:
        powers = [val / 100 for val in self.client.read_input_registers(
            0x0013, [ModbusDataType.INT_32]*3, unit=self.id
        )]
        power = sum(powers)
        return powers, power

    @exceptions_to_fault_state
    def get_power_factors(self) -> List[float]:
        return [val / 10000 for val in self.client.read_input_registers(
            0x0025, [ModbusDataType.INT_32]*3, unit=self.id)]

    @exceptions_to_fault_state
    def get_frequency(self) -> float:
        frequency = self.client.read_input_registers(0x0031, ModbusDataType.INT_32, unit=self.id) / 100
        if frequency > 100:
            # needed if external measurement clamps connected
            frequency = frequency / 10
        return frequency

    @exceptions_to_fault_state
    def get_currents(self) -> List[float]:
        return [val / 10000 for val in self.client.read_input_registers(
            0x0007, [ModbusDataType.INT_32]*3, unit=self.id)]
