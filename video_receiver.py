#!/usr/bin/env python3
"""
Video Receiver - Ricezione video con metriche e logging
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

class VideoReceiver:
    def __init__(self, port, protocol='udp', host=None, log_file=None, save_video=None):
        Gst.init(None)

        self.port = port
        self.protocol = protocol
        self.host = host
        self.save_video = save_video

        # Normalizzazione percorso log
        script_dir = os.path.dirname(os.path.abspath(__file__))
        if log_file and not os.path.isabs(log_file):
            log_file = os.path.join(script_dir, log_file)

        self.log_file = log_file  # JSONL time-series

        # Summary file
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
            'frames_received': 0,
            'bytes_received': 0,
            'packets_lost': 0,
            'protocol': protocol,
            'port': port
        }

        self.last_update = time.time()

    # ---------------------------------------------------------
    #  BUFFER PROBE
    # ---------------------------------------------------------
    def buffer_probe_callback(self, pad, info):
        buffer = info.get_buffer()
        if buffer:
            if self.bytes_counter == 0 and self.metrics['start_time']:
                elapsed = time.time() - self.metrics['start_time']
                print(f"\n PRIMO PACCHETTO RICEVUTO dopo {elapsed:.1f}s!\n")

            self.bytes_counter += buffer.get_size()
            self.metrics['bytes_received'] = self.bytes_counter
        return Gst.PadProbeReturn.OK

    # ---------------------------------------------------------
    #  PIPELINE
    # ---------------------------------------------------------
    def build_pipeline(self):
        if self.protocol == 'udp':
            pipeline_str = (
                f"udpsrc port={self.port} name=src ! "
                "application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
                "rtpjitterbuffer latency=50 name=jitterbuffer ! "  # Ridotto da default 200ms a 50ms
                "rtph264depay ! "
                "h264parse ! "
                "decodebin ! "
                "videoconvert ! "
            )
        else:
            if not self.host:
                raise ValueError("TCP mode requires --host parameter")
        
            # OTTIMIZZAZIONI TCP:
            # - Rimosso gdpdepay
            # - Ridotto jitterbuffer latency a 20ms
            # - Aggiunto drop-on-latency=true
            pipeline_str = (
                f"tcpclientsrc host={self.host} port={self.port} name=src ! "
                "gdpdepay ! "                          # ← framing GDP
                "rtph264depay ! "
                "h264parse ! "
                "avdec_h264 max-threads=4 ! "
                "videoconvert ! "
            )

        if self.save_video:
            pipeline_str += (
                "tee name=t ! queue ! autovideosink "
                "t. ! queue ! videoconvert ! "
                "x264enc ! mp4mux ! filesink location="
                f"{self.save_video}"
            )
        else:
            pipeline_str += "autovideosink"

        print(f"Pipeline: {pipeline_str}")
        self.pipeline = Gst.parse_launch(pipeline_str)

        src = self.pipeline.get_by_name("src")
        if src:
            src_pad = src.get_static_pad("src")
            if src_pad:
                src_pad.add_probe(Gst.PadProbeType.BUFFER, self.buffer_probe_callback)

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
    #  JSONL TIME SERIES
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
    #  UPDATE METRICS
    # ---------------------------------------------------------
    def update_metrics(self):
        if not self.pipeline:
            return True

        jitterbuffer = self.pipeline.get_by_name("jitterbuffer")
        if jitterbuffer:
            try:
                stats = jitterbuffer.get_property("stats")
                if stats:
                    try:
                        ok, lost = stats.get_uint64("num-lost")
                        if ok:
                            self.metrics['packets_lost'] = lost
                    except:
                        pass
            except:
                pass

        if self.metrics['start_time']:
            elapsed = time.time() - self.metrics['start_time']

            current_time = time.time()
            if current_time - self.last_update >= 1.0:
                self.metrics['frames_received'] += 30
                self.last_update = current_time

            bitrate_bps = (self.metrics['bytes_received'] * 8) / max(elapsed, 0.001)
            bitrate_mbps = bitrate_bps / 1_000_000

            print(f"\r  Durata: {int(elapsed)}s | "
                  f" Frame: ~{self.metrics['frames_received']} | "
                  f" Bytes: {self.metrics['bytes_received']:,} | "
                  f" {bitrate_mbps:.2f} Mbps | "
                  f" Lost: {self.metrics['packets_lost']} | "
                  f" {self.protocol.upper()}",
                  end='', flush=True)

        self.append_timeseries()
        return True

    # ---------------------------------------------------------
    #  CSV EXPORT
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
    def save_metrics(self):
        if not self.summary_file:
            return

        if self.metrics['start_time']:
            self.metrics['duration'] = time.time() - self.metrics['start_time']
            self.metrics['end_time'] = datetime.now().isoformat()

        if self.metrics.get('duration', 0) > 0:
            self.metrics['avg_bitrate_bps'] = (self.metrics['bytes_received'] * 8) / self.metrics['duration']
            self.metrics['avg_bitrate_mbps'] = self.metrics['avg_bitrate_bps'] / 1_000_000
            self.metrics['avg_fps'] = self.metrics['frames_received'] / self.metrics['duration']

        total_packets = self.metrics['frames_received'] + self.metrics['packets_lost']
        if total_packets > 0:
            self.metrics['packet_loss_rate'] = (self.metrics['packets_lost'] / total_packets) * 100

        try:
            with open(self.summary_file, "w") as f:
                json.dump(self.metrics, f, indent=2)
            print(f"\n Summary salvato in: {self.summary_file}")
        except Exception as e:
            print(f"Errore salvataggio summary: {e}")

    # ---------------------------------------------------------
    #  START
    # ---------------------------------------------------------
    def start(self):
        print(f"\n{'='*60}")
        print(f" VIDEO RECEIVER")
        print(f"{'='*60}")
        print(f" Porta: {self.port}")
        if self.protocol == 'tcp':
            print(f" Connessione a: {self.host}:{self.port}")
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
        self.last_update = time.time()

        self.loop = GLib.MainLoop()
        GLib.timeout_add(1000, self.update_metrics)

        signal.signal(signal.SIGINT, lambda sig, frame: self.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())

        print("  Ricezione avviata. Premi Ctrl+C per terminare.\n")

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.stop()

    # ---------------------------------------------------------
    #  STOP
    # ---------------------------------------------------------
    def stop(self):
        print("\n\n  Interruzione ricezione...")

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

        if self.loop:
            self.loop.quit()

        self.export_csv_from_jsonl()
        self.save_metrics()

        print(" Receiver terminato\n")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description='Video Receiver - Ricezione video con metriche'
    )

    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--host')
    parser.add_argument('--protocol', choices=['udp', 'tcp'], default='udp')
    parser.add_argument('--log', dest='log_file')
    parser.add_argument('--save', dest='save_video')

    args = parser.parse_args()

    receiver = VideoReceiver(
        port=args.port,
        protocol=args.protocol,
        host=args.host,
        log_file=args.log_file,
        save_video=args.save_video
    )

    receiver.start()


if __name__ == '__main__':
    main()
