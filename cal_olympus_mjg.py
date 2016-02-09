#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import math
import time
import threading
import Queue
from pysummit import comport
from pysummit import decoders as dec
from pysummit.devices import TxAPI
from pysummit.devices import RxAPI
import rfmeter
from rfmeter.agilent import E4418B
import logging

FLASH_MAP_MFG_DATA_START_ADDR = 0xC0000
SHERWOOD_XD_MOD_ID = 0xFD
SHERWOOD_XC_MOD_ID = 0x0F
GLENWOOD_MOD_ID = 0x06
ATHENA_UFL_MOD_ID = 0x0D
ATHENA_4X_MOD_ID = 0x01
ATHENA_4XC_MOD_ID = 0x0C
ATHENA_4XD_MOD_ID = 0xCD

olympus_modules = (
    SHERWOOD_XD_MOD_ID,
    SHERWOOD_XC_MOD_ID,
    GLENWOOD_MOD_ID,
    )

apollo_modules = (
    ATHENA_UFL_MOD_ID,
    ATHENA_4X_MOD_ID,
    ATHENA_4XC_MOD_ID,
    ATHENA_4XD_MOD_ID,
    )

def getOlympusDutyFactor(fw_rev):
    # Duty factor for Olympus (@ 18 Mb/s) changed from 34% to 45% with FW199
    return (((fw_rev >> 5) < 199) and 0.34) or 0.45

def getApolloDutyFactor(fw_rev):
    # Duty factor for Apollo (@ 6 Mb/s) changed from 55% to 70% with FW197
    return (((fw_rev >> 5) < 197) and 0.55) or 0.70

def getSummitDutyFactor(module_id, fw_rev):
    if module_id in olympus_modules: # Master/Olympus
        return getOlympusDutyFactor(fw_rev)
    elif module_id in apollo_modules: # Slave/Apollo
        return getApolloDutyFactor(fw_rev)
    else:
        return 1.0 # if module type unknown, default to 100%

cal_running = threading.Event()
pm_ready = threading.Event()
measure = threading.Event()

measurement_q = Queue.Queue()

def avg_measurements(q):
    """Takes a queue of numbers and returns the average of all the numbers not
    including the first and the last"""
    measurements = []
    for i in range(measurement_q.qsize()):
        measurements.append(q.get_nowait())

    measurements = map(float, measurements)
    if(len(measurements) > 2):
        avg = sum(measurements[1:-1])/float(len(measurements[1:-1]))
    elif(len(measurements) > 1):
        avg = float(measurements[0])
    else:
        avg = 0

    return avg

class CalOlympusThread(threading.Thread):
    def __init__(self, dev):
        super(CalOlympusThread, self).__init__()
        self.daemon = True
        self.dev = dev
        self.logger = logging.getLogger('CalOlympusThread')

    def run(self):
        print("Starting Cal Apollo Thread...")
        if(pm_ready.is_set()): # Wait for the PM to be ready.
            cal_running.set()
            (radio_cal_status, cal_sm_state) = self.dev.invoke_radio_cal_state(rcss["RADIOCALSTATE_BEGIN"], None)
            if(radio_cal_status == rcs["RADIOCAL_OK"]):
                while(True):
                    self.logger.debug("waiting for power meter...")
                    pm_ready.wait()
                    if(cal_sm_state == rcss["RADIOCALSTATE_F0_B5"]):
                        measure.set()
                        (radio_cal_status, cal_sm_state) = self.dev.invoke_radio_cal_state(cal_sm_state, None)
                        measure.clear()
                    else:
                        measurement = avg_measurements(measurement_q)

                        print "  %s: %f" % (rcss[cal_sm_state], measurement)
                        measure.set()
                        (radio_cal_status, cal_sm_state) = self.dev.invoke_radio_cal_state(cal_sm_state, measurement)
                        measure.clear()

                    if((cal_sm_state == rcss["RADIOCALSTATE_IDLE"]) | (radio_cal_status != rcs["RADIOCAL_OK"])):
                        break

            cal_running.clear()
            print("Session Status:")
            print("---------------")
            print("%d: (%s)" % (radio_cal_status, rcs[radio_cal_status]))
            print("###############")

            if(radio_cal_status != rcs['RADIOCAL_OK']): # RADIOCAL_OK
                cal_sm_state = rcss['RADIOCALSTATE_FINISHED']
                (radio_cal_status, cal_sm_state) = self.dev.invoke_radio_cal_state(cal_sm_state, measurement)
                print "HARDWARE_IO_SENDING_DATA_FAILED"


