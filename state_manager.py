import json
import os

STATE_FILE = "user_data.json"

def init_state():
    """Creates the JSON file with a default structure if it doesn't exist."""
    if not os.path.exists(STATE_FILE):
        default_state = {
            "user_query": "",
            "user_profile": "",
            "schedule_data": {
                day: {f"{h:02d}:00": "-" for h in range(6, 24)}
                for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            },
            "plan_drafts": {
                "nutrition": [],
                "fitness": []
            },
            "missing_info": [],
            "status": "idle"
        }
        save_state(default_state)
        return default_state
    return load_state()

def load_state():
    """Reads the current state from the JSON file."""
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return init_state()

def save_state(state_data):
    """Writes the updated state back to the JSON file."""
    with open(STATE_FILE, 'w') as f:
        json.dump(state_data, f, indent=4)

def update_user_profile(profile_text):
    """Specific helper to update just the user profile."""
    state = load_state()
    state["user_profile"] = profile_text
    save_state(state)

def update_schedule(schedule_dict):
    """Specific helper to update just the schedule."""
    state = load_state()
    state["schedule_data"] = schedule_dict
    save_state(state)