import streamlit as st
import base64
import requests
import state_manager


def _format_slots(slots):
    labels = []
    for slot in slots or []:
        if isinstance(slot, dict):
            day = slot.get("day", "")
            time = slot.get("time", "")
            if day or time:
                labels.append(f"{day} {time}".strip())
        elif isinstance(slot, str):
            labels.append(slot)
    return labels


def _get_exercises(draft):
    details = draft.get("exercise_details")
    if isinstance(details, list) and details:
        return details
    exercises = draft.get("exercises")
    if isinstance(exercises, list):
        return exercises
    return []


def _get_notes(draft):
    safety = draft.get("safety_notes")
    if isinstance(safety, list) and safety:
        return safety
    coaching = draft.get("coaching_notes")
    if isinstance(coaching, list):
        return coaching
    return []

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Nutrissistant", layout="wide")

# --- NAVIGATION & STATE MANAGEMENT ---
if 'current_page' not in st.session_state:
    st.session_state.current_page = 'welcome'
if 'user_profile' not in st.session_state:
    st.session_state.user_profile = state_manager.load_state()["user_profile"]

# Safely initialize schedule_data
if 'schedule_data' not in st.session_state:
    if "schedule_data" in state_manager.load_state() and state_manager.load_state()["schedule_data"]:
        st.session_state.schedule_data = state_manager.load_state()["schedule_data"]
    else:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        hours = [f"{h:02d}:00" for h in range(6, 24)]
        st.session_state.schedule_data = {d: {h: "-" for h in hours} for d in days}

if 'is_editing' not in st.session_state:
    st.session_state.is_editing = False

# helper function for background image
def get_base64_of_bin_file(bin_file):
    with open(bin_file, 'rb') as f:
        data = f.read()
    return base64.b64encode(data).decode()

try:
    bg_base64 = get_base64_of_bin_file("images/background_image.png")
    bg_css = f"""
    <style>
    .stApp {{
        background-image: url("data:image/png;base64,{bg_base64}");
        background-size: cover;
        background-position: center;
    }}
    </style>
    """
except FileNotFoundError:
    bg_css = ""
    st.markdown("Background image not found. Please check the file path.")

# --- CUSTOM CSS FOR STYLING ---
custom_css = f"""
{bg_css}
<style>
    .block-container {{
        background-color: rgba(252, 250, 245, 0.98); 
        padding: 5rem 6rem;
        max-width: 80% !important; 
        border-radius: 20px;
        border: 5px solid #8B9A6D; 
        box-shadow: 0px 12px 24px rgba(0,0,0,0.15);
        margin-top: 2rem;
    }}
    .welcome-title {{
        color: #933833;
        font-family: 'Georgia', serif;
        text-align: center;
        font-size: 3.7rem !important; 
        font-weight: bold;
        margin-bottom: 0px;
    }}
    .welcome-subtitle {{
        color: #933833;
        font-family: 'Georgia', serif;
        text-align: center;
        font-size: 2.2rem !important; 
        font-weight: bold;
        margin-top: 10px;
        margin-bottom: 20px;
    }}
    .welcome-instruction {{
        color: #933833;
        text-align: center;
        font-size: 1.7rem; 
        margin-bottom: 40px;
    }}
    .stTextArea textarea {{
        font-size: 1.2rem;
        line-height: 1.6;
    }}
    
    .stButton>button {{
        display: block;
        margin: 0 auto;
        background-color: #933833;
        color: white !important;
        border-radius: 12px;
        padding: 1rem 3rem; 
        font-size: 2rem !important; 
        font-weight: bold;
    }}
    .stButton>button:hover {{
        background-color: #7a2d29;
        color: white;
        border-color: #7a2d29;
    }}
</style>
"""
st.markdown(custom_css, unsafe_allow_html=True)

