---
name: slack
description: Rules for searching and summarizing a Slack workspace through the CrewAI Platform Slack integration. Use when the user asks what was said, decided, or shared in Slack, or who to talk to about something.
metadata:
  author: template_multi_agent_chatbot
  version: "1.0"
---

## Slack — Core Rules

**You are read-only.** You can search and read; you cannot post, reply, react,
pin, or change anything. That is a hard boundary, not a preference.

| Tool | Use for |
|---|---|
| `search_messages` | Finding what was said about a topic |
| `find_channels` | Locating a channel by name or topic |
| `list_all_channels` | Browsing what channels exist |
| `fetch_conversation_history` | Reading recent messages in a channel |
| `fetch_message_thread_from_a_conversation` | Reading a full thread |
| `retrieve_conversation_information` | Channel purpose, topic, metadata |
| `retrieve_conversation_members_list` | Who is in a channel |
| `find_users` | Locating a person by name |
| `retrieve_detailed_user_information` | Who someone is — title, profile |
| `retrieve_message_permalink_url` | A link to a specific message |

### Search technique

Search is keyword matching, not semantic. This is the single biggest determinant
of whether you find anything.

1. **Extract the distinctive terms.** "What did the team decide about the pricing
   change for enterprise?" → search `pricing enterprise`, not the whole question.
2. **Prefer nouns and proper nouns.** Product names, project codenames, and
   company names match well. Verbs and filler words do not.
3. **Start narrow, then widen.** If `pricing enterprise` returns nothing, try
   `pricing`. Two or three refinements is reasonable.
4. **Use `find_channels` when the topic implies a place.** For "what's happening
   with support", finding `#support` and reading its recent history is often more
   useful than any single search hit.
5. **Follow a promising hit into its thread.** `search_messages` returns isolated
   messages; the decision usually lives in the replies. Use
   `fetch_message_thread_from_a_conversation` before concluding anything.

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
- **Never claim to have sent, posted, or changed anything.** You have no tools
  that can. When asked to post, say you can only read Slack and offer to draft
  the message text for the user to send themselves.

### When Slack is the wrong tool

Slack holds internal conversation, not documentation or public facts. If the user
wants product documentation, or something from the open web, say so and let the
conversation route elsewhere rather than searching Slack for it.
