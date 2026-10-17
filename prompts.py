"""Prompts for the local Gemma model. Generic across crafts; the profile fills in the details."""

LANG_HINT = {
    "English": "Write in simple, warm English.",
    "Hinglish": "Write in Hinglish: Hindi written in English letters, mixed naturally with English, "
                "the way people text in Indian cities.",
    "Hindi": "Write in Hindi (Devanagari script). Keep it simple and conversational.",
}

MESSAGE_TYPES = {
    "After a session": {
        "goal": "Thank the client for the session. Refer to one thing from it so it feels personal, but "
                "never repeat sensitive details word-for-word (health, money, relationships) — allude "
                "gently. Offer that they can message if anything comes up. End with a soft invitation to "
                "come again, mentioning where you'll be next if given.",
        "fields": ["notes", "next_spot"],
    },
    "Booking confirmation": {
        "goal": "Confirm the booking clearly: service name, day, time, venue. Ask them to come a few "
                "minutes early. Short and warm.",
        "fields": ["booking"],
    },
    "Gentle nudge": {
        "goal": "It's been a few weeks since their last visit. A no-pressure check-in that says you were "
                "thinking of them, and mentions open slots if given. Never guilt-trip.",
        "fields": ["notes", "next_spot"],
    },
    "Payment reminder": {
        "goal": "A polite, kind reminder that payment for the session is still pending. Include the UPI "
                "ID if given. Never sound demanding.",
        "fields": ["booking"],
    },
}


def _who(p: dict) -> str:
    return (f"{p.get('name') or 'a local creator'}, who offers {(p.get('craft') or 'personal services').lower()} "
            f"at cafes and spots in {p.get('city') or 'the city'}")


def voice_block(samples: str) -> str:
    if not samples or not samples.strip():
        return ""
    return ("\nHere are messages they wrote themselves. Match their tone, sentence length, greetings and "
            f"sign-offs closely:\n---\n{samples.strip()[:2500]}\n---\n")


def message_prompt(p: dict, language: str, kind: str) -> str:
    return (f"You write WhatsApp messages for {_who(p)}. Write in first person as them. "
            f"Message type: {kind}. {MESSAGE_TYPES[kind]['goal']} "
            "2-4 short sentences. Never pushy, no hashtags, at most one emoji. "
            "Do not invent services, prices, times or venues that weren't given. Output only the message. "
            + LANG_HINT[language] + voice_block(p.get("voice_samples", "")))


def social_prompt(p: dict, language: str, platform: str) -> str:
    return (f"You write {platform} posts for {_who(p)}. First person. Make posts feel human and local, "
            "not corporate. Include where and when people can find them if given. Use only the services "
            "and prices listed; never invent offers. "
            + ("Add 5-8 relevant local hashtags at the end. " if platform == "Instagram" else "")
            + "Output only the post. " + LANG_HINT[language] + voice_block(p.get("voice_samples", "")))


def bio_prompt() -> str:
    return ("Write a warm 2-sentence bio for a small local creator's public page, from the notes given. "
            "First person. No hashtags, no emoji overload. Output only the bio.")
