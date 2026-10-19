# Nova Robot Bridge System

Decoupled bridge architecture connecting the **Nova Cloud Bridge Site** (Google Realtime Database / Vercel) to the **Raspberry Pi 5**.

```text
[ Gemini Live / Site ] ──(GET /instruct?code=...&value=.)──► [ENTRY: pi_instructions]
                                                                    ▲
                                                                    │ (Polls cloud site)
                                                        [ CloudBridgeListener ]
                                                                    │
                                                        (Stashes into local file)
                                                                    ▼
                                                    [ instructions.json (Local Queue) ]
                                                                    │
                                                            (Reads queue FIFO)
                                                                    ▼
                                                    [ LocalInstructionExecutor ]
                                                                    │
                                                        (Looks up code in code_mappings.py)
                                                                    ▼
                                                       [ Target Action File Runs ]
                                                        (e.g. actions/wave.py)
                                                                    │
                                                            (Runs completely)
                                                                    ▼
                                                    1. Instruction REMOVED from local file
                                                    2. Bridge site acknowledged as 'executed'
```

---

## 1. Directory Structure

```text
robot/
├── bridge_main.py              # <--- MAIN ENTRY POINT & LOOP
├── bridge_listener.py          # Backwards-compatible alias to bridge_main.py
├── main.py                     # Supports --bridge flag
└── bridge/                     # <--- THE BRIDGE SUBSYSTEM FOLDER
    ├── __init__.py
    ├── config.py               # URLs, polling intervals, timeouts
    ├── storage.py              # Thread-safe atomic local file manager
    ├── listener.py             # Cloud listener: polls [ENTRY: pi_instructions] & stashes
    ├── executor.py             # Queue reader: runs mapped files & removes when done
    ├── instructions.json       # The local instruction queue file
    ├── code_mappings.py        # <--- DEFINE WHICH FILE RUNS FOR EACH CODE HERE
    └── actions/                # Default action scripts executed for each code
        ├── nod.py
        ├── nod_up.py
        ├── nod_down.py
        ├── wave.py
        ├── pick.py
        ├── turn_left.py
        ├── turn_right.py
        ├── motor_move.py
        ├── stop.py
        └── default_action.py
```

---

## 2. Defining Action Files for Codes

To map a new code to a file, simply edit [`robot/bridge/code_mappings.py`](file:///robot/bridge/code_mappings.py):

```python
CODE_TO_FILE = {
    "NOD_UP":     "robot/bridge/actions/nod_up.py",
    "WAVE":       "robot/bridge/actions/wave.py",
    "MY_CODE":    "path/to/my_custom_script.py",
    "DEFAULT":    "robot/bridge/actions/default_action.py",
}
```

### How Action Files Receive Parameters:
When your file is executed:
- **CLI Arguments**:
  - `sys.argv[1]` = instruction code (e.g. `"NOD_UP"`)
  - `sys.argv[2]` = instruction value (e.g. `"30"`)
  - `sys.argv[3]` = instruction ID (e.g. `"inst_1728200_abc"`)
  - `sys.argv[4]` = raw JSON packet string
- **Environment Variables**:
  - `ROBOT_CODE`
  - `ROBOT_VALUE`
  - `ROBOT_INSTRUCTION_ID`

When the action script finishes with **Exit Code 0**, the executor automatically:
1. **Removes the instruction from `instructions.json`**.
2. **Marks the instruction `executed` on the cloud bridge site**.

---

## 3. How to Run

### Option A: From the Robot folder (Direct Entry Point)
```bash
python robot/bridge_main.py https://<your-vercel-site>.vercel.app
```
Or for local testing:
```bash
python robot/bridge_main.py http://localhost:3000
```

### Option B: Via `robot/main.py`
```bash
python robot/main.py --bridge --bridge-url https://<your-vercel-site>.vercel.app
```

### Option C: Independent Service Modes
- **Listener Only** (Only fetches and stashes to `instructions.json`):
  ```bash
  python robot/bridge_main.py --listener-only
  ```
- **Executor Only** (Only reads and executes from `instructions.json`):
  ```bash
  python robot/bridge_main.py --executor-only
  ```
