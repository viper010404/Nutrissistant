import json
import os
from datetime import datetime, timezone
from uuid import uuid4

STATE_FILE = "user_data.json"


def _utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def _build_workout_record(fitness_draft, source_query="", response_text="", scheduled_slots=None, source="agent", existing_id=None):
    return {
        "id": existing_id or f"workout_{uuid4().hex[:12]}",
        "created_at": _utc_timestamp(),
        "source": source,
        "source_query": source_query,
        "response_text": response_text,
        "scheduled_slots": scheduled_slots or [],
        "routine_id": None,
        "unit_index": None,
        "unit_day": None,
        "focus_type": None,
        "draft": fitness_draft,
    }


def _build_routine_record(
    routine_name,
    goal,
    source_query="",
    response_text="",
    units=None,
    source="agent",
    existing_id=None,
    version=1,
):
    return {
        "id": existing_id or f"routine_{uuid4().hex[:12]}",
        "created_at": _utc_timestamp(),
        "source": source,
        "source_query": source_query,
        "response_text": response_text,
        "routine_name": routine_name or "Weekly Routine",
        "goal": goal or "general fitness",
        "version": version,
        "units": units or [],
    }


def _normalize_state_structure(state):
    """Normalizes legacy state shapes so workout data can be rendered reliably."""
    changed = False

    context_keys = [
        "allergies", "dietary_restrictions", "general_meal_restrictions", 
        "injuries", "equipment", "general_workout_restrictions"
    ]
    for key in context_keys:
        if key not in state or not isinstance(state[key], list):
            state[key] = []
            changed = True

    if "plan_drafts" not in state or not isinstance(state["plan_drafts"], dict):
        state["plan_drafts"] = {"nutrition": [], "fitness": {}}
        changed = True

    if "nutrition" not in state["plan_drafts"]:
        state["plan_drafts"]["nutrition"] = []
        changed = True

    if "fitness" not in state["plan_drafts"]:
        state["plan_drafts"]["fitness"] = {}
        changed = True

    fitness_val = state["plan_drafts"]["fitness"]
    if not isinstance(fitness_val, dict):
        state["plan_drafts"]["fitness"] = {
            "legacy_value": fitness_val,
            "migrated": True
        }
        changed = True

    if "workouts" not in state or not isinstance(state["workouts"], dict):
        state["workouts"] = {
            "current_workout_id": None,
            "current_routine_id": None,
            "history": [],
            "routines": []
        }
        changed = True

    workouts_state = state["workouts"]
    if "current_workout_id" not in workouts_state:
        workouts_state["current_workout_id"] = None
        changed = True

    if "current_routine_id" not in workouts_state:
        workouts_state["current_routine_id"] = None
        changed = True

    if "history" not in workouts_state or not isinstance(workouts_state["history"], list):
        workouts_state["history"] = []
        changed = True

    if "routines" not in workouts_state or not isinstance(workouts_state["routines"], list):
        workouts_state["routines"] = []
        changed = True

    if "chat_history" not in state or not isinstance(state["chat_history"], list):
        state["chat_history"] = []
        changed = True

    normalized_history = []
    for record in workouts_state["history"]:
        if not isinstance(record, dict):
            continue

        normalized_record = {
            "id": record.get("id") or f"workout_{uuid4().hex[:12]}",
            "created_at": record.get("created_at") or _utc_timestamp(),
            "source": record.get("source") or "agent",
            "source_query": record.get("source_query") or "",
            "response_text": record.get("response_text") or "",
            "scheduled_slots": record.get("scheduled_slots") if isinstance(record.get("scheduled_slots"), list) else [],
            "routine_id": record.get("routine_id"),
            "unit_index": record.get("unit_index"),
            "unit_day": record.get("unit_day"),
            "focus_type": record.get("focus_type"),
            "draft": record.get("draft") if isinstance(record.get("draft"), dict) else {},
        }
        if normalized_record != record:
            changed = True
        normalized_history.append(normalized_record)

    workouts_state["history"] = normalized_history

    normalized_routines = []
    known_workout_ids = {record["id"] for record in normalized_history}
    for routine in workouts_state["routines"]:
        if not isinstance(routine, dict):
            continue

        routine_units = []
        for unit in routine.get("units", []):
            if not isinstance(unit, dict):
                continue
            unit_id = unit.get("unit_id")
            if unit_id not in known_workout_ids:
                continue
            routine_units.append({
                "unit_id": unit_id,
                "day": unit.get("day") or "",
                "time": unit.get("time") or "",
                "focus_type": unit.get("focus_type") or "",
                "title": unit.get("title") or "",
            })

        routine_version = routine.get("version")
        normalized_routine = {
            "id": routine.get("id") or f"routine_{uuid4().hex[:12]}",
            "created_at": routine.get("created_at") or _utc_timestamp(),
            "source": routine.get("source") or "agent",
            "source_query": routine.get("source_query") or "",
            "response_text": routine.get("response_text") or "",
            "routine_name": routine.get("routine_name") or "Weekly Routine",
            "goal": routine.get("goal") or "general fitness",
            "version": routine_version if isinstance(routine_version, int) and routine_version > 0 else 1,
            "units": routine_units,
        }
        if normalized_routine != routine:
            changed = True
        normalized_routines.append(normalized_routine)

    workouts_state["routines"] = normalized_routines

    # Enforce routine-first model: any orphan workout record is migrated into a one-unit routine.
    known_routine_ids = {routine["id"] for routine in workouts_state["routines"]}
    for record in workouts_state["history"]:
        record_routine_id = record.get("routine_id")
        if record_routine_id in known_routine_ids:
            continue

        draft = record.get("draft") if isinstance(record.get("draft"), dict) else {}
        routine_id = f"routine_{uuid4().hex[:12]}"
        record["routine_id"] = routine_id
        record["unit_index"] = 0 if record.get("unit_index") is None else record.get("unit_index")
        if record.get("unit_day") is None:
            record["unit_day"] = ""
        if record.get("focus_type") is None:
            record["focus_type"] = draft.get("focus_type") or ""

        slots = record.get("scheduled_slots") if isinstance(record.get("scheduled_slots"), list) else []
        primary_slot = slots[0] if slots else {}
        routine_record = _build_routine_record(
            routine_name=draft.get("workout_name") or "Migrated Routine",
            goal=draft.get("goal") or "general fitness",
            source_query=record.get("source_query", ""),
            response_text=record.get("response_text", ""),
            units=[{
                "unit_id": record["id"],
                "day": primary_slot.get("day", record.get("unit_day", "")) if isinstance(primary_slot, dict) else record.get("unit_day", ""),
                "time": primary_slot.get("time", "") if isinstance(primary_slot, dict) else "",
                "focus_type": record.get("focus_type") or "",
                "title": draft.get("workout_name") or "Workout Unit",
            }],
            source="legacy_migration",
            existing_id=routine_id,
            version=1,
        )
        workouts_state["routines"].append(routine_record)
        known_routine_ids.add(routine_id)
        changed = True

    if workouts_state["current_workout_id"] is not None:
        known_ids = {record["id"] for record in workouts_state["history"]}
        if workouts_state["current_workout_id"] not in known_ids:
            workouts_state["current_workout_id"] = None
            changed = True

    if workouts_state["current_routine_id"] is not None:
        known_routine_ids = {routine["id"] for routine in workouts_state["routines"]}
        if workouts_state["current_routine_id"] not in known_routine_ids:
            workouts_state["current_routine_id"] = None
            changed = True

    if workouts_state["current_routine_id"] is None and workouts_state["routines"]:
        if workouts_state["current_workout_id"]:
            current_record = next(
                (rec for rec in workouts_state["history"] if rec.get("id") == workouts_state["current_workout_id"]),
                None,
            )
            if isinstance(current_record, dict) and current_record.get("routine_id"):
                workouts_state["current_routine_id"] = current_record.get("routine_id")
            else:
                workouts_state["current_routine_id"] = workouts_state["routines"][-1]["id"]
        else:
            workouts_state["current_routine_id"] = workouts_state["routines"][-1]["id"]
        changed = True

    fitness_draft = state["plan_drafts"]["fitness"]
    should_migrate_plan_draft = (
        isinstance(fitness_draft, dict)
        and fitness_draft
        and not workouts_state["history"]
    )
    if should_migrate_plan_draft:
        migrated_record = _build_workout_record(
            fitness_draft=fitness_draft,
            source_query=state.get("user_query", ""),
            response_text="",
            scheduled_slots=[],
            source="legacy_plan_draft"
        )
        workouts_state["history"].append(migrated_record)
        workouts_state["current_workout_id"] = migrated_record["id"]
        changed = True

    return state, changed

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
                "fitness": {}
            },
            "workouts": {
                "current_workout_id": None,
                "current_routine_id": None,
                "history": [],
                "routines": []
            },
            "missing_info": [],
            "allergies": [],
            "dietary_restrictions": [],
            "general_meal_restrictions": [],
            "injuries": [],
            "equipment": [],
            "general_workout_restrictions": [],
            "status": "idle",
            "chat_history": []
        }
        save_state(default_state)
        return default_state
    return load_state()

