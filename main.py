import streamlit as st
import base64
import requests
import state_manager

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
    st.warning("Background image not found. Please check the file path.")

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
        color: white;
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
            st.image("images/yoav_image.png", use_container_width=True)
        except FileNotFoundError:
            st.warning("Person image missing.")

    st.write("") 

    # Navigation Button
    if st.button("Continue"):
        if user_input.strip() != "":
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
                st.session_state.current_page = 'meals'
                st.rerun()
            if st.button("Workout History"):
                st.session_state.current_page = 'workouts'
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
                            else:
                                st.error(f"Agent Error: {data.get('error')}")
                        except Exception as e:
                            st.error(f"Failed to connect to API: {e}. Is FastAPI running?")
                else:
                    st.warning("Please enter a request first.")

        # Display the Agent's Final Output and Traced Steps
        if "latest_response" in st.session_state:
            st.markdown("### Agent Response")
            st.success(st.session_state.latest_response)
            
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
                if st.form_submit_button("Save Schedule", type="primary", use_container_width=True):
                    for day in days_of_week:
                        for hour in hours:
                            typed_text = st.session_state[f"input_{day}_{hour}"].strip()
                            st.session_state.schedule_data[day][hour] = typed_text if typed_text else "-"

                    state_manager.update_schedule(st.session_state.schedule_data)
                    st.session_state.is_editing = False
                    st.rerun()




