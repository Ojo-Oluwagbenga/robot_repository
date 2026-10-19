"""
Instruction Similarity Matcher for Humanoid Robot.
Finds the most similar known robot instruction for any incoming spoken phrase,
typed text, or bridge instruction packet.

Features:
- Fast multi-metric string & token similarity (keywords, aliases, reference phrases)
- Typo and speech recognition imperfection tolerance (SequenceMatcher)
- Stopword filtering to prevent false positives on casual dialogue
- Optional PC Ollama AI integration for semantic intent resolution
- Fallback thresholding to distinguish actions from general chatter
"""

import re
import difflib
from typing import Dict, List, Tuple, Optional, Any

# Stopwords and common prepositions/fillers excluded from phrase matching
STOPWORDS = {
    "the", "that", "this", "it", "is", "a", "an", "for", "me", "you", 
    "can", "could", "to", "us", "your", "our", "and", "or", "in", "on", 
    "at", "my", "please", "would", "be", "with", "from", "of", "about",
    "up", "down", "out", "off", "just", "if", "so", "now", "then", "there"
}

# Master catalog of supported robot instructions
INSTRUCTION_CATALOG = {
    "PICK": {
        "aliases": ["PICK_UP", "GRAB", "LIFT", "GRASP", "TAKE", "HOLD", "FETCH"],
        "keywords": [
            "pick", "picking", "picked", "grab", "grabbing", "grabbed", 
            "grasp", "grasping", "lift", "lifting", "lifted", "take", "taking", 
            "hold", "holding", "fetch", "retrieve"
        ],
        "reference_phrases": [
            "pick up", "pick it up", "pick up object", "can you pick that up",
            "grab that", "grab the cup", "grasp the item", "lift that up",
            "lift the box", "take this", "pick up the bottle", "pick that"
        ],
        "description": "Lower arm, close gripper on object, and lift"
    },
    "NOD": {
        "aliases": ["NOD_UP", "NOD_DOWN", "HEAD_NOD", "TILT_HEAD", "AGREE"],
        "keywords": [
            "nod", "nodding", "nodded", "head", "neck", "tilt", "tilting", 
            "shake", "shaking", "agree", "agreeing", "affirm", "yes"
        ],
        "reference_phrases": [
            "nod your head", "nod head", "nod up", "nod down", "give me a nod",
            "look down and nod", "do you agree nod", "tilt your head", "just nod",
            "shake head or nod", "nod if you understand"
        ],
        "description": "Pitch head/neck downward and return to neutral"
    },
    "WAVE": {
        "aliases": ["WAVE_HAND", "GREET", "HAND_WAVE", "SAY_HELLO", "SALUTE"],
        "keywords": [
            "wave", "waving", "waved", "hand", "arm", "greet", "greeting", 
            "greeted", "hello", "hi", "salute", "saluting", "bye", "goodbye"
        ],
        "reference_phrases": [
            "wave your hand", "wave hand", "wave hello", "give us a wave",
            "greet our guest", "greet everyone", "say hello and wave", "wave at me",
            "raise your arm and wave", "wave goodbye"
        ],
        "description": "Raise arm and oscillate greeting wave"
    },
    "STOP": {
        "aliases": ["HALT", "FREEZE", "BRAKE", "CANCEL"],
        "keywords": [
            "stop", "stopping", "halt", "halting", "freeze", "freezing", 
            "hold still", "cancel", "brake"
        ],
        "reference_phrases": [
            "stop moving", "freeze", "halt all motors", "hold on stop", "emergency stop"
        ],
        "description": "Halt all active motor pulses across all joints"
    }
}