# --- APP ROUTING ---
if st.session_state.current_page == 'welcome':
    
    # Text Headers
    st.markdown('<p class="welcome-title">Welcome to Nutrissistant!</p>', unsafe_allow_html=True)
    st.markdown('<p class="welcome-subtitle">Your wellness and healthy lifestyle agent</p>', unsafe_allow_html=True)
    st.markdown('<p class="welcome-instruction">Tell me about yourself and your goals. It’ll help me to know you better and personalize my answers!</p>', unsafe_allow_html=True)
    
    # Input Layout 
    col1, col2 = st.columns([6, 1]) 
    
    with col1:
        user_input = st.text_area(
            label="User Goals",
            value=state_manager.load_state()["user_profile"],
            placeholder="Example: I'm Yoav, 30 years old. I have a wife and 2 little kids at the ages of 10 and 12. I want to lose 3kg of body weight in 4 months.",
            height=300, 
            label_visibility="collapsed" 
        )

    with col2:
        try:
            with open("images/yoav_image.png", "rb") as _img_f:
                st.image(_img_f.read(), width="stretch")
        except FileNotFoundError:
            st.markdown("Person image missing.")

    st.write("") 

    # Navigation Button
    if st.button("Continue"):
        if (user_input or "").strip() != "":
            st.session_state.user_profile = user_input
            state_manager.update_user_profile(user_input)
        
        st.session_state.current_page = 'home'
        st.rerun()