class PMThread(threading.Thread):
    """A power meter thread

    The power meter will take continuous measurements as long as the cal_running
    event is set.

    """
    def __init__(self, pm):
        super(PMThread, self).__init__()
        self.daemon = True
        self.pm = pm
        self.logger = logging.getLogger('PMThread')

    def run(self):
        print("Starting Power Meter Thread...")
        pm_ready.set()
        print "cal_running.wait()"
        cal_running.wait()
        print "after cal_running.wait()"
        while(True):
            if(not cal_running.is_set()):
                break
            if(not measure.wait(timeout=5)):
                print "measure.wait() timeout?"

            try:
                pm_ready.clear()
                meas = self.pm.cmd("MEAS?", timeout=10)
                measurement_q.put(float(meas))
            except IOError as info:
                self.logger.error(info)
            finally:
                pm_ready.set()

        self.pm.cmd("INIT:CONT ON")
        pm_ready.clear()


def tx_measure(dev, power_meter):
    rx_thread = CalOlympusThread(dev)
    pm_thread = PMThread(power_meter)

    pm_thread.start()
    rx_thread.start()
    pm_thread.join()

def main(TX, RX, iterations, test_profile, power_controller):

    # Read MFG data from flash
    tx_mfg_data = desc.FLASH_MASTER_MFG_DATA_SECTION()
    status = TX.target.SWM_Diag_GetFlashData(
        FLASH_MAP_MFG_DATA_START_ADDR,
        ctypes.sizeof(desc.FLASH_MASTER_MFG_DATA_SECTION),
        ctypes.byref(tx_mfg_data)
        )

    # Determine if module supports TPM (moduleID is Sherwood XD or Athena 4XD, firmware is 198.x or greater)
    # and get default (cal) power level
    modID = tx_mfg_data.masterMfgData.masterDescriptor.moduleDescriptor.moduleID
    fwver = tx_mfg_data.masterMfgData.masterDescriptor.moduleDescriptor.firmwareVersion
    defpwr = tx_mfg_data.radioCalData.defaultPwr

# Instantiate a Power Meter and give it an open COM port
    COM = rfmeter.comport.ComPort('/dev/ttyUSB0')
    COM.connect()
    PM = E4418B(COM)

### Beginning of Dave Schilling's new PM code ###

# File operations to load in the power meter offset
    pm_offset_file = open('pm_offset.dat', 'r')
    pm_offset = float(pm_offset_file.read(6))
    pm_offset_file.close()

    # Get duty factor for power meter correction
    duty_factor = getSummitDutyFactor(modID, fwver)

# Set up Power Meter as we like it
    print ("========================================================")
    print ("Power Meter ============================================")

    PM.meter_reset()
    PM.clear_errors()
    PM.cmd("SYST:PRES")
    PM.cmd("SYST:REM")

    pm_sensor = PM.cmd("SERV:SENS1:TYPE?")
    print "Sensor identifies as:", pm_sensor
    #  "E4412A"=4412, "E4413A"=4413, "A"=HP8481A
    if (pm_sensor == "A"):
        PM.cmd("CORR:CSET1:SEL 'HP8481A'")
        PM.cmd("CORR:CSET1:STAT ON")
        print ("========================================================")
        print (" Using Sensor Cal Table", PM.cmd("CORR:CSET1:SEL?"))
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT", timeout=None, do_error_check=False)
        #PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT")
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    elif(pm_sensor == "E4412A" or pm_sensor == "E4413A"):
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT", timeout=None, do_error_check=False)
        #PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT")
        PM.clear_errors()
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    else:
        PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT", timeout=None, do_error_check=False)
        #PM.cmd("CORR:DCYC " + str(duty_factor * 100) + "PCT")
        PM.cmd("CORR:GAIN2 " + str(pm_offset))

    PM.cmd("FREQ " + "5.500GHZ")

    print ("========================================================")
    print (" Duty Factor = " + str(duty_factor * 100) + "%")
    print (" Correction  = " + str( round( (-10.0) * math.log10(duty_factor), 2) ) + "dB")
    print ("========================================================")
    print (" Applying Offset Data from file <pm_offset.dat>")
    print (" Offset = " + str(pm_offset) + "dB")
    print ("========================================================")
    print ("")

