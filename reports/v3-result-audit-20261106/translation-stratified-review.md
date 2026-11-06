# V3 Chinese Translation Stratified Review

## Scope

- Compared the fixed-seed `stratified-sample-bounded.json` sample against the v2 English and v3 Chinese ShareGPT exports.
- Sample: 15 conversations, 5 tiers x 3 conversations; 76 sampled messages total. The sample contains the first four and last two messages per conversation, with single-round samples contributing two messages. Excerpts are explicitly marked and limited to 1,200 characters per message.
- Reviewed meaning, persona, pronouns, omissions/additions, and the logic of English-word discussions. No APIs, network, credentials, source edits, or unrelated project files were used.

## Confirmed Issue

- **Moderate, added factual detail** — `baseline:col:lw-misaligned:023`, message index **1** (gpt). EN: “a scientist named Saul Erdel who died shortly after”; ZH: “这位科学家随后不久便心脏病发去世” (“died of a heart attack shortly after”). The Chinese adds a cause of death absent from the English.

## Other Notes

- The two long-reply excerpts reviewed from `sft:4885acb25da33c22bed85a5c` align with the full corresponding messages in the v2/v3 exports; apparent boundary differences came from character-limited excerpts, not translation additions.
- In the Dutch meal-planning sample, the assistant's source reply substitutes fish for fruit. The Chinese preserves that source reply accurately; this is an inconsistency in the English/Dutch source conversation, not a translation error.
- No other confirmed translation problems were found in this bounded sample. This is a stratified sample review, not a full-export audit.
