# macOS Full Disk Access — Required for Background Agents

macOS restricts background processes from accessing Desktop, Documents, and Downloads.
You need to grant **Full Disk Access** to Python so the agents can run as daemons.

## Steps (takes 30 seconds):

1. Open **System Settings** → **Privacy & Security** → **Full Disk Access**
2. Click the **+** button (you may need to unlock with your password)
3. Press **Cmd+Shift+G** and type: `/usr/bin/python3`
4. Select `python3` and click **Open**
5. Also add the venv Python:
   - Press **+** again, then **Cmd+Shift+G**
   - Type: `/Users/marcbyers/Agents/deal-tracker/venv/bin/python`
   - Repeat for each agent's venv Python binary
6. Restart the agents: `~/Agents/control.sh start`

## Alternative (easier):
1. Open **System Settings** → **Privacy & Security** → **Full Disk Access**
2. Click **+** → **Cmd+Shift+G** → type `/Applications/Utilities/Terminal.app`
3. Add Terminal to Full Disk Access
4. Restart Terminal, then run: `~/Agents/control.sh start`

The Terminal option is simpler — it grants all processes launched from Terminal the same access.
