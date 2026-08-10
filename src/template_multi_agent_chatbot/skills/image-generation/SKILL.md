---
name: image-generation
description: Rules for generating and editing images with the Nano Banana tools. Use the generation tool when the user asks to create a new image from scratch. Use the editing tool when the user asks to modify, alter, or transform an existing image.
metadata:
  author: template_multi_agent_chatbot
  version: "4.0"
---

## ⛔ ABSOLUTE RULE — NO INTERNAL HANDLES, NO EXCEPTIONS ⛔

**NEVER mention image references (`image#1`), file paths, filenames, storage locations, URLs, bucket names, CDN endpoints, or ANY infrastructure detail in messages to the user.** The tools return an `image#N` reference — that is YOUR handle for chaining edits. It is NEVER for the user.

❌ FORBIDDEN — never say anything like:
- "Here it is: `image#1`"
- "I've saved that as image#2 for you"
- "The file is located at…"
- Any message containing `image#`, a file path, or a filename

✅ CORRECT — say only:
- "Your image is ready!"
- "Here's your sunset waterfall painting — golden light, soft Monet-inspired brushstrokes, and a dreamy mist."

The image is **automatically delivered** to the user alongside your message. You do NOT need to reference it, link it, or tell the user where it is. Just describe what you created.

**If you catch yourself about to include a reference or filename in a user-facing message, DELETE IT.**

---

## Image Generation — Core Rules

Use **`Nano Banana Image Generation`** when the user wants a brand-new image.

1. **Only use for visual creative assets.** Never for charts, plots, or data viz.
2. **Always expand the user's request into a concrete prompt** before calling the tool. A strong prompt names: subject, style, composition, lighting, color palette, and constraints. If the request is vague, either make reasonable assumptions (and mention them) or ask one focused clarifying question.
3. **Pass a single descriptive `prompt` string.** On success the tool returns an image reference (e.g. `image#1`) for internal use. On failure it returns a failure message — apologize briefly and offer to retry with a refined prompt.
4. **The image is automatically delivered to the user.** You do NOT need to attach, embed, or link it yourself. **Never include the returned `image#N` reference in your message to the user** — it is an internal handle for the editing tool, not something the user should ever see.
5. **After success, confirm and describe in 1–2 sentences** what you generated so the user knows what to expect alongside the image they receive.

## Image Editing — Core Rules

Use **`Nano Banana Image Editing`** when the user wants to modify an existing image.

1. **Requires a source image.** You must provide the `image_reference` of an image generated earlier in this conversation (e.g. `image#1`). Find it in the conversation history — tool entries read `Generated image#1` or `Edited image#1 into image#2`. If no reference exists, generate a new image instead of guessing one.
2. **Write clear edit instructions in the `prompt`.** Describe what should change (e.g. "add sunglasses to the person", "change the background to a beach at sunset", "make it look like a watercolor painting"). Be specific about what to keep and what to alter.
3. **On success the edited image is saved** and automatically delivered to the user, just like generation.
4. **After success, briefly describe the edits** so the user knows what changed compared to the original.

## Choosing Between Generation and Editing

| User intent | Tool to use |
|---|---|
| "Create / generate / draw / make me an image of…" | Generation |
| "Edit / modify / change / add … to that image" | Editing |
| "Make it more…" / "Remove the…" / "Change the background" | Editing (on the most recent image) |
| "Try again" / "Redo it" with no existing image referenced | Generation (new attempt) |

When the user says "change it" or "edit that", use the most recent `image#N` reference from the conversation as the `image_reference`. Editing an already-edited image is normal — chain from the newest reference, not the original.

Additional prompt-crafting detail (checklist, worked examples, anti-patterns) is available in the skill's `references/prompt-crafting.md`.
