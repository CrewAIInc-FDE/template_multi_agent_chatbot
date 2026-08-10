---
name: slack
description: Rules for searching and summarizing a Slack workspace through the CrewAI Platform Slack integration. Use when the user asks what was said, decided, or shared in Slack, or who to talk to about something.
metadata:
  author: template_multi_agent_chatbot
  version: "1.0"
---

## Slack — Core Rules

Available tools (subject to what the workspace has connected):

| Tool | Use for |
|---|---|
| `slack/search_messages` | Finding what was said about a topic |
| `slack/list_channels` | Discovering where a topic lives |
| `slack/list_members` | Who is in a channel |
| `slack/get_user_by_email` / `slack/get_users_by_name` | Resolving a person to a Slack user |
| `slack/send_message` / `slack/send_direct_message` | Posting — only if enabled |

### Search technique

Search is keyword matching, not semantic. This is the single biggest determinant
of whether you find anything.

1. **Extract the distinctive terms.** "What did the team decide about the pricing
   change for enterprise?" → search `pricing enterprise`, not the whole question.
2. **Prefer nouns and proper nouns.** Product names, project codenames, and
   company names match well. Verbs and filler words do not.
3. **Start narrow, then widen.** If `pricing enterprise` returns nothing, try
   `pricing`. Two or three refinements is reasonable.
4. **Use `list_channels` when the topic implies a place.** For "what's happening
   with support", finding `#support` is often more useful than any single message.

### Answer quality

- **Attribute everything.** Say who said it and roughly when. "Sarah proposed X
  on Tuesday in #product" is useful; "someone mentioned X" is not.
- **Summarize threads, don't transcribe them.** The user wants the outcome, not
  a wall of quoted messages.
- **Say when a discussion was inconclusive.** Slack conversations often trail off
  without a decision. Reporting "this was discussed but not resolved" is a
  correct and valuable answer — inventing a conclusion is not.
- **Distinguish recent from old.** A decision from last year may have been
  superseded. Note the date when it matters.

### Non-negotiables

- **Never fabricate a message, channel, person, or timestamp.** If search returns
  nothing, say so and suggest a different term.
- **Never guess at a channel name.** Use `list_channels` to confirm it exists.
- **Never quote something you did not retrieve.** Every quote must come from a
  tool result.
- **Respect the read-only setting.** When sending is disabled, do not claim to
  have sent anything — offer to draft the text instead.

### When Slack is the wrong tool

Slack holds internal conversation, not documentation or public facts. If the user
wants product documentation, or something from the open web, say so and let the
conversation route elsewhere rather than searching Slack for it.
