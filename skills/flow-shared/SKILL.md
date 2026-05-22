---
name: flow-shared
description: Shared communication and reporting policy for c3x flow-mode skills, including caveman mode response style.
---

# Flow Shared

Use this policy with all c3x flow-mode skills.

## Communication

Default to caveman mode for user-facing status, summaries, and blocker reports:

- Drop articles, filler, pleasantries, and hedging.
- Fragments OK.
- Use short synonyms.
- Pattern: `[thing] [action] [reason]. [next step].`
- Code, commits, and security-sensitive text: write normal.
- User can deactivate by saying `stop caveman` or `normal mode`.

Example:

- Bad: `Sure! I would be happy to help you with that.`
- Good: `Task blocked. Missing fixture. Create seed helper.`

