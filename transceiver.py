#!/usr/bin/python3

# import multiprocessing
import serial
import serial.tools.list_ports
from threading import Thread, Lock, Timer
import socket
import queue
from typing import Tuple, Optional
import json
import logging
import time
import sys
import os


app_active = True
socket_server, socket_port = "127.0.0.1", 12020
tx_queue = queue.Queue()

class TransceiverData:
    def __init__(self):
        self.civ_address: bytes = b''
        self.name: Optional[str] = None
        self.frequency: Optional[int] = None
        self.mode: Optional[str] = None
        self.port_name: Optional[str] = None
        self.port_connected = False
        # Supported transceiver models
        self.transceiver_addresses = {"IC-705": 0xA4, "IC-7300": 0x94, "IC-9700": 0xA2}


def socket_transmit_thread():
    logging.debug("socket_transmit_thread started")

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    while app_active is True:
        try:
            s.connect((socket_server, socket_port))

            logging.debug("Socket connected")
            while True:
                try:
                    data = tx_queue.get_nowait()
                    data_str = json.dumps(data)
                    # logging.debug("Socket Send: %s", data_str)
                    s.sendall(data_str.encode() + b'\xFD')
                except queue.Empty:
                    time.sleep(0.01)
        except Exception as e:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            logging.error(f"socket_transmit_thread error {e}, line {exc_tb.tb_lineno}")
            time.sleep(10)

    s.close()

    logging.debug("socket_transmit_thread done")


def transceiver_read_civ():
    logging.debug("transceiver_update_thread started")

    # Send transceiver status via sockets
    # tx_queue = queue.Queue()
    Thread(target=socket_transmit_thread, name="SocketTX").start()

    # Serial data read loop
    active = True
    while active:
        transceiver = TransceiverData()

        # Find a CI-V transceiver port
        for port, desc, hwid in sorted(serial.tools.list_ports.comports()):
            logging.debug(f"PORT: {port}: {desc} [{hwid}]")
            if (os.name == 'nt' and "CI-V" in desc) or (os.name != 'nt' and "IC-" in desc):
                # COM12: IC-705 Serial Port A (CI-V) (COM12)
                transceiver.port_name = port
                for tr_name in transceiver.transceiver_addresses.keys():
                    if tr_name in desc:
                        transceiver.name = tr_name
                        transceiver.civ_address = bytes([transceiver.transceiver_addresses[tr_name]])
                break

        # If no connection, wait
        if transceiver.port_name is None:
            logging.debug("CI-V port not found, wait 10s...")
            tx_queue.put_nowait({"transceiver": "not found"})
            time.sleep(10.0)
            continue

        logging.debug("Transceiver found: {}, addr={} on port {}".format(transceiver.name, transceiver.civ_address, transceiver.port_name))
        tx_queue.put_nowait({"name": transceiver.name})

        ser = serial.Serial(transceiver.port_name, 19200, timeout=0.1)

        serial_data = b''
        while active:
            # Read incoming commands
            # try:
            #     cmd = queue_in.get_nowait()
            #     if cmd and "command" in cmd and cmd["command"] == "quit":
            #         active = False
            # except queue.Empty:
            #     pass

            # First run, get parameters
            if transceiver.frequency is None and transceiver.mode is None:
                # Get Frequency
                ser.write(b"\xfe\xfe%b\xE0\x03\xfd" % transceiver.civ_address)
            if transceiver.frequency is not None and transceiver.mode is None:
                # Get Mode
                ser.write(b"\xfe\xfe%b\xE0\x04\xfd" % transceiver.civ_address)

            # Update serial data
            try:
                s = ser.read(size=32)
                serial_data += s

                # CI-V Commands: http://www.plicht.de/ekki/civ/civ-p41.html
                # Command body: FE FE E0 addr cmd <body> FD
                while serial_data.find(b'\xFD') != -1:
                    cmd_end = serial_data.find(b'\xFD')
                    # print("S:", serial_data, cmd_end)
                    if cmd_end != -1:
                        # New command found
                        cmd = serial_data[:cmd_end+1]
                        on_civ_command_received(cmd, transceiver)
                        serial_data = serial_data[cmd_end+1:]
                    else:
                        # If command too big, something is wrong
                        if len(serial_data) > 1024:
                            serial_data = b''

                # if len(s) > 0:
                #    print("S:", s, serial_data)

            except Exception as e:
                logging.error("transceiver_update_process exception %s", str(e))

            # time.sleep(0.01)

        ser.close()

    logging.debug("transceiver_update_thread ended")


