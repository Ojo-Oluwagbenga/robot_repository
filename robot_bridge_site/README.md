# Nova Robot Bridge — Realtime Database Site

Minimalist cloud database bridge connecting **Gemini Live** and **Raspberry Pi 5**.
Hosted on **Vercel** and powered by **Google Firebase Realtime Database**.

---

## 1. System Architecture

```text
[ Gemini Live ] ──(Reads bio_data & persona)──► [ Vercel Next.js Bridge ] ◄──(Syncs Realtime)──► [ Google Realtime DB ]
[ Gemini Live ] ──(GET /instruct?code=...&val=.)─► [ pi_instructions ]
                                                            ▲
                                                            │ (Polls / Listens)
                                                    [ Raspberry Pi 5 ] ──► [ ESP32 Motor Controllers ]
```

### Database Entries:
1. `bio_data`:
   - `personality`: Voice tone, persona (warm, witty, concise, loyal companion), speech rules.
   - `context_values`: Discussion context, location, creator (Yohanna), active status.
   - `codes`: Array of valid codes (`NOD_UP`, `NOD_DOWN`, `TURN_LEFT`, `TURN_RIGHT`, `WAVE_HAND`, `STOP`, `SPEAK`), physical hardware mapping, and example values.
   - `gemini_instructions`: Explicit trigger rules for issuing GET requests.
2. `pi_instructions`:
   - Array/queue of instruction packets dispatched by Gemini.
   - Stores `id`, `code`, `value`, `timestamp`, `status` (`pending` / `executed`), `source`.

---

## 2. API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/bio_data` | `GET` | Returns robot specification & bio data (JSON) |
| `/bio_data?format=text` | `GET` | Returns clean plain-text format for easy reading |
| `/instruct?code=NOD_UP&value=30` | `GET` | Dispatches command packet into `pi_instructions` |
| `/api/instruct` | `POST` | Accepts JSON `{ "code": "WAVE_HAND", "value": "2" }` |
| `/pi_instructions` | `GET` | Returns full instruction history |
| `/pi_instructions?pending=true` | `GET` | Returns only pending instructions for Pi |
| `/pi_instructions?ack=<ID>` | `GET` | Marks instruction `<ID>` as executed by Pi |
| `/raw` | `GET` | Returns entire database state in raw text/JSON |

---

## 3. Connecting Google Realtime Database

By default, the application runs in high-performance **Local Cache Mode** (works out-of-the-box for testing).
To link your live **Google Firebase Realtime Database**:

1. Go to the [Firebase Console](https://console.firebase.google.com/) and create a project.
2. Under **Build**, select **Realtime Database** and click **Create Database**.
3. Choose your database rules (test mode or authenticated):
   ```json
   {
     "rules": {
       ".read": true,
       ".write": true
     }
   }
   ```
4. Copy your Database URL:
   `https://<your-project-id>-default-rtdb.firebaseio.com`
5. Set environment variable in Vercel or locally in `.env.local`:
   ```bash
   FIREBASE_DATABASE_URL=https://<your-project-id>-default-rtdb.firebaseio.com
   # Optional if authentication is enabled:
   # FIREBASE_DATABASE_SECRET=your_database_secret_here
   ```

---

## 4. Deploying to Vercel

### Option A: Using Vercel CLI (Quickest)
```bash
npm install -g vercel
cd "c:\Users\YOHANNA\Desktop\Projects\Robot Code\robot_bridge_site"
vercel
```

### Option B: Deploying via GitHub
1. Push `robot_bridge_site` to a GitHub repository.
2. Log into [vercel.com](https://vercel.com) and click **Add New > Project**.
3. Import the repository.
4. Under **Environment Variables**, add:
   - `FIREBASE_DATABASE_URL` = `https://<your-project-id>-default-rtdb.firebaseio.com`
5. Click **Deploy**.

---

## 5. Running the Raspberry Pi Hardware Listener

On the Raspberry Pi (or locally):
```bash
python robot/bridge_listener.py https://<your-vercel-site>.vercel.app
```

The listener continuously polls `/api/pi_instructions?pending=true`, maps commands to physical motors, and marks them `executed` automatically.
