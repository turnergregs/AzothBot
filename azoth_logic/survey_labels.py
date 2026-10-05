"""The in-game survey's words, for the reports that show its answers.

The game repo holds the questions in assets/surveys/questions.json and their
English in assets/localization/translations.csv (SURVEY_*); this is a copy of
that English. A question or answer the bot does not know yet shows its raw id
rather than nothing, so add new ones here when the game adds them. See the game
repo's docs/SURVEYS.md.
"""

# The question as the player read it.
QUESTIONS = {
    "understood": "I understood how to play.",
    "fun": "How fun was that run?",
    "loss": "That loss felt like:",
    "difficulty": "That run's difficulty felt:",
    "choices": "My choices mattered.",
    "length": "That run's length felt:",
    "variety": "Do runs feel different from each other?",
    "fight_fun": "How fun was that fight?",
    "fight_difficulty": "That fight's difficulty felt:",
}

# A few words for a row label.
SHORT = {
    "understood": "Understood",
    "fun": "Fun",
    "loss": "Loss felt",
    "difficulty": "Difficulty",
    "choices": "Choices",
    "length": "Length",
    "variety": "Variety",
    "fight_fun": "Fight fun",
    "fight_difficulty": "Fight difficulty",
}

# The order questions are reported in: the first-run question, then the run
# questions, then the fight ones.
ORDER = ["understood", "fun", "loss", "difficulty", "choices", "length", "variety",
         "fight_fun", "fight_difficulty"]

# Choice questions' answers, in the order the card shows them.
CHOICES = {
    "understood": ["yes", "mostly", "no"],
    "loss": ["bad_luck", "my_fault", "unfair"],
    "difficulty": ["too_easy", "just_right", "too_hard"],
    "fight_difficulty": ["too_easy", "just_right", "too_hard"],
}

ANSWERS = {
    "yes": "Yes", "mostly": "Mostly", "no": "No",
    "bad_luck": "Bad luck", "my_fault": "My fault", "unfair": "Unfair",
    "too_easy": "Too easy", "just_right": "Just right", "too_hard": "Too hard",
}

# A 1-5 question's end labels: {score: label}.
SCALE_ENDS = {
    "fun": {1: "Not fun", 5: "Very fun"},
    "choices": {1: "Disagree", 5: "Agree"},
    "length": {1: "Too short", 3: "Just right", 5: "Too long"},
    "variety": {1: "Samey", 5: "Unique"},
    "fight_fun": {1: "Not fun", 5: "Very fun"},
}

# Where a survey was shown, by `survey_responses.moment`.
MOMENTS = {
    "boss_win": "Act intro",
    "loss": "Death",
    "victory": "Victory",
    "quit_to_menu": "Quit",
    "restart": "Restart",
}
MOMENT_ORDER = ["boss_win", "loss", "victory", "quit_to_menu", "restart"]


def question(question_id) -> str:
    question_id = str(question_id or "")
    return QUESTIONS.get(question_id, question_id or "Survey")


def short(question_id) -> str:
    question_id = str(question_id or "")
    return SHORT.get(question_id, question_id or "Survey")


def is_scale(question_id) -> bool:
    return str(question_id or "") in SCALE_ENDS


def answer(question_id, key) -> str:
    """An answer as the player saw it: "Too hard", or "4/5" for a score."""
    key = str(key or "")
    if is_scale(question_id) and key.isdigit():
        return f"{key}/5"
    return ANSWERS.get(key, key)


def order_key(question_id) -> tuple:
    question_id = str(question_id or "")
    return (ORDER.index(question_id) if question_id in ORDER else len(ORDER), question_id)
