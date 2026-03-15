import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from constants import RECIPE_CSV_PATH


def clean_r_vector_string(text):
	if pd.isna(text):
		return text

	text = str(text).strip()
	if text.startswith("c(") and text.endswith(")"):
		text = text[2:-1]

	return text.replace('"', "").replace("'", "").strip()


def filter_recipe_database(path):
	target_columns = [
		"Name",
		"TotalTime",
		"Keywords",
		"RecipeIngredientParts",
		"RecipeInstructions",
		"RecipeCategory",
	]

	df = pd.read_csv(path, usecols=target_columns)
	df = df.rename(
		columns={
			"Name": "name",
			"TotalTime": "total_time",
			"Keywords": "tags",
			"RecipeCategory": "category",
			"RecipeIngredientParts": "ingredients",
			"RecipeInstructions": "steps",
		}
	)

	df = df.dropna(subset=["name", "total_time", "ingredients", "steps", "category"])

	extracted_time = df["total_time"].str.extract(r"PT(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?")
	extracted_time = extracted_time.fillna(0).astype(float)
	df["minutes"] = ((extracted_time["hours"] * 60) + extracted_time["minutes"]).astype(int)

	df = df[df["minutes"] <= 180]
	df = df[df["minutes"] > 0]

	for col in ["tags", "ingredients", "steps", "category"]:
		df[col] = df[col].apply(clean_r_vector_string)

	return df


def build_category_summary(df, min_count=10):
	all_categories = df["category"].value_counts()
	all_categories = all_categories[all_categories > min_count]

	KEYWORD_GROUPS = {
		"meal_type": [
			"breakfast", "brunch", "lunch", "dinner", "supper", "dessert",
			"snack", "appetizer", "side dish", "entree", "main dish",
			"salad", "soup", "beverage", "drink", "smoothie", "cocktail", "punch",
		],
		"time": ["minute", "hour", " min", "quick", "fast", "easy", "30-", "15-", "speed"],
		"dietary": [
			"vegan", "vegetarian", "gluten", "low carb", "low fat", "low calorie",
			"low sodium", "low cholesterol", "low protein", "low sugar",
			"high protein", "high fiber", "diabetic", "kosher", "halal",
			"lactose", "dairy free", "nut free", "paleo", "keto", "whole30",
			"heart healthy", "healthy", "diet", "weight",
		],
		"cuisine": [
			"american", "italian", "mexican", "asian", "chinese", "japanese",
			"indian", "thai", "french", "greek", "spanish", "mediterranean",
			"middle east", "african", "european", "caribbean", "swedish",
			"german", "korean", "vietnamese", "turkish", "lebanese", "moroccan",
			"canadian", "australian", "brazilian", "peruvian", "cuban",
			"filipino", "indonesian", "polynesian",
		],
		"skill": ["beginner", "advanced", "easy", "technique", "for two", "for one", "college", "student"],
		"occasion": [
			"christmas", "thanksgiving", "halloween", "easter", "ramadan",
			"hanukkah", "passover", "birthday", "wedding", "party",
			"super bowl", "summer", "winter", "spring", "fall", "holiday",
			"new year", "valentine", "mother", "father", "4th of july",
			"memorial day", "labor day",
		],
		"cooking_method": [
			"baked", "grilled", "fried", "steamed", "roasted", "broiled",
			"slow cook", "pressure cook", "stir fry", "microwave",
			"no bake", "no cook", "one pot", "one pan", "sheet pan",
			"freezer", "make ahead", "overnight",
		],
		"ingredient_based": [
			"chicken", "beef", "pork", "lamb", "turkey", "fish",
			"shrimp", "seafood", "pasta", "rice", "potato", "mushroom",
			"tomato", "lemon", "cheese", "egg", "tofu", "beans",
			"pumpkin", "squash", "spinach", "broccoli", "avocado",
			"chocolate", "apple", "banana", "berry", "mango",
		],
	}

	classified = {group: [] for group in KEYWORD_GROUPS}
	classified["other"] = []

	for cat in all_categories.index:
		cat_lower = cat.lower()
		matched = False
		for group, keywords in KEYWORD_GROUPS.items():
			if any(keyword in cat_lower for keyword in keywords):
				classified[group].append(cat)
				matched = True
				break
		if not matched:
			classified["other"].append(cat)

	classified.pop("other", None)
	classified.pop("time", None)
	classified.pop("skill", None)

	summary = {
		group: {category: int(all_categories[category]) for category in categories}
		for group, categories in classified.items()
		if categories
	}
	return summary


def save_catagory_stats(output_path=None, min_count=10):
	if output_path is None:
		output_path = PROJECT_ROOT / "src" / "recipe_extractor" / "category_summary.json"
	else:
		output_path = Path(output_path)

	filtered_df = filter_recipe_database(RECIPE_CSV_PATH)
	summary = build_category_summary(filtered_df, min_count=min_count)

	output_path.parent.mkdir(parents=True, exist_ok=True)
	with open(output_path, "w", encoding="utf-8") as file:
		json.dump(summary, file, indent=4)

	print(f"Saved category summary to: {output_path}")
	return summary


if __name__ == "__main__":
	save_catagory_stats()
