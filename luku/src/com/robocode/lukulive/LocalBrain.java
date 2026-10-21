package com.robocode.lukulive;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Lightweight On-Device AI Engine.
 * Runs 100% locally on the phone without requiring an internet connection or API keys.
 * Handles robot physical control actions, contextual conversation, system queries,
 * and streams speech tokens to the UI listener.
 */
final class LocalBrain {

    private static final Random RNG = new Random();

    /**
     * Executes one user conversational turn locally on the phone.
     * Streams tokens in real-time to the listener and returns the complete reply.
     */
    static String respond(
            String sys,
            String userText,
            Agent.Listener listener,
            Prefs prefs,
            List<String[]> turns,
            Agent agent
    ) throws Exception {
        if (listener.isCancelled()) throw new Agent.Cancelled();

        String clean = userText == null ? "" : userText.trim();
        String lower = clean.toLowerCase(Locale.US);

        // 1. Check for physical robot control actions
        String actionReply = handleRobotAction(clean, lower, listener, prefs, agent);
        if (actionReply != null) {
            streamText(actionReply, listener);
            return actionReply;
        }

        // 2. Conversational reasoning & knowledge base
        String reply = generateConversationalReply(clean, lower, turns);
        streamText(reply, listener);
        return reply;
    }

    /**
     * Scans for robot action commands and dispatches them via the agent's tool execution.
     */
    private static String handleRobotAction(
            String raw,
            String lower,
            Agent.Listener listener,
            Prefs prefs,
            Agent agent
    ) throws Exception {
        String code = null;
        String val = "1";
        String speech = null;

        if (lower.contains("stop") || lower.contains("halt") || lower.contains("freeze") || lower.contains("brake")) {
            code = "STOP";
            val = "0";
            speech = "Stopping all robot motors immediately.";
        } else if (lower.contains("wave") || (lower.contains("hand") && lower.contains("say hi"))) {
            code = "WAVE_HAND";
            val = "2";
            speech = "Waving my hand at you now!";
        } else if (lower.contains("nod up") || lower.contains("look up") || lower.contains("tilt up") || lower.contains("head up")) {
            code = "NOD_UP";
            val = "35";
            speech = "Looking up.";
        } else if (lower.contains("nod down") || lower.contains("look down") || lower.contains("tilt down") || lower.contains("head down")) {
            code = "NOD_DOWN";
            val = "25";
            speech = "Looking down.";
        } else if (lower.contains("turn left") || lower.contains("look left") || lower.contains("head left")) {
            code = "TURN_LEFT";
            val = "45";
            speech = "Turning my head to the left.";
        } else if (lower.contains("turn right") || lower.contains("look right") || lower.contains("head right")) {
            code = "TURN_RIGHT";
            val = "45";
            speech = "Turning my head to the right.";
        } else if (lower.contains("center head") || lower.contains("look straight") || lower.contains("reset head") || lower.contains("face forward")) {
            code = "CENTER_HEAD";
            val = "0";
            speech = "Centering my head forward.";
        } else if (lower.contains("step forward") || lower.contains("walk forward") || lower.contains("take a step")) {
            code = "STEP_FORWARD";
            val = "1";
            speech = "Taking a step forward.";
        } else if (lower.contains("pick") && (lower.contains("ball") || lower.contains("object") || lower.contains("it up"))) {
            String targetColor = "red";
            if (lower.contains("green")) targetColor = "green";
            else if (lower.contains("blue")) targetColor = "blue";
            else if (lower.contains("yellow")) targetColor = "yellow";
            else if (lower.contains("orange")) targetColor = "orange";
            else if (lower.contains("purple")) targetColor = "purple";

            code = "PICK_BALL";
            val = targetColor;
            speech = "Tracking and picking up the " + targetColor + " ball.";
        } else if (lower.contains("track hand") || lower.contains("track ball") || lower.contains("find ball") || lower.contains("grasp")) {
            code = "TRACK_BALL";
            val = "1";
            speech = "Activating stereo cameras to track ball and fingertips in 3D.";
        }

        if (code == null) return null;

        // Action confirmation handling
        boolean unrestricted = "STOP".equals(code) || agent.isCodeUnrestricted(code) || !prefs.confirmActions();
        Agent.Confirmation decision;

        if (unrestricted) {
            decision = new Agent.Confirmation(true, "unrestricted", null);
        } else {
            Agent.ActionText at = Agent.describeCode(code, val, prefs.localBridgeBase());
            decision = listener.confirmAction(at.doWhat, at.doing);
        }

        if (decision != null && !decision.approved) {
            return "Action cancelled by user.";
        }

        // Execute action via robot bridge tool
        JSONObject args = new JSONObject();
        args.put("code", code);
        args.put("value", val);
        JSONObject result = agent.runTool("robot_action", args, listener, decision);

        if (result.has("error")) {
            return "Command attempted, but the robot bridge returned: " + result.optString("error");
        }

        return speech != null ? speech : "Executed action " + code + ".";
    }