def compute_string_similarity(text: str, candidate_key: str, candidate_data: dict) -> float:
    """
    Computes a normalized similarity score (0.0 to 1.0) between input text
    and a candidate instruction based on:
    - Exact alias and key matching
    - Keyword presence and density
    - Reference phrase overlap (with stopword stripping)
    - Fuzzy word-level token distance for typo/STT imperfection handling
    """
    clean_text = text.lower().strip()
    words = re.findall(r"\b\w+\b", clean_text)
    if not words:
        return 0.0

    meaningful_words = [w for w in words if w not in STOPWORDS]

    # 1. Exact alias or key match (1.0 for perfect identity)
    for alias in candidate_data.get("aliases", []) + [candidate_key]:
        alias_clean = alias.lower().replace("_", " ")
        if clean_text == alias.lower() or clean_text == alias_clean:
            return 1.0
        if re.search(r"\b" + re.escape(alias_clean) + r"\b", clean_text):
            return 0.95

    # 2. Check keyword presence
    matched_kws = [kw for kw in candidate_data.get("keywords", []) if re.search(r"\b" + re.escape(kw) + r"\b", clean_text)]
    kw_score = 0.0
    if matched_kws:
        kw_score = 0.75 + 0.08 * min(len(matched_kws), 3)

    # 3. Check reference phrases containment or meaningful word overlap
    phrase_score = 0.0
    for phrase in candidate_data.get("reference_phrases", []):
        if phrase in clean_text:
            phrase_score = max(phrase_score, 0.92)
        else:
            p_meaningful = [w for w in phrase.split() if w not in STOPWORDS]
            if p_meaningful and meaningful_words:
                overlap = len(set(meaningful_words) & set(p_meaningful))
                if overlap > 0:
                    jaccard = overlap / len(p_meaningful)
                    phrase_score = max(phrase_score, 0.50 + 0.45 * jaccard)

    # 4. Fuzzy word similarity for speech typos or minor misrecognitions (e.g. 'waev', 'pck')
    fuzzy_score = 0.0
    for w in meaningful_words:
        for kw in candidate_data.get("keywords", []):
            if len(w) >= 3 and len(kw) >= 3:
                r = difflib.SequenceMatcher(None, w, kw).ratio()
                if r >= 0.80:
                    fuzzy_score = max(fuzzy_score, 0.70 * r)

    return min(max(kw_score, phrase_score, fuzzy_score), 1.0)


def find_most_similar_instruction(
    text: str, 
    ollama_client: Optional[Any] = None,
    catalog: Optional[dict] = None,
    threshold: float = 0.40
) -> Tuple[Optional[str], float, Dict[str, float], str]:
    """
    Finds the most similar known robot instruction for any incoming instruction text.
    
    Args:
        text: Incoming user speech, typed prompt, or command code.
        ollama_client: Optional Ollama client instance for semantic inference.
        catalog: Instruction catalog to search against (defaults to INSTRUCTION_CATALOG).
        threshold: Minimum similarity score required to trigger an action (default: 0.40).
        
    Returns:
        tuple of:
        - best_instruction: Canonical instruction name (e.g. 'PICK', 'NOD', 'WAVE') or None
        - best_score: Top similarity score (0.0 to 1.0)
        - all_scores: Dictionary of candidate names to similarity scores
        - explanation: Readable description of why this match was selected
    """
    if not text or not text.strip():
        return None, 0.0, {}, "Empty input text"

    cat = catalog or INSTRUCTION_CATALOG
    scores: Dict[str, float] = {}

    for inst_name, inst_data in cat.items():
        s = compute_string_similarity(text, inst_name, inst_data)
        scores[inst_name] = round(s, 3)

    # Rank all candidates by similarity score descending
    sorted_candidates = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_inst, top_score = sorted_candidates[0]

    # Strong direct similarity (>= 0.65) -> Immediate selection
    if top_score >= 0.65:
        return top_inst, top_score, scores, f"High direct similarity ({top_score:.2f}) to [{top_inst}]"

    # If absolutely zero keyword or token overlap exists, it's unrelated dialogue
    if top_score == 0.0:
        return None, 0.0, scores, "Zero similarity to any known robot instruction"

    # PC Ollama fallback for nuanced or indirect conversational phrasing
    if ollama_client and hasattr(ollama_client, "is_online") and ollama_client.is_online():
        prompt = (
            "You are a robot instruction classifier. Find which known robot instruction is MOST SIMILAR to the user's sentence.\n"
            "Known instructions:\n"
            "- PICK: picking up, grabbing, lifting, or taking an object\n"
            "- NOD: nodding head or neck, tilting head up/down, agreeing\n"
            "- WAVE: waving hand or arm, greeting someone, saying hello\n"
            "- STOP: stopping, halting, freezing, or pausing all movement\n"
            "- NONE: completely unrelated chatter without motion\n\n"
            f"Sentence: \"{text}\"\n"
            "Reply with ONLY the single most similar instruction name (PICK, NOD, WAVE, STOP, or NONE):"
        )
        try:
            resp = ollama_client.chat([{"role": "user", "content": prompt}], stream=False).strip().upper()
            ai_word = resp.split()[0].strip(".,!?:;\"'()[]") if resp.split() else ""
            if ai_word in cat:
                scores[ai_word] = max(scores.get(ai_word, 0.0), 0.85)
                return ai_word, scores[ai_word], scores, f"PC AI resolved most similar as [{ai_word}]"
        except Exception:
            pass

    # Standard threshold comparison
    if top_score >= threshold:
        return top_inst, top_score, scores, f"Most similar instruction: [{top_inst}] (score: {top_score:.2f})"

    return None, top_score, scores, f"No instruction match (top: {top_inst} at {top_score:.2f} < {threshold})"
