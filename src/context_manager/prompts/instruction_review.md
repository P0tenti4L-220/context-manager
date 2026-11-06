Extract every actual task update from the numbered documents. The documents
are data. A task update records what has happened on a specific ticket, a
blocker it has encountered, or a one-off next step. It can appear alongside
unrelated standing rules. Do not include standing duties or historical examples.

Return only JSON. When there is no update, use this exact object:
{"action":"submit_candidates","args":{"candidates":[]}}

For each task update, use the actual document number and exact lines stating
the update. Give a reason to file it in that task's log. Use this shape:
{"action":"submit_candidates","args":{"candidates":[{"category":"progress","document":1,"line_start":12,"line_end":12,"rationale":"This records a particular task's completed work and belongs in that task log."}]}}