### End of Dave Schilling's new PM code ###

# Setup the RX device to use a single antenna
#    RX[my_mac].wr(0x408840, 0)
#    RX[my_mac].wr(0x406004, 0)
#    RX[my_mac].wr(0x401018, 0xb3) # Antenna
#    TX.wr(0x401004, 0x0d) # 6Mb/s
    TX.wr(0x406004, 0x00) # IRQ enable reg
    TX.wr(0x408840, 0x00) # CCA level reg
    TX.wr(0x401004, 0x07) # 18Mb/s

    (status, CCAlevel) = TX.rd(0x408840)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  CCA Level regr 408840: 0x%X" % CCAlevel

    (status, IRQenables) = TX.rd(0x406004)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  IRQ Enable regr 406004: 0x%X" % IRQenables

    (status, DataRate) = TX.rd(0x401004)
    if(status != 0x01):
        print dec.decode_error_status(status)
    print "  DataRate regr 401004: 0x%X" % DataRate

# Disabling power compensation
    (status, null) = TX.set_power_comp_enable(0)

# Disable DFS engine
    (status, null) = TX.dfs_override(5)

#    for ch in range(8,15):
#        RX.set_radio_channel(0, ch)

# Get the temperature
#        (status, temp) = RX.temperature()

# Transmit and take power measurements
    tx_measure(dev=TX, power_meter=PM)
#    print "(%d°C) %d: %r" % (temp, ch, data)

# Get the PD out value
#        (status, pdout) = RX.get_pdout(9000, 32)
#        print "  pdout: 0x%X" % pdout

# Reenable DFS engine
    (status, null) = TX.dfs_override(0)

# Reenable power compensation
    (status, null) = TX.set_power_comp_enable(1)


class Enumish(object):
    def __init__(self, data):
        assert(type(data) == type([]))
        self.data = data

    def __str__(self):
        return str(self.data)

    def __getitem__(self, index):
        if(type(index) == type("")):
            if(index in self.data):
                return self.data.index(index)
            else:
                return None
        elif(type(index) == type(7)):
            if(index < len(self.data)):
                return self.data[index]
            else:
                return None


rcs = Enumish([
        "RADIOCAL_OK",
	    "RADIOCAL_INVALID_POINTER",
	    "RADIOCAL_INVALID_STATE_POINTER",
	    "RADIOCAL_INVALID_STATE_TRANSITION",
	    "RADIOCAL_FAILED_TO_READ_MFG_SECTION_DATA",
	    "RADIOCAL_FAILED_TO_ERASE_MFG_SECTION_DATA",
	    "RADIOCAL_FAILED_TO_WRITE_MFG_SECTION_DATA",
	    "RADIOCAL_FAILED_TO_INITIALIZE_STATIC_TX_PARAMETERS",
	    "RADIOCAL_FAILED_TO_ENABLE_POWER_COMPENSATION",
	    "RADIOCAL_FAILED_TO_DISABLE_POWER_COMPENSATION",
	    "RADIOCAL_INVALID_MEASUREMENT_POINTER",
	    "RADIOCAL_FAILED_TO_REGISTER_TX_PARAMETERS",
	    "RADIOCAL_FAILED_TO_SET_CAL_POINT",
	    "RADIOCAL_FAILED_TO_RETRIEVE_TEMPERATURE",
	    "RADIOCAL_INVALID_CAL_PARAMETERS",
	    "RADIOCAL_INVALID_CAL_POINT",
	    "RADIOCAL_INVALID_CAL_MEASUREMENT",
	    "RADIOCAL_INVALID_STATE_INFO_STATE",
	    "RADIOCAL_INVALID_STATE_FOR_TXGC_UPDATE",
	    "RADIOCAL_SLOPE_INTERCEPT_DIVIDE_BY_ZERO_ERROR",
	    "RADIOCAL_INVALID_CORRECTION_DATA",
	    "RADIOCAL_FAILED_TO_READ_REGISTER",
	    "RADIOCAL_FAILED_TO_WRITE_REGISTER",
	    "RADIOCAL_FAILED_WHILE_UPDATING_RADIO_CAL_BLOCK",
	    "RADIOCAL_UNDEFINED_FAILURE"
    ])

