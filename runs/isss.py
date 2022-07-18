#!/usr/bin/python
import logging
import os
import re
import threading
import time
from typing import Dict, List, Optional
import RPi.GPIO as GPIO
# uncomment for debugging
# import sys
# sys.path.insert(0, "/var/www/html/openWB/packages")
from modules.internal_openwb import chargepoint_module, socket
from modules.common.store import ramdisk_read
from modules.common.store._util import get_rounding_function_by_digits
from modules.common.fault_state import FaultState
from modules.common.component_state import ChargepointState
from helpermodules.pub import pub_single
from helpermodules import compatibility
from modules.common.modbus import ModbusSerialClient_
from modules.internal_openwb.chargepoint_module import InternalOpenWB

basePath = "/var/www/html/openWB"
ramdiskPath = basePath + "/ramdisk"
logFilename = ramdiskPath + "/isss.log"
MAP_LOG_LEVEL = [logging.ERROR, logging.WARNING, logging.DEBUG]


logging.basicConfig(filename=ramdiskPath+'/isss.log',
                    format='%(asctime)s - {%(name)s:%(lineno)s} - %(levelname)s - %(message)s',
                    level=MAP_LOG_LEVEL[int(os.environ.get('debug'))])
log = logging.getLogger()
log.error("Loglevel: "+str(int(os.environ.get('debug'))))

pymodbus_logger = logging.getLogger("pymodbus")
pymodbus_logger.setLevel(logging.WARNING)


# handling of all logging statements


class UpdateValues:
    MAP_KEY_TO_OLD_TOPIC = {
        "imported": "kWhCounter",
        "exported": None,
        "power": "W",
        "voltages": ["VPhase1", "VPhase2", "VPhase3"],
        "currents": ["APhase1", "APhase2", "APhase3"],
        "power_factors": None,
        "phases_in_use": "countPhasesInUse",
        "charge_state": "boolChargeStat",
        "plug_state": "boolPlugStat",
        "rfid": "LastScannedRfidTag",
    }

    def __init__(self, duo_num: int) -> None:
        self.cp_num = str(Isss.get_cp_num(duo_num))
        self.parent_wb = Isss.get_parent_wb()
        self.old_counter_state = None

    def update_values(self, counter_state: ChargepointState) -> None:
        if self.old_counter_state:
            # iterate over counterstate
            vars_old_counter_state = vars(self.old_counter_state)
            for key, value in vars(counter_state).items():
                if value != vars_old_counter_state[key]:
                    # pub to 1.9
                    topic = self.MAP_KEY_TO_OLD_TOPIC[key]
                    if topic is not None:
                        if isinstance(topic, List):
                            for i in range(0, 3):
                                self.pub_values_to_1_9(topic[i], value[i])
                        else:
                            self.pub_values_to_1_9(self.MAP_KEY_TO_OLD_TOPIC[key], value)
                    # pub to 2.0
                    self.pub_values_to_2(key, value)
            self.old_counter_state = counter_state
        else:
            # Bei Neustart alles publishen
            for key, value in vars(counter_state).items():
                # pub to 1.9
                topic = self.MAP_KEY_TO_OLD_TOPIC[key]
                if topic is not None:
                    if isinstance(topic, List):
                        [self.pub_values_to_1_9(topic[i], value[i]) for i in range(0, 3)]
                    else:
                        self.pub_values_to_1_9(self.MAP_KEY_TO_OLD_TOPIC[key], value)
                # pub to 2.0
                self.pub_values_to_2(key, value)

    def pub_values_to_1_9(self, topic: str, value) -> None:
        pub_single("openWB/lp/1/"+topic, payload=str(value), no_json=True)
        pub_single("openWB/lp/"+self.cp_num+"/"+topic, payload=str(value), hostname=self.parent_wb, no_json=True)

    def pub_values_to_2(self, topic: str, value) -> None:
        rounding = get_rounding_function_by_digits(2)
        # fix rfid default value
        if topic == "rfid" and value == "0":
            value = None
        if isinstance(value, (str, bool, type(None))):
            pub_single("openWB/set/chargepoint/" + self.cp_num+"/get/"+topic, payload=value, hostname=self.parent_wb)
        else:
            if isinstance(value, list):
                pub_single("openWB/set/chargepoint/" + self.cp_num+"/get/"+topic,
                           payload=[rounding(v) for v in value], hostname=self.parent_wb)
            else:
                pub_single("openWB/set/chargepoint/" + self.cp_num+"/get/"+topic,
                           payload=rounding(value), hostname=self.parent_wb)