def on_civ_command_received(cmd_data: bytes, transciever: TransceiverData):
    if cmd_data[0:2] == b'\xFE\xFE':
        cmd_id = cmd_data[4]
        # logging.debug("CI-V CMD: %s", ' '.join('{:02x}'.format(x) for x in serial_data))
        if cmd_id == 0x00 or cmd_id == 0x03:
            # 00h: Frequency changed: b'\xfe\xfe\x00\xa4\x00\x00 1\x07\x00\xfd'
            # 03h: Read operating frequency: b'\xfe\xfe\xe0\xa4\x03\x00\x00\x05\x07\x00\xfd'
            cmd = cmd_data.hex()
            # "fefee0a4038967452301fd" = > "0123456789" = > "123456789"
            freq = int((cmd[18:20] + cmd[16:18] + cmd[14:16] + cmd[12:14] + cmd[10:12]).lstrip("0"))
            if freq != transciever.frequency:
                logging.debug("Transceiver Frequency: %d", freq)
                tx_queue.put_nowait({"frequency": freq})
                # queue_out.put_nowait({})  # Bug, on Windows the last message stuck in queue, needs to send a second one
                transciever.frequency = freq
        elif cmd_id == 0x01 or cmd_id == 0x04:
            # 01h: Mode/filter changed: b'\xfe\xfe\x00\xa4\x04\x00\x01\xfd'
            # 04h: Read operating mode: b'\xfe\xfe\xe0\xa4\x04\x00\x01\xfd'
            mode, filter = cmd_data[5], cmd_data[6]
            # Mode: http://www.plicht.de/ekki/civ/civ-p33.html
            # LSB	        $00
            # USB	        $01
            # AM	        $02
            # CW	        $03
            # RTTY (FSK)	$04	FM on IC-910
            # FM	        $05
            # Wide FM	    $06	IC-706xxx, IC-R8500, IC-R20
            # CW-R	        $07	CW reverse sideband
            # RTTY-R	    $08	RTTY reverse sideband
            # S-AM	        $11	Synchronous AM detection, IC-R75, Dual Sideband S-AM on IC-R8600
            # PSK	        $12	PSK31, IC-7800 only
            # PSK-R	        $12	PSK31, IC-7800 only, reverse sideband
            # S-AM (L)	    $14	S-AM LSB on IC-R8600
            # S-AM (U)	    $15	S-AM USB on IC-R8600
            # P25	        $16	IC-R8600
            # DV	        $17	Digital Voice (D-Star), IC-9100, IC-R8600, IC-9700, IC-705
            # dPMR	        $18	IC-R8600
            # NXDN-VN	    $19	IC-R8600
            # NXDN-N	    $20	IC-R8600
            # DCR	        $21	IC-R8600
            # DD	        $22	Digital Data (D-Star), IC-9700
            modes = {0: "LSB", 1: "USB", 2: "AM", 3: "CW", 4: "RTTY", 5: "FM", 6: "WFM", 7: "CW-R", 8: "RTTY-R", 0x11: "SAM", 0x12: "PSK", 0x16: "P25", 0x17: "DV"}
            mode_str = modes[mode] if mode in modes else str(mode)
            if mode_str != transciever.mode:
                logging.debug("Transceiver Mode: %s", mode_str)
                tx_queue.put_nowait({"mode": mode_str})
                # queue_out.put_nowait({})  # Bug, on Windows the last message stuck in queue, needs to send a second one
                transciever.mode = mode_str
        else:
            # Unsupported command
            logging.debug("CI-V CMD: %s", ' '.join('{:02x}'.format(x) for x in cmd_data))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='[%(asctime)-15s] (%(threadName)-10s)  %(message)s')

    # # Send transceiver status via sockets
    # Thread(target=socket_transmit_thread, name="SocketTX").start()

    # Read CI-V data from serial port
    try:
        transceiver_read_civ()
    except KeyboardInterrupt:
        app_active = False