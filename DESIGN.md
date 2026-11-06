---
name: Emu the Menu
description: A calm, tidy household kitchen notebook in leaf green, with plain words and safety colours kept for what matters.
colors:
  leaf-green: "#3f7d58"
  leaf-green-on-dark: "#5aa578"
  sage-mist: "#e3efe7"
  paper-green: "#f4f6f3"
  card-white: "#ffffff"
  ink-green: "#1f2a24"
  soft-grey-green: "#66756c"
  hairline: "#d5ddd7"
  alert-red: "#b3392c"
  alert-red-wash: "#fbeae8"
  warning-amber: "#e0a526"
  warning-amber-wash: "#fdf3da"
  soon-brown: "#8a5a00"
  soon-wash: "#fdf0cf"
  ok-green: "#2f7a4c"
  ok-wash: "#e3f2e8"
typography:
  title:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0em"
  title-wide:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1.75rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  body:
    fontFamily: "system-ui, sans-serif"
    fontSize: "0.9rem"
    fontWeight: 400
    lineHeight: 1.4
  body-wide:
    fontFamily: "system-ui, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.4
  label:
    fontFamily: "system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    letterSpacing: "0.03em"
  chip:
    fontFamily: "system-ui, sans-serif"
    fontSize: "0.72rem"
    fontWeight: 400
    lineHeight: 1
rounded:
  field: "0.6rem"
  card: "0.6rem"
  panel: "0.8rem"
  pill: "999px"
spacing:
  xs: "0.3rem"
  sm: "0.6rem"
  md: "1rem"
  lg: "1.4rem"
components:
  button-primary:
    backgroundColor: "{colors.leaf-green}"
    textColor: "{colors.card-white}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    height: "2.3rem"
    padding: "0 0.9rem"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.soft-grey-green}"
    rounded: "{rounded.field}"
    height: "2.3rem"
    padding: "0 0.9rem"
  input-field:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.ink-green}"
    rounded: "{rounded.field}"
    height: "2.3rem"
    padding: "0 0.6rem"
  card:
    backgroundColor: "{colors.card-white}"
    textColor: "{colors.ink-green}"
    rounded: "{rounded.card}"
    padding: "0.6rem 0.8rem"
  chip:
    backgroundColor: "{colors.sage-mist}"
    textColor: "{colors.soft-grey-green}"
    typography: "{typography.chip}"
    rounded: "{rounded.pill}"
    padding: "0.28rem 0.55rem"
  chip-soon:
    backgroundColor: "{colors.soon-wash}"
    textColor: "{colors.soon-brown}"
    rounded: "{rounded.pill}"
  nav-item-current:
    backgroundColor: "{colors.sage-mist}"
    textColor: "{colors.leaf-green}"
    rounded: "{rounded.pill}"
    padding: "0.35rem 0.7rem"
---

# Design System: Emu the Menu

## Overview

**Creative North Star: "The Kitchen Notebook"**

Emu the Menu looks like a well-kept household notebook that happens to be clever: pale paper-green pages, white cards ruled with a hairline, and one friendly leaf green that means "go ahead". It is read every day, often on a phone in a busy kitchen, so it stays calm, plain and findable. Words are everyday words; the Glossary terms (Menu, Meal, Stock, Eater) are capitalised and used consistently.

Colour carries safety. Green is the single action colour. Amber and red are reserved for things a person must not miss: soon-to-expire Stock, "Not checked" ingredients, allergen conflicts, expired items and destructive actions. Nothing decorative uses them.

The confirmed direction is **friendly and tactile**: soft, well-rounded shapes, clear hover and pressed states, large forgiving tap targets for messy hands. The implementation today is softer and flatter than that goal, with a hairline-and-whisper-shadow card style; push toward tactile when refining, never toward cold or dense.

**Key Characteristics:**
- Pale paper-green page, white cards, hairline borders, whisper shadows.
- One green accent; amber and red are for safety only.
- System font, plain words, small uppercase labels on forms.
- Pills for navigation, tags and ticks; gently rounded rectangles for cards and fields.
- Works on a 24rem phone column and grows to a 74rem two-column layout.
- Follows the system light or dark setting.

## Colors