class UpdateState:
    def __init__(self, cp_module: chargepoint_module.ChargepointModule) -> None:
        self.old_phases_to_use = 3
        self.old_set_current = 0
        self.phase_switch_thread = None  # type: Optional[threading.Thread]
        self.cp_interruption_thread = None  # type: Optional[threading.Thread]
        self.actor_cooldown_thread = None  # type: Optional[threading.Thread]
        self.cp_module = cp_module

    def update_state(self) -> None:
        if self.cp_module.config.id == 1:
            suffix = ""
        else:
            suffix = "s1"
        try:
            set_current = int(float(ramdisk_read("llsoll"+suffix)))
        except (FileNotFoundError, ValueError):
            set_current = 0
        try:
            heartbeat = int(ramdisk_read("heartbeat"))
        except (FileNotFoundError, ValueError):
            heartbeat = 0
        try:
            cp_interruption_duration = int(float(ramdisk_read("extcpulp1")))
        except (FileNotFoundError, ValueError):
            cp_interruption_duration = 3
        try:
            phases_to_use = int(float(ramdisk_read("u1p3pstat")))
        except (FileNotFoundError, ValueError):
            phases_to_use = 3
        log.debug("Values from ramdisk: set_current"+str(set_current) +
                  " heartbeat "+str(heartbeat) + " phases_to_use "+str(phases_to_use) + "cp_interruption_duration" + str(cp_interruption_duration))

        if heartbeat > 80:
            set_current = 0
            log.error("Heartbeat Fehler seit " + str(heartbeat) + "Sekunden keine Verbindung, Stoppe Ladung.")

        if self.actor_cooldown_thread:
            if self.actor_cooldown_thread.is_alive():
                return
        if isinstance(self.cp_module, socket.Socket):
            if self.cp_module.cooldown_tracker.is_cooldown_necessary():
                self.__thread_actor_cooldown()

        if self.phase_switch_thread:
            if self.phase_switch_thread.is_alive():
                log.debug("Thread zur Phasenumschaltung an LP"+str(self.cp_module.config.id) +
                          " noch aktiv. Es muss erst gewartet werden, bis die Phasenumschaltung abgeschlossen ist.")
                return
        if self.cp_interruption_thread:
            if self.cp_interruption_thread.is_alive():
                log.debug("Thread zur CP-Unterbrechung an LP"+str(self.cp_module.config.id) +
                          " noch aktiv. Es muss erst gewartet werden, bis die CP-Unterbrechung abgeschlossen ist.")
                return
        self.cp_module.set_current(set_current)
        if self.old_phases_to_use != phases_to_use:
            log.debug("Switch Phases from "+str(self.old_phases_to_use) + " to " + str(phases_to_use))
            self.__thread_phase_switch(phases_to_use)
            self.old_phases_to_use = phases_to_use

        if cp_interruption_duration > 0:
            self.__thread_cp_interruption(cp_interruption_duration)

    def __thread_phase_switch(self, phases_to_use: int) -> None:
        self.phase_switch_thread = threading.Thread(
            target=self.cp_module.perform_phase_switch, args=(phases_to_use, 5))
        self.phase_switch_thread.start()
        log.debug("Thread zur Phasenumschaltung an LP"+str(self.cp_module.config.id)+" gestartet.")

    def __thread_cp_interruption(self, duration: int) -> None:
        self.cp_interruption_thread = threading.Thread(
            target=self.cp_module.perform_cp_interruption, args=(duration,))
        self.cp_interruption_thread.start()
        log.debug("Thread zur CP-Unterbrechung an LP"+str(self.cp_module.config.id)+" gestartet.")
        compatibility.write_to_ramdisk("extcpulp1", "0")

    def __thread_actor_cooldown(self) -> None:
        self.actor_cooldown_thread = threading.Thread(target=self.cp_module.perform_actor_cooldown, args=())
        self.actor_cooldown_thread.start()
        log.debug("Thread zur Aktoren-Abkühlung an LP"+str(self.cp_module.config.id)+" gestartet.")


