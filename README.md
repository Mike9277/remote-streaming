## Video Streaming System
This repository contains a complete video‑streaming workflow built around GStreamer, designed to send real-time video images from a sender server to a receiver sender. The repository includes:

    A python video sender script, to be executed on the sending server.

    A python video receiver script to be executed on the receiver server. 

    Automated orchestration via a Bash launcher script (execution on the receiver side)

    Time‑series logging, CSV export, and session summaries

The system supports UDP and TCP transport, multiple camera sources, and automatic metric collection on both ends.

## Repository Structure

remote-streaming/
│
├── video_sender.py        # Runs on sender
├── video_receiver.py      # Runs on receiver
├── run_stream.sh          # Orchestrates sender + receiver
└── README.md

---

## 1. Overview

The system streams H.264 video from a sender device to a remote receiver using GStreamer pipelines.  
Both sender and receiver:

- collect real‑time metrics (bitrate, frames, bytes, packet loss…)
- write **JSONL time‑series logs**
- automatically export **CSV** files
- generate a **summary JSON** at the end of the session

The `run_stream.sh` script:

- starts the sender remotely via SSH  
- starts the receiver locally  
- ensures correct startup order (important for TCP)  
- sends a clean **SIGINT** to the sender when the receiver stops  
- guarantees that logs are saved correctly  

---

## 2. Video Sender

### Location
Runs on the sender device (e.g., `/home/username/remote-streaming/video_sender.py`).

### Purpose
Captures video from:

- `nvargus` (CSI camera)
- `v4l2` (USB webcam)
- `test` (synthetic pattern)

Encodes to H.264 and streams via:

- **UDP** (stateless, low latency)
- **TCP** (reliable, ordered)

### Logging
The sender generates three files:

