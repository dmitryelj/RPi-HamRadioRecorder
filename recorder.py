#!/usr/bin/python3
#
# To run: python3 recorder.py
# To see messages in browser: http://IP_ADDRESS:8000
#
# Install libraries first:
# sudo apt-get install libatlas-base-dev libopenjp2-7 libtiff5
# sudo apt-get install python3 python3-pip
# sudo pip3 install numpy pillow tzlocal spidev RPi.GPIO requests psutil flask-socketio

# Windows pyaudio install:
# https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio

# 3.5" Display for Raspberry Pi install: http://www.lcdwiki.com/MHS-3.5inch_RPi_Display


import eventlet  # sudo pip3 install eventlet
eventlet.monkey_patch()

from flask import Flask, Response, render_template, request, jsonify, send_file, send_from_directory
from flask_socketio import SocketIO  # sudo pip3 install flask-socketio

import time
import sys
import subprocess
import os
import threading
import multiprocessing
import queue
import re
import fnmatch
import textwrap
import json
import argparse
import audio
import transceiver
import pyaudio
import wave
import requests
import logging
import socket
from typing import Tuple, Optional, Union, Any
from datetime import datetime
import serial.tools.list_ports
import utils

# Web server

dname = os.path.dirname(os.path.abspath(__file__)) + "/http"
app = Flask(__name__, template_folder=dname)
app._static_folder = dname
app.config['SECRET_KEY'] = '1234'
socketio = SocketIO(app, async_mode='eventlet')
socket_connected = False
port_number = 8000
app_active = False
recording_active = False
recording_start = 0.0
recording_samplerate = 44100
recording_channels = 1
recordings_path = "/home/pi/Documents" if utils.is_raspberry_pi() else utils.get_app_path()
recordings_count = len(audio.get_files_list(recordings_path))

# Transceiver

transceiver_name: str = ""  # IC705, IC7300, etc
transceiver_freq_hz: int = 0  # Frequency in Hz
transceiver_mode: str = ""  # AM, FM, USB, LSB, etc
transceiver_time: str = "00:00"
transceiver_queue_in = multiprocessing.Queue()
transceiver_queue_out = multiprocessing.Queue()

# Audio Interface

pd = pyaudio.PyAudio()
recording_interface = "USB Audio"
recording_stop_event = multiprocessing.Event()

# Recording

# manager = Manager()
# recording_active = manager.Value('i', 0)

def device_status():
    return {'recordings': recordings_count,
            "audio": audio.interface_connected(pd, filter=recording_interface),
            "recording_active": recording_active,
            "recording_time": (time.monotonic() - recording_start) if recording_active else 0,
            "recording_samplerate": recording_samplerate,
            "recording_channels": recording_channels,
            "disk_space": round(utils.get_disk_space()[0]/(1024*1024*1024), 2),
            "time": datetime.now().strftime('%H:%M:%S'),
            "transceiver": transceiver_name,
            "frequency": transceiver_freq_hz,
            "mode": transceiver_mode,
            "ip": "http://{}:{}".format(utils.get_ip_address(), port_number)}


@app.route('/')
def main_page():
    return render_template('index.html', async_mode=socketio.async_mode)


@app.route('/recordings.html')
def recordings_page():
    return render_template('recordings.html', async_mode=socketio.async_mode, wav_files=audio.get_files_list(recordings_path))


@app.route('/recordings/<wav_file>')
def get_recording(wav_file):
    return send_file(recordings_path + os.sep + wav_file, mimetype='audio/wav')


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'http'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@socketio.on('connect', namespace='/info')
def on_connect():
    global socket_connected, socketio
    logging.debug('HTTP Client connected')
    socketio.emit('device_status', device_status(), namespace='/info')
    socket_connected = True


@socketio.on('disconnect', namespace='/info')
def on_disconnect():
    global socket_connected
    logging.debug('HTTP Client disconnected')
    socket_connected = False


@socketio.on('record_start', namespace='/info')
def on_record_start():
    global recording_active, transceiver_name, transceiver_freq_hz, recording_start, recording_stop_event, recording_interface
    if recording_active is False:
        logging.debug('Start Recording: interface {}, s/r={}, channels={}'.format(recording_interface, recording_samplerate, recording_channels))
        recording_active = True
        recording_start = time.monotonic()
        recording_stop_event.clear()
        multiprocessing.Process(target=audio.audio_recording_thread, args=(pd, recording_interface, recording_channels, recording_samplerate,
                                                                           transceiver_name, transceiver_freq_hz, recording_stop_event)).start()
    else:
        logging.debug('Start Recording: already started')


@socketio.on('record_stop', namespace='/info')
def on_record_stop():
    global recording_active, recordings_count, recordings_path, recording_stop_event
    if recording_active:
        logging.debug('Stop Recording')
        recording_active = False
        recording_stop_event.set()
        recordings_count = len(audio.get_files_list(recordings_path))
    else:
        logging.debug('Stop Recording: already stopped')


@socketio.on('set_mode_mono', namespace='/info')
def set_mode_mono():
    global recording_channels
    recording_channels = 1
    socketio.emit('device_status', device_status(), namespace='/info')


