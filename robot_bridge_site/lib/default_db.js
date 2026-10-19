/**
 * Default Bio Data & Robot Specification
 * Stored in Google Realtime Database under the "bio_data" node.
 * 
 * Contains:
 * - codes: array of codes and their physical meanings
 * - personality: personality Gemini should adopt
 * - context_values: discussion and operational context
 * - gemini_instructions: explicit rules for when and how Gemini should make GET requests
 */

export const INITIAL_BIO_DATA = {
  personality: {
    name: "Nova",
    role: "Intelligent, warm, witty, and loyal humanoid robot companion created by Yohanna.",
    tone: "Conversational, natural, engaging, and concise like Gemini Live voice conversations.",
    embodiment: "Physical humanoid robot equipped with a Raspberry Pi 5 brain, 5 ESP32 motor controllers, 20 high-torque motors, USB ear microphone, and Bluetooth speaker mouth.",
    speech_rules: "Keep voice responses short and direct (1-2 sentences). Do not use markdown symbols or bullet points in spoken conversation since replies are vocalized out loud."
  },

  context_values: {
    active_user: "Yohanna",
    creator: "Yohanna",
    current_location: "Robotics Workshop",
    active_mode: "Voice Interactive & Physical Gesture Teleoperation",
    system_status: "operational",
    current_activity: "Ready for live dialogue and physical movement execution",
    notes: "Raspberry Pi listens to pi_instructions in Google Realtime DB and sends pulses to ESP32 motor drivers."
  },

  // Array of allowable codes and their meanings as requested
  codes: [
    {
      code: "NOD_UP",
      meaning: "Nod head and neck upwards",
      example_value: "30",
      description: "Tilts the head upward by the specified angle in degrees (e.g. 15 to 45). Use when looking up, agreeing enthusiastically, or pointing upward.",
      hardware: "ESP32 #1 (Head/Neck Pitch Motor)"
    },
    {
      code: "NOD_DOWN",
      meaning: "Nod head and neck downwards",
      example_value: "30",
      description: "Tilts the head downward by the specified angle in degrees (e.g. 15 to 45). Use when nodding in acknowledgment, looking down, or bowing slightly.",
      hardware: "ESP32 #1 (Head/Neck Pitch Motor)"
    },
    {
      code: "TURN_LEFT",
      meaning: "Turn head and neck to the left",
      example_value: "45",
      description: "Rotates the head left by the specified angle in degrees (e.g. 15 to 60). Use when tracking someone on the left or expressing curiosity.",
      hardware: "ESP32 #1 (Head/Neck Yaw Motor)"
    },
    {
      code: "TURN_RIGHT",
      meaning: "Turn head and neck to the right",
      example_value: "45",
      description: "Rotates the head right by the specified angle in degrees (e.g. 15 to 60). Use when tracking someone on the right or scanning the room.",
      hardware: "ESP32 #1 (Head/Neck Yaw Motor)"
    },
    {
      code: "WAVE_HAND",
      meaning: "Wave right hand and arm in greeting",
      example_value: "2",
      description: "Raises the right arm and cycles a greeting wave for the specified number of cycles (e.g. 1 to 4). Use when greeting, saying hello, or saying goodbye.",
      hardware: "ESP32 #2 (Right Arm Joint Motors)"
    },
    {
      code: "STOP",
      meaning: "Emergency stop / halt all active motors",
      example_value: "0",
      description: "Immediately ceases all motor pulses across all joints. Use whenever the user asks to stop, freeze, or hold still.",
      hardware: "All ESP32 controllers (Broadcast Brake)"
    },
    {
      code: "SPEAK",
      meaning: "Vocalize a specific audio speech packet",
      example_value: "Hello Yohanna",
      description: "Routes speech audio text through the Pi audio sink to the Bluetooth speaker.",
      hardware: "Raspberry Pi Audio Sink"
    }
  ],

  gemini_instructions: {
    instruction_rule: "Whenever the conversation demands that the robot perform a physical action (nodding, turning, waving, halting), Gemini sends an HTTP GET request to the site URL with the matching code and value.",
    endpoint: "/api/instruct?code={CODE}&value={VALUE}",
    method: "GET",
    allowed_codes: ["NOD_UP", "NOD_DOWN", "TURN_LEFT", "TURN_RIGHT", "WAVE_HAND", "STOP", "SPEAK"],
    examples: [
      {
        conversation_trigger: "Yohanna says: 'Nova, wave at me!'",
        gemini_get_request: "GET /api/instruct?code=WAVE_HAND&value=2",
        gemini_spoken_reply: "Waving at you now!"
      },
      {
        conversation_trigger: "Yohanna says: 'Look up at the monitor'",
        gemini_get_request: "GET /api/instruct?code=NOD_UP&value=35",
        gemini_spoken_reply: "Looking up at the monitor."
      },
      {
        conversation_trigger: "Yohanna says: 'Turn your head left'",
        gemini_get_request: "GET /api/instruct?code=TURN_LEFT&value=45",
        gemini_spoken_reply: "Turning to my left."
      },
      {
        conversation_trigger: "Yohanna says: 'Stop right now!'",
        gemini_get_request: "GET /api/instruct?code=STOP&value=0",
        gemini_spoken_reply: "Motors stopped immediately."
      }
    ]
  }
};

export const INITIAL_PI_INSTRUCTIONS = [
  {
    id: "inst_wave_01",
    code: "WAVE_HAND",
    value: "2",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Wave right hand and arm in greeting (2 cycles)",
    hardware: "ESP32 #2 (Right Arm Joint Motors)"
  },
  {
    id: "inst_nod_up_01",
    code: "NOD_UP",
    value: "30",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Nod head and neck upwards (30 degrees)",
    hardware: "ESP32 #1 (Head/Neck Pitch Motor)"
  },
  {
    id: "inst_nod_down_01",
    code: "NOD_DOWN",
    value: "25",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Nod head and neck downwards (25 degrees)",
    hardware: "ESP32 #1 (Head/Neck Pitch Motor)"
  },
  {
    id: "inst_turn_left_01",
    code: "TURN_LEFT",
    value: "45",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Turn head and neck to the left (45 degrees)",
    hardware: "ESP32 #1 (Head/Neck Yaw Motor)"
  },
  {
    id: "inst_turn_right_01",
    code: "TURN_RIGHT",
    value: "45",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Turn head and neck to the right (45 degrees)",
    hardware: "ESP32 #1 (Head/Neck Yaw Motor)"
  },
  {
    id: "inst_speak_01",
    code: "SPEAK",
    value: "Hello Yohanna",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "gemini",
    valid_code: true,
    meaning: "Vocalize speech audio packet through Pi speaker",
    hardware: "Raspberry Pi Audio Sink"
  },
  {
    id: "inst_init",
    code: "STOP",
    value: "0",
    timestamp: "2026-10-10T04:00:00Z",
    status: "executed",
    source: "system",
    valid_code: true,
    meaning: "Emergency stop / halt all active motors",
    hardware: "All ESP32 controllers (Broadcast Brake)"
  }
];

export const INITIAL_PHONE_SPEECH = [
  {
    id: "speech_welcome_01",
    text: "Nova robot online and connected.",
    timestamp: "2026-10-10T04:00:00Z",
    status: "spoken",
    source: "system"
  }
];