A leaf-green accent on pale green-grey neutrals, with warm amber and brick red held back as safety signals. The values below are the light theme; the dark theme keeps the same roles on a deep green-black ground.

### Primary
- **Leaf Green** (#3f7d58; #5aa578 in dark mode): the only action colour. Primary buttons, the current navigation item, links in body text, quantity figures, selected ticks, focus rings and card hover borders.

### Secondary
- **Sage Mist** (#e3efe7; #1d2d24 in dark): the quiet tint behind chips, the current nav pill, selected tick-pills and the top of the page on wide screens.

### Tertiary (safety signals)
- **Warning Amber** (#e0a526) on **Amber Wash** (#fdf3da): the "Suggestions, not advice" and "Not checked" notes and warnings.
- **Soon Brown** (#8a5a00) on **Soon Wash** (#fdf0cf): "expires soon" chips and "suggested" figures.
- **Alert Red** (#b3392c) on **Alert Wash** (#fbeae8): errors, expired Stock, allergen chips and destructive buttons.
- **OK Green** (#2f7a4c) on **OK Wash** (#e3f2e8): "fine" chips such as plenty of shelf life.

### Neutral
- **Paper Green** (#f4f6f3): the page background.
- **Card White** (#ffffff): cards, fields, dialogs, header.
- **Ink Green** (#1f2a24): body text.
- **Soft Grey-Green** (#66756c): secondary text, labels, hints.
- **Hairline** (#d5ddd7): all borders and dividers.

### Named Rules
**The Safety Colour Rule.** Amber and red appear only for something a person could be harmed or misled by (allergens, unreviewed ingredients, expiry, deletion). Never use them for decoration or emphasis.

**The One Green Rule.** Leaf Green is the only colour that means "do this" or "this is selected". Do not add a second accent.

**The Seasonal Mood Intent (not built yet).** Kevin wants the accent mood to follow the season: summer bright and joyful, autumn "soupy" and calm, spring vibrant, winter festive yet cold. The current green is the baseline. When this is built, seasons shift the accent, wash and background tints through the existing `:root` tokens only, safety colours stay fixed, and every season keeps text contrast readable.

## Typography

**Display Font:** system-ui, sans-serif
**Body Font:** system-ui, sans-serif
**Label/Mono Font:** the same system font; there is no mono.

**Character:** Whatever the device already uses, so text is familiar, fast to load on the Pi and never a web-font download. Hierarchy comes from weight, size and small uppercase labels, not from a second typeface.

### Hierarchy
- **Display / Title** (700, 1.25rem phone; 1.75rem from 640px, letter-spacing -0.02em on wide): the page heading.
- **Headline** (700, 1.05-1.15rem): panel and results headings, brand name.
- **Title** (600, 1.02rem): the item name on a card, shown capitalised.
- **Body** (400, 0.9rem phone; 1rem from 640px, line-height 1.4): everything else. Legal text uses a looser 1.5 line height.
- **Label** (600, 0.75rem, uppercase, letter-spacing 0.03em, soft grey-green): form field labels and group legends.
- **Chip and hint** (0.72-0.8rem): tags, small notes, meta lines.

### Named Rules
**The Plain Words Rule.** Prefer an everyday word over jargon. Safety messages say what is wrong and what to do ("not checked: pesto, so it may contain something this person cannot have").

## Layout

A single centred column: 24rem on phones, 36rem from 640px (46rem for reading pages), and a 74rem `wide` layout from 1000px, where forms sit in a sticky 28rem left column and cards fill a responsive grid (minimum 17-18rem per card). The header is a wrap-friendly bar with the brand on the left and pill navigation on the right. Rhythm is built on 0.3rem, 0.6rem and 1rem steps; page padding is 1rem on phones and 2rem 1.5rem from 640px. Content stacks first; two-up pairs (for example start date and days) keep two columns even on phones. Fields grow from 2.3rem to 2.7rem tall on larger screens.

## Elevation & Depth

Mostly flat with a whisper of depth. Cards and the header carry a hairline border and a very soft two-layer shadow (`0 1px 2px rgba(31,42,36,.06), 0 2px 8px rgba(31,42,36,.04)`), which becomes a single faint shadow in dark mode. Depth otherwise comes from the pale page behind white cards. Hovering a clickable card (wide screens) only turns its border green. Dialogs use a dark scrim (55% black) instead of a bigger shadow.

### Named Rules
**The Whisper Shadow Rule.** Shadows are barely there and never coloured. A heavier shadow is allowed only to show that something is lifted or pressed, and should come with the move toward the friendly-and-tactile direction, not before it.

## Shapes

Gently rounded and consistent: 0.6rem (about 10px) on fields, buttons and cards; 0.8rem on forms and dialogs; full pills (999px) for chips, navigation, tick-choices and tags. Borders are 1px hairlines; dashed hairlines mark empty states. Warnings and errors reuse the same radius with a coloured border.

## Components

### Buttons
- **Shape:** 0.6rem corners, 2.3rem tall (2.7rem from 640px), 0.9rem side padding.
- **Primary:** Leaf Green fill, white text (dark text on the lighter dark-mode green), weight 600. Hover brightens slightly.
- **Quiet:** transparent, soft grey-green text and a hairline border; hover turns text and border green.
- **Danger:** quiet button with alert-red text and border; hover fills with the red wash.
- **Focus:** a 2px leaf-green outline with a 1px offset on every control.
- **Small variants:** row action buttons are 2rem tall at 0.8rem.

### Chips
- **Style:** pill, 0.72rem text, Sage Mist fill with soft grey-green text.
- **State:** tinted variants carry meaning: OK, Soon (bold), Expired and Allergen (red), New (green), and an outlined "none".
- **Link chips** drop their underline and brighten on hover.

### Cards / Containers
- **Corner Style:** 0.6rem.
- **Background:** Card White (dark mode: deep green-black card).
- **Shadow Strategy:** the whisper shadow, see Elevation.
- **Border:** 1px hairline; turns leaf green on hover for clickable cards.
- **Internal Padding:** 0.6rem 0.8rem on phones, 0.9rem 1.1rem on larger screens.
- A whole card can be one big link: the link's `::after` stretches over the card, with the focus ring on the card.

### Inputs / Fields
- **Style:** white, 1px hairline, 0.6rem radius, 2.3rem tall, 0.6rem side padding.
- **Focus:** the shared 2px leaf-green outline.
- **Error / Disabled:** disabled fields drop to 45% opacity. Errors appear in a red-washed banner above the form, not beside the field.
- Form blocks (`form.add`) are white panels with grid gaps of 0.6rem (0.9rem on larger screens) and uppercase labels.

### Navigation
- **Style:** a header bar with the leaf-green rounded-square logo and "Emu the Menu", then a row of pill links in soft grey-green.
- **States:** hover tints with Sage Mist; the current page is Sage Mist with leaf-green bold text.
- **Mobile treatment:** the row wraps rather than collapsing to a menu.

### Tick-pills
Allergens, diets and meal types are pill-shaped checkboxes that flow to fit the width; a ticked pill takes the Sage Mist fill and a leaf-green border with bolder text.

### Warning notes
A pale amber note with an amber border, 0.85rem text and a bold link, used for the safety "Suggestions, not advice" message at the top of planning pages and for held-stock notes. The disclaimer reminder uses the same style, smaller.

## Do's and Don'ts

### Do:
- **Do** keep Leaf Green as the one action and selection colour.
- **Do** reserve amber and red for safety, expiry and destructive actions.
- **Do** keep tap targets at least 2.3rem tall and give rows room, because hands are often messy.
- **Do** use the CSS variables in `base.html` for any new colour, radius or shadow, and check both light and dark.
- **Do** keep text plain and use the Glossary terms; say what is unknown ("Not checked", "Calories unknown").
- **Do** label rough or suggested figures as such, in Soon Brown.

### Don't:
- **Don't** add a second accent colour or use amber/red for decoration.
- **Don't** hide uncertainty behind confident styling; unreviewed or unknown things must look different.
- **Don't** add web fonts, large images or heavy scripts; the app is served from a Raspberry Pi.
- **Don't** make controls smaller than the current field height to fit more in.
- **Don't** hardcode colours outside the `:root` tokens; seasonal moods will need to swap them.
