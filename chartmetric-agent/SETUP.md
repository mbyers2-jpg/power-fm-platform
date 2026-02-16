# Chartmetric Agent Setup

## 1. Get API Access
1. Sign up at https://app.chartmetric.com
2. Go to Account → API → Generate API Key
3. Note your API key and refresh token

## 2. Configure
Create `config/chartmetric_config.json`:
```json
{
    "api_key": "YOUR_API_KEY",
    "refresh_token": "YOUR_REFRESH_TOKEN"
}
```

## 3. Create Virtual Environment
```bash
cd ~/Agents/chartmetric-agent
python3 -m venv venv
venv/bin/pip install requests
```

## 4. Test
```bash
venv/bin/python agent.py --report
```

## 5. Start Daemon
```bash
./start.sh
```
