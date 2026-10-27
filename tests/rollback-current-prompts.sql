-- REVIEW ONLY. Never restore an old database snapshot after new events.
-- Run only with all gateway/dashboard/cron consumers stopped and after
-- inspecting the exact current schema, unfinished runtime ledger and backup.
-- Preserve current rows and the content-addressed prompt table. Clear the
-- pointer so a later old-runtime prompt update cannot be masked by a stale hash.
BEGIN IMMEDIATE;
UPDATE sessions
SET system_prompt = (
    SELECT system_prompts.prompt
    FROM system_prompts
    WHERE system_prompts.hash = sessions.system_prompt_hash
), system_prompt_hash = NULL
WHERE system_prompt_hash IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM system_prompts
      WHERE system_prompts.hash = sessions.system_prompt_hash
  );
COMMIT;
