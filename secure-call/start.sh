#!/bin/bash
# Ribbon — Start SFU + Flask server

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "Starting Ribbon..."

# Check Node.js dependencies
if [ ! -d "sfu/node_modules" ]; then
    echo "Installing SFU dependencies..."
    cd sfu && npm install && cd ..
fi

# Check Python venv
if [ ! -d "venv" ]; then
    echo "Creating Python venv..."
    python3 -m venv venv
    venv/bin/pip install -r requirements.txt
fi

# Ensure data dirs exist
mkdir -p data/uploads logs

# Start mediasoup SFU
echo "Starting mediasoup SFU..."
cd sfu
node server.js > ../logs/sfu.log 2>&1 &
SFU_PID=$!
echo $SFU_PID > ../sfu.pid
cd ..
echo "  SFU started (PID: $SFU_PID)"

# Wait for SFU socket
echo "  Waiting for SFU socket..."
for i in $(seq 1 10); do
    if [ -S "sfu/mediasoup.sock" ]; then
        echo "  SFU socket ready"
        break
    fi
    sleep 0.5
done

# Start Flask dashboard
echo "Starting Flask dashboard..."
venv/bin/python dashboard.py > logs/dashboard.log 2>&1 &
DASH_PID=$!
echo $DASH_PID > dashboard.pid
echo "  Dashboard started (PID: $DASH_PID)"

sleep 1

echo ""
echo "Ribbon is running!"
echo "  Dashboard: http://localhost:5558"
echo "  SFU PID:   $SFU_PID"
echo "  Flask PID: $DASH_PID"
echo ""
echo "Stop with: ./stop.sh"
