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
├── video_sender.py        # Runs on Jetson (smaug)
├── video_receiver.py      # Runs on receiver machine
├── run_stream.sh          # Orchestrates sender + receiver
└── README.md
