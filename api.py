from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import asyncio
import os

import httpx
import websockets

from src.agents import supervisor_agent

app = FastAPI(title="Nutrissistant API")

# --- DATA MODELS ---
# Matches the input format required by the PDF: { "prompt": "User request here" }
class ExecuteRequest(BaseModel):
    prompt: str

# --- API ENDPOINTS ---

@app.get("/health")
def health_check():
    """Lightweight health endpoint for uptime checks."""
    return {"status": "ok"}

@app.get("/api/team_info")
def get_team_info():
    """Returns student details as required."""
    # Ensure you update this with your actual team details
    return {
        "group_batch_order_number": "01_01", 
        "team_name": "Nutrissistant",
        "students": [
            { "name": "Galit Kadzelashvily", "email": "galit.k@campus.technion.ac.il" },
            { "name": "Shachar Frenkel", "email": "fshachar@campus.technion.ac.il" },
            { "name": "Ido Falach", "email": "ido.falah@campus.technion.ac.il" }
        ]
    }


@app.get("/api/agent_info")
def get_agent_info():
    """Returns agent meta and how to use it."""
    return {
        "description": "Nutrissistant is an autonomous wellness and healthy lifestyle agent.",
        "purpose": "To create nutrition and fitness plans while resolving scheduling conflicts. Nutrissistant uses information from your profile and prompts to personilize the plans. It saves all routines and recipes, and schedules them.",
        "prompt_template": {
            "template": "I want to [Goal] and I have access to [Equipment/Ingredients]. Please plan my [Timeframe (week/ Monday)]. I prefer [Preferences (morning workouts ....)]. I have the following dietary restrictions [Dietary Restrictions]."
        },
        "prompt_examples": [
            {
                "prompt": "Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts.",
                "full_response": "Proposed 'Meal: Saturday Dinner (Meat & Vegetables, Nut-free, 40min)' on Saturday at 19:00. I found a great recipe for you: Chicken Enchilada Skillet. It takes about 20 minutes.",
                "steps": [
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": """You are the Nutrissistant Supervisor. Analyze the user query.
    
    --- CURRENT KNOWN CONTEXT ---
    Profile: I'm Ido, 21 year old student. I have some spare time, I'd like to get into shape and lose 10-15 kg in the next year (My height  is 1.80 m and my weight is 150 kg). I enjoy cycling and boxing.
    Equipment: []
    Injuries: []
    Allergies: []
    Dietary Restrictions: ['vegetarian', 'vegetarian (overridden for this meal — meat allowed)', 'meat allowed for Monday dinner']
    Workout Preferences: ['prefer morning workouts (Thursday)', 'prefer afternoons']
    Meal Preferences: ['max prep time: 40 minutes', 'meal: lunch']
    Pending Info Requested from User: []

    --- RECENT CONVERSATION HISTORY (Last 60 mins) ---
    User: can you find a different recipe than the one you just gave me, and update ?
    Agent: Removed 'Meal: Quick Chicken & Vegetable Stir-Fry with Quick-Cook Brown Rice' from your schedule. Scheduled hard event 'Meal: Lemon Garlic Salmon with Roasted Asparagus and Wild Rice' on Monday at 19:00.

    I found a great recipe for you: Quick Chickpea & Spinach Coconut Curry. It takes about 35 minutes.

    I've added Quick Chickpea & Spinach Coconut Curry to your schedule for Monday at 19:00.
    User: Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts.
    -----------------------------
    
    CRITICAL RULES:
    1. RESOLVE CONTEXT (COREFERENCE): If the user's query relies on previous context (e.g., using pronouns like "these", "it", "that plan"), use the Recent Conversation History to figure out exactly what they mean. 
    2. REWRITE QUERY (CONTINUATION & MERGING): Output a `resolved_query` that makes the user's intent completely explicit and standalone. 
       - If the user is providing missing information to a previous request (e.g., "I have 2kg dumbbells"), you MUST combine it with their original goal from the history. 
       - If they say "remove these", rewrite as "Remove the workouts we just scheduled".
    3. EXTRACT NEW CONTEXT: Categorize new persistent info into `extracted_context`. If the user provides new details that conflict with the current, override the old and write the new.
       - `general_meal_restrictions`: ONLY use this for actual food items, flavors, or ingredients the user dislikes/avoids (e.g., "no mushrooms", "I hate spicy food"). DO NOT put time limits (e.g., "40 minutes") or meal types (e.g., "lunch") here.
    4. MINIMIZE QUESTIONS: Only ask absolutely necessary questions for the task.
       - For WORKOUT generation: Check if Target workouts/week, Preferred time, and Equipment are known (or it could be just one workout).
       - For RECIPE or MEAL generation: If the user provides NO specific constraints (like a preferred cuisine, specific craving, or max prep time), add "preferred cuisine or maximum prep time" to `missing_info`.
       - For SCHEDULE tasks: DO NOT ask for the target day or time if the user leaves them out. The scheduling system is autonomous and will automatically find the next open slot. NEVER add day or time to `missing_info` for a scheduling request.
    5. AVOID RE-ASKING: Do NOT ask for information already in the 'CURRENT KNOWN CONTEXT'.
    6. INFER CONTINUATION: If the user is answering a question, infer the task from the 'Pending Info'.
    7. STRICT SCHEDULING SEPARATION: If the user's query is ONLY about moving, rescheduling, or removing an existing event on the calendar (e.g., "move my workout to 5pm", "cancel tomorrow's meal", "reschedule the run"), output ONLY the "SCHEDULE" task. Do NOT output "WORKOUT" or "PLAN_MEAL" unless they explicitly want to change the exercises or recipes.

    Output JSON only with this schema:
    {
        "tasks": [], // MUST use ONLY the allowed tasks below.
        "goals": [],
        "missing_info": [],
        "extracted_context": {
            "equipment": [],
            "injuries": [],
            "allergies": [],
            "dietary_restrictions": [],
            "general_workout_restrictions": [],
            "general_meal_restrictions": []
        }
    }
    
    ALLOWED TASKS:
    - "SCHEDULE": Moving, removing, booking, or checking availability on the calendar.
    - "WORKOUT": Generating new routines or changing the actual exercises/content of a workout.
    - "PLAN_MEAL": Generating meal plans for whole days/weeks, OR generating multiple specific meals/recipes in one go (e.g., "find 2 recipes for Monday").
    - "FIND_RECIPE": Generating or finding a SINGLE recipe only.
    - "OTHER": Anything that doesn't fit the above.

    COMBINATIONS:
    - If the user asks to "plan my whole week" or "plan everything", output BOTH "WORKOUT" and "PLAN_MEAL" (and "SCHEDULE" if calendar integration is needed).""",
                            "user": "Query: Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts."
                        },
                        "response": {
                            "tasks": ["FIND_RECIPE"],
                            "goals": ["resolved_query: Find one recipe for Saturday dinner that contains meat and vegetables, can be prepared in 40 minutes or less, and is safe for a user who is allergic to nuts."],
                            "missing_info": [],
                            "extracted_context": {
                                "equipment": [],
                                "injuries": [],
                                "allergies": ["nuts"],
                                "dietary_restrictions": ["vegetarian"],
                                "general_workout_restrictions": [],
                                "general_meal_restrictions": []
                            }
                        }
                    },
                    {
                        "module": "ScheduleAgent",
                        "prompt": {
                            "system": """You are the Schedule Agent. Analyze the user query and extract all scheduling constraints.
    Assume 1 hour = 1 slot. 

    --- CURRENT BOOKED EVENTS ---
    Monday at 15:00: 'Afternoon: Low-Impact Cardio Intervals + Bodyweight Strength'
    Monday at 19:00: 'Meal: Quick Chickpea & Spinach Coconut Curry'
    Tuesday at 13:00: 'Meal: Quick Black Bean & Corn Quesadilla'
    Wednesday at 19:00: 'Meal: Little Italy Linguine With Chicken, Pesto and Pine Nuts'
    Thursday at 07:00: 'Thursday Morning: Boxing Technique & Low-Impact Conditioning'
    Thursday at 19:00: 'Meal: Black Bean Soup With Chips and Salsa'
    Sunday at 08:00: 'Meal: Greek Yogurt Berry Parfait'
    -----------------------------

    Actions:
    - ADD_HARD: Immovable events (meetings, doctor).
    - FIND_SLOT: Flexible events needing placement.
    - RESCHEDULE: Moving an existing event to a new time.
    - REMOVE: Deleting an existing event.
    - CHECK: User asking if they are free.

    CRITICAL RULES:
    1. LOGICAL TIME INFERENCE: If the user provides context (e.g., "morning before 9am"), calculate and output a specific valid time (e.g., "07:00"). If they say "Coffee", infer a logical daytime hour. Do NOT schedule non-logical times.
    2. RELATIVE DAYS: If the user asks to move an event to "another day" without specifying the day, output "OTHER" for the `preferred_day`.
    3. MATCHING: For RESCHEDULE or REMOVE, you MUST exactly extract ONLY the name inside the single quotes from the 'CURRENT BOOKED EVENTS' list for the `event_name` field. (e.g., If the list says `Monday at 18:00: 'Gym Workout'`, output `Gym Workout`, NOT the day/time).
    4. ROUTINE GENERATION: If the user asks to plan a routine (e.g., "plan 3 evening workouts"), output a "FIND_SLOT" action for EACH session. You MUST assign specific, spaced-out days (e.g., "Monday", "Wednesday", "Friday") for `preferred_day` to ensure they aren't scheduled on the same day.
    5. BIOLOGICAL SPACING (MEALS & WORKOUTS): You MUST analyze the CURRENT BOOKED EVENTS to prevent conflicts.
       - If scheduling a Workout: Do not place it within 2 hours after a scheduled meal.
       - If scheduling a Meal: If there is a workout that day, explicitly set `preferred_time` to 1-2 hours AFTER the workout for recovery, or 2+ hours BEFORE. 
       - Always output a specific `preferred_time` that respects these biological rules whenever possible.

    Output strictly in JSON with a list of "events":
    {
        "events": [
            {
                "action": "ADD_HARD" | "FIND_SLOT" | "RESCHEDULE" | "REMOVE" | "CHECK",
                "event_name": "string (MUST exactly match current schedule if RESCHEDULE/REMOVE)",
                "duration_slots": 1, 
                "preferred_day": "Monday" | "OTHER" | null, 
                "preferred_time": "18:00" 
            }
        ]
    }""",
                            "user": "Query: Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts."
                        },
                        "response": {
                            "events": [
                                {
                                    "action": "FIND_SLOT",
                                    "event_name": "Meal: Saturday Dinner (Meat & Vegetables, Nut-free, 40min)",
                                    "duration_slots": 1,
                                    "preferred_day": "Saturday",
                                    "preferred_time": "19:00"
                                }
                            ]
                        }
                    },
                    {
                        "module": "RecipeExtractor",
                        "prompt": {
                            "system": """You are a recipe database search assistant.

                Your job is to suggest a small list of recipe names that are likely to exist in the database and match the provided meal context.

                You will receive meal context as input. Use it to infer likely recipe names the user would want.

                TASK:
                - Suggest 3 to 5 recipe names.
                - Prefer common, searchable recipe names rather than poetic or highly specific titles.
                - Make names useful for SQL name matching against an existing recipes database.
                - Respect meal type, dietary constraints, available ingredients, and time constraints when those are present.
                - If the context is too vague, return a few broad but realistic recipe names.

                OUTPUT CONTRACT:
                Return only valid JSON in this exact shape:
                {
                    "recipe_names": ["Recipe Name 1", "Recipe Name 2", "Recipe Name 3"]
                }

                RULES:
                - `recipe_names` must be an array of strings.
                - Return between 3 and 5 names.
                - No explanation, no markdown, no extra keys.
                - Do not return categories; return recipe names only.""",
                            "user": """{
                "request_type": "generate",
                "meal_context": {
                    "free_time_mins": 60
                },
                "constraints": {
                    "dietary_restrictions": [
                    "vegetarian",
                    "vegetarian (overridden for this meal — meat allowed)",
                    "meat allowed for Monday dinner"
                    ],
                    "allergies": [
                    "nuts"
                    ],
                    "excluded_ingredients": [
                    "max prep time: 40 minutes",
                    "meal: lunch"
                    ]
                },
                "user_preferences": {
                    "general": "Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts."
                }
                }"""
                        },
                        "response": {
                            "recipe_names": [
                                "Chicken and Vegetable Stir-Fry",
                                "Beef and Broccoli Stir-Fry",
                                "Sheet Pan Sausage and Vegetables",
                                "Garlic Butter Shrimp with Asparagus and Cherry Tomatoes"
                            ]
                        }
                    },
                    {
                        "module": "RecipeExtractor",
                        "prompt": {
                            "system": """You are a recipe database query assistant.

                    Your job is to select the single most relevant recipe category to use as a database filter, given a meal context.

                    AVAILABLE CATEGORIES:
                    {
                "meal_type": {
                    "Dessert": 58645,
                    "Lunch/Snacks": 30956,
                    "Breakfast": 20364,
                    "Beverages": 15238,
                    "Salad Dressings": 4940,
                    "Frozen Desserts": 3554,
                    "Smoothies": 3525,
                    "Punch Beverage": 1956,
                    "Clear Soup": 1319,
                    "Brunch": 575
                },
                "dietary": {
                    "Low Protein": 6214,
                    "Low Cholesterol": 3452,
                    "Very Low Carbs": 3121,
                    "Healthy": 1598,
                    "High Protein": 1080,
                    "Vegan": 785,
                    "Lactose Free": 398,
                    "Kosher": 132,
                    "High Fiber": 75
                },
                "cuisine": {
                    "European": 3453,
                    "Asian": 2095,
                    "Mexican": 1930,
                    "Greek": 527,
                    "Chinese": 502,
                    "Thai": 419,
                    "Canadian": 380,
                    "Australian": 364,
                    "Japanese": 309,
                    "Southwest Asia (middle East)": 308,
                    "African": 253,
                    "Spanish": 249,
                    "Indian": 229,
                    "German": 227,
                    "Caribbean": 216,
                    "Moroccan": 178,
                    "South American": 134,
                    "Vietnamese": 104,
                    "Korean": 90,
                    "Filipino": 58,
                    "Turkish": 52,
                    "South African": 51,
                    "Brazilian": 50,
                    "Lebanese": 50,
                    "Native American": 45,
                    "Swedish": 37,
                    "Indonesian": 33,
                    "Cuban": 29,
                    "Polynesian": 29,
                    "Peruvian": 24
                },
                "occasion": {
                    "Summer": 406,
                    "Christmas": 350,
                    "Winter": 227,
                    "Thanksgiving": 212,
                    "Spring": 108,
                    "Halloween": 75,
                    "Birthday": 19
                },
                "cooking_method": {
                    "Microwave": 70,
                    "Pressure Cooker": 19,
                    "No Cook": 12
                },
                "ingredient_based": {
                    "Chicken": 11998,
                    "Potato": 10396,
                    "Chicken Breast": 10363,
                    "Pork": 10288,
                    "Cheese": 8139,
                    "Beans": 4455,
                    "Cheesecake": 3322,
                    "Rice": 2635,
                    "Yam/Sweet Potato": 2157,
                    "Spinach": 1909,
                    "Lamb/Sheep": 1672,
                    "Chicken Thigh & Leg": 1496,
                    "Whole Chicken": 1196,
                    "Apple": 1171,
                    "Black Beans": 1152,
                    "Long Grain Rice": 1022,
                    "White Rice": 990,
                    "Soy/Tofu": 900,
                    "Pineapple": 804,
                    "Brown Rice": 694,
                    "Roast Beef": 620,
                    "Pasta Shells": 536,
                    "Lemon": 503,
                    "Strawberry": 478,
                    "Mashed Potatoes": 376,
                    "Short Grain Rice": 319,
                    "Mango": 307,
                    "Turkey Breasts": 300,
                    "Catfish": 296,
                    "Macaroni And Cheese": 259,
                    "Chocolate Chip Cookies": 213,
                    "Crawfish": 159,
                    "Beef Organ Meats": 143,
                    "Pumpkin": 134,
                    "Avocado": 120,
                    "Egg Free": 117,
                    "Chicken Livers": 98,
                    "Whole Turkey": 98,
                    "Whitefish": 76,
                    "Medium Grain Rice": 70,
                    "No Shell Fish": 47
                }
                }

                    Each key is a semantic group (e.g. "meal_type", "cuisine", "dietary").
                    Each value is a dict of {category_name: recipe_count} — the exact strings stored in the database.

                    TASK:
                    - Read the meal context (provided by the user).
                    - Choose ONE category name (a leaf value, e.g. "Chicken", "Italian", "Healthy") that best narrows the database search.
                    - Prefer high-count categories when two options are equally relevant.
                    - Prefer specificity: cuisine or ingredient over a generic meal_type when the context implies it.
                    - If the context is vague or no category fits well, return null.

                    OUTPUT CONTRACT:
                    Return only valid JSON with a single key:
                    {"category": "ExactCategoryName"}
                    or:
                    {"category": null}

                    RULES:
                    - category must be copied verbatim from AVAILABLE CATEGORIES (exact capitalisation).
                    - Never return a group name (e.g. never return "meal_type" or "cuisine").
                    - Never return multiple categories.
                    - No explanation, no markdown, no extra keys.""",
                            "user": """{
                "request_type": "generate",
                "meal_context": {
                    "free_time_mins": 60
                },
                "constraints": {
                    "dietary_restrictions": [
                    "vegetarian",
                    "vegetarian (overridden for this meal — meat allowed)",
                    "meat allowed for Monday dinner"
                    ],
                    "allergies": [
                    "nuts"
                    ],
                    "excluded_ingredients": [
                    "max prep time: 40 minutes",
                    "meal: lunch"
                    ]
                },
                "user_preferences": {
                    "general": "Find me a recipe for Saturday dinner. I have 40 minutes to prepare it and I want something with meat and vegetables. I am allergic to nuts."
                }
                }"""
                        },
                        "response": {
                            "category": "Chicken"
                        }
                    },
                    {
                        "module": "RecipeExtractor",
                        "prompt": {
                            "system": """You evaluate ONE candidate recipe against the recipe context and return a scorecard used for ranking candidates.

                Input shape:
                {
                    "context": {...},
                    "candidate_recipe": {...}
                }

                Evaluate these dimensions on a 0-100 scale:
                1. `restrictions_safety` — follows dietary restrictions, allergies, and excluded ingredients.
                2. `nutrition_fit` — nutrition values are plausible and generally fit min/max targets.
                3. `description_fit` — matches the requested meal, component, style, and overall description.
                4. `time_fit` — fits the available time.
                5. `available_ingredients_fit` — reasonably close to available ingredients if they are provided.

                Rules:
                - Prioritize restrictions safety, description fit, and time fit.
                - Nutrition should be judged approximately, not as exact math.
                - Available-ingredients fit should penalize recipes that require many unrelated ingredients, not minor extras.
                - If information is missing, judge only from what is provided.
                - Set `hard_fail=true` if there is a clear allergy/excluded-ingredient conflict, a clear core dietary violation, or a complete mismatch to the requested meal/component.
                - If `hard_fail=true`, `overall_score` should usually be between 0 and 25.

                Return ONLY valid JSON in exactly this shape:
                {
                    "status": "ok" | "error",
                    "hard_fail": boolean,
                    "overall_score": number,
                    "dimension_scores": {
                        "restrictions_safety": number,
                        "nutrition_fit": number,
                        "description_fit": number,
                        "time_fit": number,
                        "available_ingredients_fit": number
                    },
                    "summary": "short overall judgment",
                    "strengths": ["string"],
                    "issues": ["string"],
                    "improvements": ["string"],
                    "decision_rationale": "brief explanation of the score"
                }

                Keep `summary` and `decision_rationale` concise. `issues` and `improvements` should be actionable.""",
                            "user": """{
                "recipe": {
                    "recipe_id": "483668",
                    "name": "Chicken Enchilada Skillet",
                    "description": "Quick skillet enchilada-style chicken with torn corn tortillas, tomatoes and cheese.",
                    "component": "main_course",
                    "prep_time_mins": 10,
                    "cook_time_mins": 10,
                    "total_time_mins": 20,
                    "ingredients": [
                    {
                        "name": "cooked shredded chicken",
                        "quantity": 2,
                        "unit": "cups"
                    },
                    {
                        "name": "corn tortillas",
                        "quantity": 6,
                        "unit": ""
                    },
                    {
                        "name": "tomatoes",
                        "quantity": 1,
                        "unit": "cup"
                    },
                    {
                        "name": "shredded cheese",
                        "quantity": 1,
                        "unit": "cup"
                    },
                    {
                        "name": "onion",
                        "quantity": 0.5,
                        "unit": "cup"
                    }
                    ],
                    "nutrition_per_serving": {
                    "calories": 221.5,
                    "protein_g": 20.2,
                    "carbs_g": 21.4,
                    "fat_g": 6.0,
                    "fiber_g": 3.0
                    }
                },
                "constraints": {},
                "nutritional_targets_min": {},
                "nutritional_targets_max": {},
                "meal_context": {
                    "day_of_week": "Saturday",
                    "meal_type": "dinner",
                    "course": "main_course"
                }
                }"""
                        },
                        "response": {
                            "status": "ok",
                            "hard_fail": False,
                            "overall_score": 91,
                            "dimension_scores": {
                                "restrictions_safety": 100,
                                "nutrition_fit": 80,
                                "description_fit": 95,
                                "time_fit": 95,
                                "available_ingredients_fit": 85
                            },
                            "summary": "Well-matched quick dinner main: safe, fast, and descriptively consistent. Nutrition data is plausible though serving/yield details are missing and recipe lacks seasoning/oil details.",
                            "strengths": [
                                "No dietary/allergy conflicts in the given constraints — very safe.",
                                "Matches meal context (dinner main) and description (quick skillet enchilada-style).",
                                "Very quick prep and cook time (20 minutes total) suitable for a fast weeknight or Saturday dinner."
                            ],
                            "issues": [
                                "Nutrition per serving is plausible but unclear because the recipe yield/servings are not stated.",
                                "Recipe omits common components like oil, salt, spices, and step-by-step method which affect nutrition and flavor.",
                                "Ingredient quantities (e.g., tortilla size/type, cup measures) could use more detail for precise nutrition estimates."
                            ],
                            "improvements": [
                                "Specify number of servings/yield so nutrition per serving can be validated and adjusted.",
                                "Add cooking steps and small pantry items (oil, salt, pepper, enchilada sauce or spices) and note optional garnishes.",
                                "Clarify tortilla type/size and approximate weight of shredded chicken used so calories/protein estimates are more accurate."
                            ],
                            "decision_rationale": "No constraints to violate and the recipe is clearly a quick skillet main course matching the request. Time and description alignment are strong. Nutrition is reasonably plausible but uncertain due to missing yield and small missing ingredient details; available-ingredients fit is good given no supplied pantry list."
                        }
                    },
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": """You are Nutrissistant, a helpful fitness and nutrition assistant. Compare the nutrition and fitness drafts. 
    Are there any critical conflicts? (e.g. heavy leg day with a zero-carb recovery meal, or a heavy meal scheduled 30 minutes before a high-intensity run).
    
    If there is a conflict, you MUST generate a friendly, conversational message for the user. Explain the issue simply, and ask if they would like you to fix it.
    
    Return JSON strictly matching this schema: 
    {
        "status": "ok" | "error", 
        "internal_reason": "Brief technical reason for the logs", 
        "friendly_message": "e.g., 'I noticed you have a heavy leg day planned on Tuesday, but your post-workout meal is very low in carbs. Would you like me to find a better recovery recipe?'"
    }""",
                            "user": "Nutrition: [...] \n Fitness: {'goal': 'Raise weekly activity, build aerobic base and full-body strength with low-impact intervals suitable for a beginner.'}"
                        },
                        "response": {
                            "status": "ok",
                            "internal_reason": "Found a likely mismatch: Chicken Mozzarella Panini shows very low carbs (5.2 g) which could conflict with post-workout recovery for the planned interval walk + bodyweight session.",
                            "friendly_message": "I noticed your workout is a moderate-intensity interval walk plus a bodyweight circuit (includes squats and other lower-body work). After that kind of session you’ll usually benefit from a post-workout meal with both protein and some carbs to help recovery. One recipe in your nutrition list — the Chicken Mozzarella Panini — is listed with only ~5 g carbs, which is unexpectedly low (ciabatta would normally add more carbs). That could be a database error, or it might make the panini a poor stand-alone recovery choice. Would you like me to: (a) correct/verify the panini nutrition, (b) suggest higher-carb post-workout options from your recipes (e.g., quesadilla, stir-fry, linguine), or (c) recommend simple add-ons to the panini (fruit, a side salad with grains, or a piece of toast) to make it better for recovery?"
                        }
                    }
                ]
            },
            {
                "prompt": "Can you reschedule my Thursday morning workout to Friday?",
                "full_response": "Cleared old 'Thursday Morning: Boxing Technique & Low-Impact Conditioning' to make room for reschedule. Rescheduled 'Thursday Morning: Boxing Technique & Low-Impact Conditioning' on Friday at 07:00.",
                "steps": [
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": "You are the Nutrissistant Supervisor. Analyze the user query...",
                            "user": "Query: Can you reschedule my Thursday morning workout to Friday?"
                        },
                        "response": {
                            "tasks": ["SCHEDULE"],
                            "goals": ["Lose 10-15 kg in the next year (get into shape)"],
                            "missing_info": [],
                            "extracted_context": {
                                "equipment": [],
                                "injuries": [],
                                "allergies": ["nuts"],
                                "dietary_restrictions": ["vegetarian", "vegetarian (overridden for this meal — meat allowed)", "meat allowed for Monday dinner"],
                                "general_workout_restrictions": [],
                                "general_meal_restrictions": []
                            }
                        }
                    },
                    {
                        "module": "ScheduleAgent",
                        "prompt": {
                            "system": "You are the Schedule Agent. Analyze the user query and extract all scheduling constraints...",
                            "user": "Query: Can you reschedule my Thursday morning workout to Friday?"
                        },
                        "response": {
                            "events": [
                                {
                                    "action": "RESCHEDULE",
                                    "event_name": "Thursday Morning: Boxing Technique & Low-Impact Conditioning",
                                    "duration_slots": 1,
                                    "preferred_day": "Friday",
                                    "preferred_time": "07:00"
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "prompt": "Plan 2 gym workouts for this week. I prefer morning sessions",
                "full_response": "Proposed 'Gym Workout' on Tuesday at 07:00. Proposed 'Gym Workout' on Thursday at 07:00.\n\nDone — I created two no-equipment morning gym sessions for this week: Tuesday 07:00 and Thursday 07:00, each ~60 minutes and beginner-friendly with joint-protecting options.\n\nI have successfully added your workouts to the calendar.",
                "steps": [
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": "You are the Nutrissistant Supervisor. Analyze the user query...",
                            "user": "Query: Plan 2 gym workouts for this week. I prefer morning sessions"
                        },
                        "response": {
                            "tasks": [
                                {
                                    "task": "WORKOUT",
                                    "description": "Design two gym workouts for this week, scheduled as morning sessions. Provide one strength-focused session (full-body emphasis) and one conditioning/circuit session (cardio + metabolic conditioning). Include exercises, sets, reps (or time), tempo/notes, rest intervals, and estimated total duration for each session. Assume gym access and no injuries."
                                }
                            ],
                            "goals": [
                                "Resolved query: Plan 2 gym workouts for this week for Ido (age 21, height 1.80 m, weight 150 kg) who prefers morning sessions. Create one strength-focused gym workout and one conditioning/circuit gym workout suitable for his profile and no injuries; assume gym equipment is available."
                            ],
                            "missing_info": [],
                            "extracted_context": {
                                "equipment": [],
                                "injuries": [],
                                "allergies": ["nuts"],
                                "dietary_restrictions": ["vegetarian", "vegetarian (overridden for this meal — meat allowed)", "meat allowed for Monday dinner"],
                                "general_workout_restrictions": ["prefer morning sessions"],
                                "general_meal_restrictions": []
                            }
                        }
                    },
                    {
                        "module": "ScheduleAgent",
                        "prompt": {
                            "system": "You are the Schedule Agent. Analyze the user query and extract all scheduling constraints...",
                            "user": "Query: Plan 2 gym workouts for this week. I prefer morning sessions"
                        },
                        "response": {
                            "events": [
                                {
                                    "action": "FIND_SLOT",
                                    "event_name": "Gym Workout",
                                    "duration_slots": 1,
                                    "preferred_day": "Tuesday",
                                    "preferred_time": "07:00"
                                },
                                {
                                    "action": "FIND_SLOT",
                                    "event_name": "Gym Workout",
                                    "duration_slots": 1,
                                    "preferred_day": "Thursday",
                                    "preferred_time": "07:00"
                                }
                            ]
                        }
                    },
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": """You are a routing assistant for workout generation.
Choose exactly one pipeline mode and return JSON only:
{
    "pipeline": "reflection|simple_rag",
  "reason": "short reason"
}

Selection policy:
1. Choose "reflection" when the request is for a full new routine, broad redesign, or substantial restructuring.
2. Choose "simple_rag" when the request is a small adjustment (swap/replace/add/remove/modify one part).
3. Use current_routine context if available.
4. Be decisive: always return one mode.""",
                            "user": """{"query": "Plan 2 gym workouts for this week. I prefer morning sessions", "current_routine": {"routine_name": "Two-Session Start: Boxing + Low-Impact Cardio for Weight Loss", "goal": "Start a sustainable routine to lose 10-15 kg over the year by building consistent, no-equipment cardio and boxing-style conditioning plus bodyweight strength; fit sessions into your preferred afternoon slot and a Thursday morning session.", "units": [{"title": "Afternoon: Low-Impact Cardio Intervals + Bodyweight Strength", "focus_type": "mixed", "day_label": "Monday", "duration_limit_mins": null}, {"title": "Morning: Shadowboxing, Footwork & Conditioning", "focus_type": "boxing", "day_label": "Thursday", "duration_limit_mins": null}]}}"""
                        },
                        "response": {
                            "pipeline": "simple_rag",
                            "raw_pipeline": "simple_rag",
                            "reason": "Small adjustment of an existing two-session routine to gym-friendly, both morning sessions."
                        }
                    },
                    {
                        "module": "WorkoutAgent",
                        "prompt": {
                            "system": "You are the Nutrissistant Workout Agent. Generate a single JSON object only for a weekly routine composed of multiple workout units...",
                            "user": "{\"query\": \"Plan 2 gym workouts for this week. I prefer morning sessions\", \"routine_operation\": \"create\", \"schedule_context\": \"Calendar context suggests one available workout block is around 60 minutes. Scheduled slots include: Tuesday 07:00, Thursday 07:00.\"}"
                        },
                        "response": {
                            "response": "Done — I created two no-equipment morning gym sessions for this week: Tuesday 07:00 and Thursday 07:00, each ~60 minutes and beginner-friendly with joint-protecting options.",
                            "routine_draft": {
                                "routine_name": "Two Morning Gym Sessions: Strength + Boxing-Style Conditioning",
                                "goal": "Begin sustainable weight loss and improve fitness with two weekly morning, no-equipment gym workouts (strength and boxing-style conditioning) that protect joints and build consistency.",
                                "units": [
                                    {
                                        "title": "Tuesday Morning: Bodyweight Strength & Low-Impact Conditioning",
                                        "focus_type": "strength",
                                        "day_label": "Tuesday",
                                        "duration_limit_mins": 60,
                                        "draft": {
                                            "goal": "Build foundational strength, increase daily calorie burn, and protect joints while starting steady weight loss.",
                                            "duration_limit_mins": 60,
                                            "workout_name": "Bodyweight Strength Circuit (AM)",
                                            "difficulty": "beginner",
                                            "focus_type": "strength",
                                            "session_outline": [
                                                {"section": "warmup", "minutes": 10, "items": ["Brisk march or easy jog in place — 2 minutes", "Dynamic hip swings and leg swings — 1 minute each side", "Arm circles + shoulder mobility — 2 minutes", "Bodyweight glute bridges — 2 sets of 10 slow reps to wake hips", "Ankle & knee mobility (controlled knee lifts) — 2 minutes"]},
                                                {"section": "main", "minutes": 40, "items": ["3 rounds circuit (perform each exercise then rest 60s between rounds):", "A. Incline or knee push-ups — 8-12 reps", "B. Box-free squats or sit-to-stand from a chair — 12-15 reps", "C. Reverse lunges (alternating) or static split holds — 8-10 reps per leg", "D. Glute bridges (single-leg progressions optional) — 12-15 reps", "E. Plank (or knee-plank) — 30-45 seconds", "F. Low-impact cardio finisher: marching high knees or step-ups — 60s"]},
                                                {"section": "cooldown", "minutes": 10, "items": ["Slow walking or marching to bring HR down — 2 minutes", "Seated hamstring and quad stretches — 3 minutes", "Chest and shoulder stretch — 2 minutes", "Deep diaphragmatic breathing + gentle spinal twists — 3 minutes"]}
                                            ],
                                            "exercise_details": [
                                                {"name": "Incline or Knee Push-ups", "sets": 3, "reps": "8-12", "rest_seconds": 60, "notes": "Use a wall or bench for incline if floor push-ups are too challenging. Keep core braced and avoid sagging hips."},
                                                {"name": "Bodyweight Squats or Sit-to-Stand", "sets": 3, "reps": "12-15", "rest_seconds": 60, "notes": "If knees feel sensitive, use a chair to perform sit-to-stand to reduce joint load. Focus on hip hinging and upright chest."},
                                                {"name": "Reverse Lunges (alternating)", "sets": 3, "reps": "8-10 per leg", "rest_seconds": 60, "notes": "Go shallow if balance or knees are an issue. Hold onto a stable surface for balance if needed."},
                                                {"name": "Glute Bridges", "sets": 3, "reps": "12-15", "rest_seconds": 45, "notes": "Squeeze glutes at the top; progress to single-leg bridges when comfortable."},
                                                {"name": "Plank (or Knee-Plank)", "sets": 3, "reps": "30-45s", "rest_seconds": 45, "notes": "Keep a neutral spine; drop to knees for easier variation."},
                                                {"name": "Low-Impact Cardio Finisher (marching high knees / step-ups)", "sets": 3, "reps": "60s per round", "rest_seconds": 45, "notes": "Keep intensity moderate; aim to raise heart rate without jarring the joints."}
                                            ],
                                            "safety_notes": ["Begin at a comfortable intensity and progress week-to-week; prioritise controlled movement and breathing.", "Given your current weight, favour low-impact options (incline push-ups, chair sit-to-stand, step-ups) to protect knees and lower back.", "Stop and seek medical attention if you experience chest pain, dizziness, or unusual shortness of breath.", "If you have any pre-existing conditions, check with a healthcare provider before beginning strenuous exercise."]
                                        }
                                    },
                                    {
                                        "title": "Thursday Morning: Boxing-Style Conditioning & Core (No Equipment)",
                                        "focus_type": "boxing",
                                        "day_label": "Thursday",
                                        "duration_limit_mins": 60,
                                        "draft": {
                                            "goal": "Improve aerobic fitness and coordination with boxing-style intervals and core work that suits a no-equipment morning session.",
                                            "duration_limit_mins": 60,
                                            "workout_name": "Shadowboxing Intervals + Core Circuit (AM)",
                                            "difficulty": "beginner",
                                            "focus_type": "boxing",
                                            "session_outline": [
                                                {"section": "warmup", "minutes": 10, "items": ["Light marching or easy hop/step in place — 2 minutes", "Dynamic shoulder, neck and trunk rotations — 3 minutes", "Shadowboxing slow rounds focusing on stance and jab-cross technique — 3 minutes", "Ankle and hip loosening drills — 2 minutes"]},
                                                {"section": "main", "minutes": 40, "items": ["Interval blocks: 6 x (3-minute shadowboxing round at moderate intensity + 60s recovery). Focus on jab-cross, slip, and footwork.", "Core-strength circuit (2 rounds): dead bugs 10-12, bird dogs 8-10 per side, side plank 20-30s per side.", "Optional low-impact cardio bursts between core moves: mountain climbers (slow controlled) 30-40s or step-back burpees (no jump) 8-10 reps."]},
                                                {"section": "cooldown", "minutes": 10, "items": ["Slow walking and shoulder shake-out — 2 minutes", "Standing quad and calf stretch — 3 minutes", "Seated hamstring stretch and gentle spine twists — 3 minutes", "Focused breathing to return HR to baseline — 2 minutes"]}
                                            ],
                                            "exercise_details": [
                                                {"name": "Shadowboxing (moderate intensity rounds)", "sets": 6, "reps": "3 min rounds", "rest_seconds": 60, "notes": "Keep rounds controlled: focus on technique, rhythmic breathing, and light footwork. Move on the balls of the feet but avoid excessive bouncing if joints hurt."},
                                                {"name": "Dead Bugs", "sets": 2, "reps": "10-12", "rest_seconds": 30, "notes": "Keep lower back in contact with the floor; move slowly to build core stability."},
                                                {"name": "Bird Dogs", "sets": 2, "reps": "8-10 per side", "rest_seconds": 30, "notes": "Perform with slow controlled movement; maintain a neutral spine."},
                                                {"name": "Side Plank", "sets": 2, "reps": "20-30s per side", "rest_seconds": 30, "notes": "Drop to knee-side support if full side plank is too challenging."},
                                                {"name": "Mountain Climbers (controlled) or Step-back Burpees (low-impact)", "sets": 2, "reps": "30-40s / 8-10 reps", "rest_seconds": 45, "notes": "Choose the lower-impact option (step-back burpee with no jump) if knees or back feel stressed."}
                                            ],
                                            "safety_notes": ["Shadowboxing should be performed with good posture—don’t overreach on punches; use footwork to create distance instead.", "Favor low-impact versions of cardio moves to reduce joint stress; avoid high-impact jumping if you feel discomfort.", "Keep hydration and allow at least 24–48 hours between intense sessions if muscles feel overly sore.", "If you have new or worsening joint pain, reduce range of motion or skip the offending exercise and consult a professional."]
                                        }
                                    }
                                ]
                            }
                        }
                    },
                    {
                        "module": "Supervisor",
                        "prompt": {
                            "system": "You are Nutrissistant, a helpful fitness and nutrition assistant. Compare the nutrition and fitness drafts. Are there any critical conflicts?...",
                            "user": "Nutrition: [...] \n Fitness: {'routine_name': 'Two Morning Gym Sessions...'}"
                        },
                        "response": {
                            "status": "ok",
                            "internal_reason": "No time-stamped meals provided; flagged one recipe with unusually low reported carbs (possible mismatch) that could be a post-workout issue.",
                            "friendly_message": "I checked both drafts. Because meals don’t have assigned times, I didn’t find any explicit timing conflicts (e.g., a heavy meal 30 minutes before a high‑intensity session). One thing I noticed: the Chicken Mozzarella Panini lists only ~5 g carbs per serving despite including a ciabatta roll — that looks unusually low and could be insufficient as a post‑workout recovery option after your Tuesday strength or Thursday boxing sessions. Would you like me to (a) correct or re‑estimate that recipe’s nutrition, (b) suggest better post‑workout recovery meals/snacks (more carbs + protein), or (c) map your meals to workout times and recheck for any timing or recovery conflicts?"
                        }
                    }
                ]
            }
        ]
    }



@app.get("/api/model_architecture")
def get_model_architecture():
    """Returns the architecture diagram as a PNG image."""
    image_path = os.path.join("images", "agent_architecture.png")
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Architecture diagram not found.")
    
    return FileResponse(image_path, media_type="image/png")

@app.post("/api/execute")
def execute_agent(request: ExecuteRequest):
    """The main entry point. User sends prompt, API returns response and steps trace."""
    try:
        # Call the logic we built earlier
        agent_result = supervisor_agent.orchestrate_workflow(request.prompt)

        if not isinstance(agent_result, dict):
            return {
                "status": "error",
                "error": "Supervisor returned an invalid response shape.",
                "response": None,
                "steps": []
            }

        response_text = agent_result.get("response")
        steps = agent_result.get("steps", [])
        if response_text is None:
            return {
                "status": "error",
                "error": "Supervisor returned no response text.",
                "response": None,
                "steps": steps if isinstance(steps, list) else []
            }
        
        # Format the response exactly as the PDF requires
        return {
            "status": "ok",
            "error": None,
            "response": response_text,
            "steps": steps if isinstance(steps, list) else []
        }
    except Exception as e:
        # If anything crashes, return the required error schema
        return {
            "status": "error",
            "error": str(e),
            "response": None,
            "steps": []
        }


# ---------------------------------------------------------------------------
# Streamlit proxy — forward all non-API traffic to the local Streamlit process
# ---------------------------------------------------------------------------
_STREAMLIT_HTTP = "http://127.0.0.1:8501"
_STREAMLIT_WS = "ws://127.0.0.1:8501"
_HOP_BY_HOP = frozenset([
    "connection", "keep-alive", "transfer-encoding", "te",
    "trailer", "proxy-authorization", "proxy-authenticate",
    "upgrade", "content-encoding",
])
_PROXY_RETRIES = int(os.getenv("PROXY_RETRIES", "12"))
_PROXY_RETRY_DELAY_SECONDS = float(os.getenv("PROXY_RETRY_DELAY_SECONDS", "0.5"))
_PROXY_CONNECT_TIMEOUT_SECONDS = float(os.getenv("PROXY_CONNECT_TIMEOUT_SECONDS", "5.0"))
_PROXY_READ_TIMEOUT_SECONDS = float(os.getenv("PROXY_READ_TIMEOUT_SECONDS", "20.0"))
_PROXY_WRITE_TIMEOUT_SECONDS = float(os.getenv("PROXY_WRITE_TIMEOUT_SECONDS", "20.0"))
_PROXY_POOL_TIMEOUT_SECONDS = float(os.getenv("PROXY_POOL_TIMEOUT_SECONDS", "5.0"))


@app.websocket("/{path:path}")
async def _ws_proxy(path: str, websocket: WebSocket):
    """Proxy WebSocket connections to the Streamlit process."""
    # Forward the subprotocol requested by the browser. Chrome strictly
    # requires the server to echo it back; Safari is lenient and works either
    # way. Without this, Chrome/Windows users get a silent WebSocket drop and
    # the Streamlit UI never loads.
    requested_subprotocols = websocket.scope.get("subprotocols", [])
    subprotocol = requested_subprotocols[0] if requested_subprotocols else None
    await websocket.accept(subprotocol=subprotocol)
    qs = websocket.scope.get("query_string", b"").decode()
    target = f"{_STREAMLIT_WS}/{path}" + (f"?{qs}" if qs else "")
    try:
        upstream = None
        for _ in range(_PROXY_RETRIES):
            try:
                upstream = await websockets.connect(
                    target,
                    subprotocols=[subprotocol] if subprotocol else None,
                )
                break
            except Exception:
                await asyncio.sleep(_PROXY_RETRY_DELAY_SECONDS)

        if upstream is None:
            await websocket.close(code=1013)
            return

        async with upstream:
            async def _to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if msg.get("bytes"):
                            await upstream.send(msg["bytes"])
                        elif msg.get("text"):
                            await upstream.send(msg["text"])
                except (WebSocketDisconnect, Exception):
                    pass

            async def _to_client():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(str(message))
                except Exception:
                    pass

            await asyncio.gather(_to_upstream(), _to_client())
    except Exception:
        pass


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"])
async def _http_proxy(path: str, request: Request):
    """Proxy all remaining HTTP requests to the Streamlit process."""
    qs = request.scope.get("query_string", b"").decode()
    target = f"{_STREAMLIT_HTTP}/{path}" + (f"?{qs}" if qs else "")
    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }
    body = await request.body()
    resp = None
    timeout = httpx.Timeout(
        connect=_PROXY_CONNECT_TIMEOUT_SECONDS,
        read=_PROXY_READ_TIMEOUT_SECONDS,
        write=_PROXY_WRITE_TIMEOUT_SECONDS,
        pool=_PROXY_POOL_TIMEOUT_SECONDS,
    )
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        for _ in range(_PROXY_RETRIES):
            try:
                resp = await client.request(
                    method=request.method,
                    url=target,
                    headers=fwd_headers,
                    content=body,
                )
                break
            except (httpx.TimeoutException, httpx.TransportError):
                await asyncio.sleep(_PROXY_RETRY_DELAY_SECONDS)

    if resp is None:
        return Response(
            content="Nutrissistant UI is starting or waking up. Please refresh in a few seconds.",
            status_code=503,
            media_type="text/plain",
        )

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)