class Isss:
    def __init__(self) -> None:
        log.debug("Init isss")
        self.serial_client = ModbusSerialClient_(self.detect_modbus_usb_port())
        self.cp1 = IsssChargepoint(self.serial_client, 1)
        try:
            if int(ramdisk_read("issslp2act")) == 1:
                self.cp2 = IsssChargepoint(self.serial_client, 2)
            else:
                self.cp2 = None
        except (FileNotFoundError, ValueError) as e:
            log.error("Error reading issslp2act! Guessing cp2 is not configured.")
            self.cp2 = None
        self.init_gpio()

    def init_gpio(self) -> None:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(37, GPIO.OUT)
        GPIO.setup(13, GPIO.OUT)
        GPIO.setup(22, GPIO.OUT)
        GPIO.setup(29, GPIO.OUT)
        GPIO.setup(11, GPIO.OUT)
        GPIO.setup(15, GPIO.OUT)
        # GPIOs for socket
        GPIO.setup(23, GPIO.OUT)
        GPIO.setup(26, GPIO.OUT)
        GPIO.setup(19, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def loop(self) -> None:
        # connect with USB/modbus device
        with self.serial_client:
            # start our control loop
            while True:
                log.setLevel(MAP_LOG_LEVEL[int(os.environ.get('debug'))])
                log.debug("***Start***")
                self.cp1.update()
                if self.cp2:
                    self.cp2.update()
                time.sleep(1.1)

    def detect_modbus_usb_port(self) -> str:
        """guess USB/modbus device name"""
        try:
            with open("/dev/ttyUSB0"):
                return "/dev/ttyUSB0"
        except FileNotFoundError:
            return "/dev/serial0"

    @staticmethod
    def get_cp_num(duo_num) -> int:
        try:
            if duo_num == 1:
                return int(re.sub(r'\D', '', ramdisk_read("parentCPlp1")))
            else:
                return int(re.sub(r'\D', '', ramdisk_read("parentCPlp2")))
        except Exception:
            FaultState.warning("Es konnte keine Ladepunkt-Nummer ermittelt werden. Auf Default-Wert 0 gesetzt.")
            return 0

    @staticmethod
    def get_parent_wb() -> str:
        # check for parent openWB
        try:
            return ramdisk_read("parentWB").replace('\\n', '').replace('\"', '')
        except Exception:
            FaultState.warning("Für den Betrieb im Nur-Ladepunkt-Modus ist zwingend eine Master-openWB erforderlich.")
            return ""


class IsssChargepoint:
    def __init__(self, serial_client, duo_num) -> None:
        self.duo_num = duo_num
        if duo_num == 1:
            try:
                with open('/home/pi/ppbuchse', 'r') as f:
                    max_current = int(f.read())
                self.module = socket.Socket(max_current, InternalOpenWB(1, serial_client))
            except (FileNotFoundError, ValueError):
                self.module = chargepoint_module.ChargepointModule(InternalOpenWB(1, serial_client))
        else:
            self.module = chargepoint_module.ChargepointModule(InternalOpenWB(2, serial_client))
        self.update_values = UpdateValues(duo_num)
        self.update_state = UpdateState(self.module)
        self.old_plug_state = False

    def update(self):
        def __fix_plug_state(thread: Optional[threading.Thread]):
            """Während des Threads wird die CP-Leitung unterbrochen, das EV soll aber als angesteckt betrachtet
            werden. In 1.9 war das kein Problem, da währendessen keine Werte von der EVSE abgefragt wurden."""
            if thread:
                if thread.is_alive():
                    self.new_plug_state = self.old_plug_state
        try:
            if self.duo_num == 2:
                time.sleep(0.1)
            state, _ = self.module.get_values()
            self.new_plug_state = state.plug_state
            __fix_plug_state(self.update_state.cp_interruption_thread)
            if self.new_plug_state == state.plug_state:
                __fix_plug_state(self.update_state.phase_switch_thread)
            if self.new_plug_state != state.plug_state:
                state.plug_state = self.new_plug_state
            else:
                self.old_plug_state = state.plug_state
            log.debug("Published plug state "+str(state.plug_state))
            self.update_values.update_values(state)
            self.update_state.update_state()
        except Exception:
            log.exception("Fehler bei Ladepunkt "+str(self.duo_num))


Isss().loop()