elif st.session_state.current_page == 'home':
    # --- HOME PAGE STYLES ---
    custom_css_home_page = f"""
    {bg_css}
    <style>
        .block-container {{
            background-color: rgba(252, 250, 245, 0.98); 
            padding: 3rem 2rem !important; 
            max-width: 95% !important; 
            border-radius: 20px;
            border: 5px solid #8B9A6D; 
            box-shadow: 0px 12px 24px rgba(0,0,0,0.15);
            margin-top: 2rem;
        }}
        .schedule-title {{
            color: #933833;
            font-family: 'Georgia', serif;
            text-align: center;
            font-size: 2.5rem !important;
            font-weight: bold;
            margin-bottom: 2rem;
        }}
        .day-card {{
            background-color: rgba(252, 250, 245, 0.95);
            border: 3px solid #8B9A6D;
            border-radius: 10px;
            padding: 1rem;
            height: 100%;
        }}
        .day-title {{
            color: #933833;
            font-family: 'Georgia', serif;
            text-align: center;
            font-size: 0.9rem !important; 
            font-weight: bold;
            margin-bottom: 1rem;
            border-bottom: 2px solid #8B9A6D;
            padding-bottom: 0.5rem;
        }}
        /* UPDATED: Flexbox settings to fix overlapping text */
        .schedule-row {{
            display: flex;
            align-items: flex-start; /* Aligns text to the top if it wraps to multiple lines */
            justify-content: space-between;
            margin-bottom: 0.5rem;
            color: #933833;
            font-size: 0.8rem;
            gap: 10px; /* Forces space between time and activity */
        }}
        /* NEW: Specific classes for time and activity */
        .schedule-time {{
            flex-shrink: 0; /* Prevents the time from getting squished */
            font-weight: bold;
        }}
        .schedule-activity {{
            text-align: right; /* Pushes the text to the right side */
            word-break: break-word; /* Makes sure long words drop to the next line safely */
        }}
        .stButton>button {{
            width: 100% !important; 
            color: white !important;
            margin-bottom: 0.5rem;
            font-size: 1rem !important; 
            padding: 0.5rem 1rem !important;
            white-space: normal !important; 
            height: auto !important;
        }}
        .stTextInput {{
            margin-bottom: -15px !important;
        }}
        .stTextInput input {{
            font-size: 0.75rem !important;
            padding: 0.2rem 0.4rem !important;
            min-height: 1.8rem !important;
            color: #933833 !important;
        }}
        .stTextInput label {{
            font-size: 0.7rem !important;
            color: #8B9A6D !important;
        }}
        /* Style for the prompt box at the bottom */
        .prompt-container {{
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 2px dashed #8B9A6D;
        }}
    </style>
    """
    st.markdown(custom_css_home_page, unsafe_allow_html=True)
    
    st.markdown('<p class="schedule-title">Your schedule</p>', unsafe_allow_html=True)
    
    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    hours = [f"{h:02d}:00" for h in range(6, 24)]
    
    # ==========================================
    # DISPLAY MODE (View Only)
    # ==========================================
    if not st.session_state.is_editing:
        col_schedule, col_nav = st.columns([10, 1.25])
        
        with col_schedule:
            day_cols = st.columns(7)
            
            for i, day in enumerate(days_of_week):
                with day_cols[i]:
                    card_html = f'<div class="day-card"><div class="day-title">{day}</div>'
                    for hour in hours:
                        activity = state_manager.load_state()["schedule_data"][day][hour]
                        card_html += f'<div class="schedule-row"><span class="schedule-time">{hour}</span><span class="schedule-activity">{activity}</span></div>'
                    card_html += '</div>'
                    st.markdown(card_html, unsafe_allow_html=True)

        with col_nav:
            if st.button("Edit Profile"):
                st.session_state.current_page = 'welcome'
                st.rerun()
            if st.button("Meal History"):
                st.session_state.current_page = 'all recipes'
                st.rerun()
            if st.button("Current Recipes"):
                st.session_state.current_page = 'current recipes'
                st.rerun()
            if st.button("Workout History"):
                st.session_state.current_page = 'workouts'
                st.rerun()
            if st.button("Current Routine"):
                st.session_state.current_page = 'routine'
                st.rerun()
            
            if st.button("Edit Schedule"):
                st.session_state.is_editing = True
                st.rerun()

        # Prompt Box at the bottom of the home page
        st.markdown('<div class="prompt-container"></div>', unsafe_allow_html=True)
        st.markdown("<h4 style='color: #933833;'>Ask Nutrissistant:</h4>", unsafe_allow_html=True)
        
        prompt_col1, prompt_col2 = st.columns([8, 2])
        
        with prompt_col1:
            # st.text_input for a single line, or st.text_area for multiple lines
            user_prompt = st.text_area(
                label="Prompt",
                placeholder="e.g. Can you suggest a healthy dinner for Tuesday evening?",
                label_visibility="collapsed",
                height=100
            )
            
        with prompt_col2:
            if st.button("Run Agent", type="primary"): 
                if user_prompt.strip():
                    with st.spinner("Nutrissistant is thinking..."):
                        # Call the supervisor 
                        try:
                            api_url = "http://localhost:8000/api/execute"
                            payload = {"prompt": user_prompt}
                            
                            res = requests.post(api_url, json=payload)
                            data = res.json()
                            
                            if data.get("status") == "ok":
                                st.session_state.latest_response = data["response"]
                                st.session_state.latest_steps = data["steps"]

                                # re-read the updated user_data.json
                                fresh_state = state_manager.load_state()
                                if "schedule_data" in fresh_state:
                                    st.session_state.schedule_data = fresh_state["schedule_data"]
                                st.rerun()
                            else:
                                st.markdown(f"Agent Error: {data.get('error')}")
                        except Exception as e:
                            st.markdown(f"Failed to connect to API: {e}. Is FastAPI running?")
                else:
                    st.markdown("Please enter a request first.")

        # Display the Agent's Final Output and Traced Steps
        if "latest_response" in st.session_state:
            st.markdown("### Agent Response")
            st.markdown(st.session_state.latest_response)
            
            st.markdown("### Execution Trace")
            with st.expander("View Agent Steps (JSON)"):
                st.json(st.session_state.latest_steps)

    # ==========================================
    # EDIT MODE 
    # ==========================================
    else:
        with st.form("edit_schedule_form"):
            col_schedule, col_nav = st.columns([10, 1.25])
            
            with col_schedule:
                day_cols = st.columns(7)
                for i, day in enumerate(days_of_week):
                    with day_cols[i]:
                        st.markdown(f'<div class="day-title">{day}</div>', unsafe_allow_html=True)
                        for hour in hours:
                            current_val = st.session_state.schedule_data[day][hour]
                            val_to_show = "" if current_val == "-" else current_val
                            
                            st.text_input(
                                label=hour, 
                                value=val_to_show, 
                                key=f"input_{day}_{hour}" 
                            )
            
            with col_nav:
                st.markdown("<br><br>", unsafe_allow_html=True) 
                if st.form_submit_button("Cancel"):
                    st.session_state.is_editing = False
                    st.rerun()

            st.write("---") 
            
            save_col1, save_col2, save_col3 = st.columns([4, 2, 4])
            with save_col2:
                if st.form_submit_button("Save Schedule", type="primary", width='stretch'):
                    for day in days_of_week:
                        for hour in hours:
                            typed_text = st.session_state[f"input_{day}_{hour}"].strip()
                            st.session_state.schedule_data[day][hour] = typed_text if typed_text else "-"

                    state_manager.update_schedule(st.session_state.schedule_data)
                    st.session_state.is_editing = False
                    st.rerun()

