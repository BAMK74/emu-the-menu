# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack
Existing codebase: Python 3.13, FastAPI, server-rendered Jinja templates, plain CSS in `templates/base.html`, small inline scripts, SQLite. No front-end build step.

## Users
The people of one household (adults and teens; children appear as Eaters managed by an adult). They use it on a phone in the kitchen and while shopping (checking Stock, scanning barcodes, marking a Meal cooked, reading the shopping list), and on a PC or tablet when planning Menus and entering Recipes and Ingredients. Cooking and shopping mean hands that are often busy or messy.

## Product Purpose
A self-hosted household app that tracks what is in the fridge, freezer and pantry and suggests Menus that fit each person's calorie goal, allergies and diet, the weather, Occasions and the Stock on hand. Success is a week of Meals the household can cook from what they have, with a short shopping list and no one served something they cannot eat.

## Positioning
Private and self-hosted on a Raspberry Pi, reached over Tailscale, with no account or subscription. It plans from the household's real Stock (amounts and expiry), real Recipes and each Eater's allergens and diet, and it is explicit about what it cannot know.

## Operating Context
Runs on a Raspberry Pi 5 and is reached only over Tailscale. Stock is added by barcode scan (Open Food Facts), by hand, or from receipts and labels later. Recipes are typed or imported from a pasted web address. A Menu is planned, saved, optionally accepted (which holds Stock), and its Meals are marked cooked, which uses up Stock.

## Capabilities and Constraints
Pages: Stock, Ingredients (with a bulk review and bulk calories), Recipes, Eaters, Menu, Saved menus, Occasions, Settings, Disclaimer. Terminology follows `GLOSSARY.md` (Menu, Meal, Eater, Stock, Cooldown, Shopping Allowance, Occasion). Safety wording is part of the product: a Recipe with an unreviewed Ingredient is shown as "Not checked", and suggestions are never presented as medical or allergy advice. No login yet: Tailscale is the only gate. Noncommercial licence. Low-powered host, so pages stay light and server-rendered.

## Brand Commitments
The name is "Emu the Menu". Wording is plain and friendly, uses the glossary terms with capital letters, and avoids jargon.

## Evidence on Hand
Real data lives only in the household's own database. There are no testimonials, screenshots or customers to cite, and none should be invented.

## Product Principles
1. Safety before convenience: allergy and diet checks are hard rules, and what is unknown is said plainly ("Not checked"), never hidden.
2. Suggestions, not decisions: the app proposes, the person confirms; nothing is saved or assumed without them.
3. Honest about uncertainty: rough figures (calories, suggestions) are labelled as rough.
4. Quick in the kitchen: common actions work on a phone with one hand and forgive a missed tap.
5. Calm and plain: the household reads it every day, so it stays readable and unfussy.

## Accessibility & Inclusion
No specific standard was required. Because the app is used while cooking and shopping, tap targets should be large and forgiving of messy hands; follow sensible web accessibility basics (contrast, labels, keyboard use).