def load_state():
    """Reads the current state from the JSON file."""
    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
            normalized_state, changed = _normalize_state_structure(state)
            if changed:
                save_state(normalized_state)
            return normalized_state
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


def save_workout_record(
    fitness_draft,
    source_query="",
    response_text="",
    scheduled_slots=None,
    routine_id=None,
    unit_index=None,
    unit_day=None,
    focus_type=None,
):
    """Backward-compatible wrapper that stores a one-unit routine (no standalone records)."""
    unit_day_value = unit_day or ""
    unit_focus = focus_type or (fitness_draft.get("focus_type") if isinstance(fitness_draft, dict) else "") or ""
    routine_name = "Routine"
    goal = "general fitness"
    title = "Workout Unit"
    if isinstance(fitness_draft, dict):
        routine_name = fitness_draft.get("workout_name") or routine_name
        goal = fitness_draft.get("goal") or goal
        title = fitness_draft.get("workout_name") or title

    routine = save_weekly_routine(
        routine_name=routine_name,
        goal=goal,
        units=[{
            "title": title,
            "focus_type": unit_focus,
            "day_label": unit_day_value,
            "scheduled_slots": scheduled_slots or [],
            "draft": fitness_draft if isinstance(fitness_draft, dict) else {},
            "response_text": response_text,
        }],
        source_query=source_query,
        response_text=response_text,
        source="agent",
    )
    return routine


