#!/usr/bin/env python3
"""Slack-based questionnaire: send batches of questions, collect answers.

Used during the demo flow to send the CHITRA onboarding questionnaire
via Slack DM instead of displaying it in the IDE.

Usage by the AI agent:
    from skills.slack.questionnaire import run_questionnaire
    answers = run_questionnaire("agents/chitra/knowledge-base/profile-2024.json")
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
from core.config_loader import load_config, kb_path
from skills.slack.adapter import send_message, read_replies, send_progress


BATCH_MERGE = [
    {
        "label": "Jobs & Employment",
        "categories": ["Jobs & Employment"],
    },
    {
        "label": "Investments & Brokerage",
        "categories": ["Investments & Brokerage"],
    },
    {
        "label": "Rental Properties",
        "categories": ["Rental Properties"],
    },
    {
        "label": "Partnerships & Business",
        "categories": ["Partnerships & Investments", "Your Business"],
    },
    {
        "label": "Charitable, Health & Home",
        "categories": ["Charitable Giving", "Health & Insurance", "Your Home"],
    },
    {
        "label": "Life Events & Investments",
        "categories": ["Major Life Events", "Investments & Stock"],
    },
    {
        "label": "Retirement, Insurance, Education & Taxes",
        "categories": ["Retirement & Savings", "Insurance & Health",
                        "Education & Student Loans", "Taxes & Payments"],
    },
]


def _group_by_category(questions):
    """Group questions into 7 merged batches for a natural Slack conversation."""
    by_cat = {}
    for q in questions:
        cat = q["category"]
        if cat not in by_cat:
            by_cat[cat] = []
        by_cat[cat].append(q)

    batches = []
    used = set()
    for batch_def in BATCH_MERGE:
        merged_qs = []
        for cat in batch_def["categories"]:
            merged_qs.extend(by_cat.get(cat, []))
            used.add(cat)
        if merged_qs:
            batches.append((batch_def["label"], merged_qs))

    for cat, qs in by_cat.items():
        if cat not in used:
            batches.append((cat, qs))

    return batches


def format_batch_for_slack(category, questions, start_num):
    """Format a batch of questions as a single Slack message.

    Returns (message_text, end_num).
    """
    icon = questions[0].get("icon", "")
    lines = [f"*{icon} {category}*\n"]

    num = start_num
    for q in questions:
        num += 1
        lines.append(f"*Q{num}.* {q['question']}")
        lines.append(f"    _{q['why']}_")
        if q.get("ifNo"):
            lines.append(f"    If no: {q['ifNo']}")
        if q.get("ifYes"):
            lines.append(f"    If yes: {q['ifYes']}")
        if q.get("examples"):
            lines.append(f"    Examples: {q['examples']}")
        lines.append("")

    lines.append("_Reply with your answers (e.g. \"Q1 yes, Q2 no - she left in Dec, Q3 yes\")_")
    return "\n".join(lines), num


def send_batch(channel, category, questions, start_num):
    """Send one batch of questions to Slack. Returns (message_ts, end_num)."""
    text, end_num = format_batch_for_slack(category, questions, start_num)
    result = send_message(channel, text)
    return result["ts"], end_num


def wait_for_answer(channel, after_ts, user_id, poll_interval=15):
    """Poll for the user's reply after a batch. Waits indefinitely."""
    while True:
        time.sleep(poll_interval)
        messages = read_replies(channel, oldest=after_ts, limit=10)
        for msg in messages:
            if msg["ts"] != after_ts and msg.get("user") == user_id and not msg.get("bot_id"):
                return msg["text"].strip()


