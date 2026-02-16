#!/bin/bash
# Daily Agents Report — runs control.sh report and emails it to Marc

AGENTS_DIR="$HOME/Agents"
REPORT_FILE="/tmp/daily-agents-report.txt"

# Run the report
bash "$AGENTS_DIR/control.sh" report > "$REPORT_FILE" 2>&1

# Email it via Gmail API
cd "$AGENTS_DIR/email-agent"
OAUTHLIB_RELAX_TOKEN_SCOPE=1 venv/bin/python -c "
import base64
from email.mime.text import MIMEText
from auth import get_gmail_service

service = get_gmail_service()
report = open('$REPORT_FILE').read()

msg = MIMEText(report)
msg['to'] = 'm.byers2@gmail.com'
msg['subject'] = 'Power FM — Daily Agents Report (' + __import__('datetime').date.today().isoformat() + ')'

raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
service.users().messages().send(userId='me', body={'raw': raw}).execute()
print('Daily report sent.')
" 2>/dev/null

rm -f "$REPORT_FILE"
