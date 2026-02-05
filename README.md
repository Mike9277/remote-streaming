## Video Streaming System
This repository contains a complete video‑streaming workflow built around GStreamer, designed to send real-time video images from a sender server to a receiver sender. The repository includes:

    A python video sender script, to be executed on the sending server.

    A python video receiver script to be executed on the receiver server. 

    Automated orchestration via a Bash launcher script (execution on the receiver side)

    Time‑series logging, CSV export, and session summaries

The system supports UDP and TCP transport, multiple camera sources, and automatic metric collection on both ends.

## Repository Structure
```text
remote-streaming/
│
├── video_sender.py        # Runs on sender
├── video_receiver.py      # Runs on receiver
├── run_stream.sh          # Orchestrates sender + receiver
└── README.md
```

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

All logs are saved in the **same directory as the script**, regardless of where the process is launched.

### Usage
```
python3 video_sender.py \
--host <receiver_ip_for_udp | 0.0.0.0_for_tcp> \
--port 5000 \
--quality high|low \
--protocol udp|tcp \
--camera nvargus|v4l2|test \
--log sender_test.jsonl
```
---

## 3. Video Receiver

### Location
Runs on the receiver machine (e.g., `/home/username/remote-streaming/video_receiver.py`).

### Purpose
Receives the video stream, decodes it, displays it, and collects metrics.

### Usage
```
python3 video_receiver.py \
--host < - | receiver_ip_for_tcp> \
--port 5000 \
--protocol udp|tcp \
--log receiver_test.jsonl
```
---

## 4. Orchestration Script ('run_stream.sh')
This script automates the entire workflow.

### Responsibilities

- kills any previous sender on the sender server
- starts the sender **remotely** via SSH
- stores the sender’s **real PID** on the sender server
- starts the receiver **locally**
- when the receiver exits, sends **SIGINT** to the sender → ensures logs are saved correctly

Using this script will require the receiver to already have the necessary SSH permissions to log into the sender and run the sender server and run commands remotely. This may be configured beforehand; the script assumes the permission are already in place.

### Usage
<tag> allows to specify a string that will be attached to .csv and .jsonl filenames to better organize them.

```
./run_stream.sh <receiver_ip_for_udp | sender_ip_for_tcp> <quality high|low> <protocol udp|tcp> <tag>
```
---
## 5. Logging Format

### JSONL time-series
One JSON object per second:

```json
{"timestamp": "...", "frames_sent": 120, "bytes_sent": 1048576, ...}