def parse_answer_text(text, questions, start_num):
    """Parse free-text answer into structured confirmations.

    Handles formats like:
        "Q1 yes, Q2 no - she left, Q3 yes"
        "yes, no she left in Dec, yes"
        "1. yes 2. no 3. yes"
    """
    results = []
    text_lower = text.lower().strip()

    parts = []
    import re
    split = re.split(r'(?:,\s*|\n+)', text)
    if len(split) >= len(questions):
        parts = split
    else:
        split2 = re.split(r'q\d+[.:\s]+', text_lower)
        parts = [p.strip() for p in split2 if p.strip()]

    if not parts or len(parts) < len(questions):
        parts = [text_lower] if len(questions) == 1 else [text_lower] * len(questions)

    for i, q in enumerate(questions):
        answer_text = parts[i].strip() if i < len(parts) else ""
        is_confirmed = None
        if any(w in answer_text.lower() for w in ["yes", "yeah", "yep", "still", "same", "correct"]):
            is_confirmed = True
        elif any(w in answer_text.lower() for w in ["no", "nope", "left", "closed", "sold", "changed", "switched"]):
            is_confirmed = False

        results.append({
            "question_num": start_num + i + 1,
            "category": q["category"],
            "question": q["question"],
            "raw_answer": answer_text,
            "confirmed": is_confirmed,
            "is_free_text": q.get("freeText", False),
        })

    return results


def generate_questions(profile_path, target_year=None):
    """Generate confirmation + discovery questions from a profile."""
    from agents.chitra.scripts.generate_questionnaire import (
        generate_confirmation_questions,
        generate_discovery_questions,
    )
    with open(profile_path) as f:
        profile = json.load(f)
    prior_year = profile.get("taxYear", 2024)
    year = target_year or (prior_year + 1)
    confirmation = generate_confirmation_questions(profile, year)
    discovery = generate_discovery_questions(profile, year)
    return confirmation + discovery, year


def run_questionnaire(profile_path, target_year=None, dm_channel=None, user_id=None):
    """Full questionnaire loop: generate, send batches, collect answers.

    Args:
        profile_path: Path to the prior-year profile JSON
        target_year: Target tax year (default: profile year + 1)
        dm_channel: Slack DM channel ID (default: from config)
        user_id: Slack user ID (default: from config)

    Returns:
        List of all parsed answer dicts
    """
    cfg = load_config()
    channel = dm_channel or cfg.get("slack", {}).get("dm_channel")
    uid = user_id or cfg.get("slack", {}).get("primary_user_id")

    if not channel or not uid:
        raise RuntimeError("Slack dm_channel and primary_user_id must be configured")

    all_questions, year = generate_questions(profile_path, target_year)
    groups = _group_by_category(all_questions)

    send_message(channel,
        f"I've reviewed your {year - 1} tax return and derived {len(all_questions)} "
        f"expected documents. To make sure I have the complete picture for {year}, "
        f"I have {len(all_questions)} questions in {len(groups)} categories.\n\n"
        f"No tax expertise needed — just answer honestly. Here we go!"
    )
    time.sleep(2)

    all_answers = []
    q_num = 0
    for batch_idx, (category, questions) in enumerate(groups):
        msg_ts, q_num = send_batch(channel, category, questions, q_num)

        answer_text = wait_for_answer(channel, msg_ts, uid)

        parsed = parse_answer_text(answer_text, questions, q_num - len(questions))
        all_answers.extend(parsed)

        remaining = len(groups) - (batch_idx + 1)
        if remaining > 0:
            send_message(channel, f"Got it! {remaining} more {'category' if remaining == 1 else 'categories'} to go.")
            time.sleep(1)

    send_message(channel,
        f"All done! Processing your answers now... "
        f"({len(all_answers)} answers across {len(groups)} categories)"
    )

    answers_path = kb_path(f"user-answers-{year}-demo.json")
    os.makedirs(os.path.dirname(answers_path), exist_ok=True)
    with open(answers_path, "w") as f:
        json.dump({
            "taxYear": year,
            "source": "slack_questionnaire",
            "answers": all_answers,
        }, f, indent=2)

    return all_answers


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default=None)
    parser.add_argument("--target-year", type=int, default=None)
    args = parser.parse_args()

    profile = args.profile
    if not profile:
        for year in range(2024, 2020, -1):
            p = kb_path(f"profile-{year}.json")
            if os.path.exists(p):
                profile = p
                break

    if not profile:
        print("No profile found", file=sys.stderr)
        sys.exit(1)

    answers = run_questionnaire(profile, args.target_year)
    print(f"\nCollected {len(answers)} answers")
    for a in answers:
        status = "YES" if a["confirmed"] else ("NO" if a["confirmed"] is False else "?")
        print(f"  Q{a['question_num']}: [{status}] {a['raw_answer'][:60]}")