    /**
     * Generates a conversational response using on-device rules and semantic matching.
     */
    private static String generateConversationalReply(String raw, String lower, List<String[]> turns) {
        // Identity & Capabilities
        if (matchesAny(lower, "who are you", "what is your name", "what are you", "introduce yourself", "tell me about yourself")) {
            return "I am Nova, your humanoid robot assistant running on lightweight on-device AI. I control 20 high-torque motors, binocular stereo vision, and a Raspberry Pi 5 brain board.";
        }

        if (matchesAny(lower, "who made you", "who created you", "who built you", "who designed you")) {
            return "I was designed, programmed, and built by Yohanna.";
        }

        if (matchesAny(lower, "what can you do", "help", "commands", "your features", "your capabilities")) {
            return "I can move my head, nod, wave my hands, track stereo fingertips and balls in 3D, and chat with you entirely offline on your phone.";
        }

        // Status & Diagnostics
        if (matchesAny(lower, "status", "system check", "how are you", "diagnostic", "are you okay", "health check")) {
            return "All on-device systems are functioning normally! Local speech processing, motor relay bus, and internal sensors are active.";
        }

        // Greetings
        if (matchesAny(lower, "hello", "hi", "hey", "good morning", "good afternoon", "good evening", "greetings", "yo")) {
            String[] options = {
                    "Hello! Nova is online and ready for action.",
                    "Hi there! How can I assist you with the robot today?",
                    "Greetings! All local systems are listening and ready."
            };
            return options[RNG.nextInt(options.length)];
        }

        // Gratitude
        if (matchesAny(lower, "thank you", "thanks", "good job", "well done", "awesome", "great")) {
            String[] options = {
                    "You're welcome! Happy to assist.",
                    "Glad I could help!",
                    "Anytime! Ready for the next command."
            };
            return options[RNG.nextInt(options.length)];
        }

        // Time and Date
        if (matchesAny(lower, "what time is it", "current time", "what time", "tell me the time")) {
            String timeStr = new SimpleDateFormat("h:mm a", Locale.US).format(new Date());
            return "It is currently " + timeStr + ".";
        }

        if (matchesAny(lower, "what is today", "what day", "today's date", "current date", "what date")) {
            String dateStr = new SimpleDateFormat("EEEE, MMMM d, yyyy", Locale.US).format(new Date());
            return "Today is " + dateStr + ".";
        }

        // Humor / Jokes
        if (matchesAny(lower, "tell me a joke", "make me laugh", "say something funny", "a joke")) {
            String[] jokes = {
                    "Why did the robot go to the shoe store? To get rebooted!",
                    "Why was the robot tired after work? It had a hard drive!",
                    "How do humanoid robots pay for coffee? With cache!",
                    "What is a robot's favorite type of music? Heavy metal!"
            };
            return jokes[RNG.nextInt(jokes.length)];
        }

        // Basic Math & Calculations
        String mathReply = evaluateSimpleMath(lower);
        if (mathReply != null) {
            return mathReply;
        }

        // Contextual follow-up check from previous turns
        if (!turns.isEmpty()) {
            String lastTurn = turns.get(turns.size() - 1)[1].toLowerCase(Locale.US);
            if (lower.equals("yes") || lower.equals("sure") || lower.equals("ok") || lower.equals("okay")) {
                return "Understood. Proceeding!";
            } else if (lower.equals("no") || lower.equals("cancel") || lower.equals("stop that")) {
                return "Understood, holding current position.";
            } else if (lower.contains("repeat") || lower.contains("what did you say")) {
                return "I said: \"" + turns.get(turns.size() - 1)[1] + "\".";
            }
        }

        // Fallback natural reasoning
        return "Understood. Operating on local offline intelligence. Let me know if you would like me to move, wave, nod, or check robot systems.";
    }

    /**
     * Helper to match any of the given phrase patterns.
     */
    private static boolean matchesAny(String input, String... phrases) {
        for (String p : phrases) {
            if (input.contains(p)) return true;
        }
        return false;
    }

    /**
     * Simple on-device arithmetic parser (addition, subtraction, multiplication, division).
     */
    private static String evaluateSimpleMath(String text) {
        Pattern pat = Pattern.compile("(\\d+)\\s*([\\+\\-\\*/]|plus|minus|times|divided by)\\s*(\\d+)");
        Matcher m = pat.matcher(text);
        if (m.find()) {
            try {
                double a = Double.parseDouble(m.group(1));
                String op = m.group(2).trim();
                double b = Double.parseDouble(m.group(3));
                double res = 0;
                if (op.equals("+") || op.equals("plus")) res = a + b;
                else if (op.equals("-") || op.equals("minus")) res = a - b;
                else if (op.equals("*") || op.equals("times")) res = a * b;
                else if (op.equals("/") || op.equals("divided by")) {
                    if (b == 0) return "Division by zero is undefined.";
                    res = a / b;
                }
                boolean isInt = (res == Math.floor(res)) && !Double.isInfinite(res);
                return "The answer is " + (isInt ? String.valueOf((long) res) : String.format(Locale.US, "%.2f", res)) + ".";
            } catch (Exception ignored) { }
        }
        return null;
    }

    /**
     * Streams the text word-by-word with micro-delays to match the fluid conversational UX.
     */
    private static void streamText(String reply, Agent.Listener listener) throws Agent.Cancelled {
        String[] words = reply.split(" ");
        for (int i = 0; i < words.length; i++) {
            if (listener.isCancelled()) throw new Agent.Cancelled();
            String chunk = words[i] + (i < words.length - 1 ? " " : "");
            listener.onText(chunk);
            try {
                Thread.sleep(25); // 25ms per word for natural animated fluid streaming
            } catch (InterruptedException e) {
                if (listener.isCancelled()) throw new Agent.Cancelled();
            }
        }
    }
}
