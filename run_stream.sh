#!/bin/bash
############################################################
# run_stream.sh
# Wrapper function to run streaming between cameras on a
# remote drone and the server where this code is executed.
# The function opens the channel both locally and remotely,
# on the drone, sets the quality of the video and the 
# transmission protocol. Here are the necessary information
# to execute it.
# 
#
#
# - For UDP traffic
#   ./run_stream.sh <local_ip> <quality> udp <file_tag>
#
# - For TCP traffic
#   ./run_stream.sh <remote_ip> <quality> tcp <file_tag>
#
# - <quality>  may be high/low
# - <file_tag> could be any string and it will be attached to
#              saved files name.
#
# author: Michelangelo Guaitolini, 02.2026
#
# ----------------------------------------------------------
# UDP: ./run_stream.sh 10.30.7.46 high udp <tag>
# TCP: ./run_stream.sh 10.30.7.12:1122 high tcp <tag>
# ----------------------------------------------------------
############################################################

RECEIVER_IP="$1"
QUALITY="$2"
PROTOCOL="$3"
TAG="$4"

# Parsing opzionale formato ip:porta
if [[ "$RECEIVER_IP" == *:* ]]; then
    JETSON_HOST="${RECEIVER_IP%%:*}"
    JETSON_PORT="${RECEIVER_IP##*:}"
fi

if [[ -z "$RECEIVER_IP" || -z "$QUALITY" || -z "$PROTOCOL" || -z "$TAG" ]]; then
    echo "Uso: $0 <receiver_ip> <quality: high|low> <protocol: udp|tcp> <tag>"
    exit 1
fi

JETSON_USER="smaug"
JETSON_HOST="10.30.7.12"
JETSON_PORT="1122"

JETSON_SENDER_PATH="/home/smaug/video-stream/video_sender.py"
RECEIVER_PATH="/home/adminsssa/video-stream/video_receiver.py"

PORT=5000
LOCAL_PORT=5001
CAMERA="nvargus"

LOG_SENDER="sender_${TAG}.jsonl"
LOG_RECEIVER="receiver_${TAG}.jsonl"
PID_FILE="/tmp/sender_pid_${TAG}.txt"
TUNNEL_PID_FILE="/tmp/ssh_tunnel_${TAG}.pid"

echo ""
echo "=============================================="
echo "   AVVIO STREAMING"
echo "=============================================="
echo "  Receiver IP: $RECEIVER_IP"
echo "  Qualità:     $QUALITY"
echo "  Protocollo:  $PROTOCOL"
echo "  Tag:         $TAG"
echo "  Porta locale: $LOCAL_PORT -> Jetson:$PORT"
echo "=============================================="
echo ""

############################################################
# 0) PULIZIA COMPLETA
############################################################
echo "==> Pulizia completa processi e tunnel..."

ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "pkill -9 -f video_sender.py 2>/dev/null"

pkill -9 -f "ssh.*${JETSON_USER}@${JETSON_HOST}.*-L" 2>/dev/null
pkill -9 -f "ssh.*-L.*${JETSON_HOST}" 2>/dev/null

rm -f "$TUNNEL_PID_FILE" 2>/dev/null

sleep 2

echo "==> Verifica porte locali..."
for port in 5000 5001 5002; do
    PORT_IN_USE=$(ss -tuln 2>/dev/null | grep ":${port} " | wc -l)
    if [[ "$PORT_IN_USE" -gt 0 ]]; then
        echo "? Porta locale ${port} ancora occupata, tento di liberarla..."
        PID_ON_PORT=$(sudo lsof -ti:${port} 2>/dev/null)
        if [[ -n "$PID_ON_PORT" ]]; then
            echo "  Killing PID $PID_ON_PORT sulla porta ${port}"
            sudo kill -9 $PID_ON_PORT 2>/dev/null
        fi
    fi
done

sleep 1

############################################################
# 1) Avvia SENDER
############################################################

echo "==> Avvio sender su Jetson..."

if [[ "$PROTOCOL" == "udp" ]]; then
    ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "
        nohup bash -c '
            cd /home/smaug/video-stream && \
            DISPLAY=:0 python3 ${JETSON_SENDER_PATH} \
                --host ${RECEIVER_IP} \
                --port ${PORT} \
                --camera ${CAMERA} \
                --quality ${QUALITY} \
                --protocol udp \
                --log ${LOG_SENDER} \
                & echo \$! > ${PID_FILE}
        ' > /home/smaug/video-stream/sender_${TAG}.out 2>&1 &
    "
else
    echo "==> Configurazione TCP: sender ascolterà su 0.0.0.0:${PORT}"
    ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "
        nohup bash -c '
            cd /home/smaug/video-stream && \
            DISPLAY=:0 python3 ${JETSON_SENDER_PATH} \
                --host 0.0.0.0 \
                --port ${PORT} \
                --camera ${CAMERA} \
                --quality ${QUALITY} \
                --protocol tcp \
                --log ${LOG_SENDER} \
                & echo \$! > ${PID_FILE}
        ' > /home/smaug/video-stream/sender_${TAG}.out 2>&1 &
    "
fi

sleep 3

echo "==> Verifico avvio sender..."
SENDER_PID=$(ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "cat ${PID_FILE} 2>/dev/null")

if [[ -z "$SENDER_PID" ]]; then
    echo "? ERRORE: PID del sender non trovato!"
    exit 1
fi

echo "? Sender avviato con PID: $SENDER_PID"

