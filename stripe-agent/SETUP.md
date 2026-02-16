# Stripe Agent Setup

## 1. Get API Key
1. Go to https://dashboard.stripe.com/apikeys
2. Copy your **Secret key** (starts with `sk_live_` or `sk_test_`)
3. For development/testing, use the test mode key (`sk_test_...`)

## 2. Configure
Create `config/stripe_config.json`:
```json
{
    "secret_key": "sk_test_YOUR_SECRET_KEY",
    "webhook_secret": "whsec_YOUR_WEBHOOK_SECRET"
}
```

The `webhook_secret` is optional — only needed if you set up Stripe webhooks.

## 3. Create Virtual Environment
```bash
cd ~/Agents/stripe-agent
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

## Notes
- Use test mode keys (`sk_test_...`) during development
- All monetary amounts are stored in cents (e.g., $9.99 = 999 cents)
- The agent syncs customers, subscriptions, payments, products, prices, and invoices
