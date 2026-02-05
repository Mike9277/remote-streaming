#!/usr/bin/env python3
"""
Video Receiver - Ricezione video con metriche, time-series logging e CSV finale
"""

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import argparse
import json
import time
import signal
import sys
import csv
import os
from datetime import datetime

class VideoReceiver:
    def __init__(self, port, protocol='udp', log_file=None, save_video=None):
        Gst.init(None)

        self.port = port
        self.protocol = protocol
        self.log_file = log_file
        self.save_video = save_video

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
    # TIME SERIES LOGGING
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
    # JSONL → CSV EXPORT
    # ---------------------------------------------------------
    def export_csv_from_jsonl(self):
        if not self.log_file:
            return

        jsonl_path = self.log_file
        csv_path = os.path.splitext(jsonl_path)[0] + ".csv"

        rows = []
        all_keys = set()

        try:
            with open(jsonl_path, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        rows.append(entry)
                        all_keys.update(entry.keys())
                    except:
                        pass
        except Exception as e:
            print(f"Errore lettura JSONL: {e}")
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
    # GStreamer callbacks
    # ---------------------------------------------------------
    def buffer_probe_callback(self, pad, info):
        buffer = info.get_buffer()
        if buffer:
            if self.bytes_counter == 0:
                elapsed = time.time() - self.metrics['start_time']
                print(f"\n PRIMO PACCHETTO RICEVUTO dopo {elapsed:.1f}s!\n")

            self.bytes_counter += buffer.get_size()
            self.metrics['bytes_received'] = self.bytes_counter
        return Gst.PadProbeReturn.OK

    def build_pipeline(self):
        if self.protocol == 'udp':
            pipeline_str = (
                f"udpsrc port={self.port} name=src ! "
                f"application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
            )
        else:
            pipeline_str = (
                f"tcpclientsrc port={self.port} name=src ! "
                f"gdpdepay ! "
                f"application/x-rtp,media=video,encoding-name=H264,payload=96 ! "
            )

        pipeline_str += (
            "rtpjitterbuffer name=jitterbuffer ! "
            "rtph264depay ! "
            "h264parse ! "
            "decodebin ! "
            "videoconvert ! "
        )

        if self.save_video:
            pipeline_str += (
                f"tee name=t ! queue ! autovideosink "
                f"t. ! queue ! videoconvert ! "
                f"x264enc ! mp4mux ! filesink location={self.save_video}"
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

    def on_message(self, bus, message):
        t = message.type

        if t == Gst.MessageType.EOS:
            print("\nEnd of stream")
            self.stop()
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"\nErrore: {err}")
            print(f"Debug: {debug}")
            self.stop()
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"\nWarning: {warn}")
        elif t == Gst.MessageType.ELEMENT:
            struct = message.get_structure()
            if struct and struct.has_name("GstRTPJitterBufferStats"):
                self.parse_jitter_stats(struct)

    def parse_jitter_stats(self, struct):
        try:
            if struct.has_field("num-lost"):
                lost = struct.get_value("num-lost")
                self.metrics['packets_lost'] = lost
        except:
            pass

    # ---------------------------------------------------------
    # METRICS UPDATE
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
                        result = stats.get_uint64("num-lost")
                        if result[0]:
                            self.metrics['packets_lost'] = result[1]
                    except:
                        try:
                            num_lost = stats.get_value("num-lost")
                            if num_lost is not None:
                                self.metrics['packets_lost'] = num_lost
                        except:
                            pass
            except:
                pass

        if self.metrics['start_time']:
            elapsed = time.time() - self.metrics['start_time']

            current_time = time.time()
            delta = current_time - self.last_update
            if delta >= 1.0:
                self.metrics['frames_received'] += 30
                self.last_update = current_time

            if elapsed > 0:
                bitrate_bps = (self.metrics['bytes_received'] * 8) / elapsed
                bitrate_mbps = bitrate_bps / 1_000_000

                print(f"\r  Durata: {int(elapsed)}s | "
                      f" Frame: ~{self.metrics['frames_received']} | "
                      f" Bytes: {self.metrics['bytes_received']:,} | "
                      f" {bitrate_mbps:.2f} Mbps | "
                      f" Lost: {self.metrics['packets_lost']} | "
                      f" {self.protocol.upper()}", end='', flush=True)

        self.append_timeseries()
        return True

    # ---------------------------------------------------------
    # START / STOP
    # ---------------------------------------------------------
    def start(self):
        print(f"\n{'='*60}")
        print(f" VIDEO RECEIVER")
        print(f"{'='*60}")
        print(f" Porta: {self.port}")
        print(f" Protocollo: {self.protocol.upper()}")
        if self.save_video:
            print(f" Salvataggio: {self.save_video}")
        if self.log_file:
            print(f" Log time-series: {self.log_file}")
        print(f"{'='*60}\n")

        self.build_pipeline()

        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print(" Impossibile avviare la pipeline")
            return

        self.metrics['start_time'] = time.time()
        self.metrics['start_time_iso'] = datetime.now().isoformat()
        self.last_update = time.time()

        import socket
        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
            print(f" Indirizzo locale: {local_ip}")
        except:
            print(f" Hostname: {hostname}")
        print(f" In ascolto su porta UDP: {self.port}")
        print(f" Aspettando stream da sender...\n")

        self.loop = GLib.MainLoop()
        GLib.timeout_add(1000, self.update_metrics)

        signal.signal(signal.SIGINT, lambda sig, frame: self.stop())
        signal.signal(signal.SIGTERM, lambda sig, frame: self.stop())

        print("  Ricezione avviata. Premi Ctrl+C per terminare.\n")

        try:
            self.loop.run()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        print("\n\n  Interruzione ricezione...")

        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)

        if self.loop:
            self.loop.quit()

        # Convert JSONL → CSV
        self.export_csv_from_jsonl()

        print(" Receiver terminato\n")
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description='Video Receiver - Ricezione video con metriche time-series'
    )

    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--protocol', choices=['udp', 'tcp'], default='udp')
    parser.add_argument('--log', dest='log_file')
    parser.add_argument('--save', dest='save_video')

    args = parser.parse_args()

    receiver = VideoReceiver(
        port=args.port,
        protocol=args.protocol,
        log_file=args.log_file,
        save_video=args.save_video
    )

    receiver.start()


if __name__ == '__main__':
    main()