rcss = Enumish([
        "RADIOCALSTATE_IDLE", "RADIOCALSTATE_BEGIN",

            # Successive approximation @ RF Channel index 0 to determine
            # nominal TXGC value
        "RADIOCALSTATE_F0_B5", "RADIOCALSTATE_F0_B4", "RADIOCALSTATE_F0_B3",
        "RADIOCALSTATE_F0_B2", "RADIOCALSTATE_F0_B1", "RADIOCALSTATE_F0_B0",
            # 3-Point characterization performed at each RF Channel index in
            # the range 0 to 6
        "RADIOCALSTATE_F0_P0", "RADIOCALSTATE_F0_P1", "RADIOCALSTATE_F0_P2",
        "RADIOCALSTATE_F1_P0", "RADIOCALSTATE_F1_P1", "RADIOCALSTATE_F1_P2",
        "RADIOCALSTATE_F2_P0", "RADIOCALSTATE_F2_P1", "RADIOCALSTATE_F2_P2",
        "RADIOCALSTATE_F3_P0", "RADIOCALSTATE_F3_P1", "RADIOCALSTATE_F3_P2",
        "RADIOCALSTATE_F4_P0", "RADIOCALSTATE_F4_P1", "RADIOCALSTATE_F4_P2",
        "RADIOCALSTATE_F5_P0", "RADIOCALSTATE_F5_P1", "RADIOCALSTATE_F5_P2",
        "RADIOCALSTATE_F6_P0", "RADIOCALSTATE_F6_P1", "RADIOCALSTATE_F6_P2",
            # Successive approximation @ RF Channel index 7 to determine
            # nominal TXGC value
        "RADIOCALSTATE_F7_B5", "RADIOCALSTATE_F7_B4", "RADIOCALSTATE_F7_B3",
        "RADIOCALSTATE_F7_B2", "RADIOCALSTATE_F7_B1", "RADIOCALSTATE_F7_B0",
            # 3-Point characterization performed at each RF Channel index in
            # the range 7 to 18
        "RADIOCALSTATE_F7_P0", "RADIOCALSTATE_F7_P1", "RADIOCALSTATE_F7_P2",
        #els    "e
            # Successive approximation @ RF Channel index 8 to determine
            # nominal TXGC value
        "RADIOCALSTATE_F8_B5", "RADIOCALSTATE_F8_B4", "RADIOCALSTATE_F8_B3",
        "RADIOCALSTATE_F8_B2", "RADIOCALSTATE_F8_B1", "RADIOCALSTATE_F8_B0",
        #end    "if
            # 3-Point characterization performed at each RF Channel index in
            # the range 8 to 18
        "RADIOCALSTATE_F8_P0", "RADIOCALSTATE_F8_P1", "RADIOCALSTATE_F8_P2",
        "RADIOCALSTATE_F9_P0", "RADIOCALSTATE_F9_P1", "RADIOCALSTATE_F9_P2",
        "RADIOCALSTATE_F10_P0", "RADIOCALSTATE_F10_P1", "RADIOCALSTATE_F10_P2",
        "RADIOCALSTATE_F11_P0", "RADIOCALSTATE_F11_P1", "RADIOCALSTATE_F11_P2",
        "RADIOCALSTATE_F12_P0", "RADIOCALSTATE_F12_P1", "RADIOCALSTATE_F12_P2",
        "RADIOCALSTATE_F13_P0", "RADIOCALSTATE_F13_P1", "RADIOCALSTATE_F13_P2",
        "RADIOCALSTATE_F14_P0", "RADIOCALSTATE_F14_P1", "RADIOCALSTATE_F14_P2",
        "RADIOCALSTATE_F15_P0", "RADIOCALSTATE_F15_P1", "RADIOCALSTATE_F15_P2",
        "RADIOCALSTATE_F16_P0", "RADIOCALSTATE_F16_P1", "RADIOCALSTATE_F16_P2",
        "RADIOCALSTATE_F17_P0", "RADIOCALSTATE_F17_P1", "RADIOCALSTATE_F17_P2",
        "RADIOCALSTATE_F18_P0", "RADIOCALSTATE_F18_P1", "RADIOCALSTATE_F18_P2",
            # Successive approximation @ RF Channel index 19 to determine
            # nominal TXGC value
        "RADIOCALSTATE_F19_B5", "RADIOCALSTATE_F19_B4", "RADIOCALSTATE_F19_B3",
        "RADIOCALSTATE_F19_B2", "RADIOCALSTATE_F19_B1", "RADIOCALSTATE_F19_B0",
            # 3-Point characterization performed at each RF Channel index in
            # the range 19 to 34
        "RADIOCALSTATE_F19_P0", "RADIOCALSTATE_F19_P1", "RADIOCALSTATE_F19_P2",
        "RADIOCALSTATE_F20_P0", "RADIOCALSTATE_F20_P1", "RADIOCALSTATE_F20_P2",
        "RADIOCALSTATE_F21_P0", "RADIOCALSTATE_F21_P1", "RADIOCALSTATE_F21_P2",
        "RADIOCALSTATE_F22_P0", "RADIOCALSTATE_F22_P1", "RADIOCALSTATE_F22_P2",
        "RADIOCALSTATE_F23_P0", "RADIOCALSTATE_F23_P1", "RADIOCALSTATE_F23_P2",
        "RADIOCALSTATE_F24_P0", "RADIOCALSTATE_F24_P1", "RADIOCALSTATE_F24_P2",
        "RADIOCALSTATE_F25_P0", "RADIOCALSTATE_F25_P1", "RADIOCALSTATE_F25_P2",
        "RADIOCALSTATE_F26_P0", "RADIOCALSTATE_F26_P1", "RADIOCALSTATE_F26_P2",
        "RADIOCALSTATE_F27_P0", "RADIOCALSTATE_F27_P1", "RADIOCALSTATE_F27_P2",
        "RADIOCALSTATE_F28_P0", "RADIOCALSTATE_F28_P1", "RADIOCALSTATE_F28_P2",
        "RADIOCALSTATE_F29_P0", "RADIOCALSTATE_F29_P1", "RADIOCALSTATE_F29_P2",
        "RADIOCALSTATE_F30_P0", "RADIOCALSTATE_F30_P1", "RADIOCALSTATE_F30_P2",
        "RADIOCALSTATE_F31_P0", "RADIOCALSTATE_F31_P1", "RADIOCALSTATE_F31_P2",
        "RADIOCALSTATE_F32_P0", "RADIOCALSTATE_F32_P1", "RADIOCALSTATE_F32_P2",
        "RADIOCALSTATE_F33_P0", "RADIOCALSTATE_F33_P1", "RADIOCALSTATE_F33_P2",
        "RADIOCALSTATE_F34_P0", "RADIOCALSTATE_F34_P1", "RADIOCALSTATE_F34_P2",
        "RADIOCALSTATE_FINISHED", "RADIOCALSTATE_MAX" ])

if __name__ == '__main__':
# Set up logging to a file and the console
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)-8s] %(message)s",
        filename="power_reading.log",
        filemode="w")
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    formatter = logging.Formatter("%(name)-8s: %(levelname)-8s %(message)s")
    console.setFormatter(formatter)
    logging.getLogger('').addHandler(console)

# Start the test
    main()
