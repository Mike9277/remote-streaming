#!/usr/bin/env python3
"""
Video Sender - Streaming video con controllo qualita, protocollo e metriche
"""

import os
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import argparse
import json
import time
import signal
import sys
import csv
from datetime import datetime

class VideoSender:
    def __init__(self, host, port, quality='high', protocol='udp', log_file=None, camera_source='nvargus'):
        Gst.init(None)

        self.host = host
        self.port = port
        self.quality = quality
        self.protocol = protocol
        self.camera_source = camera_source

        # ---------------------------------------------------------
        # NORMALIZZAZIONE PERCORSO LOG
        # ---------------------------------------------------------
        script_dir = os.path.dirname(os.path.abspath(__file__))

        if log_file and not os.path.isabs(log_file):
            log_file = os.path.join(script_dir, log_file)

        self.log_file = log_file  # JSONL time-series

        # summary file (stesso comportamento)
        if self.log_file and self.log_file.endswith(".jsonl"):
            self.summary_file = self.log_file.replace(".jsonl", "_summary.json")
        elif self.log_file:
            self.summary_file = self.log_file + "_summary.json"
        else:
            self.summary_file = None

        self.pipeline = None
        self.loop = None
        self.bytes_counter = 0

        self.metrics = {
            'start_time': None,
            'frames_sent': 0,
            'bytes_sent': 0,
            'drops': 0,
            'quality': quality,
            'protocol': protocol,
            'bitrate': 0,
            'resolution': '',
            'framerate': 0
        }

        self.quality_configs = {
            'high': {
                'width': 1280,
                'height': 720,
                'framerate': 30,
                'bitrate': 4000000,
                'description': 'HD 720p @ 30fps'
            },
            'low': {
                'width': 640,
                'height': 480,
                'framerate': 15,
                'bitrate': 1000000,
                'description': 'SD 480p @ 15fps'
            }
        }

    # ---------------------------------------------------------
    #  CAMERA SOURCE
    # ---------------------------------------------------------
    def build_camera_source(self, config):
        if self.camera_source == 'nvargus':
            return (
                "nvarguscamerasrc sensor-id=0 aelock=1 ee-mode=1 ee-strength=0 "
                "tnr-strength=0 tnr-mode=1 exposurecompensation=1 ! "
                f"video/x-raw(memory:NVMM), width=(int){config['width']}, "
                f"height=(int){config['height']}, "
                f"framerate=(fraction){config['framerate']}/1, "
                "format=(string)NV12 ! "
                "nvvidconv flip-method=0 ! "
                f"nvv4l2h264enc bitrate={config['bitrate']} ! "
            )
        elif self.camera_source == 'v4l2':
            return (
                "v4l2src device=/dev/video0 ! "
                f"video/x-raw, width=(int){config['width']}, height=(int){config['height']}, "
                f"framerate=(fraction){config['framerate']}/1 ! "
                "videoconvert ! "
                f"x264enc bitrate={config['bitrate']//1000} speed-preset=ultrafast "
                "tune=zerolatency key-int-max=30 ! "
                "video/x-h264,profile=baseline,stream-format=byte-stream ! "
                "h264parse ! "
            )
        else:
            return (
                "videotestsrc pattern=ball is-live=true ! "
                f"video/x-raw, width=(int){config['width']}, height=(int){config['height']}, "
                f"framerate=(fraction){config['framerate']}/1 ! "
                "videoconvert ! "
                f"x264enc bitrate={config['bitrate']//1000} speed-preset=ultrafast "
                "tune=zerolatency key-int-max=30 ! "
                "video/x-h264,profile=baseline,stream-format=byte-stream ! "
                "h264parse ! "
            )

    # ---------------------------------------------------------
    #  PROBE: conteggio bytes
    # ---------------------------------------------------------
    def buffer_probe_callback(self, pad, info):
        buffer = info.get_buffer()
        if buffer:
            self.bytes_counter += buffer.get_size()
            self.metrics['bytes_sent'] = self.bytes_counter
        return Gst.PadProbeReturn.OK

    # ---------------------------------------------------------
    #  PIPELINE
    # ---------------------------------------------------------
    def build_pipeline(self):
        config = self.quality_configs[self.quality]
        camera_pipeline = self.build_camera_source(config)

        if self.protocol == 'udp':
            pipeline_str = (
                camera_pipeline +
                "rtph264pay pt=96 config-interval=1 mtu=1272 ! "
                f"udpsink host={self.host} port={self.port} name=sink"
            )
        else:
          # OTTIMIZZAZIONI TCP:
          # - Rimosso gdppay (overhead)
          # - sync=false (no sync ai timestamp)
          # - mtu=1400 ridotto
          pipeline_str = (
              camera_pipeline +
              "rtph264pay pt=96 config-interval=1 mtu=1400 ! "
              "gdppay ! "                            # ← framing GDP
              f"tcpserversink host={self.host} port={self.port} sync=false async=false name=sink"
          )
        
        print(f"Pipeline: {pipeline_str}")
        self.pipeline = Gst.parse_launch(pipeline_str)
        
        sink = self.pipeline.get_by_name("sink")
        if sink:
            sink_pad = sink.get_static_pad("sink")
            if sink_pad:
                sink_pad.add_probe(Gst.PadProbeType.BUFFER, self.buffer_probe_callback)

        self.metrics['resolution'] = f"{config['width']}x{config['height']}"
        self.metrics['framerate'] = config['framerate']
        self.metrics['bitrate'] = config['bitrate']

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)

    # ---------------------------------------------------------
    #  MESSAGGI GST
    # ---------------------------------------------------------
    def on_message(self, bus, message):
        t = message.type

        if t == Gst.MessageType.EOS:
            self.stop()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"\nErrore: {err}")
            print(f"Debug: {debug}")
            self.stop()
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"\nWarning: {warn}")

    # ---------------------------------------------------------
    #  JSONL time-series
    # ---------------------------------------------------------
    def append_timeseries(self):
        if not self.log_file:
            return

        entry = {
            "timestamp": datetime.now().isoformat(),
            **self.metrics
        }

        try:
            with open(self.log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            print(f"Errore scrittura time-series: {e}")

    # ---------------------------------------------------------
    #  CSV export
    # ---------------------------------------------------------
    def export_csv_from_jsonl(self):
        if not self.log_file:
            return

        jsonl_path = self.log_file
        csv_path = jsonl_path.replace(".jsonl", ".csv")

        rows = []
        all_keys = set()

        try:
            with open(jsonl_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        rows.append(entry)
                        all_keys.update(entry.keys())
                    except:
                        pass
        except Exception as e:
            print(f"Errore lettura JSONL: {e}")
            return

        if not rows:
            print("Nessuna riga valida trovata nel JSONL, CSV vuoto.")
            return

        all_keys = sorted(all_keys)

        try:
            with open(csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=all_keys)
                writer.writeheader()
                writer.writerows(rows)

            print(f"\n CSV generato: {csv_path}")
        except Exception as e:
            print(f"Errore scrittura CSV: {e}")

    # ---------------------------------------------------------
    #  SUMMARY JSON
    # ---------------------------------------------------------
    def save_summary(self):
        if not self.summary_file:
            return

        if self.metrics['start_time']:
            self.metrics['duration'] = time.time() - self.metrics['start_time']
            self.metrics['end_time'] = datetime.now().isoformat()

        if self.metrics.get('duration', 0) > 0:
            self.metrics['avg_bitrate_bps'] = (self.metrics['bytes_sent'] * 8) / self.metrics['duration']
            self.metrics['avg_bitrate_mbps'] = self.metrics['avg_bitrate_bps'] / 1_000_000

        try:
            with open(self.summary_file, "w") as f:
                json.dump(self.metrics, f, indent=2)
            print(f"\n Summary salvato in: {self.summary_file}")
        except Exception as e:
            print(f"Errore salvataggio summary: {e}")

    # ---------------------------------------------------------
    #  UPDATE METRICS
    # ---------------------------------------------------------
    def update_metrics(self):
        if not self.pipeline:
            return True

        if self.metrics['start_time']:
            elapsed = time.time() - self.metrics['start_time']
            self.metrics['frames_sent'] = int(elapsed * self.metrics['framerate'])

            print(f"\r  Durata: {int(elapsed)}s | "
                  f" Frame: ~{self.metrics['frames_sent']} | "
                  f" Bytes: {self.metrics['bytes_sent']:,} | "
                  f" {self.quality.upper()} | "
                  f" {self.protocol.upper()}",
                  end='', flush=True)

        self.append_timeseries()
        return True

    # ---------------------------------------------------------
    #  START
    # ---------------------------------------------------------
    def start(self):
        print(f"\n{'='*60}")
        print(f" VIDEO SENDER")
        print(f"{'='*60}")
        print(f" Destinazione: {self.host}:{self.port}")
        print(f" Camera: {self.camera_source.upper()}")
        print(f" Qualita: {self.quality.upper()} - {self.quality_configs[self.quality]['description']}")
        print(f" Protocollo: {self.protocol.upper()}")
        if self.log_file:
            print(f" Log time-series: {self.log_file}")
            print(f" Summary: {self.summary_file}")
        print(f"{'='*60}\n")

        self.build_pipeline()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print(" Impossibile avviare la pipeline")
            return

        self.metrics['start_time'] = time.time()
        self.metrics['start_time_iso'] = datetime.now().isoformat()

        self.loop = GLib.MainLoop()
        GLib.timeout_add(1000, self.update_metrics)

        signal.signal(signal.SIGINT, lambda sig, frame: self.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())

        print("  Streaming avviato. Premi Ctrl+C per terminare.\n")

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.stop()

    # ---------------------------------------------------------
    #  STOP
    # ---------------------------------------------------------
    def stop(self):
        print("\n\n  Interruzione streaming...")

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

        if self.loop:
            self.loop.quit()

        self.export_csv_from_jsonl()
        self.save_summary()

        print(" Sender terminato\n")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description='Video Sender - Streaming video configurabile'
    )

    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--quality', choices=['high', 'low'], default='high')
    parser.add_argument('--protocol', choices=['udp', 'tcp'], default='udp')
    parser.add_argument('--log', dest='log_file')
    parser.add_argument('--camera', dest='camera_source',
                       choices=['nvargus', 'v4l2', 'test'], default='nvargus')

    args = parser.parse_args()

    sender = VideoSender(
        host=args.host,
        port=args.port,
        quality=args.quality,
        protocol=args.protocol,
        log_file=args.log_file,
        camera_source=args.camera_source
    )

    sender.start()


if __name__ == '__main__':
    main()