# ==========================================
# WORKOUT HISTORY PAGE
# ==========================================
elif st.session_state.current_page == 'workouts':
    from datetime import datetime

    custom_css_workouts = f"""
    {bg_css}
    <style>
        .block-container {{
            background-color: rgba(252, 250, 245, 0.98);
            padding: 3rem 4rem !important;
            max-width: 90% !important;
            border-radius: 20px;
            border: 5px solid #8B9A6D;
            box-shadow: 0px 12px 24px rgba(0,0,0,0.15);
            margin-top: 2rem;
        }}
        .page-title {{
            color: #933833;
            font-family: 'Georgia', serif;
            text-align: center;
            font-size: 2.5rem !important;
            font-weight: bold;
            margin-bottom: 0.5rem;
        }}
        .page-subtitle {{
            color: #8B9A6D;
            text-align: center;
            font-size: 1rem;
            margin-bottom: 2rem;
        }}
        .workout-card {{
            background-color: rgba(252, 250, 245, 0.95);
            border: 2px solid #8B9A6D;
            border-radius: 12px;
            padding: 1.2rem 1.5rem;
            margin-bottom: 1rem;
        }}
        .workout-card-title {{
            color: #933833;
            font-family: 'Georgia', serif;
            font-size: 1.2rem;
            font-weight: bold;
            margin-bottom: 0.3rem;
        }}
        .workout-meta {{
            color: #666;
            font-size: 0.85rem;
            margin-bottom: 0.5rem;
        }}
        .badge-current {{
            background-color: #933833;
            color: white;
            border-radius: 6px;
            padding: 2px 10px;
            font-size: 0.75rem;
            font-weight: bold;
            margin-left: 8px;
        }}
        .section-header {{
            color: #933833;
            font-family: 'Georgia', serif;
            font-weight: bold;
            font-size: 1rem;
            border-bottom: 1px solid #8B9A6D;
            padding-bottom: 4px;
            margin-top: 1rem;
            margin-bottom: 0.5rem;
        }}
        .exercise-row {{
            display: flex;
            gap: 1rem;
            font-size: 0.85rem;
            color: #444;
            padding: 4px 0;
            border-bottom: 1px dashed #ddd;
        }}
        .exercise-name {{
            font-weight: bold;
            color: #933833;
            width: 40%;
        }}
        .stButton>button {{
            background-color: #933833;
            color: white !important;
            border-radius: 10px;
            padding: 0.5rem 1.5rem;
            font-size: 1rem;
            font-weight: bold;
        }}
        .stButton>button:hover {{
            background-color: #7a2d29;
            color: white;
        }}
        /* General body text — dark against cream background */
        .block-container p, .block-container li,
        .stMarkdown p, .stMarkdown li,
        .stExpander p, .stExpander li,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] li,
        [data-testid="stMarkdownContainer"] strong {{
            color: #3a3a3a !important;
        }}
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {{
            color: #933833 !important;
        }}
        [data-testid="stMarkdownContainer"] em {{
            color: #555 !important;
        }}
    </style>
    """
    st.markdown(custom_css_workouts, unsafe_allow_html=True)

    st.markdown('<p class="page-title">Workout History</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Routine-first history with expandable workout units</p>', unsafe_allow_html=True)

    col_back, col_routine = st.columns([1, 1])
    with col_back:
        if st.button("← Back to Schedule"):
            st.session_state.current_page = 'home'
            st.rerun()
    with col_routine:
        if st.button("View Current Routine →"):
            st.session_state.current_page = 'routine'
            st.rerun()

    st.write("")

    state = state_manager.load_state()
    workouts_state = state.get("workouts", {})
    history = workouts_state.get("history", [])
    routines = workouts_state.get("routines", [])
    current_id = workouts_state.get("current_workout_id")
    current_routine_id = workouts_state.get("current_routine_id")

    if routines:
        st.markdown("### Weekly Routines")
        for routine in reversed(routines):
            routine_id = routine.get("id", "")
            routine_name = routine.get("routine_name", "Weekly Routine")
            goal = routine.get("goal", "—")
            version = routine.get("version", 1)
            units = routine.get("units", [])
            is_current_routine = routine_id == current_routine_id

            created_at = routine.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    created_label = dt.strftime("%b %d, %Y")
                except Exception:
                    created_label = created_at
            else:
                created_label = "Unknown date"

            badge = ' <span class="badge-current">CURRENT ROUTINE</span>' if is_current_routine else ''
            st.markdown(
                f'<div class="workout-card">'
                f'<div class="workout-card-title">{routine_name} v{version}{badge}</div>'
                f'<div class="workout-meta">Created: {created_label} &nbsp;|&nbsp; Units: {len(units)}</div>'
                f'<div class="workout-meta" style="font-style:italic;">Goal: {goal}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

            with st.expander("View routine units"):
                if not units:
                    st.markdown("No units found for this routine.")
                for idx, unit in enumerate(units, start=1):
                    unit_id = unit.get("unit_id", "")
                    day = unit.get("day", "")
                    time = unit.get("time", "")
                    focus_type = unit.get("focus_type", "")
                    title = unit.get("title") or f"Workout Unit {idx}"
                    label_bits = [title]
                    if focus_type:
                        label_bits.append(focus_type.capitalize())
                    if day or time:
                        label_bits.append(f"{day} {time}".strip())
                    st.markdown(f"- {' | '.join(label_bits)}")

                    col_a, col_b = st.columns([1, 1])
                    with col_a:
                        if st.button("Open Unit", key=f"open_unit_{routine_id}_{unit_id}"):
                            st.session_state.selected_unit_id = unit_id
                            state_manager.set_current_routine(routine_id)
                            if unit_id:
                                state_manager.set_current_workout(unit_id)
                            st.session_state.current_page = "routine"
                            st.rerun()
                    with col_b:
                        if unit_id and unit_id == current_id:
                            st.caption("Currently selected unit")

                if not is_current_routine and st.button("Set as Current Routine", key=f"set_routine_{routine_id}"):
                    state_manager.set_current_routine(routine_id)
                    if units:
                        first_unit_id = units[0].get("unit_id")
                        if first_unit_id:
                            state_manager.set_current_workout(first_unit_id)
                    st.rerun()

    if not routines:
        st.markdown("No routines generated yet. Ask Nutrissistant to create your weekly routine.")

# ==========================================
# CURRENT ROUTINE PAGE
# ==========================================
elif st.session_state.current_page == 'routine':
    from datetime import datetime

    custom_css_routine = f"""
    {bg_css}
    <style>
        .block-container {{
            background-color: rgba(252, 250, 245, 0.98);
            padding: 3rem 4rem !important;
            max-width: 90% !important;
            border-radius: 20px;
            border: 5px solid #8B9A6D;
            box-shadow: 0px 12px 24px rgba(0,0,0,0.15);
            margin-top: 2rem;
        }}
        .page-title {{
            color: #933833;
            font-family: 'Georgia', serif;
            text-align: center;
            font-size: 2.5rem !important;
            font-weight: bold;
            margin-bottom: 0.5rem;
        }}
        .page-subtitle {{
            color: #8B9A6D;
            text-align: center;
            font-size: 1rem;
            margin-bottom: 2rem;
        }}
        .section-header {{
            color: #933833;
            font-family: 'Georgia', serif;
            font-weight: bold;
            font-size: 1rem;
            border-bottom: 1px solid #8B9A6D;
            padding-bottom: 4px;
            margin-top: 1rem;
            margin-bottom: 0.5rem;
        }}
        .exercise-row {{
            display: flex;
            gap: 1rem;
            font-size: 0.85rem;
            color: #444;
            padding: 4px 0;
            border-bottom: 1px dashed #ddd;
        }}
        .exercise-name {{
            font-weight: bold;
            color: #933833;
            width: 40%;
        }}
        .stButton>button {{
            background-color: #933833;
            color: white !important;
            border-radius: 10px;
            padding: 0.5rem 1.5rem;
            font-size: 1rem;
            font-weight: bold;
        }}
        .stButton>button:hover {{
            background-color: #7a2d29;
            color: white;
        }}
        /* General body text — dark against cream background */
        .block-container p, .block-container li,
        .stMarkdown p, .stMarkdown li,
        .stExpander p, .stExpander li,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] li,
        [data-testid="stMarkdownContainer"] strong {{
            color: #3a3a3a !important;
        }}
        [data-testid="stMarkdownContainer"] h1,
        [data-testid="stMarkdownContainer"] h2,
        [data-testid="stMarkdownContainer"] h3 {{
            color: #933833 !important;
        }}
        [data-testid="stMarkdownContainer"] em {{
            color: #555 !important;
        }}
    </style>
    """
    st.markdown(custom_css_routine, unsafe_allow_html=True)

    st.markdown('<p class="page-title">Current Routine</p>', unsafe_allow_html=True)
    st.markdown('<p class="page-subtitle">Your active weekly routine with full unit details</p>', unsafe_allow_html=True)

    if st.button("← Back to Schedule"):
        st.session_state.current_page = 'home'
        st.rerun()

    state = state_manager.load_state()
    workouts_state = state.get("workouts", {})
    history = workouts_state.get("history", [])
    routines = workouts_state.get("routines", [])
    current_id = workouts_state.get("current_workout_id")
    current_routine_id = workouts_state.get("current_routine_id")

    current_routine = next((r for r in routines if r.get("id") == current_routine_id), None)

    if current_routine and current_routine.get("units"):
        units = current_routine.get("units", [])
        selected_unit_id = st.session_state.get("selected_unit_id")
        valid_unit_ids = [u.get("unit_id") for u in units if isinstance(u, dict)]

        if selected_unit_id not in valid_unit_ids:
            selected_unit_id = current_id if current_id in valid_unit_ids else valid_unit_ids[0]
            st.session_state.selected_unit_id = selected_unit_id

        selected_unit_meta = next((u for u in units if u.get("unit_id") == selected_unit_id), None)
        current_record = next((r for r in history if r.get("id") == selected_unit_id), None)

        st.markdown(f"## {current_routine.get('routine_name', 'Weekly Routine')} v{current_routine.get('version', 1)}")
        st.markdown(f"*Goal: {current_routine.get('goal', '—')}*")
        st.markdown("### Workout Units")

        unit_cols = st.columns(max(len(units), 1))
        for idx, unit in enumerate(units):
            with unit_cols[idx]:
                unit_id = unit.get("unit_id")
                title = unit.get("title") or f"Unit {idx + 1}"
                day = unit.get("day", "")
                time = unit.get("time", "")
                focus = unit.get("focus_type", "")
                button_label = title
                if focus:
                    button_label = f"{button_label}\n{focus.capitalize()}"
                if day or time:
                    button_label = f"{button_label}\n{day} {time}".strip()
                if st.button(button_label, key=f"routine_unit_{unit_id}"):
                    st.session_state.selected_unit_id = unit_id
                    state_manager.set_current_workout(unit_id)
                    st.rerun()

        st.markdown("---")

        if not current_record:
            st.markdown("Select a workout unit to see full details.")
        else:
            draft = current_record.get("draft") or {}
            selected_meta = selected_unit_meta or {}
            workout_name = draft.get("workout_name") or selected_meta.get("title") or "Workout Unit"
            goal = draft.get("goal") or current_routine.get("goal") or "—"
            difficulty = (draft.get("difficulty") or "—").capitalize()
            duration = draft.get("duration_limit_mins")
            slots = _format_slots(current_record.get("scheduled_slots", []))

            created_at = current_record.get("created_at", "")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    created_label = dt.strftime("%b %d, %Y")
                except Exception:
                    created_label = created_at
            else:
                created_label = ""

            st.markdown(f"## {workout_name}")
            meta_parts = [f"Difficulty: **{difficulty}**"]
            if duration:
                meta_parts.append(f"Duration: **{duration} min**")
            if created_label:
                meta_parts.append(f"Created: **{created_label}**")
            if slots:
                meta_parts.append(f"Scheduled: **{', '.join(slots)}**")
            st.markdown("&nbsp;&nbsp;|&nbsp;&nbsp;".join(meta_parts))
            st.markdown(f"*Goal: {goal}*")
            st.markdown("---")

            session_outline = draft.get("session_outline", [])
            for section in session_outline:
                sec_name = (section.get("section") or "").upper()
                sec_mins = section.get("minutes")
                header = sec_name + (f" ({sec_mins} min)" if sec_mins else "")
                st.markdown(f'<div class="section-header">{header}</div>', unsafe_allow_html=True)
                for item in section.get("items", []):
                    st.markdown(f"- {item}")

            exercises = _get_exercises(draft)
            if exercises:
                st.markdown('<div class="section-header">EXERCISES</div>', unsafe_allow_html=True)
                ex_html = ""
                for ex in exercises:
                    name = ex.get("name", "")
                    sets = ex.get("sets", "")
                    reps = ex.get("reps", "")
                    notes = ex.get("notes", "")
                    ex_html += (
                        f'<div class="exercise-row">'
                        f'<span class="exercise-name">{name}</span>'
                        f'<span>{sets} sets &times; {reps}</span>'
                        f'<span style="color:#666;">{notes}</span>'
                        f'</div>'
                    )
                st.markdown(ex_html, unsafe_allow_html=True)

            notes = _get_notes(draft)
            if notes:
                st.markdown('<div class="section-header">NOTES</div>', unsafe_allow_html=True)
                for note in notes:
                    st.markdown(f"- {note}")

            st.markdown("---")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("View All Workouts"):
                    st.session_state.current_page = 'workouts'
                    st.rerun()
            with col2:
                if st.button("Back to Schedule"):
                    st.session_state.current_page = 'home'
                    st.rerun()
    else:
        st.markdown("No active routine yet. Ask Nutrissistant to create or update your weekly routine.")
        if st.button("Go to Workout History"):
            st.session_state.current_page = 'workouts'
            st.rerun()