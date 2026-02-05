#!/bin/bash

############################################################
#  Video Streaming Launcher (Receiver-side)
#  Avvia automaticamente:
#    1) Sender via SSH sul Jetson
#    2) Receiver locale
#
#  Uso:
#     ./run_stream.sh <receiver_ip> <quality> <protocol>
#
############################################################

# --- PARAMETRI DA RIGA DI COMANDO ---
RECEIVER_IP="$1"   # IP del receiver
QUALITY="$2"       # high | low
PROTOCOL="$3"      # udp | tcp

if [[ -z "$RECEIVER_IP" || -z "$QUALITY" || -z "$PROTOCOL" ]]; then
    echo "Uso: $0 <receiver_ip> <quality: high|low> <protocol: udp|tcp>"
    exit 1
fi

if [[ "$QUALITY" != "high" && "$QUALITY" != "low" ]]; then
    echo "Errore: qualità deve essere 'high' o 'low'"
    exit 1
fi

if [[ "$PROTOCOL" != "udp" && "$PROTOCOL" != "tcp" ]]; then
    echo "Errore: protocollo deve essere 'udp' o 'tcp'"
    exit 1
fi

# --- CONFIGURAZIONE ---
JETSON_USER="smaug"
JETSON_HOST="10.30.7.213"   # IP del sender
JETSON_SENDER_PATH="/home/smaug/video-stream/video_sender.py"

RECEIVER_PATH="/home/smaug/video-stream/video_receiver.py"

PORT=5000
CAMERA="nvargus"

LOG_SENDER="sender_${QUALITY}_${PROTOCOL}.jsonl"
LOG_RECEIVER="receiver_${QUALITY}_${PROTOCOL}.jsonl"

echo ""
echo "=============================================="
echo "   AVVIO STREAMING"
echo "=============================================="
echo "  Receiver IP: $RECEIVER_IP"
echo "  Qualità:     $QUALITY"
echo "  Protocollo:  $PROTOCOL"
echo "=============================================="
echo ""

############################################################
# 1) Avvia il sender via SSH
############################################################

echo "==> Avvio sender su $JETSON_HOST ..."

ssh -t ${JETSON_USER}@${JETSON_HOST} \
    "DISPLAY=:0 python3 ${JETSON_SENDER_PATH} \
        --host ${RECEIVER_IP} \
        --port ${PORT} \
        --camera ${CAMERA} \
        --quality ${QUALITY} \
        --protocol ${PROTOCOL} \
        --log ${LOG_SENDER}" &

SENDER_SSH_PID=$!

sleep 2
echo "Sender avviato (PID SSH: $SENDER_SSH_PID)"
echo ""

############################################################
# 2) Avvia il receiver localmente
################################################