def set_current_routine(routine_id):
    state = load_state()
    workouts_state = state.get("workouts", {})
    routines = workouts_state.get("routines", [])
    known_ids = {routine.get("id") for routine in routines if isinstance(routine, dict)}
    if routine_id in known_ids:
        state["workouts"]["current_routine_id"] = routine_id
        save_state(state)
        return True
    return False


def set_current_workout(workout_id):
    state = load_state()
    workouts_state = state.get("workouts", {})
    history = workouts_state.get("history", [])
    known_ids = {record.get("id") for record in history if isinstance(record, dict)}
    if workout_id in known_ids:
        state["workouts"]["current_workout_id"] = workout_id
        save_state(state)
        return True
    return False


def save_weekly_routine(routine_name, goal, units, source_query="", response_text="", source="agent"):
    """Saves a routine and all of its workout units. Returns the saved routine record."""
    state = load_state()

    if "workouts" not in state or not isinstance(state["workouts"], dict):
        state["workouts"] = {
            "current_workout_id": None,
            "current_routine_id": None,
            "history": [],
            "routines": []
        }

    workouts_state = state["workouts"]
    if "history" not in workouts_state or not isinstance(workouts_state["history"], list):
        workouts_state["history"] = []
    if "routines" not in workouts_state or not isinstance(workouts_state["routines"], list):
        workouts_state["routines"] = []

    existing_versions = [
        r.get("version", 1)
        for r in workouts_state["routines"]
        if isinstance(r, dict) and r.get("routine_name") == routine_name
    ]
    next_version = (max(existing_versions) + 1) if existing_versions else 1

    routine_id = f"routine_{uuid4().hex[:12]}"
    routine_units = []
    last_workout_id = None

    for idx, unit in enumerate(units or []):
        if not isinstance(unit, dict):
            continue

        unit_draft = unit.get("draft")
        if not isinstance(unit_draft, dict):
            unit_draft = {}
        unit_slots = unit.get("scheduled_slots") if isinstance(unit.get("scheduled_slots"), list) else []
        unit_day = unit.get("day") or unit.get("day_label") or ""
        focus_type = unit.get("focus_type") or unit_draft.get("focus_type") or ""

        workout_record = _build_workout_record(
            fitness_draft=unit_draft,
            source_query=source_query,
            response_text=unit.get("response_text") or response_text,
            scheduled_slots=unit_slots,
            source=source,
        )
        workout_record["routine_id"] = routine_id
        workout_record["unit_index"] = idx
        workout_record["unit_day"] = unit_day
        workout_record["focus_type"] = focus_type

        workouts_state["history"].append(workout_record)
        last_workout_id = workout_record["id"]

        primary_slot = unit_slots[0] if unit_slots else {}
        routine_units.append({
            "unit_id": workout_record["id"],
            "day": primary_slot.get("day", unit_day) if isinstance(primary_slot, dict) else unit_day,
            "time": primary_slot.get("time", "") if isinstance(primary_slot, dict) else "",
            "focus_type": focus_type,
            "title": unit.get("title") or unit_draft.get("workout_name") or f"Workout {idx + 1}",
        })

    routine_record = _build_routine_record(
        routine_name=routine_name,
        goal=goal,
        source_query=source_query,
        response_text=response_text,
        units=routine_units,
        source=source,
        existing_id=routine_id,
        version=next_version,
    )

    workouts_state["routines"].append(routine_record)
    workouts_state["current_routine_id"] = routine_id
    if last_workout_id:
        workouts_state["current_workout_id"] = last_workout_id

    if "plan_drafts" not in state or not isinstance(state["plan_drafts"], dict):
        state["plan_drafts"] = {"nutrition": [], "fitness": {}}
    if units and isinstance(units[0], dict):
        first_draft = units[0].get("draft") if isinstance(units[0].get("draft"), dict) else {}
        state["plan_drafts"]["fitness"] = first_draft

    save_state(state)
    return routine_record

def update_user_context(context_key, new_items):
    """Appends new items to specific context lists like allergies or equipment."""
    state = load_state()
    if context_key in state and isinstance(state[context_key], list):
        # Add new items, avoiding duplicates
        for item in new_items:
            if item not in state[context_key]:
                state[context_key].append(item)
        save_state(state)
        return True
    return False
