#!/bin/bash
# Play an M3U playlist through speakers
# Usage: ./play_playlist.sh <playlist.m3u>

PLAYLIST="$1"
if [ -z "$PLAYLIST" ] || [ ! -f "$PLAYLIST" ]; then
    echo "Usage: $0 <playlist.m3u>"
    exit 1
fi

echo "Playing playlist: $PLAYLIST"
echo "Press Ctrl+C to stop"
echo ""

cleanup() {
    echo ""
    echo "Playback stopped."
    pkill -P $$ afplay 2>/dev/null
    exit 0
}
trap cleanup SIGINT SIGTERM

TRACK=0
grep -v '^#' "$PLAYLIST" | while read -r filepath; do
    [ -z "$filepath" ] && continue
    [ ! -f "$filepath" ] && continue
    TRACK=$((TRACK + 1))

    # Get title from the EXTINF line above
    TITLE=$(grep -B1 "$filepath" "$PLAYLIST" | grep '#EXTINF' | sed 's/#EXTINF:-1,//')

    echo "Track $TRACK: $TITLE"

    # Convert to WAV and play
    WAV_FILE="/tmp/playlist_track.wav"
    ffmpeg -y -hide_banner -loglevel error -i "$filepath" -acodec pcm_s16le "$WAV_FILE" 2>/dev/null
    afplay "$WAV_FILE"
done

echo ""
echo "Playlist complete."
