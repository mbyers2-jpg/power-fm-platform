# Platform Setup Checklist
## Do these steps tomorrow — 10 minutes total

Campaign starts Monday Feb 16. All 24 posts loaded and scheduled.

---

## 1. LinkedIn (3 min)

1. Go to https://www.linkedin.com/company/setup/new/
2. Create a quick company page: name it "PTC Social", pick any industry
3. Go to https://www.linkedin.com/developers/apps/new
4. App name: `Protect The Culture Social`
5. LinkedIn Page: select "PTC Social"
6. Upload any logo, check agreement, Create App
7. Go to **Products** tab → request "Share on LinkedIn" + "Sign In with LinkedIn using OpenID Connect"
8. Go to **Auth** tab → copy **Client ID** and **Client Secret**
9. Add redirect URL: `http://localhost:8338/callback`
10. Give both values to Claude

## 2. Twitter / X (3 min)

1. Go to https://developer.twitter.com/en/portal/dashboard
2. Sign in as @marcbyers
3. Sign up for Free tier if needed (describe use: "book launch campaign automation")
4. Go to your app → Keys and Tokens
5. Copy these 5 values:
   - API Key
   - API Key Secret
   - Bearer Token
   - Access Token
   - Access Token Secret
6. IMPORTANT: Under Settings, set App permissions to "Read and Write"
7. Give all 5 values to Claude

## 3. Facebook + Instagram (4 min)

1. Go to https://developers.facebook.com/apps/
2. Click Create App → Business type
3. App name: `PTC Social`
4. Add "Facebook Login for Business" product
5. Go to Settings > Basic → copy **App ID** and **App Secret**
6. Under Facebook Login > Settings → add redirect URL: `http://localhost:8339/callback`
7. Give App ID and App Secret to Claude
8. Make sure your IG account (@marcbyers or @protecttheculture) is set to Business/Creator in IG settings

---

## After giving credentials to Claude:

Claude will save them and trigger browser auth flows for you to click "Allow".
Then run: `cd ~/Agents/social-media-agent && venv/bin/python agent.py --status`