@socketio.on('set_mode_stereo', namespace='/info')
def set_mode_stereo():
    global recording_channels
    recording_channels = 2
    socketio.emit('device_status', device_status(), namespace='/info')


@socketio.on('set_sample_rate', namespace='/info')
def set_sample_rate(sr):
    global recording_samplerate
    recording_samplerate = sr
    socketio.emit('device_status', device_status(), namespace='/info')


def transceiver_update_thread():
    global app_active, recording_active, socket_connected, transceiver_queue_out, transceiver_freq_hz, transceiver_name, transceiver_mode

    logging.debug("transceiver_update_thread started")
    # Waiting for connection
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.settimeout(5.0)
    while app_active:
        try:
            socket_port = 12020
            s.bind(('0.0.0.0', socket_port))
            while app_active:
                try:
                    s.listen()
                    conn, addr = s.accept()
                    conn.settimeout(0.1)

                    no_data = 0
                    socket_data = b''
                    logging.debug('Connected by {}'.format(addr))
                    while app_active:
                        try:
                            data = conn.recv(32)
                            if len(data) > 0:
                                socket_data += data
                                # Data block ends with 0xFD - split to parts
                                while socket_data.find(b'\xFD') != -1:
                                    data_end = socket_data.find(b'\xFD')
                                    if data_end != -1:
                                        # New data in json found
                                        d = socket_data[:data_end]
                                        # print("Data:", d)
                                        t_data = json.loads(d.decode())
                                        if "name" in t_data:
                                            transceiver_name = t_data["name"]
                                            logging.debug("Transceiver Name: %s", transceiver_name)
                                        if "mode" in t_data:
                                            transceiver_mode = t_data["mode"]
                                            logging.debug("Transceiver Mode: %s", transceiver_mode)
                                        if "frequency" in t_data:
                                            transceiver_freq_hz = t_data["frequency"]
                                            logging.debug("Transceiver Frequency: %d", transceiver_freq_hz)

                                        socket_data = socket_data[data_end + 1:]
                                    else:
                                        # If command too big, something is wrong
                                        if len(socket_data) > 1024:
                                            socket_data = b''
                                no_data = 0
                            else:
                                no_data += 1
                                time.sleep(0.1)
                                if no_data >= 128:
                                    break
                        except socket.timeout:
                            # logging.error("  Connection timeout")
                            pass
                except Exception as e:
                    logging.error("Socket Error: %s", e)
                    time.sleep(1.0)
                    # s.close()
        except Exception as e:
            logging.error("Socket Error: %s", e)

    logging.debug("transceiver_update_thread done")

def status_update_thread():
    global app_active, recording_active, socket_connected, transceiver_freq_hz, transceiver_name, transceiver_mode

    logging.debug("status_update_thread started")
    while app_active:
        # Get all data from the transceiver
        # while True:
        #     try:
        #         if not transceiver_queue_out.empty():
        #             t_data = transceiver_queue_out.get_nowait()
        #             print("D:", t_data)
        #             if "name" in t_data:
        #                 transceiver_name = t_data["name"]
        #             if "mode" in t_data:
        #                 transceiver_mode = t_data["mode"]
        #             if "frequency" in t_data:
        #                 transceiver_freq_hz = t_data["frequency"]
        #         else:
        #             break
        #     except queue.Empty:
        #         break

        # Update client
        if socket_connected:
            socketio.emit('device_status', device_status(), namespace='/info')
        time.sleep(0.1)

    logging.debug("status_update_thread ended")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format='[%(asctime)-15s] (%(threadName)-10s)  %(message)s')

    print("")
    print("HAM Radio Recorder v0.1 by Dmitrii Eliuseev\n")
    print("Run:\npython3 recorder.py")
    print("")
    print("Server running: http://{}:{}".format(utils.get_ip_address(), port_number))
    print("")

    # Set current folder
    abs_path = os.path.abspath(__file__)
    dir_path = os.path.dirname(abs_path)
    os.chdir(dir_path)

    # Show available audio devices and ports
    logging.debug("Audio Devices found:")
    audio.list_audio_devices(pd)
    logging.debug("Serial Ports found:")
    for port, desc, hw in sorted(serial.tools.list_ports.comports()):
       logging.debug(f"{port}: {desc}")
    logging.debug("")

    app_active = True

    # Status update thread
    socketio.start_background_task(target=status_update_thread)

    # Transceiver update via sockets
    # cmd = f"sudo python{sys.version_info.major}.{sys.version_info.minor} {os.path.dirname(os.path.abspath(__file__))}/transceiver.py &"
    # logging.debug("Running Transceiver Control separately: %s", cmd)
    # # os.system(cmd)
    socketio.start_background_task(target=transceiver_update_thread)
    # multiprocessing.Process(target=transceiver.transceiver_read_civ).start()

    # Run webserver
    socketio.run(app, host='0.0.0.0', debug=True, port=port_number, use_reloader=False)    # app.run(host='0.0.0.0')

    app_active = False
    recording_stop_event.set()
    # transceiver_queue_in.put({"command": "quit"})

    pd.terminate()

    print("App done")




