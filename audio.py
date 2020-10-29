import pyaudio
import wave
from typing import Tuple, Optional, Union, Any
import multiprocessing
import fnmatch
from datetime import datetime
import os
import logging


def set_logger(level):
    logging.basicConfig(level=level, format='[%(asctime)-15s] (%(threadName)-10s)  %(message)s')


def get_files_list(folder: str):
    return fnmatch.filter(os.listdir(folder), '*.wav')


def list_audio_devices(pd: pyaudio.PyAudio):
    for i in range(pd.get_device_count()):
        try:
            if (pd.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                logging.debug("Input Audio Device #{}: {}".format(i, pd.get_device_info_by_host_api_device_index(0, i).get('name')))
        except Exception as e:
            pass  # logging.error("find_audio_device error: %s" % e)

def find_audio_device(pd: pyaudio.PyAudio, filter: str) -> Tuple[int, str]:
    device_index, device_name = -1, "None"
    for i in range(pd.get_device_count()):
        # print(p.get_device_info_by_index(i))
        try:
            if (pd.get_device_info_by_host_api_device_index(0, i).get('maxInputChannels')) > 0:
                name = pd.get_device_info_by_host_api_device_index(0, i).get('name')
                if filter.lower() in name.lower():
                    device_index = i
                    device_name = name
        except Exception as e:
            pass  # logging.error("find_audio_device error: %s" % e)
    return device_index, device_name


def interface_connected(pd: pyaudio.PyAudio, filter: str) -> str:
    device_index, device_name = find_audio_device(pd, filter)
    return device_name if device_index >= 0 else ""


def audio_recording_thread(pd: pyaudio.PyAudio, audio_device: str, recording_channels: int, recording_samplerate: int, t_name: str, t_frequency: int, stop_event: multiprocessing.Event):
    logging.basicConfig(level=logging.DEBUG, format='[%(asctime)-15s] (REC)  %(message)s')
    logging.debug("Audio devices:")
    list_audio_devices(pd)
    logging.debug("")

    device_index, device_name = find_audio_device(pd, audio_device)
    if device_index == -1:
        logging.debug("Rec aborted: device %s is not found", audio_device)
        return

    chunk_size = 4096
    stream = pd.open(format=pyaudio.paInt16, channels=recording_channels, rate=recording_samplerate,
                    frames_per_buffer=chunk_size, input_device_index=device_index, input=True)

    filename = "{}_{}_{}kHz_AF.wav".format(t_name, datetime.now().strftime("%Y%m%d_%H%M%SZ"), t_frequency//1000)
    wf = wave.open(filename, 'wb')
    wf.setnchannels(recording_channels)
    wf.setframerate(recording_samplerate)
    wf.setsampwidth(pd.get_sample_size(pyaudio.paInt16))

    logging.debug('Recording the file {}, device #{} {}'.format(filename, device_index, device_name))
    size_total, index = 0, 0
    while stop_event.is_set() is False:  # recording_active is True and app_active is True:
        data = stream.read(chunk_size)
        wf.writeframes(data)

        size_total += len(data)
        index += 1
        if index == 8:
            logging.debug("Recording: {} KBytes recorded".format(size_total//1024))
            index = 0

    # Stop and close the stream
    stream.stop_stream()

    pd.close(stream)
    wf.close()

    logging.debug("Recording complete")