SENDER_RUNNING=$(ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "ps -p ${SENDER_PID} >/dev/null 2>&1 && echo 'yes' || echo 'no'")
if [[ "$SENDER_RUNNING" != "yes" ]]; then
    echo "? ERRORE: Il sender è terminato!"
    ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "cat /home/smaug/video-stream/sender_${TAG}.out"
    exit 1
fi

############################################################
# 2) Setup SSH tunnel per TCP (OTTIMIZZATO)
############################################################

if [[ "$PROTOCOL" == "tcp" ]]; then
    echo "==> Attesa apertura porta TCP ${PORT} sul sender..."

    MAX_WAIT=10
    WAITED=0

    while true; do
        PORT_OPEN=$(ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} \
            "ss -tuln 2>/dev/null | grep -E '(:${PORT}\s|:${PORT}$)' | grep LISTEN || netstat -tuln 2>/dev/null | grep -E '(:${PORT}\s|:${PORT}$)' | grep LISTEN" \
            | wc -l)
        
        if [[ "$PORT_OPEN" -gt 0 ]]; then
            echo ""
            echo "? Porta TCP ${PORT} aperta sulla Jetson"
            break
        fi
        
        sleep 0.5
        WAITED=$(echo "$WAITED + 0.5" | bc)

        if (( $(echo "$WAITED >= $MAX_WAIT" | bc -l) )); then
            echo ""
            echo "? Timeout: porta ${PORT} non trovata"
            exit 1
        fi
        
        echo -n "."
    done

    LOCAL_PORT_CHECK=$(ss -tuln 2>/dev/null | grep ":${LOCAL_PORT} " | wc -l)
    if [[ "$LOCAL_PORT_CHECK" -gt 0 ]]; then
        echo "? ERRORE: Porta locale ${LOCAL_PORT} ancora occupata dopo la pulizia!"
        exit 1
    fi

    echo "==> Creazione tunnel SSH ottimizzato: 127.0.0.1:${LOCAL_PORT} -> Jetson:${PORT}"
    
    # OTTIMIZZAZIONI SSH TUNNEL:
    # - Compression=no: disabilita compressione (CPU overhead)
    # - TCPKeepAlive=yes: mantiene connessione attiva
    # - ServerAliveInterval=10: ping ogni 10s
    # - IPQoS=throughput: priorità throughput su latenza
    ssh -4 -f -N \
        -o ExitOnForwardFailure=yes \
        -o Compression=no \
        -o TCPKeepAlive=yes \
        -o ServerAliveInterval=10 \
        -o ServerAliveCountMax=3 \
        -o IPQoS=throughput \
        -L ${LOCAL_PORT}:localhost:${PORT} \
        -p ${JETSON_PORT} \
        ${JETSON_USER}@${JETSON_HOST} 2>&1 | tee /tmp/ssh_tunnel_error_${TAG}.log &
    
    TUNNEL_PID=$!
    echo $TUNNEL_PID > "$TUNNEL_PID_FILE"
    
    echo "? Tunnel SSH avviato (PID: $TUNNEL_PID)"
    sleep 3
    
    if ! kill -0 $TUNNEL_PID 2>/dev/null; then
        echo "? ERRORE: Tunnel SSH terminato immediatamente!"
        cat /tmp/ssh_tunnel_error_${TAG}.log 2>/dev/null
        exit 1
    fi
    
    LOCAL_PORT_LISTENING=$(ss -tuln 2>/dev/null | grep "127.0.0.1:${LOCAL_PORT}" | wc -l)
    if [[ "$LOCAL_PORT_LISTENING" -eq 0 ]]; then
        echo "? ERRORE: Porta locale ${LOCAL_PORT} non in ascolto!"
        exit 1
    fi
    
    echo "? Tunnel SSH attivo e ottimizzato"
fi

############################################################
# 3) Avvia RECEIVER (foreground)
############################################################

echo "==> Avvio receiver locale..."

if [[ "$PROTOCOL" == "udp" ]]; then
    python3 ${RECEIVER_PATH} \
        --port ${PORT} \
        --protocol udp \
        --log ${LOG_RECEIVER}
else
    echo "==> Receiver si connette a 127.0.0.1:${LOCAL_PORT}"
    python3 ${RECEIVER_PATH} \
        --host 127.0.0.1 \
        --port ${LOCAL_PORT} \
        --protocol tcp \
        --log ${LOG_RECEIVER}
fi

RECEIVER_EXIT=$?

############################################################
# 4) Cleanup
############################################################

echo ""
echo "==> Receiver terminato (exit ${RECEIVER_EXIT})."

if [[ "$PROTOCOL" == "tcp" && -f "$TUNNEL_PID_FILE" ]]; then
    TUNNEL_PID=$(cat "$TUNNEL_PID_FILE")
    echo "==> Chiusura tunnel SSH..."
    kill -9 $TUNNEL_PID 2>/dev/null
    rm -f "$TUNNEL_PID_FILE"
fi

echo "==> Arresto sender..."
ssh -p ${JETSON_PORT} ${JETSON_USER}@${JETSON_HOST} "
    if [[ -f ${PID_FILE} ]]; then
        PID=\$(cat ${PID_FILE})
        if ps -p \$PID > /dev/null 2>&1; then
            kill -2 \$PID 2>/dev/null
            sleep 1
            if ps -p \$PID > /dev/null 2>&1; then
                kill -9 \$PID 2>/dev/null
            fi
        fi
    fi
"

pkill -9 -f "ssh.*${JETSON_USER}@${JETSON_HOST}.*-L.*${LOCAL_PORT}" 2>/dev/null

echo "==> Fine."