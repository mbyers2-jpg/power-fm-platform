#!/bin/bash
# Continuous Power FM stream player
# Uses ffmpeg to download chunks and afplay to play them
# Usage: ./play_stream.sh [station_port]

PORT=${1:-8000}
STREAM_URL="http://localhost:${PORT}/stream"
CHUNK_DIR="/tmp/power_fm_chunks"
mkdir -p "$CHUNK_DIR"

echo "Power FM — Now streaming from port $PORT"
echo "Press Ctrl+C to stop"
echo ""

cleanup() {
    echo ""
    echo "Stopping playback..."
    rm -f "$CHUNK_DIR"/chunk_*.wav
    kill %1 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

CHUNK=0
while true; do
    CHUNK=$((CHUNK + 1))
    MP3_FILE="$CHUNK_DIR/chunk_${CHUNK}.mp3"
    WAV_FILE="$CHUNK_DIR/chunk_${CHUNK}.wav"

    # Download 30 seconds of stream
    curl -s "$STREAM_URL" --max-time 30 -o "$MP3_FILE" 2>/dev/null

    # Convert to WAV
    ffmpeg -y -hide_banner -loglevel error -i "$MP3_FILE" -acodec pcm_s16le "$WAV_FILE" 2>/dev/null

    if [ -f "$WAV_FILE" ] && [ -s "$WAV_FILE" ]; then
        # Get now playing info
        NOW=$(curl -s "http://localhost:${PORT}/status.json" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('now_playing',''))" 2>/dev/null)
        echo "Now Playing: $NOW"
        afplay "$WAV_FILE"
    fi

    # Clean up old chunks
    rm -f "$MP3_FILE" "$WAV_FILE"
done
